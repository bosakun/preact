import pytest

from preact.core.consequences import reuse_key
from preact.core.decision import evaluate, gate
from preact.core.evidence import bind_claims
from preact.core.interfaces import EngineFailure
from preact.core.models import Decision, Policy, PredictionRequest
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from tests.fixtures.consequences import QueueEngine, QueueWorld


async def test_actual_runtime_rejects_delayed_overflow_and_executes_one_safe_action(tmp_path):
    world = QueueWorld()
    immediate, future = QueueEngine(world), QueueEngine(world, future=True)
    store = Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([immediate, future]))
    result = await runtime.run(world)
    assert result["success"] and result["steps"] == 1
    assert len(world.executed) == 1 and world.executed[0].payload["inflow"] == 1
    fast = next(n for n in runtime.nodes if n.action and n.action.payload["inflow"] == 3)
    assert fast.evaluation.risk_upper == 0
    findings = [a for a in fast.evaluation.claim_assessments if a.claim.horizon == 3]
    assert findings[0].check is False
    assert len(fast.predictions[-1].future_states) == 3
    assert all(len(r.actions) == 1 for r in future.requests)
    assert not store.error_rows() or all(
        row["prediction_id"]
        in {p.id for n in runtime.nodes if n.actual for p in n.predictions if p.horizon == 1}
        for row in store.error_rows()
    )
    assert all(
        n.actual is None
        for n in runtime.nodes
        if n is not next(n for n in runtime.nodes if n.actual)
    )
    assert any(e["kind"] == "consequence_explored" for e in store.read_events(runtime.run_id))


