import pytest

from preact.core.decision import evaluate, gate
from preact.core.evidence import bind_claims
from preact.core.interfaces import EngineFailure
from preact.core.models import (
    Capabilities,
    ClaimDefinition,
    ClaimInstance,
    ClaimResult,
    Continuation,
    Decision,
    EvidenceKind,
    Policy,
    Prediction,
    PredictionRequest,
    SequenceConditioning,
    SequenceTransition,
    identity,
)
from preact.core.registry import Registry
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier


class SequenceVerifier:
    definition = ClaimDefinition(
        namespace="software",
        name="sequence_preserves_checks",
        version="v1",
        kind="check",
        scope="action_sequence",
        temporal="through_horizon",
    )

    def __init__(self, world):
        self.world = world
        self.capabilities = Capabilities(
            engine_id="sequence-tests",
            version="1",
            family="execution",
            domains=["software"],
            evidence=EvidenceKind.EXECUTABLE,
            tier=2,
            roles=["verifier"],
            max_horizon=3,
            produces_successor=True,
            applicability="Explicit protected sequence probe",
            supported_claims=[self.definition],
            continuations=[Continuation.ACTION_SEQUENCE],
            claim_contract_version="1",
        )

    async def predict(self, request):
        state = request.conditioning.transitions[-1].successor
        result = await LocalVerifier(self.world).predict(
            PredictionRequest(state=state, actions=[request.actions[-1]])
        )
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version="1",
            family="execution",
            state_id=request.state.id,
            action_ids=[a.id for a in request.actions],
            horizon=request.horizon,
            conditioning=request.conditioning,
            evidence=EvidenceKind.EXECUTABLE,
            successor=result.successor,
            claim_results=[
                ClaimResult(claim=c, check=not result.violations) for c in request.claims
            ],
        )


async def sequence_setup(legacy_sources=False):
    world = SoftwareWorld()
    state = await world.observe()
    local, sequence = LocalVerifier(world), SequenceVerifier(world)
    registry = Registry([local, sequence])
    a = (await world.propose(state, 2))[1]
    p, _ = await registry.predict(
        local,
        PredictionRequest(
            state=state,
            actions=[a],
            claims=[] if legacy_sources else bind_claims(world.task, state.id, [a]),
        ),
    )
    b = (await world.propose(p.successor, 2))[0]
    q, _ = await registry.predict(
        local,
        PredictionRequest(
            state=p.successor,
            actions=[b],
            claims=[] if legacy_sources else bind_claims(world.task, p.successor.id, [b]),
        ),
    )
    c = (await world.propose(q.successor, 2))[0]
    conditioning = SequenceConditioning(
        transitions=[
            SequenceTransition(
                action_id=a.id, input_state_id=state.id, successor=p.successor, prediction_id=p.id
            ),
            SequenceTransition(
                action_id=b.id,
                input_state_id=p.successor.id,
                successor=q.successor,
                prediction_id=q.id,
            ),
        ]
    )
    claim = ClaimInstance(
        definition=sequence.definition,
        state_id=state.id,
        task_context=identity(world.task.model_dump()),
        horizon=3,
        action_ids=(a.id, b.id, c.id),
        action_fingerprints=(a.fingerprint, b.fingerprint, c.fingerprint),
        continuation=Continuation.ACTION_SEQUENCE,
    )
    request = PredictionRequest(
        state=state, actions=[a, b, c], horizon=3, conditioning=conditioning, claims=[claim]
    )
    return world, state, a, p, registry, sequence, request


async def test_optional_harmful_intervention_sequence_does_not_veto_root_action():
    world, state, a, immediate, registry, engine, request = await sequence_setup()
    prediction, _ = await registry.predict(engine, request)
    assert prediction.claim_results[0].check is False
    assert request.actions[1].state_id == request.conditioning.transitions[0].successor.id
    e = evaluate([immediate, prediction], world.task, {}, state=state, action=a)
    assert any(c.check is False and not c.required for c in e.claim_assessments)
    assert gate(e, state.id, a.fingerprint, Policy(), False).decision == Decision.EXECUTE


@pytest.mark.parametrize("fault", ["root-binding", "fake-source", "fake-successor"])
async def test_invalid_ordered_lineage_is_rejected(fault):
    world, state, a, p, registry, engine, request = await sequence_setup()
    if fault == "root-binding":
        request.actions[1].state_id = state.id
    elif fault == "fake-source":
        request.conditioning.transitions[0].prediction_id = "not-accepted"
    else:
        request.conditioning.transitions[0].successor = state
    with pytest.raises(EngineFailure):
        await registry.predict(engine, request)


async def test_legacy_sources_without_explicit_context_cannot_condition_a_sequence():
    _, _, _, _, registry, engine, request = await sequence_setup(legacy_sources=True)
    with pytest.raises(EngineFailure, match="context"):
        await registry.predict(engine, request)
