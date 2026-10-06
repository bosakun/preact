"""Statistical/refinement conformance; these tests do not represent live Isaac execution."""

import pytest

from preact.core.comparison import compare
from preact.core.decision import evaluate, gate
from preact.core.interfaces import EngineFailure
from preact.core.models import Decision, Policy, PredictionRequest
from preact.core.registry import Registry
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.engines.statistics import binomial_estimate


def test_exact_binomial_risk_requires_enough_actual_samples():
    assert binomial_estimate(0, 1).upper == pytest.approx(0.95)
    assert binomial_estimate(0, 58).upper > 0.05
    assert binomial_estimate(0, 59).upper < 0.05
    assert binomial_estimate(59, 59).lower > 0.95
    assert binomial_estimate(1, 59).upper > 0.05
    with pytest.raises(ValueError):
        binomial_estimate(1, 0)


async def test_stronger_measurements_refine_uncertainty_but_never_erase_a_violation():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    nominal = await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[action]))
    nominal.success, nominal.risk = binomial_estimate(1, 1), binomial_estimate(0, 1)
    stronger = nominal.model_copy(deep=True)
    stronger.id, stronger.engine_id = "strong-prediction", "strong-verifier"
    stronger.refines_engine_ids = [nominal.engine_id]
    stronger.success, stronger.risk = binomial_estimate(59, 59), binomial_estimate(0, 59)
    assessment = evaluate([nominal], world.task, {})
    assert (
        gate(assessment, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    )
    assessment = evaluate([nominal, stronger], world.task, {})
    assert (
        gate(assessment, state.id, action.fingerprint, Policy(), False).decision == Decision.EXECUTE
    )
    nominal.violations = ["Observed invariant failure"]
    nominal.mandatory_checks["regressions"] = False
    assessment = evaluate([nominal, stronger], world.task, {})
    assert (
        gate(assessment, state.id, action.fingerprint, Policy(), True).decision == Decision.ABSTAIN
    )


async def test_evidence_label_without_measured_claims_cannot_authorize_execution():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    p = await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[action]))
    p.risk.measured = False
    assessment = evaluate([p], world.task, {})
    assert assessment.risk_upper == 1 and assessment.success_lower == 0


async def test_different_success_claims_are_not_authority_or_calibration_labels():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    p = await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[action]))
    p.success_metric = "image_target_region/v1"
    assessment = evaluate([p], world.task, {})
    assert assessment.success_lower == 0 and assessment.risk_upper == 1
    observed = await world.execute(action, "test-receipt")
    comparison = compare(p, observed)
    assert "success" not in comparison
    assert "success:metric_mismatch" in comparison["unknown_claims"]


async def test_binary_receipt_cannot_label_probability_interval_coverage():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    prediction = await LocalVerifier(world).predict(
        PredictionRequest(state=state, actions=[action])
    )
    observed = await world.execute(action, "coverage-receipt")
    result = compare(prediction, observed)
    assert result["success"]["brier"] == 0
    assert result["success"]["probability_interval"] == [1, 1]
    assert result["success"]["interval_covered"] is None


async def test_registry_rejects_undeclared_refinement_and_excess_samples():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    request = PredictionRequest(state=state, actions=[action])

    class Forged(LocalVerifier):
        async def predict(self, request):
            result = await super().predict(request)
            result.refines_engine_ids = ["other-engine"]
            return result

    with pytest.raises(EngineFailure, match="undeclared"):
        await Registry([]).predict(Forged(world), request)

    class Excess(LocalVerifier):
        async def predict(self, request):
            result = await super().predict(request)
            result.sample_count = 2
            return result

    with pytest.raises(EngineFailure, match="sample budget"):
        await Registry([]).predict(Excess(world), request)


async def test_stakes_escalate_without_weakening_hard_policy():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    p = await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[action]))
    p.success, p.risk = binomial_estimate(59, 59), binomial_estimate(0, 59)
    normal = evaluate([p], world.task, {})
    high = evaluate([p], world.task.model_copy(update={"stakes": 1}), {})
    assert gate(normal, state.id, action.fingerprint, Policy(), True).decision == Decision.EXECUTE
    assert gate(high, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    p.success, p.risk = binomial_estimate(119, 119), binomial_estimate(0, 119)
    high = evaluate([p], world.task.model_copy(update={"stakes": 1}), {})
    assert gate(high, state.id, action.fingerprint, Policy(), True).decision == Decision.EXECUTE
    p.mandatory_checks["regressions"] = False
    high = evaluate([p], world.task.model_copy(update={"stakes": 1}), {})
    assert (
        gate(high, state.id, action.fingerprint, Policy(max_risk=1), True).decision
        == Decision.ABSTAIN
    )
