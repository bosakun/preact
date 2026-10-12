"""Non-authoritative scheduling records above the existing execution Runtime."""

from typing import Literal

from pydantic import ConfigDict, Field

from preact.core.models import Contract


class ScheduleDecision(Contract):
    """A proposal and lifecycle recommendation, never execution/promotion permission."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    action: Literal["task", "information"]
    lifecycle: Literal["continue", "train_evaluate", "await_promotion"]
    reasons: list[str]
    estimates: dict = Field(default_factory=dict)
    assumptions: dict = Field(default_factory=dict)
