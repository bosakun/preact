from .calibration import Calibration
from .disagreement import compare
from .disagreement import measured as is_measured
from .evidence import assess_claims, bind_claims, immediate_metric_scope, task_definitions
from .models import (
    Continuation,
    Decision,
    Evaluation,
    GateResult,
    Policy,
    Prediction,
    Task,
    identity,
)


def _evaluate_legacy(predictions: list[Prediction], task: Task, trust: dict) -> Evaluation:
    # Refinements apply only to measured, matching claims in the same correlation
    # family and exact state/action/horizon. All contradictory checks remain vetoes.
    superseded = {
        p.id
        for p in predictions
        for stronger in predictions
        if is_measured(p)
        and is_measured(stronger)
        and p.engine_id in stronger.refines_engine_ids
        and p.family == stronger.family
        and (p.state_id, p.action_ids, p.horizon, p.success_metric, p.risk_metric)
        == (
            stronger.state_id,
            stronger.action_ids,
            stronger.horizon,
            stronger.success_metric,
            stronger.risk_metric,
        )
    }
    active = [p for p in predictions if p.id not in superseded]
    # Family-level pooling prevents repeated model samples acquiring extra votes.
    families: dict[str, list[tuple[float, float]]] = {}
    bounds, risks, uncertainties = [], [], []
    checks: dict[str, bool | None] = {key: None for key in task.required_checks}
    violations = []
    progress = []
    for prediction in predictions:
        stats = trust.get(f"{prediction.engine_id}@{prediction.engine_version}")
        aligned = (prediction.success_metric, prediction.risk_metric) == (
            task.success_metric,
            task.risk_metric,
        )
        if aligned and prediction.success.value is not None:
            value = Calibration.calibrate(prediction.success.value, stats)
            families.setdefault(prediction.family, []).append(
                (value, stats["weight"] if stats else 0.5)
            )
        if is_measured(prediction):
            if prediction.id not in superseded and aligned:
                bounds.append(
                    prediction.success.lower if prediction.success.lower is not None else 0
                )
                risks.append(prediction.risk.upper if prediction.risk.upper is not None else 1)
            progress.append(prediction.metrics.get("goal_progress", 0))
            for key in checks:
                result = prediction.mandatory_checks.get(key)
                if result is False or checks[key] is not False:
                    if result is not None:
                        checks[key] = result
            violations.extend(prediction.violations)
        uncertainties.append(prediction.success.uncertainty)
        if (
            stats
            and (stats["drift"] or stats.get("risk_drift"))
            and prediction.id not in superseded
        ):
            uncertainties.append(0.6)
    means = [
        (sum(v * w for v, w in values) / sum(w for _, w in values), max(w for _, w in values))
        for values in families.values()
    ]
    success = sum(v * w for v, w in means) / sum(w for _, w in means) if means else None
    discrepancies = compare(predictions, task)
    disagreement = max(discrepancies.values(), default=0)
    # Inference conflict triggers evidence gathering. Once qualified measurement
    # resolves a claim, unmeasured opinions cannot indefinitely block it. Conflicting
    # active measurements still require stronger verification or abstention.
    unresolved = compare([p for p in active if is_measured(p)], task)
    measured = bool(bounds) and all(v is True for v in checks.values())
    lower = min(bounds) if bounds else 0
    risk = max(risks) if risks else 1
    # Executable evidence resolves the claims it actually measures, not other claims.
    uncertainty = (
        max(uncertainties, default=1)
        if not measured
        else max(
            [
                max(p.risk.uncertainty, p.success.uncertainty)
                for p in active
                if is_measured(p)
                and (p.success_metric, p.risk_metric) == (task.success_metric, task.risk_metric)
            ],
            default=1,
        )
    )
    if any(
        trust.get(f"{p.engine_id}@{p.engine_version}", {}).get("drift")
        or trust.get(f"{p.engine_id}@{p.engine_version}", {}).get("risk_drift")
        for p in active
        if is_measured(p)
        and (p.success_metric, p.risk_metric) == (task.success_metric, task.risk_metric)
    ):
        uncertainty = max(uncertainty, 0.6)
    utility = (
        lower - (2 + 2 * task.stakes) * risk + 0.75 * max(progress, default=0) - 0.1 * uncertainty
    )
    return Evaluation(
        success=success,
        stakes=task.stakes,
        success_lower=lower,
        risk_upper=risk,
        uncertainty=uncertainty,
        disagreement=disagreement,
        unresolved_disagreement=max(unresolved.values(), default=0),
        disagreement_by_claim=discrepancies,
        utility=utility,
        checks=checks,
        violations=sorted(set(violations)),
        evidence_ids=[p.id for p in predictions],
    )


