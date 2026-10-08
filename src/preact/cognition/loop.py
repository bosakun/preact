from preact.core.interfaces import World
from preact.core.models import Action, Observation, Policy, State, uid
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store

from .memory import EpisodicMemory
from .models import Belief, CognitivePlanner, CognitiveResult, Goal


class _CognitiveWorld:
    """Only proposal/perception change; execution remains Runtime-owned."""

    def __init__(self, agent: "CognitiveAgent", world: World):
        self.agent, self.world = agent, world
        self.source_task = world.task.model_copy(deep=True)
        self.task = world.task.model_copy(deep=True, update={"max_steps": 1})

    def check_task(self) -> None:
        if self.world.task != self.source_task:
            raise ValueError("Domain task constraints changed during the cognitive episode")

    async def observe(self) -> State:
        self.check_task()
        state = State.model_validate((await self.world.observe()).model_dump())
        self.check_task()
        if state.kind != "observed":
            raise ValueError("Belief requires authoritative perception")
        records = await self.agent.memory.retrieve(state.domain) if self.agent.use_memory else []
        belief = self.agent.planner.infer(state.model_copy(deep=True), records)
        if belief.observed != state:
            raise ValueError("Inference cannot alter observed facts")
        self.agent.belief = belief.model_copy(deep=True)
        return state

    async def propose(self, state: State, width: int) -> list[Action]:
        self.check_task()
        belief = self.agent.belief
        if belief is None or belief.observed != state:
            raise ValueError("Planner must use the current observed belief")
        candidates = self.agent.planner.propose(
            belief.model_copy(deep=True), self.agent.goals, width
        )
        return candidates

    def validate(self, state: State, action: Action) -> None:
        self.check_task()
        self.world.validate(state, action)
        self.check_task()

    async def execute(self, action: Action, receipt: str) -> Observation:
        self.check_task()
        return await self.world.execute(action, receipt)

    def complete(self, state: State) -> bool:
        self.check_task()
        return self.world.complete(state)


class CognitiveAgent:
    """A fresh instance owns one episode; experience survives decision rounds.

    There is deliberately no direct-execution option. The experimental ungated
    baseline lives in the benchmark harness, outside this controller.
    """

    def __init__(
        self,
        store: Store,
        artifacts: Artifacts,
        registry: Registry,
        planner: CognitivePlanner,
        goals: list[Goal],
        policy: Policy | None = None,
        *,
        use_memory: bool = True,
    ):
        self.store, self.artifacts, self.registry = store, artifacts, registry
        self.planner, self.goals = planner, sorted(goals, key=lambda g: -g.priority)
        self.policy = (policy or Policy(search=False)).model_copy(deep=True)
        if self.policy.search:
            raise ValueError("The minimal shell plans only at observed states")
        self.use_memory = use_memory
        self.memory = EpisodicMemory(store)
        self.belief: Belief | None = None
        self._started = False

    async def run(self, world: World, max_rounds: int) -> CognitiveResult:
        if max_rounds < 1:
            raise ValueError("At least one decision round is required")
        if self._started:
            raise ValueError("Create a fresh cognitive agent for each episode; reconcile failures")
        self._started = True
        adapter = _CognitiveWorld(self, world)
        rounds, run_ids = [], []
        unsafe = False
        status = "round_limit"
        for _ in range(max_rounds):
            runtime = Runtime(self.store, self.artifacts, self.registry, self.policy)
            run_id = uid()
            await self.store.call(
                "create_run",
                {
                    "task": adapter.task.model_dump(),
                    "policy": self.policy.model_dump(),
                    "cognitive_goals": [g.model_dump() for g in self.goals],
                },
                run_id=run_id,
            )
            run_ids.append(run_id)
            result = await runtime.run(adapter, run_id=run_id)
            await self.memory.remember(run_id)
            await adapter.observe()
            if self.belief is not None:
                await self.store.call(
                    "append",
                    run_id,
                    "cognitive_update",
                    {
                        "belief": self.belief.model_dump(),
                        "memory_run_ids": list(self.memory.run_ids),
                        "goals": [
                            {"goal": g.model_dump(), "progress": g.progress(self.belief.observed)}
                            for g in self.goals
                        ],
                    },
                )
            rounds.append(result)
            unsafe |= result["unsafe"]
            if result["steps"] == 0 or unsafe or result["success"]:
                status = result["status"]
                break
        final = await adapter.observe()
        return CognitiveResult(
            success=world.complete(final) and not unsafe,
            unsafe=unsafe,
            status=status,
            rounds=rounds,
            run_ids=run_ids,
            final_state=final,
        )
