"""Queue-only validation and promotion scoring; no model training or execution here."""

from datetime import datetime, timezone
from itertools import combinations
from statistics import fmean

from preact.core.models import State
from preact.core.store import Store
from preact.domains.information_queue import DOMAIN, PROVENANCE
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.learning.recovery import PromotionPolicy
from preact.learning.transitions import TransitionSnapshot


def timepoint(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Recovery requires timezone-aware times")
    return result


def observed(state: State) -> None:
    if (
        state.kind != "observed"
        or state.domain != DOMAIN
        or state.provenance != PROVENANCE
        or timepoint(state.timestamp) > datetime.now(timezone.utc)
    ):
        raise ValueError("Recovery requires a current authoritative Queue v2 State")


def same_state(a: State, b: State) -> bool:
    return a.model_dump(exclude={"timestamp"}) == b.model_dump(exclude={"timestamp"})


def effective(snapshot: TransitionSnapshot) -> int:
    return sum(
        int(QueueServiceAdapter().targets(r.model_copy(deep=True))[0]) for r in snapshot.transitions
    )


async def check_cutoff(
    store: Store, snapshot: TransitionSnapshot, cutoff: str, *, strict: bool = False
) -> None:
    snapshot.verify_identity()
    if snapshot.unlabelled_receipts:
        raise ValueError("Incomplete execution cannot enter model recovery")
    bound = timepoint(cutoff)
    if bound > datetime.now(timezone.utc):
        raise ValueError("Recovery cutoff is in the future")
    for row in snapshot.transitions:
        observed(row.before)
        observed(row.after.state)
        events = await store.call("read_events", row.run_id)
        seq = int(row.outcome_reference.rsplit(":", 1)[1])
        event = next(e for e in events if e["seq"] == seq)
        times = (timepoint(event["timestamp"]), timepoint(row.after.state.timestamp))
        if any(t > bound or (strict and t == bound) for t in times):
            raise ValueError("Future or not-yet-committed recovery outcome")


async def validate_final_observation(
    store: Store, snapshot: TransitionSnapshot, state: State, cutoff: str
) -> None:
    """Bind an actual passive observation to its completed branch, without retiming receipts."""
    observed(state)
    if not snapshot.transitions or not same_state(state, snapshot.transitions[-1].after.state):
        raise ValueError("Evaluation final observation differs from completed receipt State")
    if timepoint(state.timestamp) > timepoint(cutoff):
        raise ValueError("Evaluation final observation exceeds evaluation cutoff")
    # All required outcomes must already have been durably recorded when observed.
    await check_cutoff(store, snapshot, state.timestamp, strict=True)


def score(comparisons: list[dict], truths: list[list[list[dict]]]) -> dict:
    errors, differences = [], []
    supported = 0
    for comparison, actual in zip(comparisons, truths, strict=True):
        if comparison["status"] != "estimated":
            continue
        supported += 1
        predictions = comparison["predictions"]
        for prediction, trace in zip(predictions, actual, strict=True):
            for tick in range(3):
                for key in ("queue", "delivered"):
                    errors.append(abs(prediction["vectors"][key][tick] - trace[tick][key]))
        for left, right in combinations(range(3), 2):
            for tick in range(3):
                for key in ("queue", "delivered"):
                    predicted = (
                        predictions[left]["vectors"][key][tick]
                        - predictions[right]["vectors"][key][tick]
                    )
                    actual_difference = actual[left][tick][key] - actual[right][tick][key]
                    differences.append(abs(predicted - actual_difference))
    return {
        "comparisons": len(comparisons),
        "supported": supported,
        "coverage": supported / len(comparisons) if comparisons else 0,
        "unknown_rate": 1 - supported / len(comparisons) if comparisons else 1,
        "state_mae": fmean(errors) if errors else None,
        "difference_mae": fmean(differences) if differences else None,
    }


def promotion_reasons(scores: dict, count: int, nonempty: set[str]) -> list[str]:
    policy = PromotionPolicy()
    new, old, prior = (scores[k] for k in ("candidate", "parent", "prior"))
    reasons = []
    if count < policy.min_evaluation:
        reasons.append("insufficient_evaluation")
    if new["comparisons"] < policy.min_comparisons or nonempty != {"queue", "pending"}:
        reasons.append("insufficient_action_comparisons")
    if any(scores[k]["coverage"] != 1 for k in ("candidate", "parent", "prior")):
        reasons.append("incomplete_prediction_coverage")
        return reasons
    if new["state_mae"] > policy.max_state_mae or new["difference_mae"] > policy.max_difference_mae:
        reasons.append("prediction_quality")
    if any(new[k] > prior[k] + policy.baseline_tolerance for k in ("state_mae", "difference_mae")):
        reasons.append("prior_comparison")
    if (
        old["state_mae"] <= 0
        or new["state_mae"] > old["state_mae"] * (1 - policy.parent_improvement)
        or new["difference_mae"] > old["difference_mae"] + policy.baseline_tolerance
    ):
        reasons.append("parent_comparison")
    return reasons
