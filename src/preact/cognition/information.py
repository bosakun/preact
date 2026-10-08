"""Small two-state Bayesian model and auditable, single-probe finite-horizon VOI.

This planner sees public State and receipt-validated Experience only. Its persistence
is a model assumption, not access to the environment's regime or future schedule.
"""

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Literal

from preact.core.models import Action, State
from preact.domains.cognitive_queue import CognitiveQueueWorld, transition
from preact.domains.information_queue import (
    DOMAIN,
    InformationQueueWorld,
    action_amount,
    bounded_safe,
    reward,
)

from .models import Belief, Experience, Goal, Inference

Strategy = Literal["no_probe", "periodic", "uncertainty", "voi"]


@dataclass(frozen=True)
class InformationModel:
    prior_high: float = 0.5
    persistence: float = 0.9
    horizon: int = 6
    period: int = 4
    uncertainty_threshold: float = 0.75

    def __post_init__(self):
        if (
            type(self.horizon) is not int
            or type(self.period) is not int
            or not 0 < self.prior_high < 1
            or not 0.5 <= self.persistence < 1
            or not 2 <= self.horizon <= 8
            or self.period < 1
            or not 0 <= self.uncertainty_threshold <= 1
        ):
            raise ValueError("Invalid finite-horizon information model")

    def advance(self, p: float, ticks: int = 1) -> float:
        return 0.5 + (p - 0.5) * (2 * self.persistence - 1) ** ticks


@dataclass(frozen=True)
class InformationDecision:
    normal_values: dict[int, float]
    information_gain: float
    opportunity_cost: float
    measurement_cost: float
    net_voi: float
    uncertainty: float
    action_change_probability: float
    following_actions: tuple[int, int]
    request_probe: bool
    model: dict


