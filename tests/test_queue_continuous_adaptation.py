from types import SimpleNamespace

import pytest

from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_continuous import ContinuousQueueAdaptation
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import RecoveryRejected
from scripts.benchmark_queue_temporal import execute_sequence

POLICY = {"search": False, "calibration": False, "width": 1, "max_nodes": 1, "max_calls": 32}


async def test_continuous_monitor_epoch_anchors_at_mid_episode_receipt(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    world = InformationQueueWorld(ticks=48, target=999, capacity=12, noise=0)
    world._service = [3] * 8 + [1] * 40

    training_runs = await execute_sequence(world, ["probe"] * 8, store, artifacts, POLICY)
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, {"continuous-world": training_runs}), QueueServiceAdapter()
    )
    # This is a fresh observation in the same, non-reset World after eight real ticks.
    anchor = await world.observe()
    assert anchor.payload["tick"] == 8
    config = DriftConfig(
        window=4, min_recent=2, min_training=4, min_effect=0.2, alpha=0.05, max_gap_ticks=8
    )
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "continuous-world")
    await lifecycle.start(
        model,
        {"continuous-world": training_runs},
        world_episode_id="continuous-world",
        task=world.task,
        anchor=anchor,
        config=config,
    )

    monitoring_runs = await execute_sequence(world, ["probe"] * 2, store, artifacts, POLICY)
    current = await world.observe()
    assert current.payload["tick"] == 10
    health = await lifecycle.health(current, monitoring_runs)
    assert health.status == "invalidated"
    assert health.scope == lifecycle.epoch.epoch_id

    # Recovery begins at the first real invalidation cutoff, without changing World state.
    origin = await lifecycle.begin_recovery(current, monitoring_runs)
    assert origin
    assert world.payload["tick"] == current.payload["tick"]
    assert (await lifecycle.recovery._origin(origin)).cutoff == current
    with pytest.raises(RecoveryRejected, match="insufficient_training"):
        await lifecycle.recovery.prepare_candidate(origin, current, monitoring_runs)
    assert (await lifecycle.health(current, monitoring_runs)).status == "invalidated"

    restored = ContinuousQueueAdaptation(store, artifacts, lifecycle.journal_run)
    await restored.restore(current, monitoring_runs)
    assert restored.epoch.epoch_id == lifecycle.epoch.epoch_id
    assert (await restored.health(current, monitoring_runs)).status == "invalidated"


async def test_mid_episode_guard_restart_replays_epoch_and_rejects_retimed_anchor(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    world = InformationQueueWorld(ticks=48, target=999, capacity=12, noise=0)
    world._service = [3] * 8 + [1] * 40
    training_runs = await execute_sequence(world, ["probe"] * 8, store, artifacts, POLICY)
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, {"same-episode": training_runs}), QueueServiceAdapter()
    )
    anchor = await world.observe()
    runs = await execute_sequence(world, ["probe"] * 3, store, artifacts, POLICY)
    state = await world.observe()
    config = DriftConfig(
        window=4, min_recent=2, min_training=4, min_effect=0.2, alpha=0.05, max_gap_ticks=8
    )
    guard = QueueTemporalGuard(
        store,
        world.task,
        model,
        {"same-episode": training_runs},
        episode_id="same-episode",
        initial=anchor,
        config=config,
        monitoring_epoch="epoch-mid-8",
        allow_same_episode=True,
    )
    result = await guard.compare(state, [world.action(state, 1)], runs, horizon=1)
    replay = QueueTemporalGuard(
        store,
        world.task,
        model,
        {"same-episode": training_runs},
        episode_id="same-episode",
        initial=anchor,
        config=config,
        monitoring_epoch="epoch-mid-8",
        allow_same_episode=True,
    )
    assert (await replay.compare(state, [world.action(state, 1)], runs, horizon=1))[
        "health"
    ] == result["health"]

    changed = anchor.model_copy(update={"timestamp": "2000-01-01T00:00:00+00:00"})
    stale = QueueTemporalGuard(
        store,
        world.task,
        model,
        {"same-episode": training_runs},
        episode_id="same-episode",
        initial=changed,
        config=config,
        monitoring_epoch="epoch-mid-8",
        allow_same_episode=True,
    )
    import pytest

    with pytest.raises(ValueError):
        await stale.compare(state, [world.action(state, 1)], runs, horizon=1)


