import asyncio
import json

import pytest
from sqlalchemy import update

from preact.cognition import CognitiveAgent, Goal, WorldPlanner
from preact.core.decision import evaluate, gate
from preact.core.models import Decision, Policy, PredictionRequest
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_features import QueueDynamicsAdapter
from preact.domains.software import SoftwareWorld
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.learned_dynamics import LearnedDynamicsEngine
from preact.engines.local import LocalHeuristic, LocalVerifier
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.transitions import require_disjoint
from tests.fixtures.dynamics import collect


async def test_transition_extracts_actual_only_deduplicates_and_copies(tmp_path):
    world, agent, result, dataset = await collect(tmp_path, rounds=6)
    snapshot = await dataset.snapshot()
    assert len(snapshot.transitions) == 6 == len(world.executed)
    assert not snapshot.runs_without_outcomes
    for row in snapshot.transitions:
        assert row.before.kind == row.after.state.kind == "observed"
        assert row.action.state_id == row.before.id
        assert row.receipt_status == "complete" and row.after.receipt == row.receipt
        assert "measured_service" not in row.before.payload
        assert "prediction_ids" not in row.model_dump()
        assert row.before.provenance == row.after.state.provenance == "actual-cognitive-queue/v2"
        assert row.observed_elapsed_seconds >= 0 and row.declared_action_duration == 1
    run = result.run_ids[0]
    outcome = next(e["data"] for e in agent.store.read_events(run) if e["kind"] == "outcome")
    agent.store.append(run, "outcome", outcome)
    assert len((await dataset.snapshot()).transitions) == 6
    snapshot.transitions[0].before.payload["queue"] = 99
    with pytest.raises(ValueError, match="changed"):
        snapshot.verify_identity()
    assert (await dataset.snapshot()).transitions[0].before.payload["queue"] == 0


@pytest.mark.parametrize("status", ["pending", "aborted"])
async def test_noncomplete_receipts_with_outcomes_are_rejected(tmp_path, status):
    _, agent, _, dataset = await collect(tmp_path, rounds=1)
    receipt = (await dataset.snapshot()).transitions[0].receipt
    with agent.store.db.begin() as conn:
        conn.execute(
            update(agent.store.executions)
            .where(agent.store.executions.c.id == receipt)
            .values(status=status)
        )
    with pytest.raises(ValueError, match="committed"):
        await dataset.snapshot()
    with pytest.raises(ValueError, match="committed"):
        await TabularDynamicsTrainer().fit(dataset, QueueDynamicsAdapter())


async def test_forged_duplicate_outcome_is_checked_before_deduplication(tmp_path):
    _, agent, result, dataset = await collect(tmp_path, rounds=1)
    run = result.run_ids[0]
    outcome = next(e["data"] for e in agent.store.read_events(run) if e["kind"] == "outcome")
    changed = json.loads(json.dumps(outcome))
    changed["observation"]["metrics"]["processed"] = 999
    agent.store.append(run, "outcome", changed)
    with pytest.raises(ValueError, match="committed"):
        await dataset.snapshot()


async def test_unexecuted_branch_and_missing_after_never_create_teacher(tmp_path):
    store = Store("sqlite:///:memory:")
    run = store.create_run({})
    world = InformationQueueWorld()
    state = await world.observe()
    action = world.action(state, 1)
    store.append(run, "observed", {"state": state.model_dump()})
    store.append(
        run,
        "node",
        {"node": {"id": "imagined", "state": state.model_dump(), "action": action.model_dump()}},
    )
    receipt = store.intent(run, state.id, action.fingerprint)
    store.append(run, "execution_intent", {"node_id": "imagined", "receipt": receipt})
    dataset = TransitionDataset(store, {"missing": [run]})
    snapshot = await dataset.snapshot()
    assert not snapshot.transitions and snapshot.runs_without_outcomes == [run]
    store.abort_execution(receipt, "Not dispatched")
    assert not (await dataset.snapshot()).transitions
    assert not (await TabularDynamicsTrainer().fit(dataset, QueueDynamicsAdapter())).cells


async def test_hypothetical_input_and_changed_provenance_are_rejected(tmp_path):
    _, agent, result, dataset = await collect(tmp_path, rounds=1)
    run = result.run_ids[0]
    node = next(
        e["data"]["node"]
        for e in agent.store.read_events(run)
        if e["kind"] == "node_updated" and e["data"]["node"]["action"]
    )
    for change in ({"kind": "hypothetical"}, {"provenance": "inferred-not-observed"}):
        modified = json.loads(json.dumps(node))
        modified["state"].update(change)
        agent.store.append(run, "node_updated", {"node": modified})
        with pytest.raises(ValueError, match="authoritative"):
            await dataset.snapshot()


