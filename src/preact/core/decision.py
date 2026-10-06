from .calibration import Calibration
from .disagreement import compare
from .disagreement import measured as is_measured
from .models import Decision, Evaluation, GateResult, Policy, Prediction, Task, identity


def evaluate(predictions: list[Prediction], task: Task, trust: dict) -> Evaluation:
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


def gate(
    evaluation: Evaluation, state_id: str, action_hash: str, policy: Policy, can_verify: bool
) -> GateResult:
    reasons = []
    binding = {
        "policy_hash": identity({"policy": policy.model_dump(), "stakes": evaluation.stakes})
    }
    failed = [key for key, value in evaluation.checks.items() if value is False]
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
