import asyncio

import pytest

from preact.cognition import CognitiveAgent, Goal, WorldPlanner
from preact.cognition.lifecycle import LearningCoordinator
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.datasets.programs import LOGIC
from preact.domains.program import ProgramWorld
from preact.domains.program_learning import ProgramLearningBackend
from preact.engines.local import LocalHeuristic, LocalVerifier
from preact.learning import TransitionDataset


def setup(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    backend = ProgramLearningBackend(
        store,
        artifacts,
        ProgramWorld(LOGIC[0]),
        lambda i: ProgramWorld(LOGIC[0], seed=100 + i, evaluation_seed=700 + i),
    )
    return store, artifacts, backend


def agent_for(store, artifacts, coordinator, world, episode):
    return CognitiveAgent(
        store,
        artifacts,
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
        WorldPlanner(world),
        [Goal(name="Repair", metric="goal_complete", target=1)],
        Policy(search=False, width=2, max_nodes=2, max_calls=64, calibration=False),
        learning_coordinator=coordinator,
        episode_id=episode,
    )


async def train(tmp_path):
    store, artifacts, backend = setup(tmp_path)
    coordinator = await LearningCoordinator.create(backend)
    results = []
    for i in range(9):
        world = ProgramWorld(LOGIC[0], seed=i, evaluation_seed=i)
        result = await agent_for(store, artifacts, coordinator, world, f"train:{i}").run(world, 1)
        results.append(result)
    return store, artifacts, backend, coordinator, results


async def test_actual_program_learning_explicit_promotion_and_common_decision(tmp_path):
    store, artifacts, backend, coordinator, results = await train(tmp_path)
    assert backend.candidate is not None and backend.active_model is None
    assert backend.evaluation["passed"] and backend.evaluation["scores"]["coverage"] == 1
    assert all(r.rounds[0]["steps"] == 1 and not r.unsafe for r in results)
    world = ProgramWorld(LOGIC[0], seed=20, evaluation_seed=20)
    await coordinator.promote(backend.candidate, world)
    result = await agent_for(store, artifacts, coordinator, world, "task:20").run(world, 1)
    assert result.success and not result.unsafe
    row = (await TransitionDataset(store, {"task:20": result.run_ids}).snapshot()).transitions[0]
    assert row.after.state.payload["goal_complete"]
    events = store.read_events(result.run_ids[0])
    record = next(e["data"] for e in events if e["kind"] == "deliberation")
    assert record["simulation"]["model_version"] == backend.active_model.version
    assert all(c["basis"] == "learned_observed_goal" for c in record["simulation"]["candidates"])
    assert any(e["kind"] == "learning_binding" for e in events)
    assert any(e["kind"] == "cognitive_multi_dispatch" for e in events)
    assert store.execution_record(row.receipt)["status"] == "complete"
    assert len(backend.evaluation["episodes"]) == 8


async def test_promotion_without_samples_fails_closed(tmp_path):
    store, artifacts, backend = setup(tmp_path)
    coordinator = await LearningCoordinator.create(backend)
    with pytest.raises(ValueError):
        await coordinator.promote("missing", ProgramWorld(LOGIC[0]))
    assert coordinator._failed and backend.active_model is None


@pytest.mark.parametrize("fault", ["cancel", "exception", "journal", "promotion"])
async def test_model_basis_failure_never_executes(tmp_path, monkeypatch, fault):
    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    world = ProgramWorld(LOGIC[0], evaluation_seed=81)
    await coordinator.promote(backend.candidate, world)
    original = backend.binding

    def broken(world, checkpoint):
        binding = original(world, checkpoint)
        old = binding.planner.source.forecast

        async def fail(*args):
            result = await old(*args)
            if fault == "cancel":
                raise asyncio.CancelledError()
            if fault == "exception":
                raise ValueError("inference failed")
            if fault == "journal":
                store.append(coordinator.journal_run, "external_writer", {})
            if fault == "promotion":
                backend.active_promotion = None
                backend.active_model = None
            return result

        binding.planner.source.forecast = fail
        return binding

    monkeypatch.setattr(backend, "binding", broken)
    with pytest.raises((ValueError, asyncio.CancelledError)):
        await agent_for(store, artifacts, coordinator, world, "negative").run(world, 1)
    assert not world.receipts
    assert world.payload["stage"] == "initial"


async def test_program_promotion_restored_for_a_new_agent_and_episode(tmp_path):
    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    world = ProgramWorld(LOGIC[0], seed=19, evaluation_seed=19)
    await coordinator.promote(backend.candidate, world)
    restored_backend = ProgramLearningBackend(
        store, artifacts, world, lambda i: ProgramWorld(LOGIC[0], evaluation_seed=90 + i)
    )
    restored = await LearningCoordinator.restore(restored_backend, coordinator.journal_run, world)
    assert restored_backend.active_model == backend.active_model
    result = await agent_for(store, artifacts, restored, world, "after-restart").run(world, 1)
    assert result.success and not result.unsafe


async def test_round_boundary_forbids_promotion_and_episode_relabelling(tmp_path):
    store, artifacts, backend = setup(tmp_path)
    coordinator = await LearningCoordinator.create(backend)
    world = ProgramWorld(LOGIC[0])
    await coordinator.prepare(world, "actual", [])
    with pytest.raises(ValueError, match="round boundary"):
        await coordinator.promote("candidate", world)
    coordinator.finish_round()
    with pytest.raises(ValueError, match="relabel"):
        await coordinator.prepare(world, "pretend-new", [])


@pytest.mark.parametrize("restart", ["continue", "relabel", "replace"])
async def test_queue_and_software_use_same_agent_deliberation_and_learning_control(
    tmp_path, restart
):
    from preact.cognition.deliberation import DeliberationPlanner
    from preact.domains.information_queue import InformationQueueWorld
    from preact.domains.queue_learning import QueueLearningBackend
    from preact.domains.queue_service_features import QueueServiceAdapter
    from preact.engines.information_queue import InformationBoundVerifier
    from preact.engines.queue_continuous import ContinuousQueueAdaptation
    from preact.learning import TabularDynamicsTrainer
    from scripts.benchmark_queue_temporal import execute_sequence

    store, artifacts, _ = setup(tmp_path)
    teacher = InformationQueueWorld(seed=701, ticks=32, target=999, noise=0)
    teacher._service = [3] * 32
    training = {
        "teacher": await execute_sequence(
            teacher,
            ["probe"] * 16,
            store,
            artifacts,
            {"search": False, "calibration": False, "width": 1, "max_nodes": 1, "max_calls": 64},
        )
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    world = InformationQueueWorld(seed=1, ticks=32, target=999, noise=0)
    world._service = [3] * 32
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "queue")
    await lifecycle.start(
        model, training, world_episode_id="queue", task=world.task, anchor=await world.observe()
    )

    async def evaluate(*args):
        raise AssertionError("Stable fixture must not request recovery evaluation")

    backend = QueueLearningBackend(lifecycle, evaluate)
    coordinator = await LearningCoordinator.create(backend)
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry([InformationBoundVerifier(world.task)]),
        WorldPlanner(world),
        [Goal(name="Deliver", metric="delivered", target=999)],
        Policy(search=False, width=3, max_nodes=3, max_calls=128, calibration=False),
        learning_coordinator=coordinator,
        episode_id="queue",
    )
    result = await agent.run(world, 12)
    assert len(result.run_ids) == 12 and not result.unsafe
    assert isinstance(agent.planner, DeliberationPlanner)
    rows = (await TransitionDataset(store, {"queue": result.run_ids}).snapshot()).transitions
    assert len(rows) == 12 and world.payload["tick"] == 12
    assert any(r.action.kind == "submit" for r in rows)
    traces = [
        e["data"]
        for run in result.run_ids
        for e in store.read_events(run)
        if e["kind"] == "deliberation"
    ]
    assert any(t["simulation"].get("health", {}).get("status") == "available" for t in traces)
    assert all(store.execution_record(r.receipt)["status"] == "complete" for r in rows)

    restored_backend = QueueLearningBackend(lifecycle, evaluate)
    restored = await LearningCoordinator.restore(restored_backend, coordinator.journal_run, world)
    if restart != "continue":
        replacement = world if restart == "relabel" else InformationQueueWorld()
        episode = "pretend-new" if restart == "relabel" else "queue"
        with pytest.raises(ValueError, match=restart):
            await restored.prepare(replacement, episode, restored.episodes.get(episode, []))
        assert world.payload["tick"] == 12
        return
    resumed_agent = CognitiveAgent(
        store,
        artifacts,
        Registry([InformationBoundVerifier(world.task)]),
        WorldPlanner(world),
        [Goal(name="Deliver", metric="delivered", target=999)],
        Policy(search=False, width=3, max_nodes=3, max_calls=128, calibration=False),
        learning_coordinator=restored,
        episode_id="queue",
    )
    resumed = await resumed_agent.run(world, 1)
    assert len(resumed.run_ids) == 1 and resumed.rounds[0]["steps"] == 1
    assert world.payload["tick"] == 13
    assert restored.episodes["queue"] == result.run_ids + resumed.run_ids
    resumed_rows = (await TransitionDataset(store, restored.episodes).snapshot()).transitions
    assert len(resumed_rows) == 13
    assert resumed_rows[-1].before.payload["tick"] == 12
    assert store.execution_record(resumed_rows[-1].receipt)["status"] == "complete"


