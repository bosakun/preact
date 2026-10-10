import asyncio

import pytest
from sqlalchemy import update

from preact.core.models import EvidenceKind
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from scripts.benchmark_queue_temporal import execute_sequence

POLICY = dict(search=False, calibration=False, width=1, max_nodes=1, max_calls=32)


async def prepared(tmp_path, *, partial=False):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    train_world = InformationQueueWorld(
        seed=11, ticks=48, target=999, high_first=False, shift_tick=48, noise=0, capacity=12
    )
    train = {
        "training": await execute_sequence(train_world, ["probe"] * 24, store, artifacts, POLICY)
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, train), QueueServiceAdapter()
    )
    world = InformationQueueWorld(
        seed=101, ticks=48, target=999, high_first=False, shift_tick=8, noise=0, capacity=12
    )
    initial = await world.observe()
    guard = QueueTemporalGuard(
        store, world.task, model, train, episode_id="monitor", initial=initial
    )
    sequence = [0] * 32 if partial else ["probe"] * 32
    return store, artifacts, world, model, train, initial, guard, sequence


async def compare(guard, world, runs):
    state = await world.observe()
    return await guard.compare(state, [world.action(state, n) for n in (3, 1, 0)], runs)


async def test_stable_shift_invalidation_and_restart(tmp_path):
    store, artifacts, world, model, train, initial, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    before = await compare(guard, world, runs)
    assert before["status"] == "estimated" and before["health"]["status"] == "available"
    same = await compare(guard, world, runs)
    assert same["health"] == before["health"]
    assert same["engine_view_version"] == before["engine_view_version"]
    assert [p.vectors for p in same["predictions"]] == [p.vectors for p in before["predictions"]]
    runs += await execute_sequence(world, sequence[8:24], store, artifacts, POLICY)
    after = await compare(guard, world, runs)
    assert after["status"] == "unknown" and after["health"]["status"] == "invalidated"
    assert before["engine_view_version"] != after["engine_view_version"]
    for p in after["predictions"]:
        assert p.evidence == EvidenceKind.INFERENCE and not p.vectors and not p.metrics
        assert not p.mandatory_checks and p.success.value is None and p.risk.value is None
        assert p.raw["base_model_version"] == model.version
        assert p.raw["training_dataset_hash"] == model.dataset_hash
        assert p.engine_version == after["engine_view_version"]
    restarted = QueueTemporalGuard(
        store, world.task, model, train, episode_id="monitor", initial=initial
    )
    replay = await compare(restarted, world, runs)
    assert (
        replay["health"] == after["health"]
        and replay["engine_view_version"] == after["engine_view_version"]
    )
    # The explicitly unmanaged engine remains opt-out and unchanged.
    state = await world.observe()
    engine = QueueTemporalEngine(world.task, model)
    raw = await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
    assert raw["status"] == "estimated" and engine.capabilities.version == model.version
    with pytest.raises(ValueError, match="truncated"):
        await compare(guard, world, [])


async def test_partial_observation_not_low_labels(tmp_path):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path, partial=True)
    runs = await execute_sequence(world, sequence, store, artifacts, POLICY)
    result = await compare(guard, world, runs)
    assert result["health"]["effective_count"] == 0
    assert result["health"]["status"] == "insufficient_data"
    assert result["health"]["history"] == ()


async def test_future_and_stale_cutoff_provenance_rejected(tmp_path):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    old = await world.observe()
    runs += await execute_sequence(world, sequence[8:10], store, artifacts, POLICY)
    with pytest.raises(ValueError, match="not committed"):
        await guard.compare(old, [world.action(old, 3)], runs)
    current = await world.observe()
    for state in (
        current.model_copy(update={"provenance": "prediction"}),
        current.model_copy(update={"kind": "hypothetical"}),
        old,
    ):
        with pytest.raises(ValueError):
            await guard.compare(state, [world.action(state, 3)], runs)


@pytest.mark.parametrize("status", ["pending", "aborted"])
async def test_external_receipt_update_cannot_return_cached_available(tmp_path, status):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    assert (await compare(guard, world, runs))["status"] == "estimated"
    row = (await TransitionDataset(store, {"monitor": runs}).snapshot()).transitions[-1]
    writer = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    with writer.db.begin() as conn:
        conn.execute(
            update(writer.executions)
            .where(writer.executions.c.id == row.receipt)
            .values(status=status)
        )
    with pytest.raises(ValueError):
        await compare(guard, world, runs)
    writer.db.dispose()


async def test_forged_outcome_no_fallback(tmp_path):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    assert (await compare(guard, world, runs))["status"] == "estimated"
    event = next(e for e in store.read_events(runs[-1]) if e["kind"] == "outcome")
    event["data"]["observation"]["metrics"]["measured_service"] = 3
    store.append(runs[-1], "outcome", event["data"])
    with pytest.raises(ValueError):
        await compare(guard, world, runs)


