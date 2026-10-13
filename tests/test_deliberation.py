import asyncio
import copy

import pytest

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.deliberation import DeliberationPlanner
from preact.core.interfaces import EngineFailure
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.queue_multi_selection import QueueMultiSelectionWorld
from preact.domains.software_deliberation import (
    SoftwareGoalAdapter,
    SoftwarePredictionAdapter,
    WorkflowSoftwareWorld,
)
from preact.engines.local import LocalHeuristic, LocalVerifier


def setup(tmp_path, horizon=2, calls=64, predictor=None):
    world = WorkflowSoftwareWorld()
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    source = SoftwarePredictionAdapter(world, store, horizon=horizon, predictor=predictor)
    planner = DeliberationPlanner(world, source, SoftwareGoalAdapter())
    wrapper = QueueMultiSelectionWorld(world, planner)
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
        planner,
        [Goal(name="Reusable validator", metric="workflow_progress", target=1)],
        Policy(search=False, width=2, max_nodes=2, max_calls=calls, calibration=False),
        candidate_selector=planner,
    )
    return world, store, planner, wrapper, agent


def executed(store, runs):
    choices = []
    for run in runs:
        events = store.read_events(run)
        outcomes = [e["data"] for e in events if e["kind"] == "outcome"]
        nodes = {
            e["data"]["node"]["id"]: e["data"]["node"]
            for e in events
            if e["kind"] == "node_updated"
        }
        for outcome in outcomes:
            choices.append(nodes[outcome["node_id"]]["action"])
    return choices


async def test_short_and_sequence_change_actual_first_action_and_replan(tmp_path):
    records = []
    for horizon, first, rounds in ((1, "Immediate repair", 3), (2, "Prepare validator", 2)):
        world, store, planner, wrapper, agent = setup(tmp_path / str(horizon), horizon)
        result = await agent.run(wrapper, 4)
        actions = executed(store, result.run_ids)
        assert result.success and not result.unsafe
        assert actions[0]["name"] == first and len(actions) == rounds
        assert len(await agent.memory.retrieve("software")) == rounds
        assert any(r["memory_references"] for r in planner.records[1:])
        assert all(r["status"] == "estimated" for r in planner.records)
        for run in result.run_ids:
            events = store.read_events(run)
            assert len([e for e in events if e["kind"] == "outcome"]) == 1
            outcome = next(e["data"] for e in events if e["kind"] == "outcome")
            assert store.execution_record(outcome["observation"]["receipt"])["status"] == "complete"
            assert (
                next(e["data"] for e in events if e["kind"] == "authorization")["decision"]
                == "execute"
            )
        records.append(planner.records[0])
    assert records[0]["state"]["payload"] == records[1]["state"]["payload"]
    assert [c["fingerprint"] for c in records[0]["simulation"]["candidates"]] == [
        c["fingerprint"] for c in records[1]["simulation"]["candidates"]
    ]
    for candidate in records[1]["simulation"]["candidates"]:
        for r in candidate["records"]:
            assert r["prediction"]["evidence"] == "inference"
            assert not r["prediction"]["mandatory_checks"]
        seq = candidate["records"][-1]
        assert seq["request"]["conditioning"]
        assert seq["prediction"]["conditioning"] == seq["request"]["conditioning"]


@pytest.mark.parametrize(
    "fault",
    [
        "state",
        "action",
        "version",
        "lineage",
        "cancel",
        "exception",
        "objective",
        "record",
        "receipt",
    ],
)
async def test_invalid_deliberation_never_executes(tmp_path, monkeypatch, fault):
    world, store, planner, wrapper, agent = setup(tmp_path)
    original = planner.source.predictor.predict
    if fault in {"state", "action", "version", "lineage", "cancel", "exception"}:

        async def broken(request):
            if fault == "cancel":
                raise asyncio.CancelledError()
            if fault == "exception":
                raise ValueError("inference unavailable")
            p = await original(request)
            if fault == "state":
                p.state_id = "forged"
            if fault == "action":
                p.action_ids = ["unrequested"]
            if fault == "version":
                p.engine_version = "other-version"
            if fault == "lineage":
                p.successor.parent_id = "unrelated"
            return p

        monkeypatch.setattr(planner.source.predictor, "predict", broken)
    else:
        observe = world.observe

        async def changed():
            state = await observe()
            if planner.pending:
                if fault == "objective":
                    planner.value = SoftwareGoalAdapter(action_cost=999)
                if fault == "record":
                    planner.pending["scores"][0]["value"] += 1
                if fault == "receipt":
                    store.append(planner.run_id, "deliberation", copy.deepcopy(planner.pending))
            return state

        monkeypatch.setattr(world, "observe", changed)
    with pytest.raises((ValueError, EngineFailure, asyncio.CancelledError)):
        await agent.run(wrapper, 1)
    assert world.payload["stage"] == "initial" and not world.receipts
    assert planner.pending is None