async def test_queue_recovery_promotes_at_round_boundary_and_resumes_actual_selection(tmp_path):
    import json
    from pathlib import Path

    from preact.domains.information_queue import InformationQueueWorld
    from preact.domains.queue_learning import QueueLearningBackend
    from preact.domains.queue_service_features import QueueServiceAdapter
    from preact.engines.information_queue import InformationBoundVerifier
    from preact.engines.queue_continuous import ContinuousQueueAdaptation
    from preact.learning import TabularDynamicsTrainer
    from scripts.benchmark_queue_recovery import evaluation_cases
    from scripts.benchmark_queue_temporal import execute_sequence

    store, artifacts, _ = setup(tmp_path)
    teacher = InformationQueueWorld(seed=701, ticks=32, target=999, noise=0)
    teacher._service = [1] * 32
    training = {
        "teacher": await execute_sequence(
            teacher,
            ["probe"] * 16,
            store,
            artifacts,
            {"search": False, "calibration": False, "width": 1, "max_nodes": 1, "max_calls": 64},
        )
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    world = InformationQueueWorld(seed=1, ticks=64, target=999, noise=0)
    world._service = [3] * 64
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "queue-recovery")
    await lifecycle.start(
        model,
        training,
        world_episode_id="queue-recovery",
        task=world.task,
        anchor=await world.observe(),
    )

    async def evaluate(recovery, candidate, generation):
        protocol = json.loads(Path("benchmarks/queue-recovery-v1.json").read_text())
        cases, _ = await evaluation_cases(recovery, candidate, protocol, high=True)
        return cases

    backend = QueueLearningBackend(lifecycle, evaluate)
    coordinator = await LearningCoordinator.create(backend)
    promotions = []

    async def operator(run_id, belief):
        # Explicit test operator, not production automatic promotion.
        if backend.phase == "await_promotion":
            health = await backend.pending_guard.health(await world.observe(), backend.monitor_runs)
            # advance() accounts for the just-completed receipt before promotion.
            if health.status == "available":
                promotions.append(await coordinator.promote(backend.candidate, world))

    agent = CognitiveAgent(
        store,
        artifacts,
        Registry([InformationBoundVerifier(world.task)]),
        WorldPlanner(world),
        [Goal(name="Deliver", metric="delivered", target=999)],
        Policy(
            search=False, width=3, max_nodes=3, max_calls=256, max_seconds=120, calibration=False
        ),
        learning_coordinator=coordinator,
        episode_id="queue-recovery",
        round_observer=operator,
    )
    result = await agent.run(world, 40)
    assert promotions and lifecycle.epoch.generation == 1
    traces = [
        e["data"]
        for run in result.run_ids
        for e in store.read_events(run)
        if e["kind"] == "deliberation"
    ]
    assert any(t["simulation"].get("promotion") for t in traces)
    assert any(
        t["simulation"].get("health", {}).get("model_version") == lifecycle.epoch.model_version
        and t["simulation"]["health"]["status"] == "available"
        for t in traces
    )
    assert world.payload["tick"] == 40 and not result.unsafe
    restored_backend = QueueLearningBackend(lifecycle, evaluate)
    restored = await LearningCoordinator.restore(restored_backend, coordinator.journal_run, world)
    resumed_agent = CognitiveAgent(
        store,
        artifacts,
        Registry([InformationBoundVerifier(world.task)]),
        WorldPlanner(world),
        [Goal(name="Deliver", metric="delivered", target=999)],
        agent.policy,
        learning_coordinator=restored,
        episode_id="queue-recovery",
    )
    resumed = await resumed_agent.run(world, 1)
    assert world.payload["tick"] == 41 and resumed.rounds[0]["steps"] == 1
    assert restored.episodes["queue-recovery"] == result.run_ids + resumed.run_ids
    resumed_trace = next(
        e["data"] for e in store.read_events(resumed.run_ids[0]) if e["kind"] == "deliberation"
    )
    activated_epoch = json.loads(artifacts.read(promotions[-1]))
    assert activated_epoch["epoch_id"] == restored_backend.lifecycle.epoch.epoch_id
    assert resumed_trace["simulation"]["promotion"] == json.loads(
        artifacts.read(activated_epoch["promotion_artifact"])
    )
    assert resumed_trace["simulation"]["health"]["model_version"] == lifecycle.epoch.model_version
    assert resumed_trace["simulation"]["health"]["status"] == "available"


