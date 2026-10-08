"""Explicit heuristic allocation of evidence, never an estimated success probability."""

from .evidence import supports
from .models import EvidenceKind, identity


def admission_reasons(cap, budget):
    reasons = []
    if budget["calls"] < 1:
        reasons.append("No rollout budget remains")
    if budget["seconds"] <= 0 or budget["cost_usd"] <= 0:
        reasons.append("Episode budget exhausted")
    if cap.estimated_cost_usd is not None and cap.estimated_cost_usd > budget["cost_usd"]:
        reasons.append("Declared request cost exceeds remaining budget")
    if cap.estimated_latency_seconds is not None and cap.estimated_latency_seconds > min(
        30, budget["seconds"]
    ):
        reasons.append("Declared latency exceeds request deadline")
    return reasons


def rank_verification(
    engines, declarations, evaluation, task, trust, budget, *, used=(), predictions=()
):
    plans = []
    unresolved = {key for key, value in evaluation.checks.items() if value is not True}
    for engine in engines:
        cap = declarations[id(engine)]
        reasons = admission_reasons(cap, budget)
        measured = cap.evidence in {EvidenceKind.EXECUTABLE, EvidenceKind.SIMULATION} and bool(
            set(cap.roles).intersection({"simulator", "verifier"})
        )
        if evaluation.claim_assessments and not measured:
            reasons.append("Unmeasured predictors cannot resolve mandatory measurement claims")
        supported = unresolved.intersection(cap.verification_checks or [])
        # Mandatory coverage and uncertainty/risk resolution are useful only for
        # qualified measurements. Model opinions cannot satisfy required checks.
        impact = 1 + (len(supported) if measured else 0)
        if measured:
            impact += evaluation.risk_upper + evaluation.uncertainty
            impact += evaluation.unresolved_disagreement + task.stakes
        stats = trust.get(f"{cap.engine_id}@{cap.version}", {})
        reliability = stats.get("weight", 0.5)
        if stats.get("drift") or stats.get("risk_drift"):
            reliability *= 0.5
        # Estimates are advisory resource costs, not a certificate or billing cap.
        # Unknown estimates receive a neutral penalty and remain explicitly unknown.
        cost = (
            1
            if cap.estimated_cost_usd is None
            else cap.estimated_cost_usd / max(budget["cost_usd"], 1e-9)
        )
        latency = (
            1
            if cap.estimated_latency_seconds is None
            else (cap.estimated_latency_seconds / max(min(30, budget["seconds"]), 1e-9))
        )
        correlated = {p.engine_id for p in predictions if p.family == cap.family}
        score = impact * reliability / (1 + cost + latency) / (1 + len(correlated))
        claims = [
            a.claim for a in evaluation.claim_assessments if a.required and supports(cap, a.claim)
        ]
        groups = {}
        for claim in claims:
            key = (claim.horizon, identity(claim.conditions), claim.continuation)
            groups.setdefault(key, []).append(claim)
        if evaluation.claim_assessments and not groups:
            reasons.append("No applicable required claim")
        if not groups:
            groups = {(1, "", "environment_only"): []}
        for (horizon, _, continuation), requested in groups.items():
            request_key = identity(
                {
                    "engine": cap.engine_id,
                    "claims": [c.model_dump() for c in requested],
                    "horizon": horizon,
                }
            )
            if request_key in used:
                continue
            group_reasons = list(reasons)
            group_reliability = (
                reliability if horizon == 1 and all(not c.conditions for c in requested) else 0.5
            )
            if horizon > budget.get("max_horizon", cap.max_horizon):
                group_reasons.append("Required horizon exceeds exploration budget")
            if requested:
                covered = [a for a in evaluation.claim_assessments if a.claim in requested]
                impact_extra = sum(not a.resolved for a in covered)
                group_score = (
                    (impact + impact_extra)
                    * group_reliability
                    / (1 + cost + latency)
                    / (1 + len(correlated))
                )
            else:
                group_score = score
            plans.append(
                (
                    engine,
                    {
                        "engine_id": cap.engine_id,
                        "score": group_score,
                        "claims": [c.model_dump() for c in requested],
                        "horizon": horizon,
                        "continuation": continuation,
                        "request_key": request_key,
                        "correlation_family": cap.family,
                        "correlated_engines": sorted(correlated),
                        "decision_impact": impact,
                        "contextual_weight": group_reliability,
                        "unresolved_checks_covered": sorted(supported) if measured else [],
                        "coverage_declared": cap.verification_checks is not None,
                        "estimated_cost_usd": cap.estimated_cost_usd,
                        "estimated_latency_seconds": cap.estimated_latency_seconds,
                        "admissible": not group_reasons,
                        "reasons": group_reasons,
                        "scope": "Heuristic evidence allocation; estimates do not relax the Decision Gate",
                    },
                )
            )
    return sorted(
        plans,
        key=lambda plan: (
            not plan[1]["admissible"],
            -plan[1]["score"],
            declarations[id(plan[0])].tier,
            plan[1]["engine_id"],
        ),
    )
