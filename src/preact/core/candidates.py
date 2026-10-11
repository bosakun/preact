"""Opt-in, flat root selection over Runtime-owned Gate assessments.

Selectors are trusted in-process extensions, not sandboxed plugins. They must do
no prediction, external calls, execution or Store writes. A choice confers no
authorization: Runtime still observes, gates and binds the action before dispatch.
"""

from typing import Literal, Protocol

from .models import Action, Contract, Evaluation, GateResult, Prediction, State


class CandidateAssessment(Contract):
    action: Action
    node_id: str | None = None
    status: Literal["unexpanded", "invalid_contract", "qualified", "rejected", "unresolved"]
    verification_performed: bool = False
    predictions: list[Prediction] = []
    evaluation: Evaluation | None = None
    gate: GateResult | None = None


class CandidateBatch(Contract):
    run_id: str
    state: State
    candidates: list[CandidateAssessment]

    @property
    def complete(self) -> bool:
        return bool(self.candidates) and all(
            c.status in {"invalid_contract", "qualified", "rejected"} for c in self.candidates
        )

    @property
    def qualified_ids(self) -> tuple[str, ...]:
        return tuple(c.node_id for c in self.candidates if c.status == "qualified")


class CandidateSelector(Protocol):
    """Return only one qualified node ID from a defensive copy, synchronously.

    All costly inference belongs in the accounted proposer, not in this callback.
    Exceptions, mutations and invalid choices fail closed without utility fallback.
    """

    def select(self, batch: CandidateBatch) -> str: ...