@pytest.mark.parametrize("fault", ["receipt", "artifact", "evaluation", "cancel"])
async def test_restart_never_restores_unverified_promotion(tmp_path, fault):
    from sqlalchemy import update

    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    world = ProgramWorld(LOGIC[0], evaluation_seed=88)
    await coordinator.promote(backend.candidate, world)
    if fault == "receipt":
        receipt = backend.active_model.receipts[0]
        with store.db.begin() as connection:
            connection.execute(
                update(store.executions)
                .where(store.executions.c.id == receipt)
                .values(status="aborted")
            )
    if fault == "artifact":
        (artifacts.root / backend.active_promotion).write_text("{}")
    if fault == "evaluation":
        (artifacts.root / backend.evaluation_artifact).write_text("{}")
    restored_backend = ProgramLearningBackend(
        store, artifacts, world, lambda i: ProgramWorld(LOGIC[0], evaluation_seed=90 + i)
    )
    if fault == "cancel":

        async def cancelled():
            raise asyncio.CancelledError()

        restored_backend.validate = cancelled
    with pytest.raises((ValueError, asyncio.CancelledError)):
        await LearningCoordinator.restore(restored_backend, coordinator.journal_run, world)
    assert restored_backend.active_model is None and not world.receipts


async def test_unseen_program_cell_unknown_without_fake_successor(tmp_path):
    from preact.core.models import Action, PredictionRequest
    from preact.engines.learned_dynamics import LearnedDynamicsEngine

    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    world = ProgramWorld(LOGIC[0])
    await coordinator.promote(backend.candidate, world)
    state = await world.observe()
    action = Action(
        kind="patch",
        name="Unknown",
        state_id=state.id,
        payload={"files": {"program.py": "def unfamiliar(): return 42"}},
    )
    engine = LearnedDynamicsEngine(world.task, backend.adapter, backend.active_model)
    prediction = await engine.predict(PredictionRequest(state=state, actions=[action]))
    assert prediction.raw["status"] == "unknown"
    assert prediction.successor is None and not prediction.mandatory_checks