async def test_prediction_budget_abstains_before_computing_or_side_effect(tmp_path):
    world, store, planner, wrapper, agent = setup(tmp_path, calls=1)
    result = await agent.run(wrapper, 1)
    assert result.status == "abstained" and not world.receipts and not planner.records
    assert result.rounds[0]["steps"] == 0


async def queue_setup(
    tmp_path,
    *,
    projection=3,
    train_service=3,
    observed_service=3,
    continuation=None,
    warmup="probe",
):
    from preact.cognition.queue_model import MonitoredQueueSource
    from preact.domains.information_queue import InformationQueueWorld
    from preact.domains.queue_deliberation import QueueGoalAdapter, QueuePredictionAdapter
    from preact.domains.queue_service_features import QueueServiceAdapter
    from preact.engines.information_queue import InformationBoundVerifier
    from preact.engines.queue_temporal_guard import QueueTemporalGuard
    from preact.learning import TabularDynamicsTrainer, TransitionDataset
    from scripts.benchmark_queue_temporal import execute_sequence

    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    collect = {"search": False, "calibration": False, "width": 1, "max_nodes": 1, "max_calls": 64}
    teacher = InformationQueueWorld(seed=701, ticks=32, target=6, noise=0)
    teacher._service = [train_service] * 32
    training = {
        "teacher": await execute_sequence(teacher, ["probe"] * 16, store, artifacts, collect)
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    world = InformationQueueWorld(seed=1, ticks=32, target=6, noise=0)
    world._service = [observed_service] * 32
    initial = await world.observe()
    prefix = await execute_sequence(world, [warmup] * 8, store, artifacts, collect)
    guard = QueueTemporalGuard(
        store, world.task, model, training, episode_id="decision", initial=initial
    )
    monitored = MonitoredQueueSource(store, guard, training)
    source = QueuePredictionAdapter(
        monitored, **({"continuation": continuation} if continuation else {})
    )
    planner = DeliberationPlanner(world, source, QueueGoalAdapter(projection), prefix_runs=prefix)
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry(
            [
                InformationBoundVerifier(world.task),
                InformationBoundVerifier(world.task, future=True),
            ]
        ),
        planner,
        [Goal(name="Deliver", metric="delivered", target=6)],
        Policy(search=False, width=3, max_nodes=3, max_calls=64, calibration=False),
        candidate_selector=planner,
    )
    agent.memory.run_ids = prefix
    return world, store, planner, QueueMultiSelectionWorld(world, planner), agent


async def test_common_planner_queue_h3_changes_real_action_without_mean_state_rollout(tmp_path):
    for projection, amount in ((1, 0), (3, 3)):
        world, store, planner, wrapper, agent = await queue_setup(
            tmp_path / str(projection), projection=projection
        )
        result = await agent.run(wrapper, 3)
        assert executed(store, result.run_ids)[0]["payload"]["amount"] == amount
        assert planner.records[0]["simulation"]["continuation"] == "environment_only"
        for c in planner.records[0]["simulation"]["candidates"]:
            assert c["records"][0]["prediction"]["horizon"] == 3
            assert c["records"][0]["prediction"]["successor"] is None
        assert world.payload["tick"] == 11 and not result.unsafe


@pytest.mark.parametrize("reason", ["invalidated", "partial", "unsupported"])
async def test_queue_unavailable_or_unsupported_never_uses_old_score(tmp_path, reason):
    from preact.core.models import Continuation

    world, store, planner, wrapper, agent = await queue_setup(
        tmp_path,
        train_service=1 if reason == "invalidated" else 3,
        warmup=0 if reason == "partial" else "probe",
        continuation=Continuation.ACTION_SEQUENCE if reason == "unsupported" else None,
    )
    result = await agent.run(wrapper, 1)
    assert result.rounds[0]["steps"] == 0 and world.payload["tick"] == 8
    assert planner.records[0]["status"] == "unknown"
    assert planner.records[0]["ranked_fingerprints"] == []


