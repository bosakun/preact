"""Queue model opinions and bounded verification use public inputs only."""

from preact.cognition.belief import BeliefEstimator
from preact.core.evidence import task_definitions
from preact.core.models import (
    Capabilities,
    ClaimResult,
    Estimate,
    EvidenceKind,
    Prediction,
    PredictionRequest,
    Task,
)
from preact.domains.cognitive_queue import NO_OVERFLOW, transition


def exact(value: bool) -> Estimate:
    return Estimate(
        value=float(value), lower=float(value), upper=float(value), measured=True, uncertainty=0
    )


class QueueBoundVerifier:
    """Monotone worst-case occupancy under service >=1, no later submissions.

    nominal=True is an explicitly misspecified experimental single-model baseline.
    It assumes service=3 and may therefore issue false safety evidence.
    """

    def __init__(
        self, task: Task, *, future: bool = False, nominal: bool = False, single: bool = False
    ):
        self.future, self.nominal = future, nominal
        self.capabilities = Capabilities(
            engine_id=f"queue-{'nominal' if nominal else 'bound'}-{'future' if future else 'immediate'}",
            version="1",
            family="queue-public-dynamics",
            domains=[task.domain],
            evidence=EvidenceKind.EXECUTABLE,
            tier=2 if future else 1,
            max_horizon=3 if future or single else 1,
            roles=["verifier"],
            supported_claims=[NO_OVERFLOW]
            if future
            else task_definitions(task) + ([NO_OVERFLOW] if single else []),
            verification_checks=[] if future else task.required_checks,
            applicability="Monotone public queue bounds; nominal variant assumes maximum service",
            estimated_cost_usd=0,
            estimated_latency_seconds=0.001,
            claim_contract_version="1",
        )

    async def predict(self, request: PredictionRequest) -> Prediction:
        payload = request.state.payload
        minimum = payload["service_bounds"][1 if self.nominal else 0]
        safe = True
        for tick in range(request.horizon):
            payload, _, _ = transition(
                payload, request.actions[0].payload["amount"] if tick == 0 else 0, minimum
            )
            safe &= payload["queue"] <= payload["capacity"]
        common = dict(
            engine_id=self.capabilities.engine_id,
            engine_version="1",
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[request.actions[0].id],
            horizon=request.horizon,
            evidence=self.capabilities.evidence,
            assumptions=[
                "Service=3 (misspecified baseline)"
                if self.nominal
                else "Service remains within public [1,3] bounds"
            ],
        )
        if self.future or request.horizon > 1:
            return Prediction(
                **common,
                claim_results=[ClaimResult(claim=c, check=safe) for c in request.claims],
            )
        return Prediction(
            **common,
            success=exact(True),
            risk=exact(not safe),
            mandatory_checks={"capacity": safe},
        )


class QueueForecast:
    """An unmeasured service hypothesis; cannot resolve mandatory evidence."""

    def __init__(
        self,
        task: Task,
        planner,
        memory,
        *,
        use_memory: bool = True,
        engine_id="queue-forecast",
        belief_estimator: BeliefEstimator | None = None,
    ):
        self.planner, self.memory, self.use_memory = planner, memory, use_memory
        self.belief_estimator = belief_estimator
        self.task = task.model_copy(deep=True)
        self.capabilities = Capabilities(
            engine_id=engine_id,
            version="1",
            family="queue-service-estimator",
            domains=[task.domain],
            evidence=EvidenceKind.INFERENCE,
            tier=0,
            roles=["predictor"],
            supported_claims=task_definitions(task)[:2],
            verification_checks=[],
            applicability="EMA of uncensored observed completions; uncertain dynamics hypothesis",
            claim_contract_version="1",
        )

    async def predict(self, request: PredictionRequest) -> Prediction:
        if self.belief_estimator is None:
            records = await self.memory.retrieve(request.state.domain) if self.use_memory else []
            belief = self.planner.infer(request.state, records)
        else:
            belief = await self.belief_estimator.infer(
                request.state,
                self.planner,
                self.memory,
                task=self.task,
                use_memory=self.use_memory,
            )
        service = belief.inferred["service"]
        _, available, processed = transition(
            request.state.payload, request.actions[0].payload["amount"], service.value
        )
        # Prediction of whether all currently available work will complete. This
        # metric is separately scored as an uncalibrated model forecast.
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version="1",
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[request.actions[0].id],
            evidence=EvidenceKind.INFERENCE,
            success=Estimate(value=0.95, lower=0, upper=1),
            risk=Estimate(value=0.01, lower=0, upper=1),
            metrics={"processed": processed},
            metric_intervals={"processed": (min(available, 1), min(available, 3))},
            raw={
                "service_estimate": service.value,
                "memory_refs": service.source_refs,
                "all_processed_probability": 1
                if available <= 1
                else (service.value - 1) / 2
                if available <= 3
                else 0,
            },
            assumptions=["Service estimate is a hypothesis, not a safety certificate"],
        )
