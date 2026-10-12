import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.models import Belief, Inference
from preact.cognition.queue_task import QueueTaskPlanner, TaskInformationModel, schedule_queue
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_multi_selection import QueueMultiSelectionWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_continuous import ContinuousQueueAdaptation
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from preact.learning.drift import DriftConfig
from scripts.benchmark_queue_temporal import execute_sequence

COLLECT = {"search": False, "calibration": False, "width": 1, "max_nodes": 1, "max_calls": 128}
POLICY = {**COLLECT, "width": 3, "max_nodes": 3}
GOAL = Goal(name="task", metric="delivered", target=999)
VALUES = {"low": {3: 0.8, 1: 0.85, 0: 0.0}, "high": {3: 2.55, 1: 0.85, 0: 0.0}}


@pytest.mark.parametrize(
    "case", ["positive", "ordinary", "expensive", "short", "same_actions", "infeasible"]
)
async def test_information_value_accounts_for_task_cost_and_free_observations(case):
    world = InformationQueueWorld(ticks=128, target=999, noise=0)
    if case == "ordinary":
        world.payload["queue"] = 3
    if case == "expensive":
        world.payload["probe_cost"] = 20
    if case == "short":
        world.payload["tick"] = 126
    if case == "infeasible":
        world.payload["tick"] = 112
    state = await world.observe()
    belief = Belief(
        observed=state,
        inferred={
            "service_high_probability": Inference(value=0.5, lower=0, upper=1, status="hypothesis")
        },
    )
    values = VALUES if case != "same_actions" else {"low": VALUES["low"], "high": VALUES["low"]}
    context = (
        {"stage": "collect", "effective_training": 0}
        if case == "infeasible"
        else {"stage": "monitor"}
    )
    decision = schedule_queue(
        state, belief, GOAL, 1, values, {"status": "available"}, context, TaskInformationModel()
    )
    assert decision.action == ("information" if case == "positive" else "task")
    assert decision.assumptions["calibrated"] is False
    if case == "ordinary":
        assert decision.estimates["normal_informative_probability"] == 1
        assert decision.estimates["information_gain"] == 0


async def prepared(tmp_path, *, persistence=0.98):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    teacher = InformationQueueWorld(seed=701, ticks=32, target=999, noise=0)
    teacher._service = [1] * 32
    training = {
        "teacher": await execute_sequence(teacher, ["probe"] * 16, store, artifacts, COLLECT)
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    world = InformationQueueWorld(seed=0, ticks=128, target=999, noise=0, submission_cost=0.15)
    world._service = [1] * 128
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "task-world")
    await lifecycle.start(
        model,
        training,
        world_episode_id="task-world",
        task=world.task,
        anchor=await world.observe(),
        config=DriftConfig(),
    )
    warmup = await execute_sequence(world, ["probe"] * 8, store, artifacts, COLLECT)
    planner = QueueTaskPlanner(
        lifecycle.selection_source(),
        prefix_runs=warmup,
        information_model=TaskInformationModel(persistence=persistence),
    )
    wrapper = QueueMultiSelectionWorld(world, planner)
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
        [GOAL],
        Policy(**POLICY),
        candidate_selector=planner,
    )
    agent.memory.run_ids = warmup
    return store, world, planner, wrapper, agent


@pytest.mark.parametrize(("persistence", "action"), [(0.98, "probe_service"), (0.95, "submit")])
async def test_task_and_information_both_use_gate_intent_and_complete_receipt(
    tmp_path, persistence, action
):
    store, world, planner, wrapper, agent = await prepared(tmp_path, persistence=persistence)
    services = world._service

    class ExecutionOnlyService:
        reads = 0

        def __getitem__(self, tick):
            events = store.read_events(planner.run_id)
            intent = next(e["data"] for e in events if e["kind"] == "execution_intent")
            assert store.execution_record(intent["receipt"])["status"] == "pending"
            self.reads += 1
            return services[tick]

    private_service = ExecutionOnlyService()
    world._service = private_service
    result = await agent.run(wrapper, 1)
    assert private_service.reads == 1
    assert result.rounds[0]["steps"] == 1
    assert world.payload["tick"] == 9
    assert world.executed[-1].kind == action
    events = store.read_events(result.run_ids[0])
    assert len([e for e in events if e["kind"] == "cognitive_schedule"]) == 1
    assert len([e for e in events if e["kind"] == "authorization"]) == 1
    intent = next(e["data"] for e in events if e["kind"] == "execution_intent")
    assert store.execution_record(intent["receipt"])["status"] == "complete"
    assert result.rounds[0]["agent_calls"] == 9


