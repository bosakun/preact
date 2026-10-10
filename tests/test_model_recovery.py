import json

import pytest
from pydantic import ValidationError

from preact.domains.queue_recovery import promotion_reasons, score
from preact.learning.recovery import PromotionPolicy


def test_promotion_thresholds_are_fixed_and_unknown_is_not_improvement():
    policy = PromotionPolicy()
    with pytest.raises(ValidationError):
        PromotionPolicy(min_training=1)
    assert json.loads(policy.model_dump_json())["max_state_mae"] == 0.5
    unknown = score([{"status": "unknown"}], [[[]]])
    assert unknown["coverage"] == 0 and unknown["state_mae"] is None
    assert "incomplete_prediction_coverage" in promotion_reasons(
        {k: unknown for k in ("candidate", "parent", "prior")}, 16, {"queue", "pending"}
    )


def test_quality_and_baseline_failures_are_independent():
    scores = {
        "candidate": dict(comparisons=8, coverage=1, state_mae=0.6, difference_mae=0.7),
        "parent": dict(comparisons=8, coverage=1, state_mae=0.4, difference_mae=0.1),
        "prior": dict(comparisons=8, coverage=1, state_mae=0.2, difference_mae=0.1),
    }
    assert set(promotion_reasons(scores, 16, {"queue", "pending"})) == {
        "prediction_quality",
        "prior_comparison",
        "parent_comparison",
    }
