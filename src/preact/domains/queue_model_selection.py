"""Fresh-observation fences for opt-in model-selected proposals, never a Gate."""

from datetime import datetime
from time import time as wall_time

from preact.cognition.queue_model import QueueModelPlanner
from preact.core.models import Action, Observation, State
from preact.domains.information_queue import InformationQueueWorld


class QueueSelectionWorld:
    def __init__(self, world: InformationQueueWorld, planner: QueueModelPlanner):
        if planner._world_bound:
            raise ValueError("A selection planner belongs to one actual World episode")
        self.world, self.planner = world, planner
        self.task = world.task.model_copy(deep=True)
        planner._world_bound = True
        self.observation_calls = 0

    def _task_check(self):
        if self.world.task != self.task:
            raise ValueError("Selection World task changed")

    async def observe(self) -> State:
        self._task_check()
        self.observation_calls += 1
        state = State.model_validate((await self.world.observe()).model_dump())
        await self.planner.check(state)
        self._task_check()
        return state

    def validate(self, state: State, action: Action):
        self._task_check()
        self.world.validate(state, action)

    async def execute(self, action: Action, receipt: str) -> Observation:
        self._task_check()
        if self.planner.pending is None:
            raise ValueError("No current selection; reconcile rather than retry")
        # This is an additional real observation, not a timestamp-adjusted snapshot.
        state = await self.observe()
        await self.planner.check(state, action)
        execution = await self.planner.source.store.call("execution_record", receipt)
        if execution is None or any(
            execution.get(key) != value
            for key, value in {
                "run_id": self.planner.run_id,
                "state_id": state.id,
                "action_hash": action.fingerprint,
                "status": "pending",
            }.items()
        ):
            raise ValueError("Dispatch requires Runtime's durable intent binding")
        events = await self.planner.source.store.call("read_events", self.planner.run_id)
        intents = [e for e in events if e["kind"] == "execution_intent"]
        if (
            len(intents) != 1
            or intents[0]["data"]["receipt"] != receipt
            or datetime.fromisoformat(state.timestamp)
            < datetime.fromisoformat(intents[0]["timestamp"])
        ):
            raise ValueError("Dispatch observation must be fresh after the durable intent")
        authorization = [e["data"] for e in events if e["kind"] == "authorization"]
        if len(authorization) != 1 or any(
            authorization[-1].get(key) != value
            for key, value in {
                "decision": "execute",
                "state_id": state.id,
                "action_hash": action.fingerprint,
            }.items()
        ):
            raise ValueError("Selection cannot replace Runtime authorization")
        await self.planner.source.store.call(
            "append",
            self.planner.run_id,
            "cognitive_dispatch_binding",
            {
                "selection_state": self.planner.pending["state"],
                "fresh_state": state.model_dump(),
                "action_fingerprint": action.fingerprint,
                "receipt": receipt,
            },
        )
        await self.planner.check(state, action)
        if wall_time() >= authorization[0]["expires_at"]:
            raise ValueError("Runtime authorization expired during dispatch checks")
        # Clear before the actual side effect; post-execution observations are new facts.
        self.planner.pending = None
        self._task_check()
        return await self.world.execute(action, receipt)

    def complete(self, state: State) -> bool:
        self._task_check()
        return self.world.complete(state)
