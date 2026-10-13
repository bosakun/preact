"""Guarded Queue H3 distributions and a separate Queue-only soft Goal adapter."""

from dataclasses import asdict, dataclass

from preact.core.models import Continuation, identity
from preact.domains.information_queue import InformationQueueWorld
from preact.engines.deliberation_usage import usage_record


class QueuePredictionAdapter:
    def __init__(self, source, *, prior=None, continuation=Continuation.ENVIRONMENT_ONLY):
        self.monitored = source
        self.store = source.store
        self.prior = prior
        self.continuation = continuation
        self.usage = []
        self.max_calls = 3 + source.revalidation_calls

    def signature(self):
        guard = self.monitored.guard
        return identity(
            {
                "adapter": "guarded-queue-h3/v1",
                "model": guard._model.model_dump(),
                "config": asdict(guard._config),
                "anchor": guard._initial.model_dump(),
                "anchor_identity": guard._initial_identity,
                "episode": guard._episode,
                "epoch": guard._monitoring_epoch,
                "training": guard._training,
                "max_calls": self.max_calls,
                "task": guard._task.model_dump(),
                "prior": self.prior,
                "continuation": str(self.continuation),
                "promotion": self.monitored.promotion.model_dump()
                if self.monitored.promotion
                else None,
            }
        )

    async def heads(self, runs):
        return await self.monitored.heads(runs)

    async def candidates(self, state, width):
        return [InformationQueueWorld.action(state, n) for n in (3, 1, 0)][:width]

    async def check_basis(self, record):
        original = record["simulation"].get("health")
        guard = self.monitored.guard
        if original is not None and (
            (original["status"] == "available" and guard._latched)
            or guard._model.version != original["model_version"]
            or asdict(guard._config) != original["config"]
            or list(guard._committed) != record["monitor_runs"]
        ):
            raise ValueError("Queue model Health/scope changed")
        source = self.monitored
        if source.recovery is not None and source.promotion is not None:
            active = source.recovery._active
            if active is None or active[0] != source.promotion:
                raise ValueError("Promoted Recovery lost its active model")

    async def forecast(self, state, actions, runs):
        if self.continuation != Continuation.ENVIRONMENT_ONLY:
            return {
                "adapter": "guarded-queue-h3/v1",
                "horizon": 3,
                "continuation": str(self.continuation),
                "candidates": [
                    {
                        "action": a.model_dump(),
                        "fingerprint": a.fingerprint,
                        "status": "unknown",
                        "reason": "unsupported_action_sequence",
                        "records": [],
                        "paths": [],
                    }
                    for a in actions
                ],
                "health": None,
            }
        result = await self.monitored.compare(state, actions, runs, self.prior)
        self.usage.extend(result.get("revalidation_usage", ()))
        predictions = result.get("predictions", [])
        if predictions and (
            len(predictions) != len(actions) or len({p.engine_version for p in predictions}) != 1
        ):
            raise ValueError("Mixed or incomplete prediction view")
        futures = []
        for i, action in enumerate(actions):
            p = predictions[i] if predictions else None
            if p is not None:
                if p.state_id != state.id or p.action_ids != [action.id] or p.horizon != 3:
                    raise ValueError("Queue prediction State/Action/horizon mismatch")
                self.usage.append(usage_record(p))
            available = (
                result.get("status") == "estimated"
                and result.get("health", {}).get("status") == "available"
                and p is not None
                and p.raw.get("status") == "estimated"
            )
            futures.append(
                {
                    "action": action.model_dump(),
                    "fingerprint": action.fingerprint,
                    "status": "estimated" if available else "unknown",
                    "reason": None if available else result.get("reason", "model_unavailable"),
                    "records": [{"prediction": p.model_dump()}] if p else [],
                    "paths": p.raw["branches"] if available else [],
                }
            )
        return {
            "adapter": "guarded-queue-h3/v1",
            "horizon": 3,
            "continuation": str(Continuation.ENVIRONMENT_ONLY),
            "health": result.get("health"),
            "engine_view_version": result.get("engine_view_version"),
            "promotion": result.get("promotion"),
            "candidates": futures,
            "assumptions": [
                "H3 no additional intervention; iid learned service marginal",
                "mean vectors never used as successor State",
            ],
        }


@dataclass(frozen=True)
class QueueGoalAdapter:
    projection: int = 3
    spread_cost: float = 0.0

    def __post_init__(self):
        if self.projection not in (1, 3) or self.spread_cost < 0:
            raise ValueError("Unsupported Queue value projection")

    def signature(self):
        return identity({"adapter": "queue-goal-projection/v1", **asdict(self)})

    def evaluate(self, future, state, goals):
        if len(goals) != 1 or goals[0].metric != "delivered":
            raise ValueError("Queue Goal adapter requires delivered")
        remaining = max(0, goals[0].target - goals[0].progress(state))
        i = self.projection - 1
        amount = future["action"]["payload"]["amount"]
        values = [
            min(remaining, b["delivered"][i] - state.payload["delivered"])
            - state.payload["holding_cost"] * sum(b["queue"][: self.projection])
            - state.payload["submission_cost"] * amount
            for b in future["paths"]
        ]
        expected = sum(b["weight"] * v for b, v in zip(future["paths"], values, strict=True))
        spread = max(values) - min(values)
        return {
            "value": expected - self.spread_cost * spread,
            "expected": expected,
            "support_range": [min(values), max(values)],
            "spread": spread,
            "projection": self.projection,
            "forecast_horizon": 3,
            "calibrated": False,
            "rule": "expected_capped_progress_holding_submission/v1",
        }