async def test_episode_order_manifest_and_external_update_fence(tmp_path, monkeypatch):
    _, agent, result, dataset = await collect(tmp_path, rounds=3)
    with pytest.raises(ValueError, match="exactly one"):
        TransitionDataset(agent.store, {"a": [result.run_ids[0]], "b": [result.run_ids[0]]})
    reversed_dataset = TransitionDataset(agent.store, {"reverse": list(reversed(result.run_ids))})
    with pytest.raises(ValueError, match="continuity"):
        await reversed_dataset.snapshot()
    external = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    original = agent.store.read_events
    mutated = False

    def read_events(run, after=0):
        nonlocal mutated
        events = original(run, after)
        if not mutated:
            mutated = True
            external.append(run, "external_writer", {})
        return events

    monkeypatch.setattr(agent.store, "read_events", read_events)
    with pytest.raises(ValueError, match="authority changed"):
        await dataset.snapshot()
    assert len((await dataset.snapshot()).transitions) == 3


async def test_invalid_temporal_alignment_and_actual_failure(tmp_path):
    class BackwardsWorld(InformationQueueWorld):
        async def execute(self, action, receipt):
            observation = await super().execute(action, receipt)
            observation.state = observation.state.model_copy(
                update={"timestamp": "2000-01-01T00:00:00+00:00"}
            )
            return observation

    _, _, _, dataset = await collect(
        tmp_path / "backwards", world=BackwardsWorld(target=999), rounds=1
    )
    with pytest.raises(ValueError, match="authoritative"):
        await dataset.snapshot()

    class FailedActionWorld(InformationQueueWorld):
        async def execute(self, action, receipt):
            observation = await super().execute(action, receipt)
            observation.checks["action_success"] = False
            observation.success = False
            return observation

    _, _, _, dataset = await collect(
        tmp_path / "failed", world=FailedActionWorld(target=999), rounds=1
    )
    rows = (await dataset.snapshot()).transitions
    assert len(rows) == 1 and rows[0].after.checks["action_success"] is False
    assert (
        len(
            (
                await TabularDynamicsTrainer().fit(
                    dataset, QueueDynamicsAdapter(), TrainingConfig(min_samples=1)
                )
            ).receipts
        )
        == 1
    )


async def test_learning_changes_parameters_is_reproducible_and_artifact_safe(tmp_path):
    world, _, _, dataset = await collect(tmp_path, rounds=12)
    adapter, trainer = QueueDynamicsAdapter(), TabularDynamicsTrainer()
    config = TrainingConfig(min_samples=1, seed=17)
    empty = await trainer.fit(dataset, adapter, config, limit=0)
    first = await trainer.fit(dataset, adapter, config, limit=3)
    trained = await trainer.fit(dataset, adapter, config)
    assert not empty.cells and first.version != trained.version
    assert trained == await trainer.fit(dataset, adapter, config)
    assert trained.dataset_hash != empty.dataset_hash
    assert trained.seed == 17 and len(trained.receipts) == 12
    assert any(c.mean[1] == 1 and c.count > 1 for c in trained.cells)
    artifacts = Artifacts(str(tmp_path / "models"))
    digest = trained.save(artifacts)
    loaded = DynamicsModel.load(artifacts, digest, adapter)
    assert loaded == trained and loaded.version == trained.version
    content = json.loads(artifacts.read(digest))
    content["model"]["schema_version"] = "2"
    with pytest.raises(ValueError):
        DynamicsModel.load(artifacts, artifacts.json(content), adapter)
    (artifacts.root / digest).write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        DynamicsModel.load(artifacts, digest, adapter)

    class Forbidden:
        def __getitem__(self, key):
            raise AssertionError("Hidden schedule was read by inference")

    world._service = Forbidden()
    row = (await dataset.snapshot()).transitions[-1]
    prediction, _ = await Registry([LearnedDynamicsEngine(world.task, adapter, trained)]).predict(
        LearnedDynamicsEngine(world.task, adapter, trained),
        PredictionRequest(state=row.before, actions=[row.action]),
    )
    assert prediction.metrics["processed"] == 1
    assert prediction.metrics["next_queue"] == row.after.state.payload["queue"]
    assert prediction.evidence == "inference" and not prediction.mandatory_checks
    assert prediction.success.value is None and not prediction.success.measured


async def test_unknown_models_inputs_insufficient_data_and_adapter_changes(tmp_path):
    world, _, _, dataset = await collect(tmp_path, rounds=3)
    adapter, trainer = QueueDynamicsAdapter(), TabularDynamicsTrainer()
    row = (await dataset.snapshot()).transitions[1]
    request = PredictionRequest(state=row.before, actions=[row.action])
    for limit in (0, 3):
        model = await trainer.fit(dataset, adapter, limit=limit)
        assert (await LearnedDynamicsEngine(world.task, adapter, model).predict(request)).raw[
            "status"
        ] == "unknown"
    model = await trainer.fit(dataset, adapter, TrainingConfig(min_samples=1))
    engine = LearnedDynamicsEngine(world.task, adapter, model)
    adapter.specification = {**adapter.specification, "version": "changed"}
    assert (await engine.predict(request)).raw["reason"] == "inference_failed"
    with pytest.raises(ValueError, match="incompatible"):
        LearnedDynamicsEngine(world.task, adapter, model)
    with pytest.raises(RuntimeError, match="one action"):
        await engine.predict(request.model_copy(update={"horizon": 2}))


