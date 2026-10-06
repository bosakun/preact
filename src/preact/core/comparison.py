"""Only aligned, actually executed one-action predictions receive labels."""

import math

from .models import Observation, Prediction


def compare(prediction: Prediction, observation: Observation):
    if prediction.horizon != 1:
        return None
    label = observation.checks.get("action_success", observation.success)
    result = {
        "prediction_id": prediction.id,
        "engine_id": prediction.engine_id,
        "truth_source": observation.state.provenance,
        "horizon": 1,
        "success_metric": prediction.success_metric,
        "risk_metric": prediction.risk_metric,
        "metrics": {},
        "unknown_claims": [],
    }
    for name, estimate, truth in [
        ("success", prediction.success, label),
        ("risk", prediction.risk, observation.unsafe),
    ]:
        if getattr(prediction, f"{name}_metric") != getattr(observation, f"{name}_metric"):
            result["unknown_claims"].append(f"{name}:metric_mismatch")
            continue
        if estimate.value is None:
            result["unknown_claims"].append(name)
            continue
        p = min(1 - 1e-12, max(1e-12, estimate.value))
        result[name] = {
            "predicted": estimate.value,
            "observed": int(truth),
            "brier": (estimate.value - int(truth)) ** 2,
            "log_loss": -math.log(p if truth else 1 - p),
            # A single binary outcome is not the unknown Bernoulli probability.
            # Probability CI coverage requires a known population probability;
            # only proper scoring rules can be evaluated from this receipt.
            "interval_covered": None,
            "probability_interval": [estimate.lower, estimate.upper],
        }
    for key, value in prediction.metrics.items():
        if key in observation.metrics:
            actual = observation.metrics[key]
            result["metrics"][key] = {
                "predicted": value,
                "observed": actual,
                "absolute_error": abs(value - actual),
                "interval_covered": None
                if key not in prediction.metric_intervals
                else prediction.metric_intervals[key][0]
                <= actual
                <= prediction.metric_intervals[key][1],
            }
    for key, predicted in prediction.vectors.items():
        actual = observation.vectors.get(key)
        if actual is not None and len(predicted) == len(actual):
            result["metrics"][key] = {
                "predicted": predicted,
                "observed": actual,
                "euclidean_error": math.dist(predicted, actual),
            }
        else:
            result["unknown_claims"].append(key)
    return result