class InformationQueuePlanner:
    """Stateless tick-dependent inference; deliberately opts OUT of belief reuse.

    No capability is declared, including for subclasses. Even when an agent enables
    reuse, fresh retrieval and inference run normally for this new algorithm.
    """

    def __init__(self, strategy: Strategy = "voi", *, model: InformationModel | None = None):
        if strategy not in {"no_probe", "periodic", "uncertainty", "voi"}:
            raise ValueError("Unknown information-seeking strategy")
        self.strategy, self.model = strategy, model or InformationModel()

    def infer(self, state: State, experience: list[Experience]) -> Belief:
        if state.domain != DOMAIN or state.payload["service_values"] != [1, 3]:
            raise ValueError("Information planner requires the explicit v2 service model")
        p, last_tick = self.model.prior_high, 0
        sources, probe_sources, seen = [], [], set()
        current_tick = state.payload["tick"]
        for record in sorted(
            experience, key=lambda r: (r.input_state.payload["tick"], r.reference)
        ):
            if (
                record.input_state.domain != DOMAIN
                or record.input_state.provenance != state.provenance
                or record.observation.state.provenance != state.provenance
            ):
                continue
            tick = record.input_state.payload["tick"]
            if tick >= current_tick or record.observation.receipt in seen:
                continue
            seen.add(record.observation.receipt)
            if record.observation.state.payload["tick"] != tick + 1 or tick < last_tick:
                raise ValueError("Learning requires aligned one-tick execution observations")
            p = self.model.advance(p, tick - last_tick)
            obs = record.observation
            metrics = obs.metrics
            probe = record.action.kind == "probe_service"
            if obs.checks.get("action_success") is not True or (probe and obs.unsafe):
                # A committed failed execution is experience, but not a valid sample.
                last_tick = tick
                continue
            amount = action_amount(record.input_state, record.action)
            _, available, _ = transition(record.input_state.payload, amount, 1)
            if metrics["available_work"] != available:
                raise ValueError("Learning sample has inconsistent available work")
            processed = metrics["processed"]
            if probe and obs.checks.get("probe_success") is True:
                value = metrics.get("measured_service")
                if (
                    value not in (1, 3)
                    or metrics.get("measurement_tick") != tick
                    or processed != min(available, value)
                ):
                    raise ValueError("Probe sample is invalid or claims a future tick")
                compatible = [value]
                probe_sources.append(record.reference)
            elif probe:
                # Failed probes do not masquerade as ordinary work samples either.
                last_tick = tick
                continue
            else:
                compatible = [s for s in (1, 3) if min(available, s) == processed]
            if not compatible:
                raise ValueError("Service observation is outside the declared binary model")
            if not any(
                transition(record.input_state.payload, amount, s)[0] == obs.state.payload
                for s in compatible
            ):
                raise ValueError("Service sample disagrees with executed queue dynamics")
            if len(compatible) == 1:
                p = float(compatible[0] == 3)
                sources.append(record.reference)
            last_tick = tick
        p = self.model.advance(p, current_tick - last_tick)
        status = "estimated" if sources else "hypothesis"
        return Belief(
            observed=state,
            inferred={
                "service": Inference(
                    value=1 + 2 * p, lower=1, upper=3, status=status, source_refs=sources
                ),
                "service_high_probability": Inference(
                    value=p, lower=0, upper=1, status=status, source_refs=sources
                ),
                "probe_samples": Inference(
                    value=len(probe_sources),
                    lower=0,
                    upper=len(probe_sources),
                    status="estimated",
                    source_refs=probe_sources,
                ),
            },
            unknown=["current_service", "future_service", "change_time"],
        )

    def decide(self, belief: Belief, goals: list[Goal]) -> InformationDecision:
        state, model = belief.observed, self.model
        if state.domain != DOMAIN:
            raise ValueError("Information decisions require v2 State")
        payload = state.payload
        p = belief.inferred["service_high_probability"].value
        active = next((g for g in goals if g.progress(state) < g.target), None)
        if active is not None and active.metric != "delivered":
            raise ValueError("The queue VOI model supports delivered-job goals only")
        target = active.target if active else payload["delivered"]
        horizon = min(model.horizon, payload["episode_ticks"] - payload["tick"])

        def unpack(tick, queue, pending, delivered):
            return {
                **payload,
                "tick": tick,
                "queue": queue,
                "delivered": delivered,
                "pending": [{"due": due, "amount": n} for due, n in pending],
            }

        def key(value):
            return (
                value["tick"],
                value["queue"],
                tuple((i["due"], i["amount"]) for i in value["pending"]),
                value["delivered"],
            )

        def posterior(probability, available, processed):
            return float(processed == 3 or processed > 1) if available > 1 else probability

        def branches(value, amount, probability):
            for service, weight in ((1, 1 - probability), (3, probability)):
                next_state, available, processed = transition(value, amount, service)
                after = model.advance(posterior(probability, available, processed))
                yield weight, next_state, processed, after, model.advance(float(service == 3))

        @lru_cache(maxsize=None)
        def solve(tick, queue, pending, delivered, probability, remaining):
            if remaining == 0:
                return 0.0, 0
            value = unpack(tick, queue, pending, delivered)
            choices = (3, 1, 0) if delivered < target else (0,)
            scores = {}
            for amount in choices:
                if not bounded_safe(value, amount):
                    continue
                scores[amount] = sum(
                    weight
                    * (
                        reward(next_state, processed, amount)
                        + solve(*key(next_state), after, remaining - 1)[0]
                    )
                    for weight, next_state, processed, after, _ in branches(
                        value, amount, probability
                    )
                )
            if not scores:
                return -1000.0, 0  # Unavoidable bound failure; never an authorization.
            chosen = max(scores, key=lambda n: (scores[n], n))
            return scores[chosen], chosen

        normal_values = {}
        for amount in (3, 1, 0):
            normal_values[amount] = sum(
                weight
                * (
                    reward(next_state, processed, amount)
                    + solve(*key(next_state), after, max(0, horizon - 1))[0]
                )
                for weight, next_state, processed, after, _ in branches(payload, amount, p)
            )
        feasible = [n for n in (3, 1, 0) if bounded_safe(payload, n)]
        normal_best = (
            max(normal_values[n] for n in feasible) if active and feasible else normal_values[0]
        )
        probe_value, change_probability, following = 0.0, 0.0, []
        for weight, next_state, processed, after, measured in branches(payload, 0, p):
            measured_value, measured_action = solve(*key(next_state), measured, max(0, horizon - 1))
            _, normal_action = solve(*key(next_state), after, max(0, horizon - 1))
            following.append(measured_action)
            change_probability += weight * (measured_action != normal_action)
            probe_value += weight * (reward(next_state, processed, 0, probe=True) + measured_value)
        gain = max(0.0, probe_value + payload["probe_cost"] - normal_values[0])
        opportunity = max(0.0, normal_best - normal_values[0])
        net = gain - opportunity - payload["probe_cost"]
        uncertainty = 4 * p * (1 - p)
        if self.strategy == "periodic":
            request = payload["tick"] % model.period == 0
        elif self.strategy == "uncertainty":
            request = uncertainty > model.uncertainty_threshold
        else:
            request = (
                self.strategy == "voi"
                and active is not None
                and horizon > 1
                and bounded_safe(payload, 0)
                and net > 1e-9
                and change_probability > 1e-9
            )
        return InformationDecision(
            normal_values,
            gain,
            opportunity,
            payload["probe_cost"],
            net,
            uncertainty,
            change_probability,
            tuple(following),
            request,
            asdict(model),
        )

    def propose(self, belief: Belief, goals: list[Goal], width: int) -> list[Action]:
        decision = self.decide(belief, goals)
        state = belief.observed
        active = any(g.progress(state) < g.target for g in goals)
        order = sorted((3, 1, 0), key=lambda n: (-decision.normal_values[n], -n))
        if not active:
            order = [0, *[n for n in order if n != 0]]
        rationale = json.dumps({"strategy": self.strategy, **asdict(decision)}, sort_keys=True)
        actions = [
            CognitiveQueueWorld.action(state, n).model_copy(update={"rationale": rationale})
            for n in order
        ]
        if self.strategy != "no_probe":
            candidate = InformationQueueWorld.probe(state, rationale=rationale)
            actions = [candidate, *actions] if decision.request_probe else [*actions, candidate]
        return actions[:width]
