"""Opt-in one-candidate model selection; Runtime alone grants execution authority."""

import copy
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone

from preact.core.models import Action, Policy, State, identity
from preact.core.registry import Registry
from preact.core.store import Store
from preact.domains.information_queue import InformationQueueWorld, bounded_safe
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning.drift import DriftConfig
from preact.learning.dynamics import DynamicsModel
from preact.learning.recovery import (
    CandidateEvaluation,
    CandidateModel,
    ForecastRecord,
    ModelPromotion,
    RecoveryOrigin,
    RecoveryRejected,
)

from .information import InformationQueuePlanner
from .models import Belief, Experience, Goal


class MonitoredQueueSource:
    """Explicit owner manifest; no discovery or independent execution authority.

    Source run tokens fence all known dependencies, including the recovery journal.
    This is a single-owner, point-in-time guarantee, not a distributed transaction.
    """

    def __init__(self, store: Store, guard: QueueTemporalGuard, training: dict[str, list[str]]):
        self.store, self.guard = store, guard
        if {k: tuple(v) for k, v in training.items()} != guard._training:
            raise ValueError("Source fence must include the guard training manifest")
        self._sources = tuple(r for runs in training.values() for r in runs)
        self.recovery: QueueModelRecovery | None = None
        self.promotion: ModelPromotion | None = None
        self._static_heads = store.run_heads(self._sources)
        self._artifact_hashes: tuple[str, ...] = ()
        self._revalidation_usage: tuple[dict, ...] = ()
        self._statistical_deactivation: str | None = None

    @classmethod
    def promoted(cls, recovery: QueueModelRecovery, promotion_artifact: str):
        def load(digest, kind):
            return kind.model_validate(json.loads(recovery.artifacts.read(digest)))

        promotion = load(promotion_artifact, ModelPromotion)
        if recovery._active is None or recovery._active[0] != promotion:
            raise ValueError("Explicitly promote or restore this model before binding selection")
        report = load(promotion.evaluation_artifact, CandidateEvaluation)
        candidate = load(promotion.candidate_artifact, CandidateModel)
        origin = load(candidate.origin_artifact, RecoveryOrigin)
        source = cls.__new__(cls)
        model = DynamicsModel.load(
            recovery.artifacts, candidate.model_artifact, QueueServiceAdapter()
        )
        source.store, source.recovery = recovery.store, recovery
        source.promotion = promotion.model_copy(deep=True)
        source._statistical_deactivation = None
        source.guard = QueueTemporalGuard(
            recovery.store,
            promotion.task,
            model,
            candidate.training,
            episode_id=promotion.episode_id,
            initial=promotion.initial,
            config=DriftConfig(**promotion.config),
            monitoring_epoch=promotion.monitoring_epoch or promotion.episode_id,
            allow_same_episode=promotion.continuous_episode,
        )
        source._sources = tuple(
            dict.fromkeys(
                [recovery.journal_run]
                + [r for runs in origin.training.values() for r in runs]
                + candidate.source_runs
                + [r for case in report.cases for b in case.branches for r in b.runs]
            )
        )
        source._static_heads = recovery.store.run_heads(source._sources)
        source._artifact_hashes = tuple(
            dict.fromkeys(
                [
                    promotion_artifact,
                    promotion.candidate_artifact,
                    promotion.evaluation_artifact,
                    candidate.origin_artifact,
                    candidate.model_artifact,
                    origin.parent_artifact,
                    *[c.forecast_artifact for c in report.cases],
                ]
            )
        )
        # Recovery._evaluate recomputes three distributions for each three-action
        # case, with fresh Registries. Its successful equality check proves that
        # these saved envelopes match the completed local replay calls. They are
        # usage metadata, not external API or execution receipts.
        source._revalidation_usage = tuple(
            {
                "operation_kind": "local_promotion_revalidation",
                "engine_id": p["engine_id"],
                "engine_version": p["engine_version"],
                "forecast_artifact": case.forecast_artifact,
                "state_id": p["state_id"],
                "action_ids": p["action_ids"],
                "cost_usd": 0.0,
                "cost_known": True,
                "external_api_calls": 0,
            }
            for case in report.cases
            for comparison in load(case.forecast_artifact, ForecastRecord).forecasts.values()
            for p in comparison["predictions"]
        )
        return source

    @property
    def revalidation_calls(self) -> int:
        return len(self._revalidation_usage)

    async def heads(self, runs: list[str]):
        requested = list(dict.fromkeys([*self._sources, *runs]))
        heads = await self.store.call("run_heads", requested)
        if (
            set(heads) != set(requested)
            or {r: heads[r] for r in self._sources} != self._static_heads
        ):
            raise ValueError("Historical model source authority changed or disappeared")
        if self.recovery is not None:
            for digest in self._artifact_hashes:
                self.recovery.artifacts.read(digest)
        return heads

    async def compare(self, state: State, actions: list[Action], runs: list[str], prior=None):
        if self.recovery is not None:
            if prior is not None:
                raise ValueError("Promoted model selection does not override its distribution")
            health = await self.guard.health(state, runs)
            active = self.recovery._active
            if (
                active is None
                and health.status == "available"
                and self._statistical_deactivation is None
            ) or (active is not None and active[0] != self.promotion):
                raise ValueError("Promoted selection lost its explicitly validated active model")
            try:
                result = await self.recovery.compare(state, actions, runs)
                if (
                    result.get("reason") == "not_promoted"
                    and health.status == "available"
                    and self._statistical_deactivation is None
                ):
                    raise ValueError("Promoted selection lost its active model during comparison")
                return {
                    **result,
                    "health": asdict(health),
                    "promotion": self.promotion.model_dump(),
                    "revalidation_usage": copy.deepcopy(self._revalidation_usage)
                    if result.get("reason") != "not_promoted"
                    else (),
                    "statistical_deactivation": self._statistical_deactivation,
                }
            except RecoveryRejected as exc:
                # Only these documented statistical states permit normal fallback.
                if str(exc) not in {
                    "monitor_health_invalidated",
                    "monitor_health_suspected",
                    "monitor_health_insufficient_data",
                    "monitor_health_unavailable",
                }:
                    raise
                if health.status == "available":
                    raise ValueError("Recovered and source health disagree") from exc
                self._statistical_deactivation = str(exc)
                return {
                    "status": "unknown",
                    "reason": str(exc),
                    "predictions": [],
                    "health": asdict(health),
                    "promotion": self.promotion.model_dump(),
                    # These rejections occur after complete evaluation replay.
                    # Earlier corruption/errors propagate and retain reservation.
                    "revalidation_usage": copy.deepcopy(self._revalidation_usage),
                    "statistical_deactivation": self._statistical_deactivation,
                }
        health = await self.guard.health(state, runs)
        if health.status != "available":
            return {"status": "unknown", "health": asdict(health), "predictions": []}
        if state.payload["tick"] + 3 > state.payload["episode_ticks"]:
            return {
                "status": "unknown",
                "health": asdict(health),
                "reason": "bounded_horizon",
                "predictions": [],
            }
        if prior is None:
            return await self.guard.compare(state, actions, runs)
        engine = QueueTemporalEngine(self.guard._task, prior=prior)
        result = await compare_actions(Registry([engine]), engine, state, actions)
        return {
            **result,
            "health": asdict(health),
            "engine_view_version": engine.capabilities.version,
        }


