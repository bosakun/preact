"""Claim binding and source qualification, independent of domains and vendors."""

from .disagreement import measured
from .interfaces import EngineFailure
from .models import (
    ClaimAssessment,
    ClaimDefinition,
    ClaimInstance,
    ClaimResult,
    Continuation,
    Estimate,
    EvidenceFinding,
    EvidenceKind,
    identity,
)


def metric_definition(metric, kind):
    name, separator, version = metric.rpartition("/")
    return ClaimDefinition(
        name=name if separator else metric, version=version or "legacy", kind=kind
    )


def task_definitions(task):
    return [
        metric_definition(task.success_metric, "success"),
        metric_definition(task.risk_metric, "risk"),
        *[
            ClaimDefinition(
                namespace="legacy-check:" + task.id, name=name, version="legacy", kind="check"
            )
            for name in task.required_checks
        ],
    ]


def bind_claims(task, state_id, actions):
    base = dict(
        state_id=state_id,
        task_context=identity(task.model_dump()),
        action_ids=tuple(a.id for a in actions),
        action_fingerprints=tuple(a.fingerprint for a in actions),
    )
    immediate = [ClaimInstance(definition=d, **base) for d in task_definitions(task)]
    future = [
        ClaimInstance(
            definition=r.definition,
            horizon=r.horizon,
            continuation=r.continuation,
            conditions=r.conditions,
            **base,
        )
        for r in task.future_requirements
    ]
    return immediate + future


def supports(cap, claim):
    if claim.horizon > cap.max_horizon or claim.continuation not in cap.continuations:
        return False
    if cap.supported_claims is not None:
        return claim.definition in cap.supported_claims
    # Undeclared legacy capabilities cover only the old immediate metrics and
    # explicitly advertised legacy checks, never arbitrary future definitions.
    if claim.horizon != 1 or claim.conditions or claim.definition.scope != "root_action":
        return False
    d = claim.definition
    if d.kind == "check":
        return d.namespace.startswith("legacy-check:") and d.name in (cap.verification_checks or [])
    return d in [
        metric_definition("action_postconditions/v1", "success"),
        metric_definition("constraint_violation/v1", "risk"),
    ]


def correlation_boundary(prediction):
    # Family is a conservative boundary today, not a universal correlation model.
    return prediction.family


def immediate_metric_scope(prediction):
    """Legacy envelope probabilities are labelable only in their actual request scope."""
    if (
        prediction.horizon != 1
        or len(prediction.action_ids) != 1
        or prediction.conditioning is not None
    ):
        return False
    if prediction.requested_claims is None:
        return True  # Unmodified historical/direct single-action envelopes.
    expected = [
        metric_definition(prediction.success_metric, "success"),
        metric_definition(prediction.risk_metric, "risk"),
    ]
    return all(
        any(
            c.definition == d
            and c.horizon == 1
            and not c.conditions
            and c.continuation == Continuation.ENVIRONMENT_ONLY
            for c in prediction.requested_claims
        )
        for d in expected
    )


def prediction_findings(prediction, instances):
    results = list(prediction.claim_results)
    for claim in instances:
        if prediction.requested_claims is not None and claim not in prediction.requested_claims:
            continue
        if (prediction.state_id, tuple(prediction.action_ids), prediction.horizon) != (
            claim.state_id,
            claim.action_ids,
            claim.horizon,
        ):
            continue
        # New concrete claims are emitted explicitly; legacy normalization is
        # restricted to the established immediate envelope without added conditions.
        if (
            claim.horizon != 1
            or claim.conditions
            or claim.continuation != Continuation.ENVIRONMENT_ONLY
        ):
            continue
        d = claim.definition
        if d == metric_definition(prediction.success_metric, "success"):
            results.append(ClaimResult(claim=claim, estimate=prediction.success))
        elif d == metric_definition(prediction.risk_metric, "risk"):
            results.append(ClaimResult(claim=claim, estimate=prediction.risk))
        elif d.namespace.startswith("legacy-check:") and d.name in prediction.mandatory_checks:
            if measured(prediction):
                results.append(ClaimResult(claim=claim, check=prediction.mandatory_checks[d.name]))
    unique = {r.claim.key: r for r in results}
    findings = []
    for result in unique.values():
        executable = prediction.evidence in {EvidenceKind.EXECUTABLE, EvidenceKind.SIMULATION}
        explicit = any(r.claim == result.claim for r in prediction.claim_results)
        qualified = (
            executable
            and (explicit or measured(prediction))
            and (
                result.check is not None
                if result.claim.definition.kind == "check"
                else result.estimate.measured and result.estimate.value is not None
            )
        )
        findings.append(
            EvidenceFinding(
                result=result,
                source_kind="prediction",
                source_reference=prediction.id,
                engine_id=prediction.engine_id,
                engine_version=prediction.engine_version,
                evidence=prediction.evidence,
                correlation_boundary=correlation_boundary(prediction),
                qualified=qualified,
                qualification_reason="Claim-specific measurement"
                if qualified
                else "Unmeasured or unknown claim",
                cost_usd=prediction.cost_usd,
                latency_ms=prediction.latency_ms,
                provenance={
                    "source_digest": identity(prediction.model_dump()),
                    "assumptions": prediction.assumptions,
                    "refines_engine_ids": prediction.refines_engine_ids,
                },
            )
        )
    return findings