async def test_unlabelled_intent_and_unrelated_world_episode_rejected(tmp_path):
    store, artifacts, world, model, train, initial, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    with pytest.raises(ValueError):
        QueueTemporalGuard(store, world.task, model, train, episode_id="training", initial=initial)
    other = InformationQueueWorld(seed=999, ticks=48, target=999, capacity=12)
    other_runs = await execute_sequence(other, ["probe"] * 8, store, artifacts, POLICY)
    with pytest.raises(ValueError, match="different Task"):
        await compare(guard, other, other_runs)
    with pytest.raises(ValueError):
        await compare(guard, world, list(reversed(runs)))
    with pytest.raises(ValueError):
        await compare(guard, world, train["training"] + runs)


async def test_store_update_during_prediction_discards_staged_result(tmp_path, monkeypatch):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    original = QueueTemporalEngine.predict

    async def changed(engine, request):
        prediction = await original(engine, request)
        store.append(runs[-1], "external_update", {})
        return prediction

    monkeypatch.setattr(QueueTemporalEngine, "predict", changed)
    with pytest.raises(ValueError, match="changed during"):
        await compare(guard, world, runs)


async def test_cancellation_and_inference_exception_no_cached_fallback(tmp_path, monkeypatch):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    assert (await compare(guard, world, runs))["status"] == "estimated"

    async def cancelled(engine, request):
        raise asyncio.CancelledError

    monkeypatch.setattr(QueueTemporalEngine, "predict", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await compare(guard, world, runs)

    async def failed(engine, request):
        raise RuntimeError("inference failure")

    monkeypatch.setattr(QueueTemporalEngine, "predict", failed)
    with pytest.raises(RuntimeError):
        await compare(guard, world, runs)


async def test_training_tamper_and_future_training_rejected(tmp_path):
    store, artifacts, world, model, train, initial, _, sequence = await prepared(tmp_path)
    forged = model.model_copy(update={"dataset_hash": "0" * 64})
    guard = QueueTemporalGuard(
        store, world.task, forged, train, episode_id="monitor", initial=initial
    )
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    with pytest.raises(ValueError, match="Training provenance"):
        await compare(guard, world, runs)
    # A deployment anchor earlier than the training receipts is rejected.
    old = initial.model_copy(update={"timestamp": "2000-01-01T00:00:00+00:00"})
    future_guard = QueueTemporalGuard(
        store, world.task, model, train, episode_id="monitor", initial=old
    )
    with pytest.raises(ValueError, match="not committed"):
        await compare(future_guard, world, runs)


async def test_update_during_snapshot_no_cached_health(tmp_path, monkeypatch):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    assert (await compare(guard, world, runs))["status"] == "estimated"
    original = store.call
    changed = False

    async def modifying(method, *args, **kwargs):
        nonlocal changed
        value = await original(method, *args, **kwargs)
        if method == "read_events" and not changed:
            changed = True
            store.append(runs[-1], "external_update", {})
        return value

    monkeypatch.setattr(store, "call", modifying)
    with pytest.raises(ValueError, match="changed during"):
        await compare(guard, world, runs)


async def test_unlabelled_pending_intent_and_failed_probe_not_teacher(tmp_path):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    state = await world.observe()
    run = store.create_run({"task": world.task.model_dump()})
    action = world.probe(state)
    receipt = store.intent(run, state.id, action.fingerprint)
    store.append(run, "execution_intent", {"receipt": receipt, "node_id": "none"})
    with pytest.raises(ValueError, match="Pending"):
        await guard.health(state, runs + [run])
    store.abort_execution(receipt, "not dispatched")
    with pytest.raises(ValueError, match="Pending"):
        await guard.health(state, runs + [run])
    # A failed action can be an actual transition, but its capacity is not a teacher.
    row = (await TransitionDataset(store, {"monitor": runs}).snapshot()).transitions[-1]
    failed = row.model_copy(deep=True)
    failed.after.checks["probe_success"] = False
    assert QueueServiceAdapter().targets(failed) == (0, 0, 0, 0)


async def test_observation_copies_and_scope_configuration(tmp_path):
    store, artifacts, world, model, train, initial, guard, sequence = await prepared(tmp_path)
    initial.payload["tick"] = 999
    assert guard._initial.payload["tick"] == 0
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    result = await compare(guard, world, runs)
    result["predictions"][0].raw["model_health"]["status"] = "invalidated"
    assert (await compare(guard, world, runs))["health"]["status"] == "available"
    state = await world.observe()
    with pytest.raises(ValueError):
        QueueTemporalGuard(store, world.task, model, train, episode_id="other", initial=state)
    different = state.model_copy(update={"uncertainty": 0.5})
    with pytest.raises(ValueError, match="cutoff"):
        await guard.health(different, runs)


async def test_monitored_unknown_never_grants_execution(tmp_path):
    from preact.cognition import CognitiveAgent, Goal
    from preact.engines.queue_temporal_guard import _HealthView
    from scripts.benchmark_queue_temporal import FixedPlanner

    store, artifacts, world, model, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:24], store, artifacts, POLICY)
    health = await guard.health(await world.observe(), runs)
    assert health.status == "invalidated"
    view = _HealthView(QueueTemporalEngine(world.task, model), health)
    # This private view test exercises Core; public monitored comparisons are read-only.
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry([view]),
        FixedPlanner([3], world.payload["tick"]),
        [Goal(name="Deliver", metric="delivered", target=999)],
    )
    before = len(world.executed)
    result = await agent.run(world, 1)
    assert result.rounds[0]["steps"] == 0 and len(world.executed) == before
    assert not (await TransitionDataset(store, {"abstain": result.run_ids}).snapshot()).transitions


