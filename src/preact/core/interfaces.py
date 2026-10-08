from typing import Literal, Protocol

from pydantic import Field, ValidationError

from .models import (
    Action,
    Capabilities,
    Contract,
    Observation,
    Prediction,
    PredictionRequest,
    State,
    Task,
)


class FutureEngine(Protocol):
    """Predict/simulate/verify declared claims; authoritative observation belongs to World."""

    capabilities: Capabilities

    async def predict(self, request: PredictionRequest) -> Prediction: ...


class World(Protocol):
    """Optional proposal_calls reserves external work before invocation.

    proposal_usage_complete=True certifies that a successful propose() supplies every
    external-call receipt in usage. Failures and uncertified adapters remain conservative.
    """

    task: Task

    async def observe(self) -> State: ...
    async def propose(self, state: State, width: int) -> list[Action]: ...
    def validate(self, state: State, action: Action) -> None: ...
    async def execute(self, action: Action, receipt: str) -> Observation: ...
    def complete(self, state: State) -> bool: ...


class EngineFailure(RuntimeError):
    """Missing capabilities or failed infrastructure, never a successful prediction."""


class CleanupEvidence(Contract):
    """Bounded diagnostics; no endpoint, headers, response body or arbitrary message."""

    schema_version: Literal["1"] = "1"
    job_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    operation_kind: Literal["remote_job", "local_process"] | None = None
    submission_outcome: Literal["acknowledged", "unconfirmed"] | None = None
    request_accepted: bool = Field(strict=True)
    terminal_state_confirmed: bool = Field(strict=True)
    worker_status: Literal["complete", "failed", "cancelled", "interrupted"] | None = None
    error: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    http_status: int | None = Field(default=None, ge=100, le=599)


def cleanup_evidence(error: BaseException) -> dict | None:
    # asyncio.wait_for/timeout can wrap the original cancellation. Keep its
    # bounded diagnostics without importing any vendor implementation into Core.
    for _ in range(4):
        payload = getattr(error, "preact_cleanup", None)
        if payload is not None:
            try:
                return CleanupEvidence.model_validate(payload).model_dump(exclude_none=True)
            except ValidationError:
                return None
        error = error.__cause__
        if error is None:
            break
    return None
