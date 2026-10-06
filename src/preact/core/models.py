from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: str = "1"


class State(Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    id: str
    domain: str
    kind: Literal["observed", "hypothetical"] = "observed"
    payload: dict[str, Any]
    timestamp: str = Field(default_factory=now)
    parent_id: str | None = None
    uncertainty: float = Field(default=0, ge=0, le=1)
    provenance: str

    @model_validator(mode="after")
    def content_identity(self):
        if self.id != identity({"domain": self.domain, "payload": self.payload}):
            raise ValueError("WorldState identity does not match its content")
        return self

    @classmethod
    def create(cls, domain: str, payload: dict, provenance: str, **kwargs) -> State:
        return cls(
            id=identity({"domain": domain, "payload": payload}),
            domain=domain,
            payload=payload,
            provenance=provenance,
            **kwargs,
        )


class Action(Contract):
    id: str = Field(default_factory=uid)
    name: str
    kind: str
    state_id: str
    payload: dict[str, Any]
    rationale: str = ""
    duration: float = Field(default=1.0, gt=0)

    @property
    def fingerprint(self) -> str:
        return identity(
            {
                "kind": self.kind,
                "payload": self.payload,
                "duration": self.duration,
                "state": self.state_id,
            }
        )


class Estimate(Contract):
    value: float | None = Field(default=None, ge=0, le=1)
    lower: float | None = Field(default=None, ge=0, le=1)
    upper: float | None = Field(default=None, ge=0, le=1)
    uncertainty: float = Field(default=1, ge=0, le=1)
    measured: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("lower must not exceed upper")
        if self.value is not None:
            if self.lower is not None and self.value < self.lower:
                raise ValueError("value below lower bound")
            if self.upper is not None and self.value > self.upper:
                raise ValueError("value above upper bound")
        return self


class EvidenceKind(StrEnum):
    INFERENCE = "inference"
    VISUAL = "generated_visual"
    EXECUTABLE = "executable"
    SIMULATION = "simulation"
    OBSERVATION = "observation"


class FutureOutcome(Contract):
    label: str
    probability: Estimate = Field(default_factory=Estimate)
    state: State
    assumptions: list[str] = Field(default_factory=list)


class Prediction(Contract):
    id: str = Field(default_factory=uid)
    engine_id: str
    engine_version: str
    family: str
    state_id: str
    action_ids: list[str]
    horizon: int = Field(default=1, ge=1)
    evidence: EvidenceKind
    success_metric: str = "action_postconditions/v1"
    risk_metric: str = "constraint_violation/v1"
    success: Estimate
    risk: Estimate
    violations: list[str] = Field(default_factory=list)
    successor: State | None = None
    outcomes: list[FutureOutcome] = Field(default_factory=list, max_length=10)
    metrics: dict[str, float] = Field(default_factory=dict)
    metric_intervals: dict[str, tuple[float, float]] = Field(default_factory=dict)
    vectors: dict[str, list[float]] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    mandatory_checks: dict[str, bool | None] = Field(default_factory=dict)
    latency_ms: float = 0
    cost_usd: float = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)
    refines_engine_ids: list[str] = Field(default_factory=list)
    sample_count: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def valid_metric_intervals(self):
        for key, (lower, upper) in self.metric_intervals.items():
            if key not in self.metrics or lower > upper or not lower <= self.metrics[key] <= upper:
                raise ValueError("Metric interval must contain its declared measurement")
        return self


class Capabilities(Contract):
    engine_id: str
    version: str
    family: str
    domains: list[str]
    evidence: EvidenceKind
    tier: int = Field(ge=0)
    max_horizon: int = Field(default=1, ge=1)
    produces_successor: bool = False
    applicability: str
    refines_engine_ids: list[str] = Field(default_factory=list)
    max_samples: int = Field(default=1, ge=1, le=200)
    verification_checks: list[str] | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    estimated_latency_seconds: float | None = Field(default=None, gt=0)


class PredictionRequest(Contract):
    state: State
    actions: list[Action] = Field(min_length=1)
    seed: int = 0
    horizon: int = Field(default=1, ge=1)
    deadline_seconds: float = Field(default=30, gt=0)
    sample_budget: int = Field(default=1, ge=1, le=200)


class Task(Contract):
    id: str
    domain: str
    title: str
    goal: str
    required_checks: list[str]
    seed: int = 0
    max_steps: int = Field(default=5, ge=1, le=20)
    success_metric: str = "action_postconditions/v1"
    risk_metric: str = "constraint_violation/v1"
    metric_scales: dict[str, float] = Field(default_factory=dict)
    stakes: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def positive_scales(self):
        if any(value <= 0 for value in self.metric_scales.values()):
            raise ValueError("Task measurement scales must be positive")
        return self


class Policy(Contract):
    max_depth: int = Field(default=3, ge=1, le=5)
    width: int = Field(default=3, ge=1, le=5)
    max_nodes: int = Field(default=40, ge=1, le=200)
    max_calls: int = Field(default=80, ge=1)
    max_seconds: float = Field(default=120, gt=0)
    max_cost_usd: float = Field(default=1, gt=0)
    max_risk: float = Field(default=0.05, ge=0, le=1)
    min_success: float = Field(default=0.6, ge=0, le=1)
    disagreement_threshold: float = Field(default=0.2, ge=0, le=1)
    uncertainty_threshold: float = Field(default=0.25, ge=0, le=1)
    search: bool = True
    adaptive: bool = True
    calibration: bool = True


class Decision(StrEnum):
    EXECUTE = "execute"
    VERIFY = "verify_more"
    ABSTAIN = "abstain"
    COMPLETE = "task_complete"


class Evaluation(Contract):
    success: float | None
    stakes: float = Field(default=0, ge=0, le=1)
    success_lower: float
    risk_upper: float
    uncertainty: float
    disagreement: float
    unresolved_disagreement: float = 0
    disagreement_by_claim: dict[str, float] = Field(default_factory=dict)
    utility: float
    checks: dict[str, bool | None]
    violations: list[str]
    evidence_ids: list[str]


class GateResult(Contract):
    decision: Decision
    reasons: list[str]
    state_id: str
    action_hash: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    policy_hash: str = ""
    expires_at: float = Field(default_factory=lambda: time.time() + 30)


class TreeNode(Contract):
    id: str = Field(default_factory=uid)
    parent_id: str | None
    kind: Literal["state", "action", "outcome"] = "action"
    label: str = ""
    outcome_probability: Estimate | None = None
    state: State
    action: Action | None = None
    depth: int = 0
    predictions: list[Prediction] = Field(default_factory=list)
    evaluation: Evaluation | None = None
    status: str = "proposed"
    actual: dict[str, Any] | None = None
    cumulative_risk: float = 0
    value: float = 0
    reused_from: str | None = None


class Observation(Contract):
    state: State
    success: bool
    unsafe: bool
    metrics: dict[str, float] = Field(default_factory=dict)
    vectors: dict[str, list[float]] = Field(default_factory=dict)
    checks: dict[str, bool] = Field(default_factory=dict)
    receipt: str
    success_metric: str = "action_postconditions/v1"
    risk_metric: str = "constraint_violation/v1"
    artifacts: dict[str, str] = Field(default_factory=dict)
    execution_calls: int = Field(default=1, ge=1)
    cost_usd: float | None = Field(default=None, ge=0)


class RunEvent(Contract):
    run_id: str
    seq: int
    kind: str
    timestamp: str = Field(default_factory=now)
    data: dict[str, Any]
