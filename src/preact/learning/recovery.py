"""Small immutable lifecycle records; derived artifacts never replace receipt authority."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from preact.core.models import Action, Contract, State, Task, identity
from preact.learning.drift import DriftConfig


class RecoveryRejected(ValueError):
    """A legitimate unmet lifecycle condition, distinct from corrupt source data."""


class RecoveryRecord(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1"] = "1"


class PromotionPolicy(RecoveryRecord):
    min_training: Literal[16] = 16
    min_evaluation: Literal[16] = 16
    min_comparisons: Literal[8] = 8
    max_state_mae: Literal[0.5] = 0.5
    max_difference_mae: Literal[0.5] = 0.5
    baseline_tolerance: Literal[0.05] = 0.05
    parent_improvement: Literal[0.10] = 0.10
    prior: Literal[0.5] = 0.5


class RecoveryOrigin(RecoveryRecord):
    parent_artifact: str
    training: dict[str, list[str]]
    episode_id: str
    task: Task
    initial: State
    cutoff: State
    runs: list[str]
    health_version: str
    config: dict = Field(default_factory=lambda: DriftConfig().__dict__.copy())


class CandidateModel(RecoveryRecord):
    origin_artifact: str
    source_cutoff: State
    source_runs: list[str]
    training: dict[str, list[str]]
    model_artifact: str
    model_version: str
    dataset_hash: str
    effective_count: int
    code_hash: str


class ForecastRecord(RecoveryRecord):
    candidate_artifact: str
    task: Task
    state: State
    actions: list[Action]
    forecasts: dict[str, dict]


class EvaluationBranch(RecoveryRecord):
    schema_version: Literal["2"] = "2"
    episode_id: str
    initial: State
    runs: list[str]
    final_observation: State


class EvaluationCase(RecoveryRecord):
    forecast_artifact: str
    branches: list[EvaluationBranch]


class CandidateEvaluation(RecoveryRecord):
    candidate_artifact: str
    cases: list[EvaluationCase]
    cutoff: str
    policy: PromotionPolicy = Field(default_factory=PromotionPolicy)
    scores: dict
    effective_count: int
    passed: bool
    reasons: list[str]
    code_hash: str


class ModelPromotion(RecoveryRecord):
    candidate_artifact: str
    evaluation_artifact: str
    parent_version: str
    model_version: str
    episode_id: str
    initial: State
    task: Task
    runs: list[str]
    cutoff: State
    health_version: str
    config: dict
    code_hash: str


def recovery_code_hash() -> str:
    root = Path(__file__).parents[1]
    paths = [
        Path(__file__),
        root / "domains/queue_recovery.py",
        root / "engines/queue_temporal_recovery.py",
    ]
    return identity({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