def assess_claims(predictions, instances, trust):
    findings = [f for p in predictions for f in prediction_findings(p, instances)]
    keys = {c.key for c in instances}
    claims = {c.key: c for c in instances}
    claims.update({f.result.claim.key: f.result.claim for f in findings})
    assessments = []
    for key, claim in claims.items():
        sources = {
            (f.source_kind, f.source_reference): f for f in findings if f.result.claim == claim
        }
        group = list(sources.values())
        qualified = [f for f in group if f.qualified]
        active = [
            f
            for f in qualified
            if not any(
                f.engine_id in stronger.provenance.get("refines_engine_ids", [])
                and f.correlation_boundary == stronger.correlation_boundary
                and f.result.claim == stronger.result.claim
                for stronger in qualified
            )
        ]
        check = None
        if claim.definition.kind == "check":
            values = [f.result.check for f in qualified]
            check = False if False in values else True if True in values else None
            resolved = check is not None
            uncertainty = 0 if resolved else 1
        else:
            resolved = bool(active) and all(
                f.result.estimate.lower is not None and f.result.estimate.upper is not None
                for f in active
            )
            uncertainty = max((f.result.estimate.uncertainty for f in active), default=1)
        for f in active:
            # Existing calibration certifies only its aligned immediate context.
            # No long-horizon observations exist yet; do not transfer that trust.
            stats = (
                trust.get(f"{f.engine_id}@{f.engine_version}", {})
                if claim.horizon == 1 and not claim.conditions
                else {}
            )
            if stats.get("drift") or stats.get("risk_drift"):
                uncertainty = max(uncertainty, 0.6)
        values = [f.result.estimate.value for f in active if f.result.estimate.value is not None]
        assessments.append(
            ClaimAssessment(
                claim=claim,
                findings=group,
                required=key in keys,
                resolved=resolved,
                check=check,
                lower=min(
                    (
                        f.result.estimate.lower
                        for f in active
                        if f.result.estimate.lower is not None
                    ),
                    default=None,
                ),
                upper=max(
                    (
                        f.result.estimate.upper
                        for f in active
                        if f.result.estimate.upper is not None
                    ),
                    default=None,
                ),
                uncertainty=uncertainty,
                disagreement=max(values, default=0) - min(values, default=0),
            )
        )
    return assessments


def observation_findings(observation, *, execution, action, task, source_reference):
    """Only Runtime's completed actual execution path can invoke this binding."""
    receipt, run_id, state_id = execution["id"], execution["run_id"], execution["state_id"]
    if (
        execution["status"] != "complete"
        or execution["receipt"] != observation.model_dump()
        or execution["action_hash"] != action.fingerprint
        or action.state_id != state_id
        or observation.receipt != receipt
        or observation.state.kind != "observed"
        or observation.state.domain != task.domain
    ):
        raise EngineFailure("Observation does not align with completed actual execution")
    claims = bind_claims(task, state_id, [action])[: len(task_definitions(task))]
    findings = []
    for claim in claims:
        if (
            claim.horizon != 1
            or claim.conditions
            or claim.continuation != Continuation.ENVIRONMENT_ONLY
        ):
            continue
        d = claim.definition
        if d.kind == "check":
            if d.name not in observation.checks:
                continue
            result = ClaimResult(claim=claim, check=observation.checks[d.name])
        else:
            metric = observation.success_metric if d.kind == "success" else observation.risk_metric
            if d != metric_definition(metric, d.kind):
                continue
            value = (
                float(observation.checks.get("action_success", observation.success))
                if d.kind == "success"
                else float(observation.unsafe)
            )
            result = ClaimResult(
                claim=claim,
                estimate=Estimate(
                    value=value, lower=value, upper=value, uncertainty=0, measured=True
                ),
            )
        findings.append(
            EvidenceFinding(
                result=result,
                source_kind="observation",
                source_reference=source_reference,
                evidence=EvidenceKind.OBSERVATION,
                correlation_boundary="actual:" + receipt,
                qualified=True,
                qualification_reason="Aligned completed execution observation",
                cost_usd=observation.cost_usd,
                provenance={
                    "run_id": run_id,
                    "receipt": receipt,
                    "observed_state_id": observation.state.id,
                    "truth_source": observation.state.provenance,
                },
            )
        )
    return findings
