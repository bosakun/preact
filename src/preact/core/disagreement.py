"""Compare aligned claims without treating agreement as independent corroboration."""

import math
from itertools import combinations

from .models import Prediction, Task


def measured(prediction: Prediction) -> bool:
    return (
        prediction.evidence in {"executable", "simulation"}
        and prediction.success.measured
        and prediction.risk.measured
    )


def compare(predictions: list[Prediction], task: Task) -> dict[str, float]:
    """Return bounded discrepancies; absent or unaligned claims remain unscored.

    Continuous normalization belongs to the trusted task, never to an engine that
    could conceal discrepancies by selecting a large denominator. Categorical
    labels must match exactly; videos alone do not define comparable outcomes.
    """
    result: dict[str, float] = {}

    def record(key, value):
        result[key] = max(result.get(key, 0), min(1.0, value))

    for left, right in combinations(predictions, 2):
        if (
            left.state_id,
            left.action_ids,
            left.horizon,
            left.success_metric,
            left.risk_metric,
            left.conditioning,
        ) != (
            right.state_id,
            right.action_ids,
            right.horizon,
            right.success_metric,
            right.risk_metric,
            right.conditioning,
        ) or (left.success_metric, left.risk_metric) != (task.success_metric, task.risk_metric):
            continue
        for claim in ("success", "risk"):
            a, b = getattr(left, claim).value, getattr(right, claim).value
            if a is not None and b is not None:
                record(claim, abs(a - b))
        a = {outcome.label: outcome.probability.value for outcome in left.outcomes}
        b = {outcome.label: outcome.probability.value for outcome in right.outcomes}
        if a and a.keys() == b.keys() and all(v is not None for v in [*a.values(), *b.values()]):
            divergence = 0.0
            for key in a:
                midpoint = (a[key] + b[key]) / 2
                for value in (a[key], b[key]):
                    if value:
                        divergence += 0.5 * value * math.log(value / midpoint)
            record("outcomes", math.sqrt(max(0.0, divergence / math.log(2))))
        for key, scale in task.metric_scales.items():
            if key not in left.metrics or key not in right.metrics:
                continue
            record("metric:" + key, abs(left.metrics[key] - right.metrics[key]) / scale)
            if key in left.metric_intervals and key in right.metric_intervals:
                la, ua = left.metric_intervals[key]
                lb, ub = right.metric_intervals[key]
                record("interval_gap:" + key, max(0.0, la - ub, lb - ua) / scale)
    return result