def evaluate(
    predictions: list[Prediction], task: Task, trust: dict, *, state=None, action=None
) -> Evaluation:
    unique = {}
    for prediction in predictions:
        if prediction.id in unique and unique[prediction.id] != prediction:
            raise ValueError("Conflicting results share an evidence source identity")
        unique[prediction.id] = prediction
    predictions = list(unique.values())
    # The old summary is exclusively immediate; sequence/future findings stay typed.
    immediate = [p for p in predictions if immediate_metric_scope(p)]
    if state is not None and action is not None:
        immediate = [p for p in immediate if p.state_id == state.id and p.action_ids == [action.id]]
        instances = bind_claims(task, state.id, [action])
    else:
        instances = []
    result = _evaluate_legacy(immediate, task, trust)
    result.evidence_ids = sorted({p.id for p in predictions})
    result.claim_assessments = assess_claims(predictions, instances, trust)
    result.immediate_claim_keys = [c.key for c in instances[: len(task_definitions(task))]]
    for assessment in result.claim_assessments:
        claim = assessment.claim
        if claim.key not in result.immediate_claim_keys:
            continue
        if claim.definition.kind == "check":
            if claim.definition.namespace.startswith("legacy-check:"):
                result.checks[claim.definition.name] = assessment.check
        elif assessment.resolved:
            if claim.definition.kind == "success":
                result.success_lower = assessment.lower if assessment.lower is not None else 0
            elif claim.definition.kind == "risk":
                result.risk_upper = assessment.upper if assessment.upper is not None else 1
    required = [a for a in result.claim_assessments if a.claim.key in result.immediate_claim_keys]
    if required and all(a.resolved for a in required):
        result.uncertainty = max(a.uncertainty for a in required)
        result.unresolved_disagreement = max(a.disagreement for a in required)
        result.utility = (
            result.success_lower
            - (2 + 2 * task.stakes) * result.risk_upper
            + 0.75
            * max(
                (p.metrics.get("goal_progress", 0) for p in immediate if is_measured(p)), default=0
            )
            - 0.1 * result.uncertainty
        )
    return result


def gate(
    evaluation: Evaluation, state_id: str, action_hash: str, policy: Policy, can_verify: bool
) -> GateResult:
    reasons = []
    binding = {
        "policy_hash": identity({"policy": policy.model_dump(), "stakes": evaluation.stakes}),
        "evidence_hash": identity(evaluation.model_dump()),
    }
    failed = [key for key, value in evaluation.checks.items() if value is False]
    for assessment in evaluation.claim_assessments:
        c = assessment.claim
        if (
            not assessment.required
            or c.definition.scope != "root_action"
            or c.continuation != Continuation.ENVIRONMENT_ONLY
        ):
            continue
        if c.state_id != state_id or c.action_fingerprints != (action_hash,):
            failed.append("Claim instance does not match authorized state/action")
        if c.key in evaluation.immediate_claim_keys:
            continue
        label = f"{c.definition.namespace}:{c.definition.name}/{c.definition.version}@{c.horizon}"
        if c.definition.kind == "check" and assessment.check is False:
            failed.append(label)
        elif not assessment.resolved:
            reasons.append("Required future claim unresolved: " + label)
        else:
            if c.definition.kind == "risk" and (
                assessment.upper is None
                or assessment.upper > policy.max_risk * (1 - 0.5 * evaluation.stakes)
            ):
                reasons.append("Future risk exceeds policy: " + label)
            if c.definition.kind == "success" and (
                assessment.lower is None
                or assessment.lower
                < policy.min_success + (1 - policy.min_success) * 0.5 * evaluation.stakes
            ):
                reasons.append("Future success evidence insufficient: " + label)
            if assessment.uncertainty > policy.uncertainty_threshold / (1 + evaluation.stakes):
                reasons.append("Future uncertainty too high: " + label)
            if assessment.disagreement > policy.disagreement_threshold:
                reasons.append("Future measurements disagree: " + label)
    if evaluation.violations or failed:
        return GateResult(
            **binding,
            decision=Decision.ABSTAIN,
            reasons=sorted(
                set(evaluation.violations + [f"Mandatory check failed: {k}" for k in failed])
            ),
            state_id=state_id,
            action_hash=action_hash,
            evidence_ids=evaluation.evidence_ids,
        )
    if any(v is not True for v in evaluation.checks.values()):
        reasons.append("Mandatory checks are unresolved or failed")
    # Stakes can only tighten the supplied policy. Hard checks remain unconditional.
    if evaluation.risk_upper > policy.max_risk * (1 - 0.5 * evaluation.stakes):
        reasons.append("Risk upper bound exceeds policy at these stakes")
    if (
        evaluation.success_lower
        < policy.min_success + (1 - policy.min_success) * 0.5 * evaluation.stakes
    ):
        reasons.append("Action success lower bound is insufficient")
    if evaluation.uncertainty > policy.uncertainty_threshold / (1 + evaluation.stakes):
        reasons.append("Measured uncertainty remains too high")
    if evaluation.unresolved_disagreement > policy.disagreement_threshold:
        reasons.append("Measured engines disagree on aligned claims")
    if reasons:
        return GateResult(
            **binding,
            decision=Decision.VERIFY if can_verify else Decision.ABSTAIN,
            reasons=reasons,
            state_id=state_id,
            action_hash=action_hash,
            evidence_ids=evaluation.evidence_ids,
        )
    return GateResult(
        **binding,
        decision=Decision.EXECUTE,
        reasons=["Required evidence passed within policy"],
        state_id=state_id,
        action_hash=action_hash,
        evidence_ids=evaluation.evidence_ids,
    )
