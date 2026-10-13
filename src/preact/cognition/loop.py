import inspect
import time

from preact.core.interfaces import World
from preact.core.models import Action, Observation, Policy, State, uid
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store

from .belief import BeliefEstimator
from .lifecycle import LearningCoordinator
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
            self.agent.belief_estimator.clear()
            self.agent.belief = None
            raise ValueError("Domain task constraints changed during the cognitive episode")

    async def observe(self) -> State:
        self.agent.belief = None
        try:
            return await self._observe()
        except BaseException:
            self.agent.belief_estimator.clear()
            raise

    async def _observe(self) -> State:
        self.check_task()
        state = State.model_validate((await self.world.observe()).model_dump())
        self.check_task()
        if state.kind != "observed":
            raise ValueError("Belief requires authoritative perception")
        if self.agent.reuse_beliefs:
            belief = await self.agent.belief_estimator.infer(
                state,
                self.agent.planner,
                self.agent.memory,
                task=self.source_task,
                use_memory=self.agent.use_memory,
            )
        else:
            records = (
                await self.agent.memory.retrieve(state.domain) if self.agent.use_memory else []
            )
            belief = self.agent.planner.infer(state.model_copy(deep=True), records)
        if belief.observed != state:
            raise ValueError("Inference cannot alter observed facts")
        self.agent.belief = belief.model_copy(deep=True)
        return state

    async def propose(self, state: State, width: int) -> list[Action]:
        self.check_task()
        if state.kind == "hypothetical":
            # Hypotheses remain Core search inputs, never authoritative Beliefs.
            propose = getattr(self.agent.planner, "propose_hypothetical", None)
            if not callable(propose):
                raise ValueError("Planner does not support hypothetical proposals")
            isolated = state.model_copy(deep=True)
            candidates = propose(isolated, width)
        else:
            belief = self.agent.belief
            if belief is None or belief.observed != state:
                raise ValueError("Planner must use the current observed belief")
            isolated = belief.model_copy(deep=True)
            candidates = self.agent.planner.propose(
                isolated,
                [g.model_copy(deep=True) for g in self.agent.goals],
                width,
            )
        if inspect.isawaitable(candidates):
            candidates = await candidates
        if isolated != (state if state.kind == "hypothetical" else belief):
            raise ValueError("Planner mutated its proposal input")
        self.check_task()
        return candidates

    @property
    def proposal_calls(self):
        return getattr(self.agent.planner, "proposal_calls", 0)

    @property
    def proposal_usage_complete(self):
        return getattr(self.agent.planner, "proposal_usage_complete", False)

    @property
    def usage(self):
        # Preserve the generator's accounting channel; Runtime consumes receipts.
        return getattr(self.agent.planner, "usage", None)

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
        reuse_beliefs: bool = False,
        episode_budget: bool = False,
        candidate_selector=None,
        learning_coordinator: LearningCoordinator | None = None,
        episode_id: str | None = None,
        round_observer=None,
    ):
        self.store, self.artifacts, self.registry = store, artifacts, registry
        self.planner, self.goals = planner, sorted(goals, key=lambda g: -g.priority)
        self._policy = Policy.model_validate((policy or Policy(search=False)).model_dump())
        if self._policy.search and not callable(getattr(planner, "propose_hypothetical", None)):
            raise ValueError("Search requires explicit hypothetical proposal support")
        self.use_memory = use_memory
        self._episode_budget = episode_budget
        self.candidate_selector = candidate_selector
        self.reuse_beliefs = reuse_beliefs
        self.belief_estimator = BeliefEstimator(enabled=reuse_beliefs)
        self.memory = EpisodicMemory(store)
        self.belief: Belief | None = None
        self.learning_coordinator = learning_coordinator
        self.episode_id = episode_id
        self.round_observer = round_observer
        if learning_coordinator is not None and not episode_id:
            raise ValueError("Learning requires explicit World episode identity")
        self._started = False

    @property
    def policy(self) -> Policy:
        """Caller-owned snapshot; planners cannot relax the episode policy."""
        return self._policy.model_copy(deep=True)

    async def run(self, world: World, max_rounds: int) -> CognitiveResult:
        if max_rounds < 1:
            raise ValueError("At least one decision round is required")
        if self._started:
            raise ValueError("Create a fresh cognitive agent for each episode; reconcile failures")
        self._started = True
        adapter = _CognitiveWorld(self, world)
        self.belief_estimator.begin(adapter.source_task)
        try:
            return await self._run_episode(world, max_rounds, adapter)
        except BaseException:
            if self.learning_coordinator is not None:
                self.learning_coordinator.finish_round(failed=True)
            self.belief = None
            raise
        finally:
            self.belief_estimator.end()

    async def _run_episode(
        self, world: World, max_rounds: int, adapter: _CognitiveWorld
    ) -> CognitiveResult:
        rounds, run_ids = [], []
        episode_prefix = (
            list(self.learning_coordinator.episodes.get(self.episode_id, []))
            if self.learning_coordinator is not None
            else []
        )
        for previous_run in episode_prefix:
            await self.memory.remember(previous_run)
        unsafe = False
        status = "round_limit"
        started = time.monotonic()
        calls, cost = 0, 0.0
        # Legacy queue Tasks describe one round. Software callers may explicitly
        # bind the source Task's action/time/call/cost limits to the whole episode.
        round_limit = (
            min(max_rounds, adapter.source_task.max_steps) if self._episode_budget else max_rounds
        )
        for _ in range(round_limit):
            policy = self.policy
            if self._episode_budget:
                remaining = {
                    "max_calls": policy.max_calls - calls,
                    "max_cost_usd": policy.max_cost_usd - cost,
                    "max_seconds": policy.max_seconds - (time.monotonic() - started),
                }
                if any(value <= 0 for value in remaining.values()):
                    status = "abstained"
                    if run_ids:
                        await self.store.call(
                            "append",
                            run_ids[-1],
                            "cognitive_budget_exhausted",
                            {"remaining": remaining},
                        )
                    if self.learning_coordinator is not None:
                        self.learning_coordinator.finish_round()
                    break
                policy = Policy.model_validate({**policy.model_dump(), **remaining})
            binding = None
            if self.learning_coordinator is not None:
                binding = await self.learning_coordinator.prepare(
                    world, self.episode_id, episode_prefix + run_ids
                )
                if binding.world.task != adapter.source_task:
                    self.learning_coordinator.finish_round(failed=True)
                    raise ValueError("Learning binding cannot change the World Task")
                self.planner, self.registry = binding.planner, binding.registry
                self.candidate_selector = binding.selector
                self.belief_estimator.clear()
                adapter = _CognitiveWorld(self, binding.world)
            if self.learning_coordinator is not None and self._episode_budget:
                remaining_seconds = self._policy.max_seconds - (time.monotonic() - started)
                if remaining_seconds > 0:
                    policy = Policy.model_validate(
                        {**policy.model_dump(), "max_seconds": remaining_seconds}
                    )
            if (
                self.learning_coordinator is not None
                and self._episode_budget
                and time.monotonic() - started >= self._policy.max_seconds
            ):
                if self.learning_coordinator is not None:
                    self.learning_coordinator.finish_round()
                status = "abstained"
                break
            runtime = Runtime(
                self.store,
                self.artifacts,
                self.registry,
                policy,
                candidate_selector=self.candidate_selector,
            )
            run_id = uid()
            await self.store.call(
                "create_run",
                {
                    "task": adapter.task.model_dump(),
                    "policy": policy.model_dump(),
                    "cognitive_goals": [g.model_dump() for g in self.goals],
                },
                run_id=run_id,
            )
            run_ids.append(run_id)
            # Optional cognition-only trace lifecycle. Hooks confer no execution authority.
            begin = getattr(self.planner, "begin_round", None)
            end = getattr(self.planner, "end_round", None)
            failed = True
            try:
                if binding is not None:
                    await self.learning_coordinator.bind_run(run_id, binding)
                if callable(begin):
                    value = begin(
                        run_id, policy.model_copy(deep=True), tuple(episode_prefix + run_ids[:-1])
                    )
                    if inspect.isawaitable(value):
                        if inspect.iscoroutine(value):
                            value.close()
                        raise ValueError("Round trace hooks must be synchronous context setup")
                result = await runtime.run(adapter, run_id=run_id)
                failed = False
            finally:
                if self.learning_coordinator is not None:
                    self.learning_coordinator.finish_round(failed=failed)
                if callable(end):
                    value = end()
                    if inspect.isawaitable(value):
                        if inspect.iscoroutine(value):
                            value.close()
                        raise ValueError("Round trace cleanup must be synchronous")
            calls += result["calls"] + result["agent_calls"]
            cost += result["cost_usd"]
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
            if self.learning_coordinator is not None:
                await self.learning_coordinator.remember(self.episode_id, episode_prefix + run_ids)
            if self.round_observer is not None:
                observed = self.round_observer(run_id, self.belief.model_copy(deep=True))
                if inspect.isawaitable(observed):
                    await observed
            rounds.append(result)
            unsafe |= result["unsafe"]
            if result["steps"] == 0 or unsafe or result["success"]:
                status = result["status"]
                break
            if self._episode_budget and not result["cost_known"]:
                status = "abstained"
                await self.store.call(
                    "append", run_id, "cognitive_budget_exhausted", {"reason": "Unknown cost"}
                )
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
