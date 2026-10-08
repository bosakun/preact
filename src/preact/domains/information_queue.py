"""Versioned queue sensing: an externally executed, costly one-tick drain plus sample."""

import math

from preact.core.models import Action, ClaimDefinition, ClaimRequirement, Observation, State
from preact.domains.cognitive_queue import DYNAMICS, CognitiveQueueWorld, transition

DOMAIN = "cognitive_queue_v2"
PROVENANCE = "actual-cognitive-queue/v2"
NO_OVERFLOW = ClaimDefinition(
    namespace="cognitive-queue",
    name="no_overflow",
    version="v2",
    kind="check",
    temporal="through_horizon",
)
DYNAMICS_V2 = {
    **DYNAMICS,
    "version": "2",
    "service_values": [1, 3],
    "probe_service": "one tick, no submissions, sample the executed tick only",
}


def action_amount(state: State, action: Action) -> int:
    """Validate the v2 intervention contract, without inspecting private World state."""
    if (
        state.domain != DOMAIN
        or action.state_id != state.id
        or action.duration != 1
        or state.payload["tick"] >= state.payload["episode_ticks"]
    ):
        raise ValueError("Invalid or stale information-queue intervention")
    if action.kind == "probe_service" and action.payload == {}:
        return 0
    if (
        action.kind == "submit"
        and set(action.payload) == {"amount"}
        and type(action.payload["amount"]) is int
        and action.payload["amount"] in (0, 1, 3)
    ):
        return action.payload["amount"]
    raise ValueError("Invalid information-queue Action contract")


def bounded_safe(payload: dict, amount: int, horizon: int = 3) -> bool:
    """Public service>=1 occupancy bound, independent of any learned sample."""
    for tick in range(horizon):
        payload, _, _ = transition(payload, amount if tick == 0 else 0, 1)
        if payload["queue"] > payload["capacity"]:
            return False
    return True


def reward(payload: dict, processed: float, amount: int, *, probe: bool = False) -> float:
    return (
        processed
        - payload["holding_cost"] * payload["queue"]
        - payload["submission_cost"] * amount
        - (payload["probe_cost"] if probe else 0)
    )


class InformationQueueWorld(CognitiveQueueWorld):
    """Opt-in v2. Sensing is separate from fresh, passive State observations.

    Monetary API cost remains zero in this CPU environment. probe_cost is in the
    declared reward units, in addition to time and the opportunity not to submit.
    Normal actions/observations never include a hidden service measurement.
    """

    def __init__(
        self,
        *args,
        probe_cost: float = 0.05,
        holding_cost: float = 0.25,
        submission_cost: float = 0.1,
        **kwargs,
    ):
        if any(not math.isfinite(c) or c < 0 for c in (probe_cost, holding_cost, submission_cost)):
            raise ValueError("Reward costs must be finite and nonnegative")
        self.probe_cost, self.holding_cost, self.submission_cost = (
            probe_cost,
            holding_cost,
            submission_cost,
        )
        super().__init__(*args, **kwargs)
        self.task = self.task.model_copy(
            deep=True,
            update={
                "id": "cognitive-queue-v2",
                "domain": DOMAIN,
                "title": "Sustain throughput with optional costly service sensing",
                "future_requirements": [
                    ClaimRequirement(
                        definition=NO_OVERFLOW, horizon=3, conditions={"dynamics": DYNAMICS_V2}
                    )
                ],
            },
        )

    def reset(self) -> None:
        super().reset()
        self.payload.update(
            probe_cost=self.probe_cost,
            holding_cost=self.holding_cost,
            submission_cost=self.submission_cost,
            service_values=[1, 3],
        )

    async def observe(self) -> State:
        base = await super().observe()
        return State.create(self.task.domain, base.model_copy(deep=True).payload, PROVENANCE)

    @staticmethod
    def probe(state: State, *, rationale: str = "") -> Action:
        return Action(
            name="Probe current service",
            kind="probe_service",
            state_id=state.id,
            payload={},
            duration=1,
            rationale=rationale,
        )

    def validate(self, state: State, action: Action) -> None:
        action_amount(state, action)

    async def execute(self, action: Action, receipt: str) -> Observation:
        if (
            not isinstance(receipt, str)
            or len(receipt) != 64
            or any(c not in "0123456789abcdef" for c in receipt)
        ):
            raise ValueError("Execution requires a normal durable receipt identifier")
        state = await self.observe()
        amount = action_amount(state, action)
        measured_tick = state.payload["tick"]
        # The executor alone reads the private schedule, after validating the action.
        service = self._service[measured_tick]
        self.payload, available, processed = transition(self.payload, amount, service)
        probe = action.kind == "probe_service"
        self.unsafe = self.payload["queue"] > self.capacity
        value = reward(self.payload, processed, amount, probe=probe)
        self.reward += value
        self.executed.append(action.model_copy(deep=True))
        metrics = {
            "available_work": available,
            "processed": processed,
            "reward": value,
            "holding_loss": self.holding_cost * self.payload["queue"],
            "submission_cost": self.submission_cost * amount,
            "probe_cost": self.probe_cost if probe else 0,
        }
        checks = {"capacity": not self.unsafe, "action_success": True}
        if probe:
            metrics.update(measured_service=service, measurement_tick=measured_tick)
            checks["probe_success"] = True
        return Observation(
            state=await self.observe(),
            success=self.complete(await self.observe()),
            unsafe=self.unsafe,
            checks=checks,
            metrics=metrics,
            receipt=receipt,
            cost_usd=0,
        )