async def test_unseen_program_input_uses_explicit_native_fallback_through_gate(tmp_path):
    from preact.core.models import Action

    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    world = ProgramWorld(LOGIC[0], evaluation_seed=77)
    original = world.propose

    async def prepare_unseen_input(state, width):
        return [
            Action(
                kind="patch",
                name="Owned preparation",
                state_id=state.id,
                payload={"files": {"program.py": world.spec.candidates[1]}},
            )
        ]

    world.propose = prepare_unseen_input
    preparation = await agent_for(store, artifacts, None, world, "owned-preparation").run(world, 1)
    assert preparation.rounds[0]["steps"] == 1
    world.propose = original
    await coordinator.promote(backend.candidate, world)
    result = await agent_for(store, artifacts, coordinator, world, "unknown-input").run(world, 1)
    assert result.rounds[0]["steps"] == 1 and not result.unsafe
    events = store.read_events(result.run_ids[0])
    trace = next(e["data"] for e in events if e["kind"] == "deliberation")
    futures = trace["simulation"]["candidates"]
    assert all(f["basis"] == "native_unknown_input_fallback" for f in futures)
    assert all(f["learned_status"] == "unknown" and f["value"] == -i for i, f in enumerate(futures))
    assert all(f["records"][0]["prediction"]["successor"] is None for f in futures)
    row = (await TransitionDataset(store, coordinator.episodes).snapshot()).transitions[-1]
    assert row.action.fingerprint == futures[0]["fingerprint"]
    assert store.execution_record(row.receipt)["status"] == "complete"
    import copy
    import json

    from sqlalchemy import update

    from scripts.audit_common_learning import audit_case

    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "domain": "program",
                "episodes": {"unknown-input": result.run_ids},
                "journal": coordinator.journal_run,
                "backend_state": backend.state(),
            }
        )
    )
    assert (await audit_case(tmp_path))["task_receipts"] == 1
    event = next(e for e in events if e["kind"] == "deliberation")
    forged = copy.deepcopy(trace)
    forged["simulation"]["candidates"][0]["learned_status"] = "estimated"
    with store.db.begin() as connection:
        connection.execute(
            update(store.events)
            .where(store.events.c.run_id == result.run_ids[0], store.events.c.seq == event["seq"])
            .values(data=forged)
        )
    with pytest.raises(ValueError, match="Fallback"):
        await audit_case(tmp_path)


