"""Replayed finite-sample Bernoulli drift checks, without observation authority."""

from dataclasses import asdict, dataclass
from typing import Literal

from scipy.stats import fisher_exact

from preact.core.models import identity


@dataclass(frozen=True)
class DriftConfig:
    window: int = 16
    min_recent: int = 8
    min_training: int = 16
    min_effect: float = 0.10
    alpha: float = 0.05
    max_gap_ticks: int = 16

    def __post_init__(self):
        if (
            any(
                type(n) is not int
                for n in (self.window, self.min_recent, self.min_training, self.max_gap_ticks)
            )
            or not 1 <= self.min_recent <= self.window
            or self.min_training < 1
            or not 0 < self.alpha < 1
            or not 0 <= self.min_effect <= 1
            or self.max_gap_ticks < 1
        ):
            raise ValueError("Invalid drift configuration")


@dataclass(frozen=True)
class ServiceSample:
    receipt: str
    reference: str
    tick: int
    high: bool
    source: Literal["probe", "ordinary"]


@dataclass(frozen=True)
class DriftCheck:
    look: int
    tick: int
    effective_count: int
    training_count: int
    training_high: int
    recent_count: int
    recent_high: int
    effect: float
    p_value: float
    alpha: float
    references: tuple[str, ...]
    status: Literal["available", "suspected", "invalidated"]


@dataclass(frozen=True)
class ModelHealth:
    status: Literal["available", "suspected", "invalidated", "insufficient_data", "unavailable"]
    reason: str
    model_version: str
    dataset_hash: str
    scope: str
    cutoff_tick: int
    basis_hash: str
    config: DriftConfig
    training_count: int = 0
    training_high: int = 0
    effective_count: int = 0
    history: tuple[DriftCheck, ...] = ()

    @property
    def usable(self) -> bool:
        return self.status in {"available", "suspected"}

    @property
    def version(self) -> str:
        return identity(asdict(self))


def replay_health(
    *,
    model_version: str,
    dataset_hash: str,
    scope: str,
    basis_hash: str,
    training_count: int,
    training_high: int,
    samples: tuple[ServiceSample, ...],
    cutoff_tick: int,
    config: DriftConfig = DriftConfig(),
) -> ModelHealth:
    """Every informative prefix gets one look. Replays spend no additional alpha.

    Overlapping windows are permitted: the union bound does not need independent
    tests, but each Fisher p-value still needs the stated iid sampling assumptions.
    The budget sums to alpha; it is not a calibrated probability of environmental drift.
    Invalidation latches for this model/scope. Censored ticks never enter the window.
    """
    if (
        any(type(n) is not int for n in (training_count, training_high, cutoff_tick))
        or not 0 <= training_high <= training_count
        or cutoff_tick < 0
    ):
        raise ValueError("Invalid training counts or cutoff")
    if len({s.receipt for s in samples}) != len(samples):
        raise ValueError("Duplicate informative receipt")
    if any(
        type(s.high) is not bool
        or type(s.tick) is not int
        or s.source not in {"probe", "ordinary"}
        or s.tick < 0
        or s.tick >= cutoff_tick
        or (i and samples[i - 1].tick >= s.tick)
        for i, s in enumerate(samples)
    ):
        raise ValueError("Future or unordered informative observations")
    history = []
    invalidated = False
    if training_count >= config.min_training:
        for count in range(config.min_recent, len(samples) + 1):
            recent = samples[max(0, count - config.window) : count]
            high = sum(s.high for s in recent)
            effect = abs(high / len(recent) - training_high / training_count)
            p = float(
                fisher_exact(
                    [[training_high, training_count - training_high], [high, len(recent) - high]],
                    alternative="two-sided",
                ).pvalue
            )
            look = len(history) + 1
            alpha = config.alpha / (look * (look + 1))
            invalidated |= p <= alpha and effect >= config.min_effect
            status = (
                "invalidated"
                if invalidated
                else "suspected"
                if p <= config.alpha and effect >= config.min_effect
                else "available"
            )
            history.append(
                DriftCheck(
                    look,
                    recent[-1].tick,
                    count,
                    training_count,
                    training_high,
                    len(recent),
                    high,
                    effect,
                    p,
                    alpha,
                    tuple(s.reference for s in recent),
                    status,
                )
            )
    stale = not samples or cutoff_tick - 1 - samples[-1].tick > config.max_gap_ticks
    insufficient = training_count < config.min_training or len(samples) < config.min_recent or stale
    status = (
        "invalidated"
        if invalidated
        else "insufficient_data"
        if insufficient
        else history[-1].status
    )
    return ModelHealth(
        status=status,
        reason="statistical_change"
        if invalidated
        else "insufficient_or_stale"
        if insufficient
        else "not_invalidated",
        model_version=model_version,
        dataset_hash=dataset_hash,
        scope=scope,
        cutoff_tick=cutoff_tick,
        basis_hash=basis_hash,
        config=config,
        training_count=training_count,
        training_high=training_high,
        effective_count=len(samples),
        history=tuple(history),
    )
