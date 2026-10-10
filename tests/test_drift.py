from dataclasses import asdict

import pytest

from preact.learning.drift import DriftConfig, ServiceSample, replay_health


def evaluate(values, *, config=None, cutoff=None, training=64, high=0):
    samples = tuple(
        ServiceSample(str(i), f"r:{i}", i, bool(v), "probe") for i, v in enumerate(values)
    )
    return replay_health(
        model_version="model",
        dataset_hash="dataset",
        scope="episode",
        basis_hash="prefix",
        training_count=training,
        training_high=high,
        samples=samples,
        cutoff_tick=len(values) if cutoff is None else cutoff,
        config=config or DriftConfig(),
    )


@pytest.mark.parametrize("high", [0, 64])
def test_stable_distributions_and_replay(high):
    result = evaluate([high > 0] * 32, high=high)
    assert result.status == "available"
    assert all(c.p_value == 1 for c in result.history)
    assert asdict(result) == asdict(evaluate([high > 0] * 32, high=high))
    assert len(result.history) == 25
    assert sum(c.alpha for c in result.history) < 0.05
    assert [c.look for c in result.history] == list(range(1, 26))


@pytest.mark.parametrize(
    "high,values", [(0, [False] * 8 + [True] * 24), (64, [True] * 8 + [False] * 24)]
)
def test_change_latches_and_uses_each_new_sample_once(high, values):
    result = evaluate(values, high=high)
    assert result.status == "invalidated"
    first = next(c for c in result.history if c.status == "invalidated")
    assert first.p_value <= first.alpha and first.effect >= 0.10
    assert first.effective_count == first.tick + 1
    assert evaluate(values + [high > 0] * 64, high=high).status == "invalidated"


def test_shortage_and_stale_are_not_statistical_invalidation():
    assert evaluate([True] * 7).status == "insufficient_data"
    assert evaluate([True] * 32, training=4).status == "insufficient_data"
    assert evaluate([False] * 8, cutoff=40).status == "insufficient_data"
    assert evaluate([True] * 16, cutoff=40).status == "invalidated"


def test_future_reversed_duplicate_samples_rejected():
    params = dict(
        model_version="m",
        dataset_hash="d",
        scope="s",
        basis_hash="b",
        training_count=64,
        training_high=0,
        cutoff_tick=2,
    )
    for samples in (
        (ServiceSample("a", "r", 2, True, "probe"),),
        (ServiceSample("a", "r", 1, True, "probe"), ServiceSample("b", "s", 0, False, "probe")),
        (ServiceSample("a", "r", 0, True, "probe"), ServiceSample("a", "s", 1, False, "probe")),
    ):
        with pytest.raises(ValueError):
            replay_health(samples=samples, **params)


def test_minimum_effect_blocks_statistically_small_changes():
    result = evaluate([False, True] * 32, training=10000, config=DriftConfig(min_effect=0.9))
    assert result.status == "available"


@pytest.mark.parametrize(
    "kwargs", [{"window": 7}, {"alpha": 0}, {"min_training": 0}, {"max_gap_ticks": 0}]
)
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        DriftConfig(**kwargs)
