"""Inference-only explicit sequence envelope over existing LocalHeuristic computation."""

from preact.core.evidence import bind_claims, supports
from preact.core.models import (
    Capabilities,
    ClaimDefinition,
    Continuation,
    EvidenceKind,
    PredictionRequest,
)
from preact.core.registry import Registry
from preact.engines.deliberation_usage import usage_record


class SoftwareSequenceHeuristic:
    definition = ClaimDefinition(
        namespace="software-deliberation",
        name="hypothetical-terminal-state",
        version="v1",
        kind="success",
        scope="action_sequence",
    )

    def __init__(self, world, predictor, usage):
        self.world, self.predictor, self.usage = world, predictor, usage
        self.capabilities = Capabilities(
            engine_id="software-sequence-heuristic",
            version="1:" + predictor.capabilities.version,
            family=predictor.capabilities.family,
            domains=["software"],
            evidence=EvidenceKind.INFERENCE,
            tier=0,
            roles=["predictor"],
            max_horizon=3,
            produces_successor=True,
            supported_claims=[self.definition],
            continuations=[Continuation.ACTION_SEQUENCE],
            claim_contract_version="1",
            applicability="Accepted ordered template states; no executable safety evidence",
        )

    async def predict(self, request):
        if request.conditioning is None or len(request.actions) != request.horizon:
            raise ValueError("Explicit ordered intervention sequence required")
        state = request.conditioning.transitions[-1].successor
        action = request.actions[-1]
        inner = PredictionRequest(
            state=state,
            actions=[action],
            seed=request.seed,
            deadline_seconds=request.deadline_seconds,
            claims=[
                c
                for c in bind_claims(self.world.task, state.id, [action])
                if supports(self.predictor.capabilities, c)
            ],
        )
        result, _ = await Registry([self.predictor]).predict(self.predictor, inner)
        if result.evidence != EvidenceKind.INFERENCE or result.successor is None or result.outcomes:
            raise ValueError("Sequence adapter supports deterministic inference successors only")
        self.usage.append(usage_record(result, "sequence_terminal"))
        return result.model_copy(
            deep=True,
            update={
                "id": __import__("uuid").uuid4().hex,
                "engine_id": self.capabilities.engine_id,
                "engine_version": self.capabilities.version,
                "state_id": request.state.id,
                "action_ids": [a.id for a in request.actions],
                "horizon": request.horizon,
                "conditioning": request.conditioning,
                "requested_claims": None,
                "raw": {
                    "terminal_request": inner.model_dump(),
                    "terminal_prediction": result.model_dump(),
                },
            },
        )
