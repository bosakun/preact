"""Bounded learned-service forecasts; known queue rules, no execution authority."""

import itertools
import math
from statistics import NormalDist

from preact.core.evidence import bind_claims, task_definitions
from preact.core.models import (
    Action,
    Capabilities,
    Continuation,
    EvidenceKind,
    Prediction,
    PredictionRequest,
    State,
    Task,
    identity,
)
from preact.core.registry import Registry
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import DYNAMICS_V2, NO_OVERFLOW, action_amount
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.learning.dynamics import DynamicsModel


def service_interval(p: float, count: int) -> tuple[float, float]:
    """Wilson 95% interval UNDER iid sampling; not a calibrated safety bound."""
    z = NormalDist().inv_cdf(0.975)
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return max(0, center - radius), min(1, center + radius)


def paths(payload: dict, amount: int, horizon: int, p: float) -> list[dict]:
    """Enumerate shared exogenous service paths, preserving integer branch states."""
    result = []
    for services in itertools.product((1, 3), repeat=horizon):
        weight = math.prod(p if service == 3 else 1 - p for service in services)
        current = payload
        queues, delivered, pending = [], [], []
        for tick, service in enumerate(services):
            current, _, _ = transition(current, amount if tick == 0 else 0, service)
            queues.append(current["queue"])
            delivered.append(current["delivered"])
            pending.append(sum(j["amount"] for j in current["pending"]))
        result.append(
            {
                "service": list(services),
                "weight": weight,
                "queue": queues,
                "delivered": delivered,
                "pending_work": pending,
            }
        )
    return result


class QueueTemporalEngine:
    """Opt-in analysis engine. Existing safety claims remain unresolved inference."""

    def __init__(
        self, task: Task, model: DynamicsModel | None = None, *, prior: float | None = None
    ):
        if (model is None) == (prior is None):
            raise ValueError("Supply exactly one learned model or explicit fixed prior")
        if prior is not None and (not math.isfinite(prior) or not 0 <= prior <= 1):
            raise ValueError("Invalid fixed service prior")
        self.adapter, self.model, self.prior = QueueServiceAdapter(), model, prior
        self.task = task.model_copy(deep=True)
        if model is not None:
            model.compatible(self.adapter)
            if task.domain != model.domain:
                raise ValueError("Task/model domain mismatch")
        elif task.domain != self.adapter.specification["domain"]:
            raise ValueError("Unsupported fixed-prior domain")
        self.capabilities = Capabilities(
            engine_id="queue-temporal-learned" if model is not None else "queue-temporal-prior",
            version=model.version
            if model is not None
            else identity({"prior": prior, "version": "1"}),
            family="queue-service-temporal",
            tier=0,
            domains=[task.domain],
            evidence=EvidenceKind.INFERENCE,
            roles=["predictor"],
            supported_claims=[*task_definitions(task)[:2], NO_OVERFLOW],
            max_horizon=3,
            max_samples=8,
            claim_contract_version="1",
            applicability="Known delayed arrivals; historical binary service; iid stationary ticks",
        )

    async def predict(self, request: PredictionRequest) -> Prediction:
        common = dict(
            engine_id=self.capabilities.engine_id,
            engine_version=self.capabilities.version,
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[a.id for a in request.actions],
            horizon=request.horizon,
            evidence=EvidenceKind.INFERENCE,
            assumptions=[
                "Known arrival_delay=2 and queue conservation; these rules were not learned",
                "Historical service marginal; independent stationary future ticks assumed",
                "Shared service is exogenous to action; hidden regime changes may invalidate forecasts",
                "Wilson interval assumes iid data; predictive ranges are not calibrated safety evidence",
            ],
        )
        try:
            if (
                len(request.actions) != 1
                or request.conditioning is not None
                or not 1 <= request.horizon <= 3
                or request.sample_budget < 2**request.horizon
                or request.state.kind != "observed"
                or request.actions[0].kind != "submit"
                or request.state.payload["tick"] + request.horizon
                > request.state.payload["episode_ticks"]
                or (request.horizon > 1 and not request.claims)
                or any(
                    c.definition != NO_OVERFLOW
                    or c.continuation != Continuation.ENVIRONMENT_ONLY
                    or c.conditions != {"dynamics": DYNAMICS_V2}
                    or c.task_context != identity(self.task.model_dump())
                    for c in request.claims
                    if request.horizon > 1
                )
            ):
                raise ValueError("Unsupported temporal request")
            action = request.actions[0]
            features = self.adapter.features(request.state, action)
            statistics = {}
            if self.model is not None:
                if self.model.version != self.capabilities.version:
                    raise ValueError("Model changed after registration")
                self.model.compatible(self.adapter)
                cell = self.model.lookup(features)
                if cell is None:
                    return Prediction(
                        **common, raw={"status": "unknown", "reason": "insufficient_training"}
                    )
                statistics, _ = self.adapter.decode(request.state, action, cell)
                if statistics["informative_count"] < self.model.min_samples:
                    return Prediction(
                        **common,
                        raw={
                            "status": "unknown",
                            "reason": "insufficient_informative",
                            **statistics,
                        },
                    )
                p = statistics["p_high"]
                p_interval = service_interval(p, int(statistics["informative_count"]))
            else:
                p, p_interval = self.prior, (self.prior, self.prior)
            branches = paths(
                request.state.payload, action_amount(request.state, action), request.horizon, p
            )
            vectors = {
                key: [
                    sum(b["weight"] * b[key][tick] for b in branches)
                    for tick in range(request.horizon)
                ]
                for key in ("queue", "delivered", "pending_work")
            }
            intervals = {
                key: (min(b[key][-1] for b in branches), max(b[key][-1] for b in branches))
                for key in ("queue", "delivered")
            }
            sensitivity = {}
            for key in ("queue", "delivered"):
                endpoints = [
                    [
                        sum(
                            math.prod(bound if s == 3 else 1 - bound for s in b["service"])
                            * b[key][tick]
                            for b in branches
                        )
                        for tick in range(request.horizon)
                    ]
                    for bound in p_interval
                ]
                sensitivity[key] = {
                    "lower": [min(x, y) for x, y in zip(*endpoints)],
                    "upper": [max(x, y) for x, y in zip(*endpoints)],
                }
            # Support ranges include even unobserved service outcomes at p=0 or 1.
            return Prediction(
                **common,
                vectors=vectors,
                metrics={
                    "queue": vectors["queue"][-1],
                    "delivered": vectors["delivered"][-1],
                    "holding_work": sum(vectors["queue"]),
                },
                metric_intervals=intervals,
                sample_count=len(branches),
                raw={
                    "status": "estimated",
                    "p_high": p,
                    "p_high_interval": p_interval,
                    "mean_parameter_sensitivity": sensitivity,
                    "statistics": statistics,
                    "model_version": self.capabilities.version,
                    "dataset_hash": self.model.dataset_hash if self.model else None,
                    "branches": branches,
                    "interval_kind": "all binary service paths, not probability coverage",
                },
            )
        except (ValueError, KeyError, TypeError, ArithmeticError):
            return Prediction(
                **common, raw={"status": "unknown", "reason": "unsupported_or_failed"}
            )