@pytest.mark.parametrize("failure", ["decoder", "engine"])
async def test_program_inference_failure_is_not_unknown_input_fallback(
    tmp_path, monkeypatch, failure
):
    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    world = ProgramWorld(LOGIC[0])
    await coordinator.promote(backend.candidate, world)

    def broken(*args):
        raise ValueError("Invalid decoder")

    if failure == "decoder":
        monkeypatch.setattr(backend.adapter, "decode", broken)
    else:
        from preact.engines.learned_dynamics import LearnedDynamicsEngine

        original = LearnedDynamicsEngine.predict

        async def unavailable(engine, request):
            prediction = await original(engine, request)
            return prediction.model_copy(
                update={
                    "metrics": {},
                    "metric_intervals": {},
                    "raw": {"status": "unknown", "reason": "inference_failed"},
                }
            )

        monkeypatch.setattr(LearnedDynamicsEngine, "predict", unavailable)
    with pytest.raises(ValueError, match="decoder|inference failed"):
        await agent_for(store, artifacts, coordinator, world, "decoder-failure").run(world, 1)
    assert not world.receipts


async def test_prediction_has_no_access_to_protected_oracle(tmp_path, monkeypatch):
    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    world = ProgramWorld(LOGIC[0])
    await coordinator.promote(backend.candidate, world)
    state = await world.observe()
    source = backend.binding(world, backend.state()).planner.source
    actions = await source.candidates(state, 2)

    def forbidden(*args):
        raise AssertionError("Prediction consulted protected oracle/evaluator")

    monkeypatch.setattr(type(world.spec), "cases", forbidden)
    monkeypatch.setattr(world, "measure", forbidden)
    forecasts = await source.forecast(state, actions, [])
    assert all(f["status"] == "estimated" for f in forecasts["candidates"])
    assert all(
        r["prediction"]["evidence"] == "inference"
        and r["prediction"]["successor"] is None
        and not r["prediction"]["mandatory_checks"]
        for f in forecasts["candidates"]
        for r in f["records"]
    )


async def test_lifecycle_deadline_cancellation_has_no_side_effect(tmp_path, monkeypatch):
    store, artifacts, backend = setup(tmp_path)
    coordinator = await LearningCoordinator.create(backend, max_seconds=0.01)

    async def slow(*args):
        await asyncio.sleep(1)

    monkeypatch.setattr(backend, "advance", slow)
    world = ProgramWorld(LOGIC[0])
    with pytest.raises(TimeoutError):
        await agent_for(store, artifacts, coordinator, world, "slow").run(world, 1)
    assert not world.receipts and coordinator._failed


