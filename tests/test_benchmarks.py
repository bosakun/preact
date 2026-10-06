import pytest

from preact.benchmarks import metrics, paired_analysis, probability_scores


def episode(task, variant, seed, success, **overrides):
    return dict(
        domain="software",
        task_id=task,
        seed=seed,
        variant=variant,
        success=success,
        unsafe=not success,
        steps=1,
        status="complete",
        latency_ms=10,
        calls=2,
        cost_usd=0,
        cost_known=True,
        simulation_calls=0,
        disagreement=0,
        **overrides,
    )


def test_repeated_seeds_do_not_create_fake_task_clusters():
    rows = [
        episode("one-task", variant, seed, variant == "preact")
        for seed in range(5)
        for variant in ["direct", "preact"]
    ]
    result = paired_analysis(rows)["software"]
    assert result["paired_tasks"] == 1 and result["paired_episodes"] == 5
    assert result["success"]["mean_task_delta"] == 1
    assert result["success"]["task_cluster_bootstrap_95"] is None
    # A second distinct task yields two clusters, including a loss for PreAct.
    rows += [episode("other-task", "direct", 0, True), episode("other-task", "preact", 0, False)]
    result = paired_analysis(rows)["software"]
    assert result["success"]["mean_task_delta"] == 0
    assert result["success"]["task_cluster_bootstrap_95"] == [-1, 1]


def test_unknown_cost_is_reported_unknown_not_free():
    row = episode("one", "preact", 0, True)
    row["cost_known"] = False
    result = metrics([row], [])
    assert result["mean_cost_usd"] is None and result["cost_coverage"] == 0


def test_uncertain_execution_is_not_reported_as_a_safe_failure():
    row = episode("one", "preact", 0, False)
    row.update(unsafe=False, unsafe_observation_known=False, status="failed", steps=0)
    result = metrics([row], [])
    assert result["unsafe_episode_rate"] is None
    assert result["unsafe_episode_bounds"] == [0, 1]
    assert result["unsafe_observation_coverage"] == 0
    assert result["unsafe_wilson_95"] is None


def test_risk_accuracy_continuous_coverage_and_pending_attempts_stay_distinct():
    row = episode("one", "preact", 0, True)
    row.update(
        execution_attempts=2,
        unsafe_observation_known=False,
        comparisons=[
            {
                "metrics": {"distance_m": {"absolute_error": 0.02, "interval_covered": True}},
            }
        ],
    )
    errors = [{"value": 0.9, "label": 1, "risk_value": 0.8, "risk_label": 1}]
    result = metrics([row], errors)
    assert result["brier"] == pytest.approx(0.01)
    assert result["risk_brier"] == pytest.approx(0.04)
    assert result["risk_ece_10_bins"] == pytest.approx(0.2)
    assert result["unsafe_action_rate"] is None
    assert result["unsafe_action_bounds"] == [0, 0.5]
    assert result["continuous_errors"]["distance_m"] == {
        "n": 1,
        "mean_error": 0.02,
        "interval_coverage": 1,
        "interval_sample_count": 1,
    }
    assert probability_scores([])["log_loss"] is None
