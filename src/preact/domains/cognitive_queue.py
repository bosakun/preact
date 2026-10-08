"""A CPU persistent queue with delayed arrivals and privately changing service.

No engine receives the random service schedule. Public bounds are part of the
environment contract, not inferred safety certificates.
"""

import random

from preact.core.models import (
    Action,
    ClaimDefinition,
    ClaimRequirement,
    Observation,
    State,
    Task,
)

NO_OVERFLOW = ClaimDefinition(
    namespace="cognitive-queue",
    name="no_overflow",
    version="v1",
    kind="check",
    temporal="through_horizon",
)
DYNAMICS = {
    "name": "delayed-partially-observed-queue",
    "version": "1",
    "unit": "system_tick",
    "agent_interventions": "none",
    "service_bounds": [1, 3],
    "arrival_delay": 2,
}


def transition(payload: dict, amount: int, service: float) -> tuple[dict, float, float]:
    """The public dynamics; callers choose their own service hypothesis."""
    tick = payload["tick"] + 1
    pending = [dict(item) for item in payload["pending"]]
    if amount:
        pending.append({"due": tick + 1, "amount": amount})
    arrived = sum(item["amount"] for item in pending if item["due"] <= tick)
    available = payload["queue"] + arrived
    processed = min(available, service)
    queue = available - processed
    return (
        {
            **payload,
            "tick": tick,
            "queue": queue,
            "pending": [item for item in pending if item["due"] > tick],
            "delivered": payload["delivered"] + processed,
        },
        available,
        processed,
    )


class CognitiveQueueWorld:
    def __init__(
        self,
        seed: int = 0,
        *,
        ticks: int = 60,
        target: int = 85,
        shift_tick: int | None = None,
        high_first: bool = True,
        noise: float = 0.1,
        capacity: int = 8,
    ):
        shift_tick = ticks // 2 if shift_tick is None else shift_tick
        if ticks < 1 or not 0 <= shift_tick <= ticks or not 0 <= noise <= 1 or capacity < 3:
            raise ValueError("Invalid queue configuration")
        self.seed, self.ticks, self.target = seed, ticks, target
        self.shift_tick, self.high_first, self.noise = shift_tick, high_first, noise
        self.capacity = capacity
        self.task = Task(
            id="cognitive-queue-v1",
            domain="cognitive_queue",
            title="Sustain throughput with bounded queue occupancy",
            goal=f"Deliver at least {target} jobs in {ticks} ticks without overflow",
            required_checks=["capacity"],
            max_steps=1,
            seed=seed,
            future_requirements=[
                ClaimRequirement(
                    definition=NO_OVERFLOW, horizon=3, conditions={"dynamics": DYNAMICS}
                )
            ],
        )
        self.reset()

    def reset(self) -> None:
        rng = random.Random(self.seed)
        self._service = []
        for tick in range(self.ticks):
            high = self.high_first if tick < self.shift_tick else not self.high_first
            if rng.random() < self.noise:
                high = not high
            self._service.append(3 if high else 1)
        self.payload = {
            "tick": 0,
            "queue": 0,
            "pending": [],
            "delivered": 0,
            "capacity": self.capacity,
            "service_bounds": [1, 3],
            "arrival_delay": 2,
            "episode_ticks": self.ticks,
            "target": self.target,
        }
        self.executed: list[Action] = []
        self.reward = 0.0
        self.unsafe = False

    async def observe(self) -> State:
        # Isolate mutable payload so a prediction cannot modify authority.
        return State.create(
            self.task.domain,
            {**self.payload, "pending": [dict(item) for item in self.payload["pending"]]},
            "actual-cognitive-queue/v1",
        )

    async def propose(self, state: State, width: int) -> list[Action]:
        return [self.action(state, n) for n in (3, 1, 0)][:width]

    @staticmethod
    def action(state: State, amount: int) -> Action:
        return Action(
            name=f"Submit {amount} jobs" if amount else "Drain queue",
            kind="submit",
            state_id=state.id,
            payload={"amount": amount},
        )

    def validate(self, state: State, action: Action) -> None:
        if (
            state.domain != self.task.domain
            or action.state_id != state.id
            or action.kind != "submit"
            or set(action.payload) != {"amount"}
            or type(action.payload["amount"]) is not int
            or action.payload["amount"] not in (0, 1, 3)
            or state.payload["tick"] >= self.ticks
        ):
            raise ValueError("Invalid or stale queue intervention")

    def complete(self, state: State) -> bool:
        return state.payload["tick"] >= self.ticks and state.payload["delivered"] >= self.target

    async def execute(self, action: Action, receipt: str) -> Observation:
        state = await self.observe()
        self.validate(state, action)
        service = self._service[self.payload["tick"]]
        self.payload, available, processed = transition(
            self.payload, action.payload["amount"], service
        )
        self.unsafe = self.payload["queue"] > self.capacity
        self.reward += processed - 0.25 * self.payload["queue"] - 0.1 * action.payload["amount"]
        self.executed.append(action.model_copy(deep=True))
        return Observation(
            state=await self.observe(),
            success=self.complete(await self.observe()),
            unsafe=self.unsafe,
            checks={"capacity": not self.unsafe, "action_success": True},
            metrics={
                "available_work": available,
                "processed": processed,
                "reward": processed - 0.25 * self.payload["queue"] - 0.1 * action.payload["amount"],
            },
            receipt=receipt,
            cost_usd=0,
        )
