"""Opt-in receipt-validated temporal comparisons with fresh private registries."""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from preact.core.models import (
    Action,
    Capabilities,
    EvidenceKind,
    Prediction,
    PredictionRequest,
    State,
    Task,
    identity,
)
from preact.core.registry import Registry
from preact.core.store import Store
from preact.domains.information_queue import DOMAIN, PROVENANCE
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.learning.drift import DriftConfig, ModelHealth, ServiceSample, replay_health
from preact.learning.dynamics import DynamicsModel, TabularDynamicsTrainer, TrainingConfig
from preact.learning.transitions import TransitionDataset


def _time(state: State) -> datetime:
    result = datetime.fromisoformat(state.timestamp)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Observation requires a timezone-aware timestamp")
    return result


def _same_observation(left: State, right: State) -> bool:
    # Timestamp alone may change on a fresh passive observation; all other fields matter.
    return left.model_dump(exclude={"timestamp"}) == right.model_dump(exclude={"timestamp"})


@dataclass(frozen=True)
class _HealthView:
    """One comparison only; never expose this as a self-refreshing registered engine."""

    _engine: QueueTemporalEngine
    _health: ModelHealth
    task: Task = field(init=False)
    capabilities: Capabilities = field(init=False)

    def __post_init__(self):
        engine, health = self._engine, self._health
        object.__setattr__(self, "task", engine.task.model_copy(deep=True))
        code = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        object.__setattr__(
            self,
            "capabilities",
            engine.capabilities.model_copy(
                deep=True,
                update={
                    "engine_id": "queue-temporal-monitored",
                    "version": identity(
                        {
                            "base": engine.capabilities.version,
                            "health": health.version,
                            "code": code,
                        }
                    ),
                },
            ),
        )

    async def predict(self, request: PredictionRequest) -> Prediction:
        if self._health.usable:
            result = await self._engine.predict(request)
        else:
            result = Prediction(
                engine_id=self._engine.capabilities.engine_id,
                engine_version=self._engine.capabilities.version,
                family=self.capabilities.family,
                state_id=request.state.id,
                action_ids=[a.id for a in request.actions],
                horizon=request.horizon,
                evidence=EvidenceKind.INFERENCE,
                raw={"status": "unknown", "reason": "model_" + self._health.status},
            )
        return result.model_copy(
            deep=True,
            update={
                "engine_id": self.capabilities.engine_id,
                "engine_version": self.capabilities.version,
                "raw": {
                    **result.raw,
                    "base_model_version": self._health.model_version,
                    "training_dataset_hash": self._health.dataset_hash,
                    "model_health": asdict(self._health),
                    "health_version": self._health.version,
                    "engine_view_version": self.capabilities.version,
                },
            },
        )


