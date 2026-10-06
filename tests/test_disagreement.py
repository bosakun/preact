import pytest
from pydantic import ValidationError

from preact.core.decision import evaluate, gate
from preact.core.disagreement import compare
from preact.core.models import Decision, Estimate, FutureOutcome, Policy, PredictionRequest
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


async def evidence():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    request = PredictionRequest(state=state, actions=[action])
    prediction = await LocalVerifier(world).predict(request)
    return world, state, action, request, prediction


async def test_conflicting_measurements_escalate_but_resolved_model_opinion_does_not():
    world, state, action, request, verified = await evidence()
    conflicting = verified.model_copy(deep=True)
    conflicting.id, conflicting.engine_id, conflicting.family = "second", "second", "other"
    conflicting.success = Estimate(value=0.65, lower=0.65, upper=0.65, uncertainty=0, measured=True)
    assessment = evaluate([verified, conflicting], world.task, {})
    assert assessment.success_lower >= Policy().min_success
    assert assessment.unresolved_disagreement == pytest.approx(0.35)
    decision = gate(assessment, state.id, action.fingerprint, Policy(), True)
    assert decision.decision == Decision.VERIFY
    assert "Measured engines disagree on aligned claims" in decision.reasons
    assert (
        gate(assessment, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN
    )

    cheap = await LocalHeuristic(world).predict(request)
    cheap.success = Estimate(value=0.1)
    resolved = evaluate([cheap, verified], world.task, {})
    assert resolved.disagreement == pytest.approx(0.9)
    assert resolved.unresolved_disagreement == 0
    assert (
        gate(resolved, state.id, action.fingerprint, Policy(), False).decision == Decision.EXECUTE
    )


async def test_risk_distributions_and_task_scaled_intervals_are_compared_only_when_aligned():
    world, _, _, _, left = await evidence()
    right = left.model_copy(deep=True)
    right.id, right.engine_id = "right", "other"
    left.risk = Estimate(value=0.01)
    right.risk = Estimate(value=0.8)
    world.task.metric_scales = {"distance_m": 0.1}
    left.metrics = {"distance_m": 0.1, "unscaled": 1000}
    right.metrics = {"distance_m": 0.15, "unscaled": 0}
    left.metric_intervals = {"distance_m": (0.09, 0.11)}
    right.metric_intervals = {"distance_m": (0.14, 0.16)}
    successor = left.successor
    for prediction, probabilities in [(left, [1, 0]), (right, [0, 1])]:
        prediction.outcomes = [
            FutureOutcome(label=label, state=successor, probability=Estimate(value=value))
            for label, value in zip(["grasped", "dropped"], probabilities)
        ]
        prediction.successor = None
    result = compare([left, right], world.task)
    assert result["risk"] == pytest.approx(0.79)
    assert result["outcomes"] == pytest.approx(1)
    assert result["metric:distance_m"] == pytest.approx(0.5)
    assert result["interval_gap:distance_m"] == pytest.approx(0.3)
    assert "metric:unscaled" not in result
    right.risk_metric = "different/v1"
    assert compare([left, right], world.task) == {}


async def test_failed_mandatory_check_is_a_veto_even_without_engine_violation_text():
    world, state, action, _, verified = await evidence()
    verified.mandatory_checks[world.task.required_checks[0]] = False
    verified.violations = []
    result = gate(
        evaluate([verified], world.task, {}), state.id, action.fingerprint, Policy(), True
    )
    assert result.decision == Decision.ABSTAIN


async def test_invalid_intervals_and_untrusted_scales_are_rejected():
    world, _, _, _, prediction = await evidence()
    data = prediction.model_dump()
    data["metric_intervals"] = {"goal_progress": [2, 1]}
    with pytest.raises(ValidationError):
        type(prediction).model_validate(data)
    data = world.task.model_dump()
    data["metric_scales"] = {"distance": 0}
    with pytest.raises(ValidationError):
        type(world.task).model_validate(data)