@pytest.mark.parametrize(
    ("monitor_service", "expected"),
    [
        ([1] * 8, "available"),
        ([1, 1, 3, 1, 1, 1, 3, 1], "available"),
    ],
    ids=["stable-low", "noisy-low-with-occasional-high"],
)
async def test_continuous_health_stable_and_moderate_noise(tmp_path, monitor_service, expected):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    world = InformationQueueWorld(ticks=40, target=999, capacity=12, noise=0)
    world._service = [1] * 16 + monitor_service + [1] * 16
    training_runs = await execute_sequence(world, ["probe"] * 16, store, artifacts, POLICY)
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, {"stable-noisy-episode": training_runs}),
        QueueServiceAdapter(),
    )
    anchor = await world.observe()
    config = DriftConfig(min_training=8, min_recent=4, window=8, max_gap_ticks=16)
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "stable-noisy-episode")
    await lifecycle.start(
        model,
        {"stable-noisy-episode": training_runs},
        world_episode_id="stable-noisy-episode",
        task=world.task,
        anchor=anchor,
        config=config,
    )
    monitoring = await execute_sequence(world, ["probe"] * 8, store, artifacts, POLICY)
    health = await lifecycle.health(await world.observe(), monitoring)
    assert health.status == expected


async def test_noninformative_interaction_does_not_count_as_learning_evidence(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    world = InformationQueueWorld(ticks=24, target=999, capacity=12, noise=0)
    world._service = [1] * 24
    training_runs = await execute_sequence(world, ["probe"] * 8, store, artifacts, POLICY)
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, {"sparse-episode": training_runs}), QueueServiceAdapter()
    )
    anchor = await world.observe()
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "sparse-episode")
    config = DriftConfig(min_training=4, min_recent=2, window=4, max_gap_ticks=8)
    await lifecycle.start(
        model,
        {"sparse-episode": training_runs},
        world_episode_id="sparse-episode",
        task=world.task,
        anchor=anchor,
        config=config,
    )
    # No queued work is available, so an ordinary drain/submit(0) cannot identify service.
    monitor = await execute_sequence(world, [0], store, artifacts, POLICY)
    health = await lifecycle.health(await world.observe(), monitor)
    assert health.status == "insufficient_data"
    assert health.effective_count == 0


async def test_promoted_epoch_never_falls_back_to_unpromoted_guard_when_active_is_lost(tmp_path):
    """A lost Recovery handle must not make a promoted model selectable via Guard."""
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    world = InformationQueueWorld(ticks=32, target=999, capacity=12, noise=0)
    world._service = [1] * 32
    training_runs = await execute_sequence(world, ["probe"] * 8, store, artifacts, POLICY)
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, {"promoted-episode": training_runs}), QueueServiceAdapter()
    )
    anchor = await world.observe()
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "promoted-episode")
    await lifecycle.start(
        model,
        {"promoted-episode": training_runs},
        world_episode_id="promoted-episode",
        task=world.task,
        anchor=anchor,
        config=DriftConfig(min_training=4, min_recent=2, window=4, max_gap_ticks=8),
    )
    runs = await execute_sequence(world, ["probe"] * 2, store, artifacts, POLICY)
    state = await world.observe()

    # Model a committed promoted epoch whose in-memory Recovery active state has
    # been lost (e.g. after an exception/cancellation before reconstruction).
    lifecycle._epoch = lifecycle.epoch.model_copy(
        update={"promotion_artifact": "committed-promotion"}
    )
    lifecycle._recovery = SimpleNamespace(_active=None)

    with pytest.raises(RecoveryRejected, match="promoted_epoch_recovery_unavailable"):
        lifecycle.selection_source()

    direct = await lifecycle.compare(
        state, [world.action(state, amount) for amount in (3, 1, 0)], runs
    )
    assert direct["status"] == "unknown"
    assert direct["reason"] == "promoted_epoch_recovery_unavailable"
    assert direct["predictions"] == []