async def compare_actions(
    registry: Registry,
    engine: QueueTemporalEngine,
    state: State,
    actions: list[Action],
    *,
    horizon: int = 3,
) -> dict:
    """Read-only comparison, never candidate ranking, authorization or execution."""
    if not actions or len(actions) > 3 or len({a.fingerprint for a in actions}) != len(actions):
        raise ValueError("Compare one to three distinct actions")
    observed = State.model_validate(state.model_dump())
    task = engine.task.model_copy(deep=True)
    version = engine.capabilities.version
    predictions = []
    for action in actions:
        claims = [
            c.model_copy(update={"horizon": horizon})
            for c in bind_claims(task, observed.id, [action])
            if c.definition == NO_OVERFLOW
        ]
        prediction, _ = await registry.predict(
            engine,
            PredictionRequest(
                state=observed,
                actions=[action],
                horizon=horizon,
                claims=claims,
                sample_budget=2**horizon,
            ),
        )
        predictions.append(prediction)
    if engine.task != task or engine.capabilities.version != version:
        raise ValueError("Comparison task or engine changed")
    if any(p.raw.get("status") != "estimated" for p in predictions):
        return {"status": "unknown", "predictions": predictions, "differences": []}
    differences = []
    for left, right in itertools.combinations(range(len(predictions)), 2):
        a, b = predictions[left], predictions[right]
        if a.engine_version != b.engine_version or a.raw["p_high"] != b.raw["p_high"]:
            raise ValueError("Comparison model changed")
        differences.append(
            {
                "left": left,
                "right": right,
                "vectors": {
                    key: [x - y for x, y in zip(a.vectors[key], b.vectors[key])]
                    for key in ("queue", "delivered")
                },
                "ranges": {
                    key: [
                        [
                            min(
                                x[key][tick] - y[key][tick]
                                for x, y in zip(a.raw["branches"], b.raw["branches"])
                            ),
                            max(
                                x[key][tick] - y[key][tick]
                                for x, y in zip(a.raw["branches"], b.raw["branches"])
                            ),
                        ]
                        for tick in range(horizon)
                    ]
                    for key in ("queue", "delivered")
                },
            }
        )
    return {"status": "estimated", "predictions": predictions, "differences": differences}
