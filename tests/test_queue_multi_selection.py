import asyncio

import pytest
from sqlalchemy import update

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.queue_model import MonitoredQueueSource
from preact.cognition.queue_multi import QueueMultiPlanner
from preact.core.candidates import CandidateBatch
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_admission_fixture import AdmissionFixtureVerifier, AdmissionQueueFixture
from preact.domains.queue_multi_selection import QueueMultiSelectionWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from scripts.benchmark_queue_multi_selection import prepare
from scripts.benchmark_queue_temporal import execute_sequence

COLLECTION = dict(search=False, calibration=False, width=1, max_nodes=1, max_calls=128)


async def setup(tmp_path, *, services=None, admission=False, max_calls=128, engines=True):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    training_world = InformationQueueWorld(ticks=64, target=999, noise=0, capacity=12)
    training_world._service = (services or [1] * 16) + [1] * 48
    training = {
        "training": await execute_sequence(
            training_world, ["probe"] * 16, store, artifacts, COLLECTION
        )
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    cls = AdmissionQueueFixture if admission else InformationQueueWorld
    world = cls(seed=101, ticks=48, target=20, capacity=12, noise=0)
    world._service = ([3] * 48) if admission else [3, 3] + [1] * 46
    guard = QueueTemporalGuard(
        store, world.task, model, training, episode_id="monitor", initial=await world.observe()
    )
    prefix = await prepare(
        world,
        ["probe"] * 8 if admission else ["probe"] * 8 + [3, 3, 0, 0, 0],
        store,
        artifacts,
        COLLECTION,
    )
    planner = QueueMultiPlanner(MonitoredQueueSource(store, guard, training), prefix_runs=prefix)
    wrapper = QueueMultiSelectionWorld(world, planner)
    verifier = (
        AdmissionFixtureVerifier(world.task) if admission else InformationBoundVerifier(world.task)
    )
    registry = Registry(
        [verifier, InformationBoundVerifier(world.task, future=True)] if engines else []
    )
    agent = CognitiveAgent(
        store,
        artifacts,
        registry,
        planner,
        [Goal(name="Deliver", metric="delivered", target=20)],
        Policy(search=False, calibration=False, width=3, max_nodes=3, max_calls=max_calls),
        candidate_selector=planner,
    )
    return store, world, planner, wrapper, agent


async def test_top_value_is_independently_rejected_and_second_executes(tmp_path):
    store, world, planner, wrapper, agent = await setup(tmp_path, services=[3] * 16, admission=True)
    result = await agent.run(wrapper, 1)
    assert result.rounds[0]["steps"] == 1
    record = planner.records[0]
    assert record["health"]["status"] == "available"
    assert record["selected"] is None  # Prediction record never claims a final choice.
    assert all(c["public_bound_safe"] for c in record["candidates"])
    events = store.read_events(result.run_ids[0])
    batch = CandidateBatch.model_validate(
        next(e["data"]["batch"] for e in events if e["kind"] == "candidate_qualification")
    )
    assert [c.status for c in batch.candidates] == ["rejected", "qualified", "qualified"]
    assert all(c.verification_performed for c in batch.candidates)
    assert batch.candidates[0].evaluation.checks["submission_permit"] is False
    assert (
        next(
            c
            for c in record["candidates"]
            if c["fingerprint"] == record["first_choice_fingerprint"]
        )["amount"]
        == 3
    )
    row = (await TransitionDataset(store, {"monitor": result.run_ids}).snapshot()).transitions[0]
    assert row.action.payload["amount"] == 1
    assert store.execution_record(row.receipt)["status"] == "complete"
    assert world.executed[-1].fingerprint == row.action.fingerprint


async def test_two_receipt_trained_models_change_execution_not_utility_or_proposal_order(tmp_path):
    states, amounts = [], []
    for label, services in (("low", [3, 3] + [1] * 14), ("mixed", [3, 1] * 8)):
        store, world, planner, wrapper, agent = await setup(tmp_path / label, services=services)
        states.append((await world.observe()).model_dump(exclude={"timestamp"}))
        result = await agent.run(wrapper, 1)
        events = store.read_events(result.run_ids[0])
        batch = CandidateBatch.model_validate(
            next(e["data"]["batch"] for e in events if e["kind"] == "candidate_qualification")
        )
        assert all(c.status == "qualified" for c in batch.candidates)
        assert len({c.evaluation.utility for c in batch.candidates}) == 1
        assert [c.action.payload["amount"] for c in batch.candidates] == [3, 1, 0]
        assert planner.records[0]["health"]["status"] == "available"
        rows = (await TransitionDataset(store, {"monitor": result.run_ids}).snapshot()).transitions
        amounts.append(rows[0].action.payload["amount"])
    assert states[0] == states[1]
    assert amounts == [1, 3]


@pytest.mark.parametrize("max_calls,engines", [(4, True), (7, True), (128, False)])
async def test_unresolved_or_unexpanded_candidates_never_call_selector(
    tmp_path, max_calls, engines, monkeypatch
):
    store, world, planner, wrapper, agent = await setup(
        tmp_path, max_calls=max_calls, engines=engines
    )
    tick = world.payload["tick"]

    def forbidden(batch):
        raise AssertionError("Incomplete qualification reached selector")

    monkeypatch.setattr(planner, "select", forbidden)
    result = await agent.run(wrapper, 1)
    assert result.rounds[0]["steps"] == 0 and world.payload["tick"] == tick
    events = store.read_events(result.run_ids[0])
    batch = next(e["data"] for e in events if e["kind"] == "candidate_qualification")
    assert not batch["complete"]
    assert not any(e["kind"] == "execution_intent" for e in events)


@pytest.mark.parametrize(
    "failure",
    [
        "invalid",
        "mutate",
        "same_value_mutation",
        "exception",
        "cancel",
        "store_write",
        "async",
        "policy",
    ],
)
async def test_selector_failure_never_falls_back_to_core_utility(tmp_path, failure, monkeypatch):
    store, world, planner, wrapper, agent = await setup(tmp_path, services=[3, 1] * 8)
    tick = world.payload["tick"]
    existing = {r["id"] for r in store.list_runs()}
    active_runtime = []
    emit = Runtime.emit

    async def capture(runtime, kind, data):
        if kind == "candidate_qualification":
            active_runtime.append(runtime)
        return await emit(runtime, kind, data)

    monkeypatch.setattr(Runtime, "emit", capture)

    def fail(batch):
        if failure == "invalid":
            return "not-a-qualified-node"
        if failure == "mutate":
            batch.state.payload["queue"] = 100
            return batch.qualified_ids[0]
        if failure == "same_value_mutation":
            # Python considers 13 == 13.0; exact authority content must not.
            batch.state.payload["tick"] = float(batch.state.payload["tick"])
            return batch.qualified_ids[0]
        if failure == "cancel":
            raise asyncio.CancelledError()
        if failure == "store_write":
            store.append(batch.run_id, "forbidden_selector_write", {})
            return batch.qualified_ids[0]
        if failure == "policy":
            active_runtime[0].policy.risk_threshold = 1
            return batch.qualified_ids[0]
        if failure == "async":

            async def extra_work():
                return batch.qualified_ids[0]

            return extra_work()
        raise RuntimeError("selector failed")

    monkeypatch.setattr(planner, "select", fail)
    with pytest.raises((ValueError, RuntimeError, asyncio.CancelledError)):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == tick and planner.pending is None
    assert not any(
        e["kind"] == "execution_intent"
        for r in store.list_runs()
        if r["id"] not in existing
        for e in store.read_events(r["id"])
    )


@pytest.mark.parametrize(
    "change",
    [
        "state",
        "provenance",
        "uncertainty",
        "store",
        "receipt",
        "forged_outcome",
        "health",
        "cancel",
        "authorization",
    ],
)
async def test_change_after_qualification_stops_before_native_effect(tmp_path, change, monkeypatch):
    store, world, planner, wrapper, agent = await setup(tmp_path, services=[3, 1] * 8)
    tick = world.payload["tick"]
    original = Runtime.emit

    async def emit(runtime, kind, data):
        result = await original(runtime, kind, data)
        if kind == "candidate_selection":
            if change == "state":
                world.payload["queue"] += 1
            if change in {"provenance", "uncertainty"}:
                observe = world.observe

                async def changed():
                    return (await observe()).model_copy(
                        update={change: "forged" if change == "provenance" else 0.5}
                    )

                monkeypatch.setattr(world, "observe", changed)
            if change == "store":
                store.append(planner.prefix_runs[0], "external_update", {})
            if change == "receipt":
                receipt = planner.source.guard._model.receipts[0]
                with store.db.begin() as connection:
                    connection.execute(
                        update(store.executions)
                        .where(store.executions.c.id == receipt)
                        .values(status="aborted")
                    )
            if change == "forged_outcome":
                receipt = planner.source.guard._model.receipts[0]
                forged = store.execution_record(receipt)["receipt"]
                forged["metrics"]["measured_service"] = 99
                with store.db.begin() as connection:
                    connection.execute(
                        update(store.executions)
                        .where(store.executions.c.id == receipt)
                        .values(receipt=forged)
                    )
            if change == "cancel":
                raise asyncio.CancelledError()
            if change == "health":
                planner.source.guard._latched = True
            if change == "authorization":
                monkeypatch.setattr(
                    "preact.domains.queue_multi_selection.wall_time", lambda: 10**16
                )
        return result

    monkeypatch.setattr(Runtime, "emit", emit)
    with pytest.raises((ValueError, asyncio.CancelledError)):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == tick
    assert planner.pending is None


async def test_all_hard_vetoes_abstain_without_selector_or_side_effect(tmp_path, monkeypatch):
    store, world, planner, wrapper, agent = await setup(tmp_path)
    tick = world.payload["tick"]
    verifier = agent.registry.engines[0]
    original = verifier.predict

    async def veto(request):
        prediction = await original(request)
        prediction.mandatory_checks["capacity"] = False
        return prediction

    monkeypatch.setattr(verifier, "predict", veto)

    def forbidden(batch):
        raise AssertionError("No qualified candidates")

    monkeypatch.setattr(planner, "select", forbidden)
    result = await agent.run(wrapper, 1)
    events = store.read_events(result.run_ids[0])
    batch = next(e["data"] for e in events if e["kind"] == "candidate_qualification")
    assert batch["complete"] and not batch["qualified_ids"]
    assert all(c["status"] == "rejected" for c in batch["batch"]["candidates"])
    assert result.rounds[0]["steps"] == 0 and world.payload["tick"] == tick


async def test_invalid_contract_is_recorded_without_losing_qualified_alternative(
    tmp_path, monkeypatch
):
    store, world, planner, wrapper, agent = await setup(tmp_path, services=[3, 1] * 8)
    validate = world.validate

    def reject(state, action):
        if action.payload["amount"] == 3:
            raise ValueError("Owned structural rejection")
        validate(state, action)

    monkeypatch.setattr(world, "validate", reject)
    result = await agent.run(wrapper, 1)
    assert result.rounds[0]["steps"] == 1 and world.executed[-1].payload["amount"] == 1
    events = store.read_events(result.run_ids[0])
    batch = next(e["data"] for e in events if e["kind"] == "candidate_qualification")
    assert batch["complete"]
    assert batch["batch"]["candidates"][0]["status"] == "invalid_contract"


async def test_selector_cannot_use_search_or_direct_execution(tmp_path):
    store, world, planner, wrapper, agent = await setup(tmp_path)
    with pytest.raises(ValueError):
        Runtime(
            store, agent.artifacts, agent.registry, Policy(search=True), candidate_selector=planner
        )
    runtime = Runtime(
        store, agent.artifacts, agent.registry, Policy(search=False), candidate_selector=planner
    )
    with pytest.raises(ValueError):
        await runtime.run(world, direct=True)


@pytest.mark.parametrize("stage", ["verification_requested", "execution_intent"])
async def test_cancellation_during_verification_or_after_intent_has_no_outcome(
    tmp_path, stage, monkeypatch
):
    store, world, planner, wrapper, agent = await setup(tmp_path, services=[3, 1] * 8)
    tick = world.payload["tick"]
    original = Runtime.emit

    async def cancel(runtime, kind, data):
        result = await original(runtime, kind, data)
        if kind == stage:
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(Runtime, "emit", cancel)
    before = {r["id"] for r in store.list_runs()}
    with pytest.raises(asyncio.CancelledError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == tick and planner.pending is None
    runs = [r["id"] for r in store.list_runs() if r["id"] not in before]
    snapshot = await TransitionDataset(store, {"interrupted": runs}).snapshot()
    assert not snapshot.transitions
