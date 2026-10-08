"""Versioned sensing interventions, public-bound proof and unmeasured forecasts."""

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
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import NO_OVERFLOW, action_amount, bounded_safe
from preact.engines.cognitive_queue import QueueForecast, exact


class InformationBoundVerifier:
    """A probe admits zero jobs; minimum service bounds apply to the whole horizon."""

    def __init__(self, task: Task, *, future: bool = False):
        self.future = future
        self.capabilities = Capabilities(
            engine_id=f"information-bound-{'future' if future else 'immediate'}",
            version="2",
            family="queue-public-dynamics-v2",
            domains=[task.domain],
            evidence=EvidenceKind.EXECUTABLE,
            tier=2 if future else 1,
            max_horizon=3 if future else 1,
            roles=["verifier"],
            supported_claims=[NO_OVERFLOW] if future else task_definitions(task),
            verification_checks=[] if future else task.required_checks,
            applicability="Public binary service>=1; probe and drain admit zero jobs",
            estimated_cost_usd=0,
            estimated_latency_seconds=0.001,
            claim_contract_version="1",
        )

    async def predict(self, request: PredictionRequest) -> Prediction:
        amount = action_amount(request.state, request.actions[0])
        safe = bounded_safe(request.state.payload, amount, request.horizon)
        common = dict(
            engine_id=self.capabilities.engine_id,
            engine_version="2",
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[request.actions[0].id],
            horizon=request.horizon,
            evidence=EvidenceKind.EXECUTABLE,
            assumptions=["Public service>=1, no later admissions; probe is one-tick drain"],
        )
        if self.future:
            return Prediction(
                **common, claim_results=[ClaimResult(claim=c, check=safe) for c in request.claims]
            )
        return Prediction(
            **common,
            success=exact(True),
            risk=exact(not safe),
            mandatory_checks={"capacity": safe},
        )


class InformationForecast(QueueForecast):
    """Only predicts public work completion; never a pre-execution measurement."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.capabilities = self.capabilities.model_copy(
            update={
                "engine_id": "information-forecast",
                "version": "2",
                "applicability": "Binary Bayesian model, ordinary censored data and executed probes",
            }
        )

    async def predict(self, request: PredictionRequest) -> Prediction:
        amount = action_amount(request.state, request.actions[0])
        if self.belief_estimator is None:
            records = await self.memory.retrieve(request.state.domain) if self.use_memory else []
            belief = self.planner.infer(request.state, records)
        else:
            belief = await self.belief_estimator.infer(
                request.state, self.planner, self.memory, task=self.task, use_memory=self.use_memory
            )
        service = belief.inferred["service"]
        p = belief.inferred["service_high_probability"].value
        _, available, low = transition(request.state.payload, amount, 1)
        _, _, high = transition(request.state.payload, amount, 3)
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version="2",
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[request.actions[0].id],
            evidence=EvidenceKind.INFERENCE,
            success=Estimate(value=0.95, lower=0, upper=1),
            risk=Estimate(value=0.01, lower=0, upper=1),
            metrics={"processed": (1 - p) * low + p * high},
            metric_intervals={"processed": (low, high)},
            raw={
                "service_estimate": service.value,
                "memory_refs": service.source_refs,
                "all_processed_probability": 1 if available <= 1 else p if available <= 3 else 0,
            },
            assumptions=["Markov persistence is a model assumption, not a safety certificate"],
        )
