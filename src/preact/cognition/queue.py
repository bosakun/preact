"""Small observed-data estimator and planner; no access to private dynamics."""

from preact.core.models import Action, State
from preact.domains.cognitive_queue import CognitiveQueueWorld, transition

from .models import Belief, Experience, Goal, Inference


class QueuePlanner:
    def __init__(self, *, adaptation: bool = True, prior: float = 3):
        if not 1 <= prior <= 3:
            raise ValueError("Prior must fit public service bounds")
        self.adaptation, self.prior = adaptation, prior

    def infer(self, state: State, experience: list[Experience]) -> Belief:
        estimate, sources = self.prior, []
        if self.adaptation:
            for record in experience:
                if record.observation.state.provenance != state.provenance:
                    continue
                metrics = record.observation.metrics
                # If demand is smaller than the maximum capacity, completion is
                # censored: do not learn "capacity=1" just because only 1 job existed.
                available, processed = metrics["available_work"], metrics["processed"]
                if processed < available or available >= state.payload["service_bounds"][1]:
                    estimate = 0.5 * estimate + 0.5 * processed
                    sources.append(record.reference)
        return Belief(
            observed=state,
            inferred={
                "service": Inference(
                    value=estimate,
                    lower=1,
                    upper=3,
                    status="estimated" if sources else "hypothesis",
                    source_refs=sources,
                )
            },
            unknown=["current_service", "future_service", "change_time"],
        )

    def propose(self, belief: Belief, goals: list[Goal], width: int) -> list[Action]:
        state, service = belief.observed, belief.inferred["service"].value
        active = next((g for g in goals if g.progress(state) < g.target), None)
        if active is None:
            order = (0, 1, 3)
        else:
            # Receding-horizon value is an inference, never measured gate evidence.
            # Holding cost makes excessive admission unattractive at slow service.
            scores = {}
            for amount in (3, 1, 0):
                payload = state.payload
                value = -0.1 * amount
                for horizon in range(6):
                    payload, _, processed = transition(
                        payload, amount if horizon == 0 else 0, service
                    )
                    value += processed - 0.25 * payload["queue"]
                scores[amount] = value
            order = sorted(scores, key=lambda n: (-scores[n], -n))
        return [CognitiveQueueWorld.action(state, amount) for amount in order][:width]
