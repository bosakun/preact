"""Explicit heuristic allocation of evidence, never an estimated success probability."""

from .models import EvidenceKind


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


def rank_verification(engines, declarations, evaluation, task, trust, budget):
    plans = []
    unresolved = {key for key, value in evaluation.checks.items() if value is not True}
    for engine in engines:
        cap = declarations[id(engine)]
        reasons = admission_reasons(cap, budget)
        measured = cap.evidence in {EvidenceKind.EXECUTABLE, EvidenceKind.SIMULATION}
        supported = (
            unresolved
            if cap.verification_checks is None
            else unresolved.intersection(cap.verification_checks)
        )
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
        score = impact * reliability / (1 + cost + latency)
        plans.append(
            (
                engine,
                {
                    "engine_id": cap.engine_id,
                    "score": score,
                    "decision_impact": impact,
                    "contextual_weight": reliability,
                    "unresolved_checks_covered": sorted(supported) if measured else [],
                    "coverage_declared": cap.verification_checks is not None,
                    "estimated_cost_usd": cap.estimated_cost_usd,
                    "estimated_latency_seconds": cap.estimated_latency_seconds,
                    "admissible": not reasons,
                    "reasons": reasons,
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
