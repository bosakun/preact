"""Explicit, single-owner Queue recovery. No World reset, ranking or execution authority."""

import asyncio
import json
from dataclasses import asdict

from preact.core.models import Action, State, Task, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import action_amount
from preact.domains.queue_recovery import (
    check_cutoff,
    effective,
    observed,
    promotion_reasons,
    same_state,
    score,
    timepoint,
    validate_final_observation,
)
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import (
    CandidateEvaluation,
    CandidateModel,
    EvaluationCase,
    ForecastRecord,
    ModelPromotion,
    RecoveryOrigin,
    RecoveryRejected,
    recovery_code_hash,
)
from preact.learning.transitions import require_disjoint


def comparison_data(result: dict) -> dict:
    """Retain all semantic forecast content, excluding identity/latency bookkeeping."""
    return {
        **result,
        "predictions": [p.model_dump(exclude={"id", "latency_ms"}) for p in result["predictions"]],
    }


class QueueModelRecovery:
    """One explicit recovery per journal; only this owner's compare uses its active model.

    Artifacts are derived records; every operation revalidates their receipt sources.
    The journal uses existing Store events, not an execution/evidence ledger. An async
    lock serializes this owner's operations; multiple active owners are not supported.
    Restart must explicitly restore against a fresh supplied World observation and
    complete manifest. No saved available verdict authorizes a forecast by itself.
    """

    def __init__(self, store: Store, artifacts: Artifacts, journal_run: str):
        self.store, self.artifacts, self.journal_run = store, artifacts, journal_run
        self._lock = asyncio.Lock()
        self._active: tuple[ModelPromotion, QueueTemporalGuard] | None = None

    @classmethod
    async def create(cls, store: Store, artifacts: Artifacts):
        run = await store.call("create_run", {"kind": "queue_model_recovery", "version": "1"})
        return cls(store, artifacts, run)

    def _load(self, digest: str, record_type):
        return record_type.model_validate(json.loads(self.artifacts.read(digest)))

    async def _record(self, kind: str, record) -> str:
        record = type(record).model_validate(record.model_dump())
        digest = self.artifacts.json(record.model_dump())
        existing = [
            e
            for e in await self._journal()
            if e["kind"] == kind and e["data"] == {"artifact": digest}
        ]
        if len(existing) > 1:
            raise ValueError("Duplicate lifecycle stage record")
        if existing:
            return digest
        await self.store.call("append", self.journal_run, kind, {"artifact": digest})
        return digest

    async def _journal(self):
        run = await self.store.call("get_run", self.journal_run)
        if run is None or run["config"] != {"kind": "queue_model_recovery", "version": "1"}:
            raise ValueError("Invalid recovery journal")
        return await self.store.call("read_events", self.journal_run)

    async def _event(self, kind: str, digest: str):
        matches = [
            e
            for e in await self._journal()
            if e["kind"] == kind and e["data"] == {"artifact": digest}
        ]
        if len(matches) != 1:
            raise ValueError("Missing or duplicate lifecycle stage record")
        return matches[0]

    def _parent(self, origin: RecoveryOrigin):
        return DynamicsModel.load(self.artifacts, origin.parent_artifact, QueueServiceAdapter())

    def _guard(self, origin: RecoveryOrigin):
        return QueueTemporalGuard(
            self.store,
            origin.task,
            self._parent(origin),
            origin.training,
            episode_id=origin.episode_id,
            initial=origin.initial,
            config=DriftConfig(**origin.config),
        )

    async def begin(
        self,
        parent: DynamicsModel,
        training: dict[str, list[str]],
        *,
        episode_id: str,
        task: Task,
        initial: State,
        cutoff: State,
        runs: list[str],
        config: DriftConfig = DriftConfig(),
    ) -> str:
        """Capture the first invalidation observation, BEFORE collecting recovery data."""
        async with self._lock:
            task = Task.model_validate(task.model_dump())
            initial, cutoff = (State.model_validate(s.model_dump()) for s in (initial, cutoff))
            runs, training = list(runs), {k: list(v) for k, v in training.items()}
            if await self._journal():
                raise ValueError("Recovery journal already started")
            origin = RecoveryOrigin(
                parent_artifact=parent.save(self.artifacts),
                training=training,
                episode_id=episode_id,
                task=task,
                initial=initial,
                cutoff=cutoff,
                runs=runs,
                health_version="",
                config=asdict(config),
            )
            origin = RecoveryOrigin.model_validate(origin.model_dump())
            health = await self._guard(origin).health(cutoff, runs)
            first = next((h for h in health.history if h.status == "invalidated"), None)
            if first is None:
                raise RecoveryRejected("parent_not_invalidated")
            if first.tick + 1 != cutoff.payload["tick"]:
                raise ValueError("Capture the first invalidation cutoff, not a later suffix")
            origin = origin.model_copy(update={"health_version": health.version})
            return await self._record("model_recovery_started", origin)

    async def _origin(self, digest: str) -> RecoveryOrigin:
        origin = self._load(digest, RecoveryOrigin)
        event = await self._event("model_recovery_started", digest)
        if timepoint(origin.cutoff.timestamp) > timepoint(event["timestamp"]):
            raise ValueError("Recovery start predates its invalidation observation")
        health = await self._guard(origin).health(origin.cutoff, origin.runs)
        first = next((h for h in health.history if h.status == "invalidated"), None)
        if (
            first is None
            or first.tick + 1 != origin.cutoff.payload["tick"]
            or health.version != origin.health_version
        ):
            raise ValueError("Invalidation basis changed")
        return origin

    async def _training(self, origin: RecoveryOrigin, state: State, runs: list[str]):
        if runs[: len(origin.runs)] != origin.runs:
            raise ValueError("Recovery source truncated or replaced")
        await self._guard(origin).health(state, runs)
        snapshot = await TransitionDataset(self.store, {origin.episode_id: runs}).snapshot()
        await check_cutoff(self.store, snapshot, state.timestamp)
        rows = [
            r
            for r in snapshot.transitions
            if r.before.payload["tick"] >= origin.cutoff.payload["tick"]
        ]
        if any(timepoint(r.before.timestamp) < timepoint(origin.cutoff.timestamp) for r in rows):
            raise ValueError("Recovery teacher precedes invalidation cutoff")
        selected = list(dict.fromkeys(r.run_id for r in rows))
        dataset = TransitionDataset(self.store, {origin.episode_id: selected})
        suffix = await dataset.snapshot()
        if [r.model_dump(exclude={"continuous_from_previous"}) for r in suffix.transitions] != [
            r.model_dump(exclude={"continuous_from_previous"}) for r in rows
        ]:
            raise ValueError("Recovery training requires exact whole-run suffix")
        return dataset, suffix

    async def prepare_candidate(self, origin_artifact: str, state: State, runs: list[str]) -> str:
        state, runs = State.model_validate(state.model_dump()), list(runs)
        async with self._lock:
            origin = await self._origin(origin_artifact)
            all_runs = [r for v in origin.training.values() for r in v] + runs
            heads = await self.store.call("run_heads", all_runs)
            dataset, snapshot = await self._training(origin, state, runs)
            count = effective(snapshot)
            if count < 16:
                raise RecoveryRejected("insufficient_training")
            model = await TabularDynamicsTrainer().fit(
                dataset,
                QueueServiceAdapter(),
                TrainingConfig(min_samples=16),
            )
            if model.version == self._parent(origin).version:
                raise ValueError("Candidate did not create a new model version")
            if await self.store.call("run_heads", all_runs) != heads:
                raise ValueError("Authority changed during candidate creation")
            candidate = CandidateModel(
                origin_artifact=origin_artifact,
                source_cutoff=state,
                source_runs=runs,
                training=snapshot.episodes,
                model_artifact=model.save(self.artifacts),
                model_version=model.version,
                dataset_hash=model.dataset_hash,
                effective_count=count,
                code_hash=recovery_code_hash(),
            )
            return await self._record("model_candidate_created", candidate)

    async def _candidate(self, digest: str):
        candidate = self._load(digest, CandidateModel)
        event = await self._event("model_candidate_created", digest)
        origin = await self._origin(candidate.origin_artifact)
        if candidate.code_hash != recovery_code_hash() or timepoint(
            candidate.source_cutoff.timestamp
        ) > timepoint(event["timestamp"]):
            raise ValueError("Candidate code or time mismatch")
        dataset, snapshot = await self._training(
            origin, candidate.source_cutoff, candidate.source_runs
        )
        model = DynamicsModel.load(self.artifacts, candidate.model_artifact, QueueServiceAdapter())
        refit = await TabularDynamicsTrainer().fit(
            dataset,
            QueueServiceAdapter(),
            TrainingConfig(min_samples=16),
        )
        if (
            model != refit
            or model.version != candidate.model_version
            or model.dataset_hash != candidate.dataset_hash
            or candidate.training != snapshot.episodes
            or candidate.effective_count != effective(snapshot)
            or candidate.effective_count < 16
        ):
            raise ValueError("Candidate does not match verified training authority")
        return candidate, origin, model, snapshot

    async def _forecasts(self, task: Task, state: State, actions: list[Action], model, parent):
        task = Task.model_validate(task.model_dump())
        state = State.model_validate(state.model_dump())
        actions = [Action.model_validate(a.model_dump()) for a in actions]
        observed(state)
        if [action_amount(state, a) for a in actions] != [3, 1, 0]:
            raise ValueError("Evaluation requires submit(3), submit(1), drain in order")
        result = {}
        for name, engine in (
            ("candidate", QueueTemporalEngine(task, model)),
            ("parent", QueueTemporalEngine(task, parent)),
            ("prior", QueueTemporalEngine(task, prior=0.5)),
        ):
            result[name] = comparison_data(
                await compare_actions(Registry([engine]), engine, state, actions)
            )
        return result

    async def forecast_candidate(
        self, digest: str, task: Task, state: State, actions: list[Action]
    ) -> str:
        """Persist forecasts before any evaluation root action executes."""
        task, state = (
            Task.model_validate(task.model_dump()),
            State.model_validate(state.model_dump()),
        )
        actions = [Action.model_validate(a.model_dump()) for a in actions]
        async with self._lock:
            sources = await self._source_runs(digest, [])
            heads = await self.store.call("run_heads", sources)
            candidate, origin, model, _ = await self._candidate(digest)
            if timepoint(state.timestamp) < timepoint(candidate.source_cutoff.timestamp):
                raise ValueError("Forecast predates training")
            forecasts = await self._forecasts(task, state, actions, model, self._parent(origin))
            if await self.store.call("run_heads", sources) != heads:
                raise ValueError("Authority changed during evaluation forecasting")
            return await self._record(
                "model_evaluation_forecast",
                ForecastRecord(
                    candidate_artifact=digest,
                    task=task,
                    state=state,
                    actions=actions,
                    forecasts=forecasts,
                ),
            )

    async def _evaluate(self, digest: str, cases: list[EvaluationCase], cutoff: str):
        candidate, origin, model, training = await self._candidate(digest)
        comparisons = {k: [] for k in ("candidate", "parent", "prior")}
        truths, count, nonempty, episodes, used_forecasts = [], 0, set(), {}, set()
        source = await TransitionDataset(
            self.store, {origin.episode_id: candidate.source_runs}
        ).snapshot()
        parent_training = await TransitionDataset(self.store, origin.training).snapshot()
        for case in cases:
            if case.forecast_artifact in used_forecasts or len(case.branches) != 3:
                raise ValueError("Duplicate forecast or invalid comparison branch count")
            used_forecasts.add(case.forecast_artifact)
            record = self._load(case.forecast_artifact, ForecastRecord)
            event = await self._event("model_evaluation_forecast", case.forecast_artifact)
            if record.candidate_artifact != digest or timepoint(event["timestamp"]) > timepoint(
                cutoff
            ):
                raise ValueError("Evaluation candidate or cutoff mismatch")
            recalculated = await self._forecasts(
                record.task, record.state, record.actions, model, self._parent(origin)
            )
            if identity(recalculated) != identity(record.forecasts):
                raise ValueError("Forecast content mismatch")
            actual = []
            for i, branch in enumerate(case.branches):
                if branch.episode_id in episodes:
                    raise ValueError("Evaluation episode reused")
                episodes[branch.episode_id] = branch.runs
                snapshot = await TransitionDataset(
                    self.store, {branch.episode_id: branch.runs}
                ).snapshot()
                for other in (training, source, parent_training):
                    require_disjoint(other, snapshot)
                await check_cutoff(self.store, snapshot, cutoff)
                await validate_final_observation(
                    self.store, snapshot, branch.final_observation, cutoff
                )
                rows = snapshot.transitions
                if len(rows) < 3:
                    raise ValueError("Evaluation requires actual three-tick outcomes")
                root, rest = rows[-3], rows[-2:]
                if (
                    not same_state(root.before, record.state)
                    or root.action.fingerprint != record.actions[i].fingerprint
                    or timepoint(root.before.timestamp) < timepoint(event["timestamp"])
                    or any(
                        action_amount(r.before, r.action) != 0 or r.action.kind != "submit"
                        for r in rest
                    )
                ):
                    raise ValueError("Evaluation root or environment-only continuation mismatch")
                guard = QueueTemporalGuard(
                    self.store,
                    record.task,
                    model,
                    candidate.training,
                    episode_id=branch.episode_id,
                    initial=branch.initial,
                    config=DriftConfig(**origin.config),
                )
                await guard.health(branch.final_observation, branch.runs)
                count += sum(int(QueueServiceAdapter().targets(r)[0]) for r in rows[-3:])
                actual.append([r.after.state.payload for r in rows[-3:]])
            if record.state.payload["queue"]:
                nonempty.add("queue")
            if record.state.payload["pending"]:
                nonempty.add("pending")
            truths.append(actual)
            for name in comparisons:
                comparisons[name].append(record.forecasts[name])
        evaluation = await TransitionDataset(self.store, episodes).snapshot()
        for other in (training, source, parent_training):
            require_disjoint(other, evaluation)
        scores = {k: score(v, truths) for k, v in comparisons.items()}
        reasons = promotion_reasons(scores, count, nonempty)
        return CandidateEvaluation(
            candidate_artifact=digest,
            cases=cases,
            cutoff=cutoff,
            scores=scores,
            effective_count=count,
            passed=not reasons,
            reasons=reasons,
            code_hash=recovery_code_hash(),
        ), evaluation

    async def evaluate_candidate(
        self, digest: str, cases: list[EvaluationCase], cutoff: str
    ) -> str:
        cases = [EvaluationCase.model_validate(c.model_dump()) for c in cases]
        async with self._lock:
            heads = await self.store.call("run_heads", await self._source_runs(digest, cases))
            report, _ = await self._evaluate(digest, cases, cutoff)
            if await self.store.call("run_heads", await self._source_runs(digest, cases)) != heads:
                raise ValueError("Authority changed during candidate evaluation")
            return await self._record("model_candidate_evaluated", report)

    async def _source_runs(self, digest, cases):
        candidate = self._load(digest, CandidateModel)
        origin = self._load(candidate.origin_artifact, RecoveryOrigin)
        return list(
            dict.fromkeys(
                [r for v in origin.training.values() for r in v]
                + candidate.source_runs
                + [r for c in cases for b in c.branches for r in b.runs]
            )
        )

    async def _promotion_guard(self, promotion: ModelPromotion, state: State, runs: list[str]):
        report = self._load(promotion.evaluation_artifact, CandidateEvaluation)
        event = await self._event("model_candidate_evaluated", promotion.evaluation_artifact)
        if (
            not report.passed
            or promotion.candidate_artifact != report.candidate_artifact
            or promotion.code_hash != recovery_code_hash()
            or timepoint(report.cutoff) > timepoint(event["timestamp"])
            or timepoint(event["timestamp"]) > timepoint(promotion.initial.timestamp)
            or runs[: len(promotion.runs)] != promotion.runs
        ):
            raise ValueError("Promotion provenance, time or prefix mismatch")
        recalculated, evaluation = await self._evaluate(
            report.candidate_artifact, report.cases, report.cutoff
        )
        if recalculated != report:
            raise ValueError("Independent evaluation no longer matches")
        candidate, origin, model, training = await self._candidate(report.candidate_artifact)
        if (
            promotion.parent_version != self._parent(origin).version
            or promotion.model_version != model.version
        ):
            raise ValueError("Promotion model versions mismatch")
        monitoring = await TransitionDataset(self.store, {promotion.episode_id: runs}).snapshot()
        for other in (training, evaluation):
            require_disjoint(other, monitoring)
        source = await TransitionDataset(
            self.store, {origin.episode_id: candidate.source_runs}
        ).snapshot()
        parent = await TransitionDataset(self.store, origin.training).snapshot()
        for other in (source, parent):
            require_disjoint(other, monitoring)
        guard = QueueTemporalGuard(
            self.store,
            promotion.task,
            model,
            candidate.training,
            episode_id=promotion.episode_id,
            initial=promotion.initial,
            config=DriftConfig(**promotion.config),
        )
        # Validate the exact recorded promotion prefix too; it cannot be replaced at restart.
        recorded = await guard.health(promotion.cutoff, promotion.runs)
        if promotion.health_version and recorded.version != promotion.health_version:
            raise ValueError("Promotion health basis changed")
        health = await guard.health(state, runs)
        if health.status != "available":
            raise RecoveryRejected("monitor_health_" + health.status)
        return guard, health

    async def promote(
        self,
        evaluation_artifact: str,
        *,
        episode_id: str,
        task: Task,
        initial: State,
        state: State,
        runs: list[str],
    ) -> str:
        """Explicit activation. Clear availability before any potentially durable operation."""
        async with self._lock:
            self._active = None
            try:
                task = Task.model_validate(task.model_dump())
                initial, state = (State.model_validate(s.model_dump()) for s in (initial, state))
                runs = list(runs)
                if any(e["kind"] == "model_promotion_committed" for e in await self._journal()):
                    raise ValueError("One promotion per recovery journal")
                report = self._load(evaluation_artifact, CandidateEvaluation)
                if not report.passed:
                    raise RecoveryRejected("evaluation_rejected:" + ",".join(report.reasons))
                candidate, origin, model, _ = await self._candidate(report.candidate_artifact)
                promotion = ModelPromotion(
                    candidate_artifact=report.candidate_artifact,
                    evaluation_artifact=evaluation_artifact,
                    parent_version=self._parent(origin).version,
                    model_version=model.version,
                    episode_id=episode_id,
                    initial=initial,
                    task=task,
                    runs=runs,
                    cutoff=state,
                    health_version="",
                    config=origin.config,
                    code_hash=recovery_code_hash(),
                )
                promotion = ModelPromotion.model_validate(promotion.model_dump())
                state, runs = promotion.cutoff, list(promotion.runs)
                sources = await self._source_runs(report.candidate_artifact, report.cases) + runs
                heads = await self.store.call("run_heads", sources)
                guard, health = await self._promotion_guard(promotion, state, runs)
                promotion = promotion.model_copy(update={"health_version": health.version})
                digest = await self._record("model_promotion_approved", promotion)
                if await self.store.call("run_heads", sources) != heads:
                    raise ValueError("Authority changed before active model switch")
                await self.store.call(
                    "append", self.journal_run, "model_promotion_committed", {"artifact": digest}
                )
                if await self.store.call("run_heads", sources) != heads:
                    raise ValueError("Authority changed during durable model switch")
                # No await between the final fence and publishing this complete bundle.
                self._active = (promotion, guard)
                return digest
            except BaseException:
                self._active = None
                raise

    async def restore(self, state: State, runs: list[str]) -> str:
        async with self._lock:
            self._active = None
            state, runs = State.model_validate(state.model_dump()), list(runs)
            events = await self._journal()
            switches = [
                e
                for e in events
                if e["kind"] in {"model_promotion_approved", "model_promotion_committed"}
            ]
            if not switches or switches[-1]["kind"] != "model_promotion_committed":
                raise RecoveryRejected("no_committed_promotion")
            committed = switches[-1]
            digest = committed["data"]["artifact"]
            approved = await self._event("model_promotion_approved", digest)
            if approved["seq"] >= committed["seq"] or timepoint(committed["timestamp"]) > timepoint(
                state.timestamp
            ):
                raise ValueError("Promotion commit is outside current cutoff")
            promotion = self._load(digest, ModelPromotion)
            report = self._load(promotion.evaluation_artifact, CandidateEvaluation)
            sources = (
                await self._source_runs(report.candidate_artifact, report.cases)
                + runs
                + [self.journal_run]
            )
            heads = await self.store.call("run_heads", sources)
            guard, _ = await self._promotion_guard(promotion, state, runs)
            if await self.store.call("run_heads", sources) != heads:
                raise ValueError("Authority changed during promotion restore")
            self._active = (promotion, guard)
            return digest

    async def compare(self, state: State, actions: list[Action], runs: list[str]) -> dict:
        async with self._lock:
            if self._active is None:
                return {
                    "status": "unknown",
                    "reason": "not_promoted",
                    "predictions": [],
                    "differences": [],
                }
            promotion, guard = self._active
            try:
                state, runs = State.model_validate(state.model_dump()), list(runs)
                actions = [Action.model_validate(a.model_dump()) for a in actions]
                report = self._load(promotion.evaluation_artifact, CandidateEvaluation)
                sources = await self._source_runs(report.candidate_artifact, report.cases) + runs
                heads = await self.store.call("run_heads", sources)
                validated, _ = await self._promotion_guard(promotion, state, runs)
                # Retain the live Guard prefix/latched invalidation as well as replay validation.
                await validated.health(state, runs)
                result = await guard.compare(state, actions, runs)
                if await self.store.call("run_heads", sources) != heads:
                    raise ValueError("Authority changed during recovered prediction")
                return {**result, "promotion": promotion.model_dump()}
            except BaseException:
                self._active = None
                raise
