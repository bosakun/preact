from preact.core.decision import evaluate, gate
from preact.core.evidence import bind_claims, task_definitions
from preact.core.models import (
    Capabilities,
    ClaimResult,
    Decision,
    EvidenceKind,
    Policy,
    Prediction,
    PredictionRequest,
)
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


class NarrowVerifier:
    def __init__(self, world, name):
        self.world, self.name, self.requests = world, name, []
        self.capabilities = Capabilities(
            engine_id="check-" + name,
            version="1",
            family="protected-tests",
            domains=[world.task.domain],
            evidence=EvidenceKind.EXECUTABLE,
            tier=2,
            roles=["verifier"],
            claim_contract_version="1",
            applicability="One real protected check",
            supported_claims=[d for d in task_definitions(world.task) if d.name == name],
            verification_checks=[name],
        )

    async def predict(self, request):
        self.requests.append(request)
        result = await LocalVerifier(self.world).predict(request)
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version="1",
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[request.actions[0].id],
            evidence=EvidenceKind.EXECUTABLE,
            claim_results=[
                ClaimResult(claim=c, check=result.mandatory_checks[self.name])
                for c in request.claims
            ],
        )


class MetricsVerifier(LocalVerifier):
    def __init__(self, world):
        super().__init__(world)
        self.capabilities.supported_claims = task_definitions(world.task)[:2]
        self.capabilities.verification_checks = []
        self.capabilities.engine_id = "metrics-only"

    async def predict(self, request):
        result = await super().predict(request)
        result.engine_id = self.capabilities.engine_id
        result.mandatory_checks = {}
        return result


async def test_narrow_verifier_does_not_resolve_unchecked_claims():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    engine = NarrowVerifier(world, "syntax")
    claims = [
        c for c in bind_claims(world.task, state.id, [action]) if c.definition.name == "syntax"
    ]
    p, _ = await Registry([engine]).predict(
        engine, PredictionRequest(state=state, actions=[action], claims=claims)
    )
    evaluation = evaluate([p], world.task, {}, state=state, action=action)
    assert evaluation.checks == {"syntax": True, "regressions": None}
    assert evaluation.success_lower == 0 and evaluation.risk_upper == 1
    assert (
        gate(evaluation, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    )
    assert (
        gate(evaluation, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN
    )


async def test_heterogeneous_claim_specific_routing_uses_real_probes(tmp_path):
    world = SoftwareWorld()
    verifiers = [NarrowVerifier(world, name) for name in world.task.required_checks]
    runtime = Runtime(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([LocalHeuristic(world), MetricsVerifier(world), *verifiers]),
    )
    result = await runtime.run(world)
    assert result["success"] and not result["unsafe"]
    assert all(v.requests for v in verifiers)
    assert all(
        all(c.definition.name == v.name for c in r.claims) for v in verifiers for r in v.requests
    )
    events = runtime.store.read_events(runtime.run_id)
    assert any(e["kind"] == "verification_allocation" for e in events)


async def test_duplicate_source_and_family_never_add_independent_votes():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    engine = LocalVerifier(world)
    registry = Registry([engine])
    request = PredictionRequest(state=state, actions=[action])
    p, cached = await registry.predict(engine, request)
    repeat, cached_again = await registry.predict(engine, request)
    assert not cached and cached_again and p.id == repeat.id
    a = evaluate([p], world.task, {}, state=state, action=action)
    b = evaluate([p, repeat], world.task, {}, state=state, action=action)
    assert a == b
    assert all(len(c.findings) == 1 for c in b.claim_assessments)


async def test_conditioned_legacy_probabilities_and_unrequested_checks_do_not_resolve_root(
    tmp_path,
):
    from preact.core.comparison import compare
    from preact.core.models import ClaimDefinition, ClaimRequirement
    from preact.engines.local import certain

    world = SoftwareWorld()
    definition = ClaimDefinition(
        namespace="software", name="conditioned_test", version="v1", kind="check"
    )
    world.task.future_requirements = [
        ClaimRequirement(definition=definition, conditions={"assumed_configuration": "different"})
    ]
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    claim = bind_claims(world.task, state.id, [action])[-1]

    class ConditionedVerifier:
        capabilities = Capabilities(
            engine_id="conditioned",
            version="1",
            family="execution",
            domains=["software"],
            evidence=EvidenceKind.EXECUTABLE,
            tier=2,
            roles=["verifier"],
            supported_claims=[definition],
            applicability="Conditional check only",
            claim_contract_version="1",
        )

        async def predict(self, request):
            return Prediction(
                engine_id="conditioned",
                engine_version="1",
                family="execution",
                state_id=request.state.id,
                action_ids=[request.actions[0].id],
                evidence=EvidenceKind.EXECUTABLE,
                success=certain(True),
                risk=certain(False),
                mandatory_checks={"syntax": True, "regressions": True},
                claim_results=[ClaimResult(claim=c, check=True) for c in request.claims],
            )

    engine = ConditionedVerifier()
    prediction, _ = await Registry([engine]).predict(
        engine, PredictionRequest(state=state, actions=[action], claims=[claim])
    )
    e = evaluate([prediction], world.task, {}, state=state, action=action)
    assert e.success_lower == 0 and e.risk_upper == 1
    assert e.checks == {"syntax": None, "regressions": None}
    assert next(a for a in e.claim_assessments if a.claim == claim).resolved
    assert gate(e, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN
    assert compare(prediction, await world.execute(action, "actual-one-action")) is None
    other_world = SoftwareWorld()
    other_world.task.future_requirements = world.task.future_requirements
    runtime = Runtime(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry(
            [
                MetricsVerifier(other_world),
                ConditionedVerifier(),
                *[NarrowVerifier(other_world, name) for name in other_world.task.required_checks],
            ]
        ),
    )
    result = await runtime.run(other_world)
    assert result["success"] and not result["unsafe"]
    events = runtime.store.read_events(runtime.run_id)
    assert any(
        e["kind"] == "prediction" and e["data"]["prediction"]["engine_id"] == "conditioned"
        for e in events
    )
    assert not any(
        e["kind"] in {"comparison", "prediction_error"} and e["data"]["engine_id"] == "conditioned"
        for e in events
    )