@pytest.mark.parametrize(
    "failure",
    ["context", "source", "cancel", "exception", "budget", "lost_recovery", "foreign_memory"],
)
async def test_schedule_failures_never_execute_or_reuse_old_predictions(
    tmp_path, monkeypatch, failure
):
    store, world, planner, wrapper, agent = await prepared(tmp_path)
    observe = world.observe

    async def changed_observation():
        state = await observe()
        if planner.schedule is not None:
            if failure == "context":
                planner.context["stage"] = "changed"
            elif failure == "source":
                run = planner.source._sources[0]
                store.append(run, "external_writer", {})
        return state

    if failure in {"context", "source"}:
        monkeypatch.setattr(world, "observe", changed_observation)
    if failure in {"cancel", "exception"}:

        async def broken(*args):
            raise (
                asyncio.CancelledError()
                if failure == "cancel"
                else ValueError("invalid promotion basis")
            )

        monkeypatch.setattr(planner.source, "compare", broken)
    if failure == "lost_recovery":
        planner.source.recovery = SimpleNamespace(_active=None)
    if failure == "foreign_memory":
        agent.memory.run_ids = [planner.source._sources[0]]
    if failure == "budget":
        agent._policy = Policy(**{**POLICY, "max_calls": 8})
        result = await agent.run(wrapper, 1)
        assert result.rounds[0]["steps"] == 0
    else:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else ValueError):
            await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8
    assert planner.pending is planner.schedule is None


@pytest.mark.parametrize("status", ["pending", "aborted", "forged"])
async def test_task_scheduler_rejects_unconfirmed_or_forged_memory(tmp_path, status):
    store, world, planner, wrapper, agent = await prepared(tmp_path)
    run = agent.memory.run_ids[-1]
    outcome = next(e["data"] for e in store.read_events(run) if e["kind"] == "outcome")
    if status == "forged":
        outcome["observation"]["metrics"]["processed"] = 999
        store.append(run, "outcome", outcome)
    else:
        with store.db.begin() as connection:
            connection.execute(
                update(store.executions)
                .where(store.executions.c.id == outcome["observation"]["receipt"])
                .values(status=status)
            )
    with pytest.raises(ValueError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8
    assert planner.pending is planner.schedule is None


@pytest.mark.parametrize("field", ["schedule", "prediction_state", "authorization", "outcome"])
async def test_independent_auditor_rejects_trace_tampering(tmp_path, field):
    import json

    from scripts.audit_queue_task import audit_case
    from scripts.benchmark_queue_task import run_case

    protocol = json.loads(
        (
            __import__("pathlib").Path(__file__).parents[1] / "benchmarks/queue-task-pilot-v2.json"
        ).read_text()
    )
    protocol["episode_ticks"], protocol["execution_ticks"], protocol["goal"] = 32, 4, 999
    protocol["protocol_id"] = "task-aware-owned-test-v1"
    protocol["conditions"] = {"stable": {"regimes": [{"start": 0, "end": 32, "service": 1}]}}
    bundle = tmp_path / "case"
    await run_case(protocol, "stable", "task_aware", 409, bundle)
    assert (await audit_case(bundle, protocol))["status"] == "passed"
    store = Store("sqlite:///" + str(bundle / "ledger.db"))
    manifest = json.loads((bundle / "task-result.json").read_text())
    run = manifest["runs"][0]
    kind = {
        "schedule": "cognitive_schedule",
        "prediction_state": "cognitive_prediction",
        "authorization": "authorization",
        "outcome": "outcome",
    }[field]
    event = next(e for e in store.read_events(run) if e["kind"] == kind)
    if field == "schedule":
        event["data"]["decision"]["estimates"]["net_voi"] += 10
    elif field == "prediction_state":
        event["data"]["state"]["uncertainty"] = 0.5
    elif field == "authorization":
        event["data"]["expires_at"] = 0
    else:
        event["data"]["observation"]["metrics"]["processed"] = 999
    with store.db.begin() as connection:
        connection.execute(
            update(store.events)
            .where((store.events.c.run_id == run) & (store.events.c.seq == event["seq"]))
            .values(data=event["data"])
        )
    with pytest.raises(ValueError):
        await audit_case(bundle, protocol)
