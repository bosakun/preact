"""Explicit same-World Queue model epochs over the existing receipt lifecycle.

This coordinator owns no execution path. It composes the existing Guard and
QueueModelRecovery; all World actions must still use the ordinary Runtime.
"""

import asyncio
import json
from dataclasses import asdict

from preact.core.models import Action, State, Task, identity
from preact.core.store import Artifacts, Store
from preact.domains.queue_recovery import observed
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import DynamicsModel
from preact.learning.continuous import InvalidationRecord, MonitoringEpoch
from preact.learning.drift import DriftConfig
from preact.learning.recovery import CandidateEvaluation, RecoveryRejected


class ContinuousQueueAdaptation:
    """Durable orchestration for repeated recovery within one declared World episode.

    Lifecycle records are derived artifacts in the existing Store, not a new
    execution ledger. Every operation revalidates receipt-backed source data.
    A recovery journal is created per invalidated generation; a failed partial
    promotion leaves the active model unavailable rather than reviving its parent.
    """

    def __init__(self, store: Store, artifacts: Artifacts, journal_run: str):
        self.store, self.artifacts, self.journal_run = store, artifacts, journal_run
        self._lock = asyncio.Lock()
        self._epoch: MonitoringEpoch | None = None
        self._guard: QueueTemporalGuard | None = None
        self._recovery: QueueModelRecovery | None = None
        self._model: DynamicsModel | None = None

    @classmethod
    async def create(cls, store: Store, artifacts: Artifacts, world_episode_id: str):
        if not world_episode_id:
            raise ValueError("A stable World episode identifier is required")
        run = await store.call(
            "create_run",
            {"kind": "queue_continuous_adaptation", "version": "1", "episode": world_episode_id},
        )
        return cls(store, artifacts, run)

    async def _events(self):
        run = await self.store.call("get_run", self.journal_run)
        if run is None or run["config"].get("kind") != "queue_continuous_adaptation":
            raise ValueError("Invalid continuous adaptation journal")
        return await self.store.call("read_events", self.journal_run)

    async def _record(self, kind: str, record) -> str:
        digest = self.artifacts.json(record.model_dump())
        events = await self._events()
        if any(e["kind"] == kind and e["data"] == {"artifact": digest} for e in events):
            raise ValueError("Duplicate continuous lifecycle event")
        await self.store.call("append", self.journal_run, kind, {"artifact": digest})
        return digest

    def _load_epoch(self, digest: str) -> MonitoringEpoch:
        return MonitoringEpoch.model_validate(json.loads(self.artifacts.read(digest)))

    def _build(self, epoch: MonitoringEpoch, model: DynamicsModel) -> QueueTemporalGuard:
        return QueueTemporalGuard(
            self.store,
            epoch.task,
            model,
            epoch.training,
            episode_id=epoch.world_episode_id,
            initial=epoch.anchor,
            config=DriftConfig(**epoch.drift_config),
            monitoring_epoch=epoch.epoch_id,
            allow_same_episode=True,
        )

    async def start(
        self,
        model: DynamicsModel,
        training: dict[str, list[str]],
        *,
        world_episode_id: str,
        task: Task,
        anchor: State,
        monitor_runs: list[str] | None = None,
        config: DriftConfig = DriftConfig(),
    ) -> str:
        """Start the initial model scope at an actual observation; never resets World."""
        async with self._lock:
            if self._epoch is not None or await self._events():
                raise ValueError("Continuous lifecycle already started")
            anchor = State.model_validate(anchor.model_dump())
            observed(anchor)
            training = {k: list(v) for k, v in training.items()}
            monitor_runs = list(monitor_runs or [])
            epoch_id = identity(
                {
                    "generation": 0,
                    "model": model.version,
                    "episode": world_episode_id,
                    "anchor": anchor.model_dump(),
                }
            )
            provisional = MonitoringEpoch(
                epoch_id=epoch_id,
                world_episode_id=world_episode_id,
                generation=0,
                parent_epoch=None,
                model_artifact=model.save(self.artifacts),
                model_version=model.version,
                dataset_hash=model.dataset_hash,
                task=task,
                training=training,
                anchor=anchor,
                monitor_runs=monitor_runs,
                health_version="",
                drift_config=asdict(config),
            )
            model.compatible(QueueServiceAdapter())
            guard = self._build(provisional, model)
            current = (
                anchor
                if not monitor_runs
                else await self._state_at(world_episode_id, monitor_runs, anchor)
            )
            health = await guard.health(current, monitor_runs)
            epoch = provisional.model_copy(update={"health_version": health.version})
            digest = await self._record("continuous_epoch_started", epoch)
            self._epoch, self._guard, self._model = epoch, self._build(epoch, model), model
            return digest

    async def _state_at(self, episode_id: str, runs: list[str], fallback: State) -> State:
        """Recover the last actual State only from validated committed transitions."""
        from preact.learning.transitions import TransitionDataset

        snapshot = await TransitionDataset(self.store, {episode_id: runs}).snapshot()
        if not snapshot.transitions:
            return fallback
        return snapshot.transitions[-1].after.state

    @property
    def epoch(self) -> MonitoringEpoch:
        if self._epoch is None:
            raise RecoveryRejected("continuous_lifecycle_not_started")
        return self._epoch.model_copy(deep=True)

    def selection_source(self):
        """Build the existing planner source for this exact model epoch."""
        from preact.cognition.queue_model import MonitoredQueueSource

        if self._epoch is None or self._guard is None:
            raise RecoveryRejected("continuous_lifecycle_not_started")
        if self._recovery is not None and self._recovery._active is not None:
            assert self._epoch.promotion_artifact is not None
            return MonitoredQueueSource.promoted(self._recovery, self._epoch.promotion_artifact)
        return MonitoredQueueSource(self.store, self._guard, self._epoch.training)

    async def health(self, state: State, monitor_runs: list[str]):
        if self._guard is None or self._epoch is None:
            raise RecoveryRejected("continuous_lifecycle_not_started")
        if monitor_runs[: len(self._epoch.monitor_runs)] != self._epoch.monitor_runs:
            raise ValueError("Monitoring run prefix changed")
        return await self._guard.health(state, monitor_runs)

    async def compare(self, state: State, actions: list[Action], monitor_runs: list[str]) -> dict:
        if self._epoch is None or self._guard is None or self._model is None:
            return {"status": "unknown", "reason": "not_started", "predictions": []}
        if self._recovery is not None and self._recovery._active is not None:
            return await self._recovery.compare(state, actions, monitor_runs)
        return await self._guard.compare(state, actions, monitor_runs)

    async def begin_recovery(self, state: State, monitor_runs: list[str]) -> str:
        """Freeze the first invalidation cutoff and open a generation-specific journal."""
        async with self._lock:
            if self._epoch is None or self._guard is None or self._model is None:
                raise RecoveryRejected("continuous_lifecycle_not_started")
            health = await self.health(state, monitor_runs)
            if health.status != "invalidated":
                raise RecoveryRejected("model_not_invalidated")
            manager = await QueueModelRecovery.create(self.store, self.artifacts)
            origin = await manager.begin(
                self._model,
                self._epoch.training,
                episode_id=self._epoch.world_episode_id,
                task=self._epoch.task,
                initial=self._epoch.anchor,
                cutoff=state,
                runs=monitor_runs,
                config=self._guard._config,
                continuous_episode=True,
                monitoring_epoch=self._epoch.epoch_id,
            )
            record = InvalidationRecord(
                epoch_id=self._epoch.epoch_id,
                model_version=self._model.version,
                health_version=health.version,
                cutoff=state,
                monitor_runs=list(monitor_runs),
                recovery_journal=manager.journal_run,
            )
            await self._record("continuous_epoch_invalidated", record)
            self._recovery = manager
            return origin

    @property
    def recovery(self) -> QueueModelRecovery:
        if self._recovery is None:
            raise RecoveryRejected("recovery_not_started")
        return self._recovery

    async def promote(
        self,
        evaluation_artifact: str,
        *,
        anchor: State,
        state: State,
        monitor_runs: list[str],
    ) -> str:
        """Explicitly activate the evaluated candidate at a fresh same-World anchor."""
        async with self._lock:
            if self._epoch is None or self._recovery is None:
                raise RecoveryRejected("recovery_not_started")
            anchor = State.model_validate(anchor.model_dump())
            observed(anchor)
            report = self._recovery._load(evaluation_artifact, CandidateEvaluation)
            candidate, _, model, _ = await self._recovery._candidate(report.candidate_artifact)
            generation = self._epoch.generation + 1
            epoch_id = identity(
                {
                    "generation": generation,
                    "model": model.version,
                    "episode": self._epoch.world_episode_id,
                    "anchor": anchor.model_dump(),
                }
            )
            monitor_runs = list(monitor_runs)
            state = State.model_validate(state.model_dump())
            observed(state)
            receipt_state = await self._state_at(self._epoch.world_episode_id, monitor_runs, anchor)
            if (
                receipt_state.model_dump(exclude={"timestamp"})
                != state.model_dump(exclude={"timestamp"})
                or receipt_state.payload["tick"] != state.payload["tick"]
                or receipt_state.timestamp > state.timestamp
            ):
                raise ValueError("Promotion cutoff is not the fresh World observation")
            promotion = await self._recovery.promote(
                evaluation_artifact,
                episode_id=self._epoch.world_episode_id,
                task=self._epoch.task,
                initial=anchor,
                state=state,
                runs=monitor_runs,
                continuous_episode=True,
                monitoring_epoch=epoch_id,
            )
            # Rebuild and replay the just-committed model epoch before publishing it.
            model = DynamicsModel.load(
                self.artifacts, candidate.model_artifact, QueueServiceAdapter()
            )
            next_epoch = MonitoringEpoch(
                epoch_id=epoch_id,
                world_episode_id=self._epoch.world_episode_id,
                generation=generation,
                parent_epoch=self._epoch.epoch_id,
                model_artifact=candidate.model_artifact,
                model_version=model.version,
                dataset_hash=model.dataset_hash,
                task=self._epoch.task,
                training=candidate.training,
                anchor=anchor,
                monitor_runs=monitor_runs,
                recovery_journal=self._recovery.journal_run,
                promotion_artifact=promotion,
                health_version=self._recovery._active[0].health_version,
                drift_config=self._epoch.drift_config,
            )
            digest = await self._record("continuous_epoch_activated", next_epoch)
            self._epoch = next_epoch
            self._model = model
            self._guard = self._build(next_epoch, model)
            return digest

    async def restore(self, state: State, monitor_runs: list[str]) -> str:
        """Restore only the latest fully activated epoch; never revive an invalid parent."""
        async with self._lock:
            self._epoch = self._guard = self._recovery = self._model = None
            events = await self._events()
            active = [
                e
                for e in events
                if e["kind"] in {"continuous_epoch_started", "continuous_epoch_activated"}
            ]
            if not active:
                raise RecoveryRejected("no_continuous_epoch")
            lineage = [self._load_epoch(e["data"]["artifact"]) for e in active]
            if (
                lineage[0].generation != 0
                or lineage[0].parent_epoch is not None
                or lineage[0].world_episode_id != lineage[-1].world_episode_id
                or any(
                    later.generation != earlier.generation + 1
                    or later.parent_epoch != earlier.epoch_id
                    or later.world_episode_id != earlier.world_episode_id
                    for earlier, later in zip(lineage, lineage[1:])
                )
            ):
                raise ValueError("Continuous model epoch lineage is inconsistent")
            record = self._load_epoch(active[-1]["data"]["artifact"])
            model = DynamicsModel.load(self.artifacts, record.model_artifact, QueueServiceAdapter())
            if model.version != record.model_version or model.dataset_hash != record.dataset_hash:
                raise ValueError("Active model artifact provenance mismatch")
            if monitor_runs[: len(record.monitor_runs)] != record.monitor_runs:
                raise ValueError("Restored monitor run prefix changed")
            guard = self._build(record, model)
            invalidations = [
                e
                for e in events
                if e["kind"] == "continuous_epoch_invalidated" and e["data"].get("artifact")
            ]
            epoch_invalidations = [
                e
                for e in invalidations
                if self.artifacts.read(e["data"]["artifact"])
                and InvalidationRecord.model_validate(
                    json.loads(self.artifacts.read(e["data"]["artifact"]))
                ).epoch_id
                == record.epoch_id
            ]
            if epoch_invalidations:
                invalidation = InvalidationRecord.model_validate(
                    json.loads(self.artifacts.read(epoch_invalidations[-1]["data"]["artifact"]))
                )
                if monitor_runs[: len(invalidation.monitor_runs)] != invalidation.monitor_runs:
                    raise ValueError("Restored invalidation evidence prefix changed")
                cutoff_health = await guard.health(invalidation.cutoff, invalidation.monitor_runs)
                if (
                    cutoff_health.version != invalidation.health_version
                    or cutoff_health.status != "invalidated"
                ):
                    raise ValueError("Restored invalidation evidence no longer verifies")
            if record.recovery_journal is not None:
                recovery = QueueModelRecovery(self.store, self.artifacts, record.recovery_journal)
                await recovery.restore(state, monitor_runs)
                if (
                    recovery._active is None
                    or recovery._active[0].monitoring_epoch != record.epoch_id
                ):
                    raise ValueError("Restored promotion does not match active monitoring epoch")
                self._recovery = recovery
            else:
                health = await guard.health(state, monitor_runs)
                if health.status == "invalidated":
                    # Initial epochs may restore as invalidated, but cannot predict.
                    guard._latched = True
            self._epoch, self._guard, self._model = record, guard, model
            return active[-1]["data"]["artifact"]