async def test_missing_required_future_evidence_verifies_or_abstains(tmp_path):
    world = QueueWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    p = await QueueEngine(world).predict(PredictionRequest(state=state, actions=[action]))
    e = evaluate([p], world.task, {}, state=state, action=action)
    assert gate(e, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    assert gate(e, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN
    runtime = Runtime(
        Store("sqlite:///:memory:"), Artifacts(str(tmp_path)), Registry([QueueEngine(world)])
    )
    result = await runtime.run(world)
    assert result["status"] == "abstained" and not world.executed


async def test_environment_trace_requires_correct_lineage_and_domain_dynamics():
    world = QueueWorld()
    state = await world.observe()
    action = (await world.propose(state, 1))[0]
    claim = bind_claims(world.task, state.id, [action])[-1]

    class Broken(QueueEngine):
        async def predict(self, request):
            p = await super().predict(request)
            p.future_states[-1] = p.future_states[-1].model_copy(update={"parent_id": state.id})
            return p

    engine = Broken(world, future=True)
    with pytest.raises(EngineFailure, match="lineage"):
        await Registry([engine]).predict(
            engine, PredictionRequest(state=state, actions=[action], horizon=3, claims=[claim])
        )
    engine = QueueEngine(world, future=True)
    with pytest.raises(EngineFailure, match="dynamics"):
        await Registry([engine]).predict(
            engine,
            PredictionRequest(
                state=state,
                actions=[action],
                horizon=3,
                claims=[claim.model_copy(update={"conditions": {}})],
            ),
        )


async def test_unproven_reuse_is_disabled_for_context_or_free_form_assumptions(tmp_path):
    world = QueueWorld()
    runtime = Runtime(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([QueueEngine(world), QueueEngine(world, True)]),
    )
    await runtime.run(world)
    node = next(n for n in runtime.nodes if n.actual)
    state = node.predictions[0].successor
    key = reuse_key(state, node, world.task, runtime.policy, [], 2)
    assert key is not None
    assert key != reuse_key(
        state, node, world.task.model_copy(update={"stakes": 1}), runtime.policy, [], 2
    )
    assert key != reuse_key(state, node, world.task, runtime.policy, [], 1)
    node.predictions[0].assumptions = ["unknown operating condition"]
    assert reuse_key(state, node, world.task, runtime.policy, [], 2) is None


async def test_additional_horizon_one_definition_cannot_disappear_into_legacy_summary():
    from preact.core.models import ClaimDefinition, ClaimRequirement

    world = QueueWorld()
    world.task.future_requirements = [
        ClaimRequirement(
            definition=ClaimDefinition(
                namespace="queue", name="capacity", version="v2", kind="check"
            )
        )
    ]
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    p = await QueueEngine(world).predict(PredictionRequest(state=state, actions=[action]))
    e = evaluate([p], world.task, {}, state=state, action=action)
    assert e.checks["capacity"] is True and e.risk_upper == 0
    assert any(a.required and not a.resolved for a in e.claim_assessments)
    assert gate(e, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN


async def test_immediate_calibration_drift_is_not_transferred_to_long_horizon():
    world = QueueWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    immediate, future = QueueEngine(world), QueueEngine(world, True)
    p = await immediate.predict(PredictionRequest(state=state, actions=[action]))
    claims = bind_claims(world.task, state.id, [action])
    q = await future.predict(
        PredictionRequest(state=state, actions=[action], claims=[claims[-1]], horizon=3)
    )
    e = evaluate(
        [p, q], world.task, {"queue-consequences@1": {"drift": True}}, state=state, action=action
    )
    assert next(a for a in e.claim_assessments if a.claim.horizon == 3).uncertainty == 0


async def test_distinct_predictor_simulator_and_narrow_verifier_cooperate(tmp_path):
    from preact.core.models import EngineRole, Estimate, EvidenceKind

    class QueuePredictor(QueueEngine):
        def __init__(self, world):
            super().__init__(world)
            self.capabilities.engine_id = "queue-predictor"
            self.capabilities.roles = [EngineRole.PREDICTOR]
            self.capabilities.evidence = EvidenceKind.INFERENCE
            self.capabilities.tier = 0
            self.capabilities.supported_claims = self.capabilities.supported_claims[:2]
            self.capabilities.verification_checks = []

        async def predict(self, request):
            prediction = await super().predict(request)
            prediction.evidence = EvidenceKind.INFERENCE
            prediction.success = Estimate(value=0.9, lower=0, upper=1, uncertainty=0.8)
            prediction.risk = Estimate(value=0.03, lower=0, upper=1, uncertainty=0.8)
            prediction.mandatory_checks = {}
            prediction.assumptions = ["Unmeasured development hypothesis"]
            return prediction

    world = QueueWorld()
    predictor, simulator, verifier = (
        QueuePredictor(world),
        QueueEngine(world),
        QueueEngine(world, True),
    )
    simulator.capabilities.roles = [EngineRole.SIMULATOR]
    verifier.capabilities.roles = [EngineRole.VERIFIER]
    runtime = Runtime(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([predictor, simulator, verifier]),
    )
    assert (await runtime.run(world))["success"]
    assert predictor.requests and simulator.requests and verifier.requests
    selected = next(n for n in runtime.nodes if n.actual)
    assert len({p.engine_id for p in selected.predictions}) == 3
    assert all(
        not f.qualified
        for a in selected.evaluation.claim_assessments
        for f in a.findings
        if f.engine_id == "queue-predictor"
    )


async def test_unmeasured_future_opinion_is_not_routed_as_an_appropriate_verifier(tmp_path):
    from preact.core.models import EngineRole, EvidenceKind

    world = QueueWorld()
    opinion = QueueEngine(world, True)
    opinion.capabilities.roles = [EngineRole.PREDICTOR]
    opinion.capabilities.evidence = EvidenceKind.INFERENCE
    runtime = Runtime(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([QueueEngine(world), opinion]),
    )
    result = await runtime.run(world)
    assert result["status"] == "abstained" and result["steps"] == 0
    assert opinion.requests == []
    assert any(
        "Unmeasured predictors" in reason
        for event in runtime.store.read_events(runtime.run_id)
        if event["kind"] == "verification_allocation"
        for plan in event["data"]["plans"]
        for reason in plan["reasons"]
    )
