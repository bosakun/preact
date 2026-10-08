from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from preact.core.models import Action, Contract, Observation, State


class Goal(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    name: str
    metric: str
    target: float
    priority: int = 0

    def progress(self, state: State) -> float:
        return float(state.payload.get(self.metric, 0))


class Inference(Contract):
    value: float
    lower: float
    upper: float
    status: Literal["estimated", "hypothesis"]
    source_refs: list[str] = Field(default_factory=list)


class Belief(Contract):
    """Facts stay in the observed State; estimates cannot enter its payload."""

    observed: State
    inferred: dict[str, Inference] = Field(default_factory=dict)
    unknown: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def actual_perception(self):
        if self.observed.kind != "observed":
            raise ValueError("Belief facts require authoritative observed state")
        return self


class Experience(Contract):
    reference: str
    run_id: str
    input_state: State
    action: Action
    prediction_ids: list[str]
    observation: Observation


@dataclass(frozen=True)
class BeliefReusePolicy:
    """Planner opt-in promise: infer is pure for the keyed inputs and this token.

    The token must bind every relevant setting, algorithm and internal state. Any
    external time/randomness/side effect not represented by the inputs forbids reuse.
    Ignoring State.timestamp requires the additional timestamp_independent promise.
    Concrete subclasses must redeclare this capability; inheritance is not consent.
    """

    token: str
    timestamp_independent: bool = False


class CognitivePlanner(Protocol):
    def infer(self, state: State, experience: list[Experience]) -> Belief: ...
    def propose(self, belief: Belief, goals: list[Goal], width: int) -> list[Action]: ...


class CognitiveResult(Contract):
    success: bool
    unsafe: bool
    status: str
    rounds: list[dict[str, Any]]
    run_ids: list[str]
    final_state: State
