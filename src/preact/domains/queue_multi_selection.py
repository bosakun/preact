"""Dispatch fences for a final Runtime choice, separate from prediction ranking."""

from datetime import datetime
from time import time as wall_time

from preact.cognition.queue_multi import QueueMultiPlanner
from preact.domains.information_queue import InformationQueueWorld


class QueueMultiSelectionWorld:
    def __init__(self, world: InformationQueueWorld, planner: QueueMultiPlanner):
        if planner._world_bound:
            raise ValueError("One planner belongs to one actual World episode")
        self.world, self.planner = world, planner
        self.task = world.task.model_copy(deep=True)
        self.observations = 0
        planner._world_bound = True

    def _task_check(self):
        if self.world.task != self.task:
            raise ValueError("World Task changed")

    async def observe(self):
        self._task_check()
        self.observations += 1
        state = (await self.world.observe()).model_copy(deep=True)
        await self.planner.check(state)
        self._task_check()
        return state

    def validate(self, state, action):
        self._task_check()
        self.world.validate(state, action)

    async def execute(self, action, receipt):
        self._task_check()
        if self.planner.pending is None:
            raise ValueError("No current prediction and final selection")
        state = await self.observe()
        await self.planner.check(state, action)
        execution = await self.planner.source.store.call("execution_record", receipt)
        if execution is None or any(
            execution.get(k) != v
            for k, v in {
                "run_id": self.planner.run_id,
                "state_id": state.id,
                "action_hash": action.fingerprint,
                "status": "pending",
            }.items()
        ):
            raise ValueError("Dispatch requires Runtime's durable intent")
        events = await self.planner.source.store.call("read_events", self.planner.run_id)
        intents = [e for e in events if e["kind"] == "execution_intent"]
        authorizations = [e["data"] for e in events if e["kind"] == "authorization"]
        if (
            len(intents) != 1
            or intents[0]["data"]["receipt"] != receipt
            or datetime.fromisoformat(state.timestamp)
            < datetime.fromisoformat(intents[0]["timestamp"])
            or len(authorizations) != 1
            or any(
                authorizations[0].get(k) != v
                for k, v in {
                    "decision": "execute",
                    "state_id": state.id,
                    "action_hash": action.fingerprint,
                }.items()
            )
        ):
            raise ValueError("Fresh dispatch or final authorization binding changed")
        await self.planner.source.store.call(
            "append",
            self.planner.run_id,
            "cognitive_multi_dispatch",
            {
                "prediction_hash": self.planner._seal,
                "fresh_state": state.model_dump(),
                "action_fingerprint": action.fingerprint,
                "receipt": receipt,
            },
        )
        await self.planner.check(state, action)
        if wall_time() >= authorizations[0]["expires_at"]:
            raise ValueError("Runtime authorization expired during dispatch checks")
        self.planner.pending = None
        self._task_check()
        return await self.world.execute(action, receipt)

    def complete(self, state):
        self._task_check()
        return self.world.complete(state)
