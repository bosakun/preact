"""Opt-in learned metric forecast, never mandatory safety evidence or observation."""

from preact.core.evidence import task_definitions
from preact.core.interfaces import EngineFailure
from preact.core.models import Capabilities, EvidenceKind, Prediction, PredictionRequest, Task
from preact.learning.dynamics import DynamicsAdapter, DynamicsModel


class LearnedDynamicsEngine:
    def __init__(self, task: Task, adapter: DynamicsAdapter, model: DynamicsModel):
        model.compatible(adapter)
        if task.domain != model.domain:
            raise ValueError("Learned dynamics task domain mismatch")
        self.adapter, self.model = adapter, model
        self.capabilities = Capabilities(
            engine_id="learned-dynamics",
            version=model.version,
            family="receipt-trained-dynamics",
            domains=[task.domain],
            evidence=EvidenceKind.INFERENCE,
            tier=0,
            roles=["predictor"],
            supported_claims=task_definitions(task)[:2],
            verification_checks=[],
            max_horizon=1,
            applicability="Conditional means of observed deltas; historical empirical ranges only",
            claim_contract_version="1",
        )

    async def predict(self, request: PredictionRequest) -> Prediction:
        if request.horizon != 1 or len(request.actions) != 1 or request.conditioning is not None:
            raise EngineFailure("Learned dynamics supports only one action and one step")
        common = dict(
            engine_id=self.capabilities.engine_id,
            engine_version=self.capabilities.version,
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[request.actions[0].id],
            evidence=EvidenceKind.INFERENCE,
            assumptions=[
                "Historical conditional mean; feature equivalence and stationarity are assumptions",
                "Empirical ranges are not calibrated bounds or mandatory safety evidence",
            ],
        )
        try:
            if self.model.version != self.capabilities.version:
                raise ValueError("Registered model version changed")
            self.model.compatible(self.adapter)
            cell = self.model.lookup(self.adapter.features(request.state, request.actions[0]))
            if cell is None:
                return Prediction(**common, raw={"status": "unknown", "reason": "unsupported_cell"})
            metrics, intervals = self.adapter.decode(request.state, request.actions[0], cell)
            return Prediction(
                **common,
                metrics=metrics,
                metric_intervals=intervals,
                raw={
                    "status": "estimated",
                    "model_version": self.model.version,
                    "dataset_hash": self.model.dataset_hash,
                    "cell_samples": cell.count,
                    "target_variance": cell.variance,
                },
            )
        except (ValueError, KeyError, TypeError, ArithmeticError):
            return Prediction(**common, raw={"status": "unknown", "reason": "inference_failed"})
