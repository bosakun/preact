"""Executable bounded queue dynamics, not cloud or physical-model validation."""

from preact.core.evidence import task_definitions
from preact.core.models import (
    Action,
    Capabilities,
    ClaimDefinition,
    ClaimRequirement,
    ClaimResult,
    Estimate,
    EvidenceKind,
    Observation,
    Prediction,
    State,
    Task,
)


class QueueWorld:
    dynamics = {
        "name": "bounded-queue",
        "version": "1",
        "unit": "system_tick",
        "agent_interventions": "none",
    }
    future = ClaimDefinition(
        namespace="queue",
        name="no_overflow",
        version="v1",
        kind="check",
        temporal="through_horizon",
    )

    def __init__(self):
        self.task = Task(
            id="new-queue-consequence-v1",
            domain="queue",
            title="Enable bounded ingestion",
            goal="Enable ingestion without later overflow",
            required_checks=["capacity"],
            future_requirements=[
                ClaimRequirement(
                    definition=self.future, horizon=3, conditions={"dynamics": self.dynamics}
                )
            ],
        )
        self.payload = {"inventory": 0, "inflow": 0, "tick": 0, "enabled": False}
        self.executed = []

    async def observe(self):
        return State.create("queue", dict(self.payload), "actual-queue")

    async def propose(self, state, width):
        return [
            Action(name=name, kind="enable", state_id=state.id, payload={"inflow": rate})
            for name, rate in [("Fast ingestion", 3), ("Bounded ingestion", 1)][:width]
        ]

    def validate(self, state, action):
        if (
            action.state_id != state.id
            or action.kind != "enable"
            or action.payload["inflow"] not in (1, 3)
        ):
            raise ValueError("Invalid queue intervention")

    def materialize(self, state, action):
        self.validate(state, action)
        return self.advance(state, action.payload["inflow"])

    def advance(self, state, inflow=None):
        payload = {**state.payload, "tick": state.payload["tick"] + 1, "enabled": True}
        if inflow is not None:
            payload["inflow"] = inflow
        payload["inventory"] = max(0, payload["inventory"] + payload["inflow"] - 1)
        return State.create(
            "queue", payload, "executable-queue-dynamics", kind="hypothetical", parent_id=state.id
        )

    def complete(self, state):
        return state.payload["enabled"]

    async def execute(self, action, receipt):
        before = await self.observe()
        self.payload = self.materialize(before, action).payload
        self.executed.append(action)
        return Observation(
            state=await self.observe(),
            success=True,
            unsafe=self.payload["inventory"] > 3,
            checks={"capacity": self.payload["inventory"] <= 3},
            receipt=receipt,
            cost_usd=0,
        )


def exact(value):
    return Estimate(
        value=float(value), lower=float(value), upper=float(value), measured=True, uncertainty=0
    )


class QueueEngine:
    def __init__(self, world, future=False):
        self.world, self.future, self.requests = world, future, []
        self.capabilities = Capabilities(
            engine_id="queue-consequences" if future else "queue-immediate",
            version="1",
            family="queue-dynamics",
            domains=["queue"],
            evidence=EvidenceKind.SIMULATION,
            tier=2 if future else 1,
            roles=["simulator", "verifier"],
            produces_successor=True,
            applicability="Exact bounded queue dynamics fixture",
            max_horizon=3 if future else 1,
            supported_claims=[world.future] if future else task_definitions(world.task),
            verification_checks=[] if future else world.task.required_checks,
            claim_contract_version="1",
        )

    async def predict(self, request):
        self.requests.append(request)
        first = self.world.materialize(request.state, request.actions[0])
        common = dict(
            engine_id=self.capabilities.engine_id,
            engine_version="1",
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[request.actions[0].id],
            horizon=request.horizon,
            evidence=EvidenceKind.SIMULATION,
        )
        if not self.future:
            safe = first.payload["inventory"] <= 3
            return Prediction(
                **common,
                success=exact(safe),
                risk=exact(not safe),
                mandatory_checks={"capacity": safe},
                successor=first,
            )
        states = [first]
        for _ in range(request.horizon - 1):
            states.append(self.world.advance(states[-1]))
        safe = all(s.payload["inventory"] <= 3 for s in states)
        return Prediction(
            **common,
            future_states=states,
            claim_results=[
                ClaimResult(claim=c, check=safe, severity=0 if safe else 1, reversible=safe)
                for c in request.claims
            ],
        )