def candidate_value(prediction: dict, state: State, goal: Goal, amount: int) -> float:
    """Fixed H3 expected capped progress minus holding and root submission costs."""
    remaining = max(0.0, goal.target - goal.progress(state))
    payload = state.payload
    return sum(
        b["weight"]
        * (
            min(remaining, b["delivered"][-1] - payload["delivered"])
            - payload["holding_cost"] * sum(b["queue"])
            - payload["submission_cost"] * amount
        )
        for b in prediction["raw"]["branches"]
    )


class QueueModelPlanner:
    """Stateful, episode-owned selector; deliberately does not opt into Belief reuse.

    A selection is a proposal, never evidence or execution approval. Failure/cancel
    clears the pending decision. Every dispatch must pass SelectionWorld checks.
    Local predictions use Runtime's existing proposal reservation/usage channel.
    """

    proposal_usage_complete = True
    multi_candidate = False

    @property
    def proposal_calls(self):
        return 3 + self.source.revalidation_calls if self.mode in {"learned", "prior"} else 0

    def __init__(
        self,
        source: MonitoredQueueSource,
        *,
        prefix_runs=(),
        prior: float | None = None,
        mode: str = "learned",
        fallback: str = "existing",
    ):
        if mode not in {"learned", "prior", "existing", "fixed", "unavailable"}:
            raise ValueError("Unknown selection mode")
        if fallback not in {"existing", "abstain"} or (mode == "prior" and prior is None):
            raise ValueError("Invalid explicit fallback/prior")
        self.source, self.prior, self.mode, self.fallback = source, prior, mode, fallback
        self.prefix_runs = tuple(prefix_runs)
        self.baseline = InformationQueuePlanner("no_probe")
        self.usage: list[dict] = []
        self.records: list[dict] = []
        self.pending: dict | None = None
        self._seal = ""
        self.run_id: str | None = None
        self.runs: list[str] = []
        self.policy: Policy | None = None
        self._world_bound = False

    def begin_round(self, run_id: str, policy: Policy, previous: tuple[str, ...]):
        self.end_round()
        width = 3 if self.multi_candidate else 1
        if (
            policy.width != width
            or policy.max_nodes < width
            or policy.search
            or (not self.multi_candidate and policy.max_nodes != 1)
        ):
            raise ValueError(
                f"Queue model selection requires width={width}/max_nodes={width}/search=False"
            )
        self.run_id, self.policy = run_id, policy.model_copy(deep=True)
        self.runs = [*self.prefix_runs, *previous]

    def end_round(self):
        self.pending = None
        self._seal = ""
        self.run_id = None
        self.policy = None

    def infer(self, state: State, experience: list[Experience]) -> Belief:
        return self.baseline.infer(state, experience)

    async def propose(self, belief: Belief, goals: list[Goal], width: int) -> list[Action]:
        self.pending = None
        self.usage.clear()
        if (
            not self._world_bound
            or self.run_id is None
            or width != (3 if self.multi_candidate else 1)
        ):
            raise ValueError("Use QueueSelectionWorld with an active CognitiveAgent round")
        if len(goals) != 1 or goals[0].metric != "delivered":
            raise ValueError("Selection v1 requires exactly one delivered goal")
        cpu, wall = time.process_time(), time.perf_counter()
        state = State.model_validate(belief.observed.model_dump())
        heads = await self.source.heads(self.runs)
        actions = [InformationQueueWorld.action(state, n) for n in (3, 1, 0)]
        result = {"status": "unknown", "reason": self.mode, "predictions": []}
        try:
            if self.mode in {"learned", "prior"}:
                result = await self.source.compare(
                    state, actions, self.runs, self.prior if self.mode == "prior" else None
                )
            else:
                # Baselines still validate the same receipt/scope authority.
                if self.source.guard is not None:
                    health = await self.source.guard.health(state, self.runs)
                    result["health"] = asdict(health)
            predictions = [p.model_dump() for p in result["predictions"]]
            self.usage.extend(result.get("revalidation_usage", ()))
            for p in predictions:
                self.usage.append(
                    {
                        "operation_kind": "local_model_prediction",
                        "engine_id": p["engine_id"],
                        "engine_version": p["engine_version"],
                        "cost_usd": 0.0,
                        "cost_known": True,
                        "external_api_calls": 0,
                    }
                )
            if state.payload["tick"] + 3 <= state.payload["episode_ticks"] and any(
                p["raw"].get("reason") == "unsupported_or_failed" for p in predictions
            ):
                raise ValueError("Temporal inference failed or unsupported input")
            available = (
                self.mode in {"learned", "prior"}
                and result.get("health", {}).get("status") == "available"
                and result["status"] == "estimated"
                and len(predictions) == 3
                and all(p["raw"]["status"] == "estimated" for p in predictions)
            )
            candidates = []
            for i, action in enumerate(actions):
                amount = action.payload["amount"]
                candidates.append(
                    {
                        "action": action.model_dump(),
                        "fingerprint": action.fingerprint,
                        "amount": amount,
                        "public_bound_safe": bounded_safe(state.payload, amount),
                        "prediction": predictions[i] if len(predictions) == 3 else None,
                        "value": candidate_value(predictions[i], state, goals[0], amount)
                        if available
                        else None,
                    }
                )
            safe = (
                candidates
                if self.multi_candidate
                else [c for c in candidates if c["public_bound_safe"]]
            )
            if available:
                ranked = sorted(safe, key=lambda c: (-c["value"], c["amount"]))
                chosen = Action.model_validate(ranked[0]["action"]) if ranked else None
                basis = "fixed_prior" if self.mode == "prior" else "learned_model"
            elif self.mode == "fixed":
                ranked = sorted(safe, key=lambda c: (c["amount"] != 1, c["amount"]))
                chosen = Action.model_validate(ranked[0]["action"]) if ranked else None
                basis = "fixed_rule"
            elif self.fallback == "existing":
                proposals = self.baseline.propose(belief, goals, 3)
                chosen = next(
                    (a for a in proposals if bounded_safe(state.payload, a.payload["amount"])), None
                )
                ranked, basis = [], "existing_planner" if self.mode == "existing" else "fallback"
                if self.multi_candidate:
                    order = [a.fingerprint for a in proposals]
                    ranked = sorted(
                        candidates,
                        key=lambda c: (
                            order.index(c["fingerprint"])
                            if c["fingerprint"] in order
                            else len(order),
                            c["amount"],
                        ),
                    )
            else:
                chosen, ranked, basis = None, [], "abstain"
            if await self.source.heads(self.runs) != heads:
                raise ValueError("Authority changed during action selection")
            record = {
                "schema_version": "1",
                "run_id": self.run_id,
                "state": state.model_dump(),
                "goals": [g.model_dump() for g in goals],
                "evaluation_function": "capped-progress-holding-root-cost/v1",
                "horizon": 3,
                "mode": self.mode,
                "basis": basis,
                "health": result.get("health"),
                "reason": result.get("reason", result["status"]),
                "engine_view_version": result.get("engine_view_version"),
                "promotion": result.get("promotion"),
                "statistical_deactivation": result.get("statistical_deactivation"),
                "source_runs": list(heads),
                "source_heads": {r: asdict(h) if h is not None else None for r, h in heads.items()},
                "monitor_runs": self.runs.copy(),
                "candidates": candidates,
                "ranked_fingerprints": [c["fingerprint"] for c in ranked],
                "selected": chosen.model_dump() if chosen else None,
                "selected_fingerprint": chosen.fingerprint if chosen else None,
                "local_prediction_calls": len(predictions),
                "local_revalidation_calls": len(result.get("revalidation_usage", ())),
                "local_total_prediction_calls": len(self.usage),
                "cpu_seconds": time.process_time() - cpu,
                "wall_seconds": time.perf_counter() - wall,
                "external_api_calls": 0,
                "local_api_cost_usd": 0,
                "compute_dollar_cost": None,
            }
            if self.multi_candidate:
                record["first_choice_fingerprint"] = ranked[0]["fingerprint"] if ranked else None
                record["selected"] = record["selected_fingerprint"] = None
                record["selection_mode"] = "qualified_value/v1"
                record["selection_rule"] = self.selection_rule
            record = json.loads(json.dumps(record, allow_nan=False))
            await self.source.store.call(
                "append",
                self.run_id,
                "cognitive_prediction" if self.multi_candidate else "cognitive_selection",
                record,
            )
            self.records.append(copy.deepcopy(record))
            self.pending = copy.deepcopy(record)
            self._seal = identity(record)
            if self.multi_candidate:
                return actions if ranked else []
            return [chosen] if chosen else []
        except BaseException:
            self.pending = None
            self._seal = ""
            raise

    async def check(self, state: State, action: Action | None = None):
        if self.pending is None:
            return
        record = self.pending
        if identity(record) != self._seal or self.run_id != record["run_id"]:
            raise ValueError("Selection record changed")
        expected = State.model_validate(record["state"])
        actual_time = datetime.fromisoformat(state.timestamp)
        expected_time = datetime.fromisoformat(expected.timestamp)
        if actual_time.tzinfo is None or actual_time.utcoffset() is None:
            raise ValueError("Fresh observation requires an aware timestamp")
        if (
            state.model_dump(exclude={"timestamp"}) != expected.model_dump(exclude={"timestamp"})
            or actual_time < expected_time
            or actual_time > datetime.now(timezone.utc)
        ):
            raise ValueError("Fresh observation differs from selection authority")
        heads = await self.source.heads(record["monitor_runs"])
        if {r: asdict(h) if h is not None else None for r, h in heads.items()} != record[
            "source_heads"
        ]:
            raise ValueError("Selection source authority changed before dispatch")
        events = await self.source.store.call("read_events", self.run_id)
        records = [
            e
            for e in events
            if e["kind"]
            == ("cognitive_prediction" if self.multi_candidate else "cognitive_selection")
        ]
        if len(records) != 1 or records[0]["data"] != record:
            raise ValueError("Ledger selection trace changed")
        if action is not None:
            if self.multi_candidate:
                self.check_final_selection(events, action)
            elif action.fingerprint != record["selected_fingerprint"]:
                raise ValueError("Dispatch action differs from selection")