async def test_cancelled_training_has_no_stale_model_fallback(tmp_path, monkeypatch):
    _, _, _, dataset = await collect(tmp_path, rounds=1)
    trainer = TabularDynamicsTrainer()
    assert (await trainer.fit(dataset, QueueDynamicsAdapter())).cells

    async def cancelled():
        raise asyncio.CancelledError

    monkeypatch.setattr(dataset, "snapshot", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await trainer.fit(dataset, QueueDynamicsAdapter())


async def test_train_evaluation_split_checks_episode_run_and_receipt(tmp_path):
    _, agent, result, dataset = await collect(tmp_path, rounds=2)
    snapshot = await dataset.snapshot()
    with pytest.raises(ValueError, match="disjoint"):
        require_disjoint(snapshot, snapshot)
    other = TransitionDataset(agent.store, {"renamed": result.run_ids})
    with pytest.raises(ValueError, match="disjoint"):
        require_disjoint(snapshot, await other.snapshot())
    _, _, _, heldout = await collect(tmp_path / "heldout", episode="heldout", rounds=2)
    require_disjoint(snapshot, await heldout.snapshot())


async def test_wrong_learned_prediction_never_replaces_verifier_or_gate(tmp_path):
    world, _, _, dataset = await collect(tmp_path / "train", rounds=12)
    adapter = QueueDynamicsAdapter()
    model = await TabularDynamicsTrainer().fit(dataset, adapter, TrainingConfig(min_samples=1))
    engine = LearnedDynamicsEngine(world.task, adapter, model)
    row = (await dataset.snapshot()).transitions[1]
    prediction = await engine.predict(PredictionRequest(state=row.before, actions=[row.action]))
    evaluation = evaluate([prediction], world.task, {}, state=row.before, action=row.action)
    assert (
        gate(evaluation, row.before.id, row.action.fingerprint, Policy(), True).decision
        == Decision.VERIFY
    )
    _, _, result, empty = await collect(tmp_path / "no-verifier", rounds=1, engines=[engine])
    assert result.rounds[0]["steps"] == 0 and not (await empty.snapshot()).transitions
    # A model trained on low service is wrong in high service, yet safe execution
    # requires the same public-bound verifiers, authorization and complete receipt.
    high = InformationQueueWorld(ticks=12, target=999, capacity=12, noise=0, shift_tick=12)
    _, agent, result, actual = await collect(
        tmp_path / "integrated",
        world=high,
        engines=[
            engine,
            InformationBoundVerifier(high.task),
            InformationBoundVerifier(high.task, future=True),
        ],
    )
    assert len((await actual.snapshot()).transitions) == 12 and not result.unsafe
    rows = (await actual.snapshot()).transitions
    predictions = [
        await engine.predict(PredictionRequest(state=r.before, actions=[r.action])) for r in rows
    ]
    assert any(
        p.metrics.get("processed", r.after.metrics["processed"]) != r.after.metrics["processed"]
        for p, r in zip(predictions, rows)
    )
    for run in result.run_ids:
        events = agent.store.read_events(run)
        assert sum(e["kind"] == "authorization" for e in events) == 1
        assert sum(e["kind"] == "outcome" for e in events) == 1


async def test_learning_contract_accepts_actual_software_world(tmp_path):
    class SoftwareAdapter:
        specification = {"domain": "software", "adapter": "visible-progress", "version": "1"}
        feature_names = ("before_progress",)
        target_names = ("progress_delta",)

        def features(self, state, action):
            return (float(state.payload["goal_progress"]),)

        def targets(self, row):
            return (row.after.state.payload["goal_progress"] - row.before.payload["goal_progress"],)

        def decode(self, state, action, cell):
            return {"progress_delta": cell.mean[0]}, {
                "progress_delta": (cell.minimum[0], cell.maximum[0])
            }

    world = SoftwareWorld()
    store = Store("sqlite:///:memory:")
    agent = CognitiveAgent(
        store,
        Artifacts(str(tmp_path / "artifacts")),
        Registry([LocalHeuristic(world), LocalVerifier(world)]),
        WorldPlanner(world),
        [Goal(name="Repair", metric="goal_progress", target=1)],
        Policy(calibration=False),
    )
    result = await agent.run(world, 3)
    assert result.success
    adapter = SoftwareAdapter()
    dataset = TransitionDataset(store, {"software": result.run_ids})
    model = await TabularDynamicsTrainer().fit(dataset, adapter, TrainingConfig(min_samples=1))
    assert model.domain == "software" and len(model.receipts) == 2
    row = (await dataset.snapshot()).transitions[-1]
    prediction = await LearnedDynamicsEngine(world.task, adapter, model).predict(
        PredictionRequest(state=row.before, actions=[row.action])
    )
    assert prediction.metrics["progress_delta"] > 0


async def test_real_execution_exception_cannot_create_a_teacher(tmp_path):
    class BrokenWorld(InformationQueueWorld):
        async def execute(self, action, receipt):
            raise RuntimeError("Execution result unavailable")

    store = Store("sqlite:///:memory:")
    world = BrokenWorld(target=999)
    with pytest.raises(RuntimeError, match="unavailable"):
        await collect(tmp_path, world=world, store=store, rounds=1)
    run = store.list_runs()[0]["id"]
    dataset = TransitionDataset(store, {"failed": [run]})
    snapshot = await dataset.snapshot()
    assert not snapshot.transitions and snapshot.runs_without_outcomes == [run]
    assert not world.executed
    intent = next(e["data"] for e in store.read_events(run) if e["kind"] == "execution_intent")
    assert store.execution_record(intent["receipt"])["status"] == "pending"
    assert snapshot.unlabelled_receipts == {intent["receipt"]: "pending"}


async def test_unknown_cell_and_insufficient_budget_do_not_execute(tmp_path):
    world, _, _, dataset = await collect(tmp_path / "train", rounds=12)
    adapter = QueueDynamicsAdapter()
    model = await TabularDynamicsTrainer().fit(dataset, adapter, TrainingConfig(min_samples=1))
    engine = LearnedDynamicsEngine(world.task, adapter, model)
    fresh = InformationQueueWorld(capacity=3, target=999)
    fresh.payload.update(queue=3, pending=[{"due": 1, "amount": 3}])
    state = await fresh.observe()
    assert (await engine.predict(PredictionRequest(state=state, actions=[fresh.probe(state)]))).raw[
        "status"
    ] == "unknown"
    # Pending admissions violate the existing public bound at a full queue, even if a model
    # predicts a benign immediate result. Verification remains mandatory.
    _, _, result, rejected = await collect(
        tmp_path / "unsafe",
        world=fresh,
        rounds=1,
        engines=[
            engine,
            InformationBoundVerifier(fresh.task),
            InformationBoundVerifier(fresh.task, future=True),
        ],
    )
    assert result.rounds[0]["steps"] == 0 and not fresh.executed
    assert not (await rejected.snapshot()).transitions
    _, _, budget_result, budget_dataset = await collect(
        tmp_path / "budget",
        rounds=1,
        engines=[
            engine,
            InformationBoundVerifier(world.task),
            InformationBoundVerifier(world.task, future=True),
        ],
        policy=Policy(search=False, calibration=False, width=1, max_nodes=1, max_calls=1),
    )
    assert (
        budget_result.rounds[0]["steps"] == 0 and not (await budget_dataset.snapshot()).transitions
    )


async def test_external_receipt_only_update_during_snapshot_is_detected(tmp_path, monkeypatch):
    _, agent, result, dataset = await collect(tmp_path, rounds=1)
    row = (await dataset.snapshot()).transitions[0]
    external = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    original = agent.store.read_events
    calls = 0

    def read_events(run, after=0):
        nonlocal calls
        calls += 1
        events = original(run, after)
        # Memory has already validated the complete receipt when the second read
        # happens. No event append accompanies this separate writer's update.
        if calls == 2:
            with external.db.begin() as conn:
                conn.execute(
                    update(external.executions)
                    .where(external.executions.c.id == row.receipt)
                    .values(status="pending")
                )
        return events

    monkeypatch.setattr(agent.store, "read_events", read_events)
    with pytest.raises(ValueError, match="authority changed"):
        await dataset.snapshot()
    with pytest.raises(ValueError, match="committed"):
        await dataset.snapshot()


async def test_exogenous_change_is_not_attributed_to_an_action(tmp_path):
    store = Store("sqlite:///:memory:")
    world, _, first, _ = await collect(
        tmp_path,
        store=store,
        rounds=1,
        world=InformationQueueWorld(ticks=3, target=999, high_first=False, shift_tick=3, noise=0),
    )
    world.payload["queue"] += 1
    _, _, second, _ = await collect(tmp_path, store=store, world=world, rounds=1)
    snapshot = await TransitionDataset(
        store, {"episode": first.run_ids + second.run_ids}
    ).snapshot()
    assert len(snapshot.transitions) == 2
    assert snapshot.transitions[0].continuous_from_previous is None
    assert snapshot.transitions[1].continuous_from_previous is False