@pytest.mark.parametrize("fault", ["score", "goal", "prediction", "selection", "outcome"])
async def test_common_auditor_rejects_tampered_execution_evidence(tmp_path, fault):
    import copy

    from sqlalchemy import update

    from scripts.audit_common_learning import audit_case
    from scripts.bench_common_learning import program_case

    protocol = {"program": {"max_preparation_actions": 16, "evaluation_tasks": 1}}
    folder = tmp_path / "case"
    await program_case(folder, "learned", 11, protocol)
    assert (await audit_case(folder))["status"] == "passed"
    import json

    metadata = json.loads((folder / "case.json").read_text())
    store = Store("sqlite:///" + str(folder / "ledger.db"))
    run = metadata["episodes"]["task:0"][0]
    kind = {"selection": "candidate_selection", "outcome": "outcome"}.get(fault, "deliberation")
    event = next(e for e in store.read_events(run) if e["kind"] == kind)
    data = copy.deepcopy(event["data"])
    if fault == "score":
        data["scores"][0]["value"] += 0.1
    elif fault == "goal":
        data["goals"][0]["target"] += 1
    elif fault == "prediction":
        data["simulation"]["candidates"][0]["records"][0]["prediction"]["metrics"][
            "goal_complete"
        ] += 0.1
    elif fault == "selection":
        data["node_id"] = "unqualified"
    else:
        data["observation"]["metrics"]["test_pass_fraction"] = 0.123
    with store.db.begin() as connection:
        connection.execute(
            update(store.events)
            .where(store.events.c.run_id == run, store.events.c.seq == event["seq"])
            .values(data=data)
        )
    with pytest.raises(ValueError):
        await audit_case(folder)


async def test_program_quality_rejection_uses_actual_independent_outcomes(tmp_path):
    from dataclasses import replace

    store, artifacts, _ = setup(tmp_path)
    # Declared owned evaluator-distribution mismatch; only the evaluator gets it.
    shifted = replace(LOGIC[0], oracle=lambda n, size: (n + size - 1) // size + 7)
    backend = ProgramLearningBackend(
        store,
        artifacts,
        ProgramWorld(LOGIC[0]),
        lambda i: ProgramWorld(shifted, evaluation_seed=700 + i),
    )
    coordinator = await LearningCoordinator.create(backend)
    for i in range(8):
        world = ProgramWorld(LOGIC[0], evaluation_seed=i)
        await agent_for(store, artifacts, coordinator, world, f"train:{i}").run(world, 1)
    assert backend.evaluation and not backend.evaluation["passed"]
    assert backend.evaluation["scores"]["goal_mae"] == 0.5
    assert backend.state()["status"] == "rejected" and backend.active_model is None
    with pytest.raises(ValueError, match="independent evaluation"):
        await coordinator.promote(backend.candidate, ProgramWorld(LOGIC[0]))


async def test_public_candidate_order_changes_actual_model_decision(tmp_path):
    from preact.core.models import identity
    from scripts.bench_common_learning import program_case

    protocol = {"program": {"max_preparation_actions": 16, "evaluation_tasks": 1}}
    learned = await program_case(tmp_path / "learned", "learned", 11, protocol, order="reverse")
    fixed = await program_case(tmp_path / "fixed", "fixed_prior", 11, protocol, order="reverse")
    assert learned["goal_rate"] == 1 and fixed["goal_rate"] == 0
    assert learned["action_codes"] != fixed["action_codes"]
    assert learned["unsafe"] == fixed["unsafe"] == 0
    assert learned["action_codes"] == [identity(LOGIC[0].source(LOGIC[0].repair))]


async def test_independent_program_evaluation_cannot_execute_source_world(tmp_path):
    store, artifacts, _ = setup(tmp_path)
    worlds = [ProgramWorld(LOGIC[0], evaluation_seed=i) for i in range(8)]
    backend = ProgramLearningBackend(store, artifacts, worlds[0], lambda i: worlds[-1])
    coordinator = await LearningCoordinator.create(backend)
    with pytest.raises(ValueError, match="distinct actual ProgramWorlds"):
        for i, world in enumerate(worlds):
            await agent_for(store, artifacts, coordinator, world, f"train:{i}").run(world, 1)
    assert coordinator._failed and backend.active_model is None
    assert all(len(w.receipts) <= 1 for w in worlds)


async def test_program_evaluation_cutoff_rejects_future_and_unconfirmed_time(tmp_path):
    from datetime import datetime, timezone

    store, artifacts, backend, coordinator, _ = await train(tmp_path)
    snapshot = await TransitionDataset(store, backend.training).snapshot()
    with pytest.raises(ValueError, match="actual past time"):
        await backend._complete_before(snapshot, "2999-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="evaluation cutoff"):
        await backend._complete_before(snapshot, "2000-01-01T00:00:00+00:00")
    await backend._complete_before(snapshot, datetime.now(timezone.utc).isoformat())


@pytest.mark.parametrize("first", ["learning", "cognition"])
def test_public_learning_api_import_order(first):
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import preact.{first}; from preact.cognition import LearningCoordinator; from preact.learning import TransitionDataset",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
