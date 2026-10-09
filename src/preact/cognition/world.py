"""Native World proposals with receipt-grounded retry ordering, independent of queue."""

from typing import Protocol

from preact.core.interfaces import World
from preact.core.models import Action, State, identity

from .models import Belief, Experience, Goal, Inference


class CandidateGenerator(Protocol):
    """Same proposal/accounting contract as World; no execution authority.

    External work must declare proposal_calls and supply usage receipts, as for World.
    A successful generator may certify proposal_usage_complete. Core owns budgets.
    """

    async def propose(self, state: State, width: int) -> list[Action]: ...


def attempt_key(action: Action) -> str:
    # State context is matched separately; random Action IDs do not identify a retry.
    return "failed_attempts:" + identity(
        {"kind": action.kind, "payload": action.payload, "duration": action.duration}
    )


class WorldPlanner:
    """Use native candidates and optionally demote unsuccessful same-state retries.

    Memory inputs must come from EpisodicMemory. The ordering is a soft heuristic,
    not probability, verification or permission to execute. No candidates are deleted
    and no tests/oracles are called here. Native hypothetical proposals preserve Core
    search without creating Beliefs or learning from unexecuted branches.

    This adapter deliberately does not opt in to Belief Reuse: a supplied generator
    may be stateful, and no general purity promise can be established for it.
    """

    def __init__(
        self,
        world: World,
        candidate_generator: CandidateGenerator | None = None,
        *,
        avoid_failed_retries: bool = True,
    ):
        self.generator = candidate_generator if candidate_generator is not None else world
        self.avoid_failed_retries = avoid_failed_retries

    @property
    def proposal_calls(self):
        return getattr(self.generator, "proposal_calls", 0)

    @property
    def proposal_usage_complete(self):
        return getattr(self.generator, "proposal_usage_complete", False)

    @property
    def usage(self):
        return getattr(self.generator, "usage", None)

    def infer(self, state: State, experience: list[Experience]) -> Belief:
        inferred: dict[str, Inference] = {}
        if self.avoid_failed_retries:
            for record in experience:
                if (
                    record.input_state.domain != state.domain
                    or record.input_state.id != state.id
                    or record.input_state.payload != state.payload
                    or record.input_state.provenance != state.provenance
                    or record.observation.success
                ):
                    continue
                key = attempt_key(record.action)
                previous = inferred.get(key)
                count = previous.value + 1 if previous else 1
                inferred[key] = Inference(
                    value=count,
                    lower=count,
                    upper=count,
                    status="estimated",
                    source_refs=[*(previous.source_refs if previous else []), record.reference],
                )
        return Belief(
            observed=state.model_copy(deep=True),
            inferred=inferred,
            unknown=["unexecuted_candidate_outcomes", "fresh_verification_required"],
        )

    async def propose(self, belief: Belief, goals: list[Goal], width: int) -> list[Action]:
        # Goals remain external soft objectives; native task semantics own proposals.
        candidates = await self._propose(belief.observed, width)
        return sorted(
            candidates,
            key=lambda action: (
                belief.inferred[attempt_key(action)].value
                if attempt_key(action) in belief.inferred
                else 0
            ),
        )

    async def propose_hypothetical(self, state: State, width: int) -> list[Action]:
        if state.kind != "hypothetical":
            raise ValueError("Hypothetical proposal requires a hypothetical State")
        return await self._propose(state, width)

    async def _propose(self, state: State, width: int) -> list[Action]:
        isolated = state.model_copy(deep=True)
        candidates = await self.generator.propose(isolated, width)
        if isolated != state:
            raise ValueError("Candidate generator mutated its State input")
        return candidates