class QueueTemporalGuard:
    """Scope-bound, replayable monitor. Ledger/receipts remain the only authority.

    Callers own a manifest of one actual World episode, an initial authoritative
    State and its Task. No global World identity exists in the current contracts;
    this API does not discover unrelated writers/runs or authenticate arbitrary
    user-supplied State. It checks the supplied observation against that manifest.
    Each call revalidates all source runs and creates a private, ephemeral Registry.
    No accepted prediction/available verdict is stored for fallback on errors.
    """

    def __init__(
        self,
        store: Store,
        task: Task,
        model: DynamicsModel,
        training_episodes: dict[str, list[str]],
        *,
        episode_id: str,
        initial: State,
        config: DriftConfig = DriftConfig(),
    ):
        if not episode_id or episode_id in training_episodes:
            raise ValueError("Monitoring episode must be separate from training")
        self._store = store
        self._task = Task.model_validate(task.model_dump())
        self._model = DynamicsModel.model_validate(model.model_dump())
        self._training = {k: tuple(v) for k, v in training_episodes.items()}
        self._episode = episode_id
        self._initial = State.model_validate(initial.model_dump())
        self._config = config
        if (
            initial.kind != "observed"
            or initial.domain != DOMAIN
            or initial.provenance != PROVENANCE
            or initial.payload.get("tick") != 0
        ):
            raise ValueError("Monitoring requires an authoritative Queue v2 observation")
        if _time(self._initial) > datetime.now(timezone.utc):
            raise ValueError("Deployment observation is in the future")
        self._initial_identity = identity(self._initial.model_dump())
        # A source-prefix commitment prevents a caller silently truncating/replacing
        # history within this instance. Restart callers must supply the complete manifest.
        self._committed: tuple[str, ...] = ()
        self._latched = False

    async def _health(self, state: State, run_ids: tuple[str, ...]) -> ModelHealth:
        if (
            self._initial_identity != identity(self._initial.model_dump())
            or state.kind != "observed"
            or state.domain != DOMAIN
            or state.provenance != PROVENANCE
            or _time(state) < _time(self._initial)
            or _time(state) > datetime.now(timezone.utc)
            or run_ids[: len(self._committed)] != self._committed
        ):
            raise ValueError("Changed scope, stale observation or truncated monitoring history")
        adapter = QueueServiceAdapter()
        self._model.compatible(adapter)
        episodes = {k: list(v) for k, v in self._training.items()}
        episodes[self._episode] = list(run_ids)
        dataset = TransitionDataset(self._store, episodes)
        snapshot = await dataset.snapshot()
        snapshot.verify_identity()
        if snapshot.unlabelled_receipts:
            raise ValueError("Pending/aborted/incomplete source execution")
        for run_id in run_ids:
            run = await self._store.call("get_run", run_id)
            if run is None or run["config"].get("task") != self._task.model_dump():
                raise ValueError("Monitoring run belongs to a different Task/World scope")
            if run["status"] not in {"complete", "failed_task", "abstained"}:
                raise ValueError("Monitoring source run is not finalized")
        training = [r for r in snapshot.transitions if r.episode_id != self._episode]
        monitoring = [r for r in snapshot.transitions if r.episode_id == self._episode]
        # An observation can precede its durable outcome commit. A historical
        # cutoff must not learn from a receipt that became available afterward.
        for row in snapshot.transitions:
            events = await self._store.call("read_events", row.run_id)
            seq = int(row.outcome_reference.rsplit(":", 1)[1])
            event = next(e for e in events if e["seq"] == seq)
            committed = datetime.fromisoformat(event["timestamp"])
            cutoff = _time(state) if row.episode_id == self._episode else _time(self._initial)
            if committed.tzinfo is None or committed.utcoffset() is None or committed > cutoff:
                raise ValueError("Outcome was not committed by the observation cutoff")
        selected = training[: len(self._model.receipts)]
        if (
            tuple(r.receipt for r in selected) != self._model.receipts
            or tuple(r.outcome_reference for r in selected) != self._model.outcome_references
            or identity([r.model_dump() for r in selected]) != self._model.dataset_hash
            or any(_time(r.after.state) > _time(self._initial) for r in training)
            or set(run_ids) & set(self._model.run_ids)
        ):
            raise ValueError("Training provenance, overlap or temporal alignment mismatch")
        # Reuse the original Trainer to verify every statistic, not a parallel learner.
        # Its bounded selected-row hash also handles a model fitted with limit=N.
        refit = await TabularDynamicsTrainer().fit(
            TransitionDataset(self._store, {k: list(v) for k, v in self._training.items()}),
            adapter,
            TrainingConfig(min_samples=self._model.min_samples, seed=self._model.seed),
            limit=len(selected),
        )
        if refit != self._model:
            raise ValueError("Model statistics/code do not match verified training data")
        previous = self._initial
        samples = []
        for row in monitoring:
            before, after = row.before, row.after.state
            if (
                not _same_observation(previous, before)
                or _time(before) < _time(previous)
                or _time(after) > _time(state)
                or before.payload["tick"] >= state.payload["tick"]
                or after.payload["tick"] != before.payload["tick"] + 1
                or before.provenance != PROVENANCE
                or after.provenance != PROVENANCE
            ):
                raise ValueError("Future, discontinuous or unrelated monitoring transition")
            target = adapter.targets(row.model_copy(deep=True))
            if target[0]:
                samples.append(
                    ServiceSample(
                        row.receipt,
                        row.outcome_reference,
                        before.payload["tick"],
                        bool(target[1]),
                        "probe" if target[2] else "ordinary",
                    )
                )
            previous = after
        if not _same_observation(previous, state) or _time(state) < _time(previous):
            raise ValueError("Current observation is not the source manifest cutoff")
        targets = [adapter.targets(r.model_copy(deep=True)) for r in selected]
        basis = identity(
            {
                "initial": self._initial.model_dump(),
                "episode": self._episode,
                "rows": [r.model_dump() for r in monitoring],
                "runs": list(run_ids),
            }
        )
        health = replay_health(
            model_version=self._model.version,
            dataset_hash=self._model.dataset_hash,
            scope=self._episode,
            basis_hash=basis,
            training_count=sum(int(t[0]) for t in targets),
            training_high=sum(int(t[1]) for t in targets),
            samples=tuple(samples),
            cutoff_tick=state.payload["tick"],
            config=self._config,
        )
        if self._latched and health.status != "invalidated":
            raise ValueError("Invalidation history disappeared")
        return health

    async def health(self, state: State, run_ids: list[str]) -> ModelHealth:
        """Read-only cutoff validation, including terminal observations and replay."""
        observed = State.model_validate(state.model_dump())
        runs = tuple(run_ids)
        all_runs = [r for v in self._training.values() for r in v] + list(runs)
        heads = await self._store.call("run_heads", all_runs)
        health = await self._health(observed, runs)
        if await self._store.call("run_heads", all_runs) != heads:
            raise ValueError("Source authority changed during health validation")
        self._committed = runs
        self._latched |= health.status == "invalidated"
        return health

    async def compare(
        self, state: State, actions: list[Action], run_ids: list[str], *, horizon: int = 3
    ) -> dict:
        """Validate the explicit cutoff, compare, then fence source updates.

        Validation failures raise; legitimate statistical invalidation or data
        shortage returns INFERENCE unknown. Cancellation never returns stale output.
        """
        observed = State.model_validate(state.model_dump())
        actions = [Action.model_validate(a.model_dump()) for a in actions]
        runs = tuple(run_ids)
        all_runs = [r for v in self._training.values() for r in v] + list(runs)
        heads = await self._store.call("run_heads", all_runs)
        health = await self._health(observed, runs)
        self._committed = runs
        self._latched |= health.status == "invalidated"
        engine = QueueTemporalEngine(self._task, self._model)
        view = _HealthView(engine, health)
        # Registry cache is intentionally episode-call local. Even identical
        # State IDs cannot retrieve a prior usable view after invalidation.
        result = await compare_actions(Registry([view]), view, observed, actions, horizon=horizon)
        if await self._store.call("run_heads", all_runs) != heads:
            raise ValueError("Source authority changed during monitored prediction")
        return {
            **result,
            "health": asdict(health),
            "health_version": health.version,
            "engine_view_version": view.capabilities.version,
        }
