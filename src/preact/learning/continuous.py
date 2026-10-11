"""Immutable lineage records for explicitly managed Queue monitoring epochs."""

from typing import Literal

from pydantic import ConfigDict

from preact.core.models import Contract, State, Task


class ContinuousRecord(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1"] = "1"


class MonitoringEpoch(ContinuousRecord):
    epoch_id: str
    world_episode_id: str
    generation: int
    parent_epoch: str | None
    model_artifact: str
    model_version: str
    dataset_hash: str
    task: Task
    training: dict[str, list[str]]
    anchor: State
    monitor_runs: list[str]
    recovery_journal: str | None = None
    promotion_artifact: str | None = None
    health_version: str
    drift_config: dict


class InvalidationRecord(ContinuousRecord):
    epoch_id: str
    model_version: str
    health_version: str
    cutoff: State
    monitor_runs: list[str]
    recovery_journal: str