async def test_high_value_unqualified_future_does_not_override_gate(tmp_path):
    world, store, planner, wrapper, agent = setup(tmp_path)

    class Veto(LocalVerifier):
        async def predict(self, request):
            p = await super().predict(request)
            if request.actions[0].name == "Prepare validator":
                p.mandatory_checks["regressions"] = False
                p.violations.append("owned_prepare_veto")
            return p

    agent.registry = Registry([LocalHeuristic(world), Veto(world)])
    result = await agent.run(wrapper, 1)
    assert (
        planner.records[0]["simulation"]["candidates"][1]["action"]["name"] == "Prepare validator"
    )
    assert (
        planner.records[0]["ranked_fingerprints"][0]
        == planner.records[0]["simulation"]["candidates"][1]["fingerprint"]
    )
    assert executed(store, result.run_ids)[0]["name"] == "Immediate repair"


async def test_software_h3_has_accepted_finite_intervention_branches(tmp_path):
    world, store, planner, wrapper, agent = setup(tmp_path, horizon=3)
    result = await agent.run(wrapper, 1)
    assert not result.unsafe and len(executed(store, result.run_ids)) == 1
    assert planner.records[0]["simulation"]["horizon"] == 3
    for f in planner.records[0]["simulation"]["candidates"]:
        for path in f["paths"]:
            assert len(path["actions"]) == len(path["states"]) == 3
            assert path["weight"] is None
            assert all(s["kind"] == "hypothetical" for s in path["states"])
    assert len(await agent.memory.retrieve("software")) == 1


@pytest.mark.parametrize("fault", ["source_update", "health", "anchor", "provenance"])
async def test_queue_basis_change_before_dispatch_never_executes(tmp_path, monkeypatch, fault):
    world, store, planner, wrapper, agent = await queue_setup(tmp_path)
    observe = world.observe

    async def changed():
        state = await observe()
        if planner.pending:
            if fault == "source_update":
                store.append(planner.prefix_runs[0], "external_writer", {"changed": True})
            elif fault == "health":
                planner.source.monitored.guard._latched = True
            elif fault == "anchor":
                planner.source.monitored.guard._monitoring_epoch = "other-epoch"
            else:
                state.provenance = "forged"
        return state

    monkeypatch.setattr(world, "observe", changed)
    with pytest.raises(ValueError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8
    assert planner.pending is None


@pytest.mark.parametrize(
    "fault",
    ["score", "continuation", "lineage", "model_version", "gate", "authorization", "outcome"],
)
async def test_independent_auditor_rejects_modified_saved_evidence(tmp_path, fault):
    import json

    from sqlalchemy import update

    from scripts.audit_deliberation import audit_case
    from scripts.bench_deliberation import run_case

    protocol = json.loads(open("benchmarks/deliberation-pilot-v2.json").read())
    folder = tmp_path / "bundle"
    await run_case(protocol, "software", "workflow", "sequence", 11, folder)
    assert (await audit_case(folder))["status"] == "passed"
    metadata = json.loads((folder / "case.json").read_text())
    store = Store("sqlite:///" + str(folder / "ledger.db"))
    run = metadata["runs"][0]
    kind = {
        "gate": "candidate_qualification",
        "authorization": "authorization",
        "outcome": "outcome",
    }.get(fault, "deliberation")
    event = next(e for e in store.read_events(run) if e["kind"] == kind)
    data = copy.deepcopy(event["data"])
    if fault == "score":
        data["scores"][0]["value"] += 1
    elif fault == "continuation":
        data["simulation"]["continuation"] = "environment_only"
    elif fault == "lineage":
        data["simulation"]["candidates"][0]["records"][-1]["request"]["conditioning"][
            "transitions"
        ][0]["successor"]["parent_id"] = "forged"
    elif fault == "model_version":
        data["simulation"]["candidates"][0]["records"][0]["prediction"]["engine_version"] = "wrong"
    elif fault == "gate":
        data["batch"]["candidates"][0]["evaluation"]["utility"] = 123
    elif fault == "authorization":
        data["expires_at"] = 0
    else:
        data["observation"]["state"]["payload"]["workflow_progress"] = 100
    with store.db.begin() as connection:
        connection.execute(
            update(store.events)
            .where(store.events.c.run_id == run, store.events.c.seq == event["seq"])
            .values(data=data)
        )
    with pytest.raises((ValueError, EngineFailure)):
        await audit_case(folder)
