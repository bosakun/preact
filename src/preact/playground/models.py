"""Typed presentation contracts, separate from the Core execution contracts."""

from typing import Any, Literal

from pydantic import Field

from preact.core.models import Contract, RunEvent

Scenario = Literal["stable_high", "stable_low", "high_to_low", "low_to_high"]


class PlaygroundStart(Contract):
    scenario: Scenario = "stable_high"
    seed: int = Field(default=11, ge=0, le=100000)


class PlaygroundCommand(Contract):
    command: Literal["step", "auto", "pause", "stop"]
    # Compare-and-set prevents retries/double clicks from admitting extra rounds.
    revision: int = Field(ge=0)


class PlaygroundEvent(Contract):
    index: int = Field(ge=1)
    reference: str
    scope: Literal["world", "learning", "lifecycle"]
    event: RunEvent
    receipt_verified: bool = False


class PlaygroundSession(Contract):
    id: str
    label: str
    mode: Literal["live", "recorded"]
    scenario: Scenario
    status: str
    revision: int = 0
    rounds: int = 0
    auto: bool = False
    event_count: int = 0
    error: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class PlaygroundSnapshot(Contract):
    session: PlaygroundSession
    events: list[PlaygroundEvent]
    artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    reset: bool = False


class PlaygroundRecording(Contract):
    session: PlaygroundSession
    events: list[PlaygroundEvent]
    artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # A checksum is archival integrity, not authentication or execution permission.
    digest: str