async def test_small_drift_benchmark_and_independent_auditor_reject_tampering(tmp_path):
    import json
    from pathlib import Path

    from scripts.audit_queue_drift import audit
    from scripts.benchmark_queue_drift import benchmark

    protocol = json.loads(Path("benchmarks/queue-drift-v1.json").read_text())
    protocol.update(training_seeds=[11], evaluation_seeds=[101], evaluation_ticks=16)
    protocol["environments"] = {"stable_low": protocol["environments"]["stable_low"]}
    protocol["environments"]["stable_low"]["world"]["shift_tick"] = 16
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    output = tmp_path / "evaluation"
    await benchmark(path, output)
    assert (await audit(path, output))["passed"]
    summary_path = output / "summary.json"
    original = summary_path.read_text()
    summary = json.loads(original)
    summary["scores"]["stable_low"]["scores"]["fixed"]["state_mae"] += 1
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="MAE"):
        await audit(path, output)
    summary_path.write_text(original)
    cases_path = output / "cases.json"
    cases = json.loads(cases_path.read_text())
    cases[0]["trace"][0]["delivered"] += 10
    cases_path.write_text(json.dumps(cases))
    with pytest.raises(ValueError, match="trace forged"):
        await audit(path, output)


async def test_episode_reset_cannot_reuse_prior_health(tmp_path):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    assert (await compare(guard, world, runs))["status"] == "estimated"
    world.reset()
    with pytest.raises(ValueError):
        await compare(guard, world, runs)
    with pytest.raises(ValueError, match="truncated"):
        await compare(guard, world, [])


async def test_nonempty_authoritative_prefix_matches_unmanaged_inference_without_execution(
    tmp_path,
):
    store, artifacts, world, model, _, _, guard, _ = await prepared(tmp_path)
    runs = await execute_sequence(world, ["probe"] * 8 + [3, 3], store, artifacts, POLICY)
    state = await world.observe()
    assert state.payload["pending"]
    before = len(world.executed)

    class Forbidden:
        def __getitem__(self, key):
            raise AssertionError("Prediction read the private schedule")

    world._service = Forbidden()
    actions = [world.action(state, n) for n in (3, 1, 0)]
    guarded = await guard.compare(state, actions, runs)
    engine = QueueTemporalEngine(world.task, model)
    raw = await compare_actions(Registry([engine]), engine, state, actions)
    assert guarded["status"] == raw["status"] == "estimated"
    assert [p.vectors for p in guarded["predictions"]] == [p.vectors for p in raw["predictions"]]
    assert guarded["differences"] == raw["differences"]
    assert len(world.executed) == before
    assert all(
        p.evidence == EvidenceKind.INFERENCE and not p.mandatory_checks
        for p in guarded["predictions"]
    )


async def test_observation_before_receipt_outcome_commit_is_not_a_valid_cutoff(tmp_path):
    store, artifacts, world, _, _, _, guard, sequence = await prepared(tmp_path)
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    rows = (await TransitionDataset(store, {"monitor": runs}).snapshot()).transitions
    # Receipt is complete now, but the domain observed this State before Runtime
    # persisted the durable outcome. It was unavailable at that historical instant.
    with pytest.raises(ValueError, match="not committed"):
        await guard.health(rows[-1].after.state, runs)
    assert (await compare(guard, world, runs))["status"] == "estimated"
    future = (await world.observe()).model_copy(update={"timestamp": "2100-01-01T00:00:00+00:00"})
    with pytest.raises(ValueError, match="stale observation"):
        await guard.health(future, runs)


async def test_training_outcome_must_already_be_committed_at_deployment(tmp_path):
    store, artifacts, world, model, train, _, _, sequence = await prepared(tmp_path)
    rows = (await TransitionDataset(store, train).snapshot()).transitions
    initial = await world.observe()
    initial = initial.model_copy(update={"timestamp": rows[-1].after.state.timestamp})
    guard = QueueTemporalGuard(
        store, world.task, model, train, episode_id="monitor", initial=initial
    )
    runs = await execute_sequence(world, sequence[:8], store, artifacts, POLICY)
    with pytest.raises(ValueError, match="not committed"):
        await compare(guard, world, runs)
