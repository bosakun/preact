import asyncio
import copy
import json
import subprocess
import sys

import pytest
from sqlalchemy import update

from preact.cognition.belief import BeliefEstimator
from preact.cognition.loop import CognitiveAgent, _CognitiveWorld
from preact.cognition.memory import EpisodicMemory
from preact.cognition.models import BeliefReusePolicy, Goal, Inference
from preact.cognition.queue import QueuePlanner
from preact.core.models import Policy, State
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import CognitiveQueueWorld
from preact.engines.cognitive_queue import QueueBoundVerifier, QueueForecast
from tests.fixtures.memory_ledger import append_execution


class CountedPlanner(QueuePlanner):
    """Only the call counter is mutable; the estimator itself stays pure."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = 0
        self.fail = False

    def belief_reuse_policy(self):
        return BeliefReusePolicy(f"counted-ema-v1:{self.adaptation}:{self.prior}:{self.fail}", True)

    def infer(self, state, experience):
        self.calls += 1
        if self.fail:
            raise RuntimeError("inference failure")
        return super().infer(state, experience)


async def context(tmp_path, *, records=2, file_store=False):
    world = CognitiveQueueWorld(ticks=20, seed=2, target=50)
    url = f"sqlite:///{tmp_path / 'ledger.db'}" if file_store else "sqlite:///:memory:"
    memory = EpisodicMemory(Store(url))
    outcomes = []
    for i in range(records):
        run = memory.store.create_run({}, run_id=f"run-{i}")
        outcomes.append(await append_execution(memory.store, world, run))
        await memory.remember(run)
    estimator = BeliefEstimator(enabled=True)
    estimator.begin(world.task)
    return world, memory, CountedPlanner(), estimator, outcomes


async def infer(context, state=None, **kwargs):
    world, memory, planner, estimator, _ = context
    return await estimator.infer(
        state or await world.observe(), planner, memory, task=world.task, **kwargs
    )


async def test_time_independent_reuse_binds_latest_authoritative_observation(tmp_path):
    ctx = await context(tmp_path)
    world, _, planner, estimator, _ = ctx
    old = await world.observe()
    new = old.model_copy(update={"timestamp": "2099-01-01T00:00:00+00:00"})
    assert old.id == new.id and old != new
    before, after = await infer(ctx, old), await infer(ctx, new)
    assert planner.calls == 1 and estimator.counts.hits == 1
    assert after.observed == new and after.observed is not new
    assert after.inferred == before.inferred
    after.inferred["service"].source_refs.clear()
    after.unknown.clear()
    after.observed.payload["queue"] = 999
    again = await infer(ctx, new)
    assert again.observed == new and again.inferred == before.inferred and again.unknown


@pytest.mark.parametrize(
    "field", ["payload", "domain", "provenance", "uncertainty", "parent_id", "schema_version"]
)
async def test_state_id_alone_is_never_a_reuse_key(tmp_path, field):
    ctx = await context(tmp_path)
    state = await ctx[0].observe()
    await infer(ctx, state)
    if field == "payload":
        changed = State.create(state.domain, {**state.payload, "queue": 1}, state.provenance)
    elif field == "domain":
        changed = State.create("other", state.payload, state.provenance)
    else:
        changed = state.model_copy(update={field: 0.5 if field == "uncertainty" else "changed"})
        assert changed.id == state.id
    result = await infer(ctx, changed)
    assert ctx[2].calls == 2 and ctx[3].counts.hits == 0
    assert result.observed == changed


async def test_timestamp_dependent_opt_in_keeps_timestamp_in_key(tmp_path):
    class TimestampPlanner(CountedPlanner):
        def belief_reuse_policy(self):
            return BeliefReusePolicy("timestamp-v1", False)

        def infer(self, state, experience):
            belief = super().infer(state, experience)
            belief.inferred["timestamp"] = Inference(
                value=float(len(state.timestamp)), lower=0, upper=100, status="estimated"
            )
            return belief

    ctx = list(await context(tmp_path))
    ctx[2] = TimestampPlanner()
    state = (await ctx[0].observe()).model_copy(update={"timestamp": "1"})
    before = await infer(ctx, state)
    after = await infer(ctx, state.model_copy(update={"timestamp": "12"}))
    assert before.inferred["timestamp"].value == 1 and after.inferred["timestamp"].value == 2
    assert ctx[2].calls == 2 and ctx[3].counts.hits == 0


async def test_inherited_policy_does_not_opt_in_stateful_or_time_dependent_subclasses(tmp_path):
    class Stateful(QueuePlanner):
        def __init__(self):
            super().__init__()
            self.counter = 0

        def infer(self, state, experience):
            self.counter += 1
            result = super().infer(state, experience)
            result.inferred["service"].value = float(self.counter)
            return result

    ctx = list(await context(tmp_path))
    ctx[2] = Stateful()
    before, after = await infer(ctx), await infer(ctx)
    assert before.inferred["service"].value == 1 and after.inferred["service"].value == 2
    assert ctx[3].counts.bypasses == 2 and ctx[3].counts.hits == 0


async def test_internal_state_token_change_during_inference_forbids_publication(tmp_path):
    class Stateful(CountedPlanner):
        def belief_reuse_policy(self):
            return BeliefReusePolicy(f"internal-state:{self.calls}", True)

    ctx = list(await context(tmp_path))
    ctx[2] = Stateful()
    await infer(ctx)
    await infer(ctx)
    assert ctx[2].calls == 2 and ctx[3]._entry is None


async def test_real_queue_planner_extra_state_disables_its_purity_declaration(tmp_path):
    ctx = list(await context(tmp_path))
    ctx[2] = QueuePlanner()
    await infer(ctx)
    assert ctx[3]._entry is not None
    ctx[2].hidden_state = 1
    await infer(ctx)
    assert ctx[3]._entry is None and ctx[3].counts.bypasses == 1


@pytest.mark.parametrize("setting", ["prior", "adaptation", "instance", "algorithm", "task"])
async def test_planner_or_task_change_does_not_reuse_old_estimate(tmp_path, setting):
    ctx = list(await context(tmp_path))
    await infer(ctx)
    if setting == "prior":
        ctx[2].prior = 1
    elif setting == "adaptation":
        ctx[2].adaptation = False
    elif setting == "instance":
        ctx[2] = CountedPlanner()
    elif setting == "algorithm":
        original = ctx[2].infer
        ctx[2].infer = lambda state, records: original(state, records)
    else:
        ctx[0].task.goal = "changed goal"
    await infer(ctx)
    assert ctx[3].counts.hits == 0
    assert ctx[2].calls == (1 if setting == "instance" else 2)


async def test_memory_append_clear_replacement_and_external_update_invalidate(tmp_path):
    ctx = list(await context(tmp_path, file_store=True))
    world, memory, planner, estimator, _ = ctx
    await infer(ctx)
    # No Experience contents change, but the authority run did change.
    writer = Store(str(memory.store.db.url))
    writer.append("run-1", "diagnostic", {"external": True})
    await infer(ctx)
    assert planner.calls == 2
    await memory.clear()
    await infer(ctx)
    assert planner.calls == 3
    await append_execution(memory.store, world, "run-1")
    await infer(ctx)
    assert planner.calls == 4
    ctx[1] = EpisodicMemory(memory.store)
    ctx[1].run_ids = memory.run_ids.copy()
    await infer(ctx)
    assert planner.calls == 5 and estimator.counts.hits == 0


async def test_experience_content_order_and_references_are_bound_even_with_same_generation(
    tmp_path,
):
    ctx = await context(tmp_path)
    _, memory, planner, estimator, _ = ctx
    original = memory.retrieve
    current = await original(ctx[0].task.domain)

    async def changed(*args):
        return [e.model_copy(deep=True) for e in current]

    memory.retrieve = changed
    await infer(ctx)
    current.reverse()
    await infer(ctx)
    current[0].prediction_ids.append("new-source")
    await infer(ctx)
    current[0].reference += "-different"
    await infer(ctx)
    current[0].observation.metrics["processed"] = 0
    await infer(ctx)
    assert planner.calls == 5 and estimator.counts.hits == 0


@pytest.mark.parametrize("fault", ["forged", "pending", "aborted", "unexecuted"])
async def test_receipt_failure_never_falls_back_to_old_belief(tmp_path, fault):
    ctx = await context(tmp_path)
    _, memory, planner, estimator, outcomes = ctx
    await infer(ctx)
    if fault in {"forged", "unexecuted"}:
        outcome = copy.deepcopy(outcomes[-1])
        if fault == "forged":
            outcome["observation"]["unsafe"] = True
        else:
            events = memory.store.read_events("run-1")
            outcome["node_id"] = next(
                e["data"]["node"]["id"]
                for e in events
                if e["kind"] == "node" and "unexecuted" in e["data"]["node"]["id"]
            )
        memory.store.append("run-1", "outcome", outcome)
    else:
        with memory.store.db.begin() as conn:
            conn.execute(
                update(memory.store.executions)
                .where(memory.store.executions.c.id == outcomes[-1]["observation"]["receipt"])
                .values(status=fault)
            )
    with pytest.raises(ValueError, match="committed"):
        await infer(ctx)
    assert planner.calls == 1 and estimator._entry is None


async def test_separate_process_forged_update_is_checked_on_would_be_hit(tmp_path):
    ctx = await context(tmp_path, file_store=True)
    _, memory, planner, estimator, outcomes = ctx
    await infer(ctx)
    outcome = copy.deepcopy(outcomes[-1])
    outcome["observation"]["unsafe"] = True
    script = (
        "import json,sys; from preact.core.store import Store; "
        "Store(sys.argv[1]).append('run-1','outcome',json.load(sys.stdin))"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(memory.store.db.url)],
        input=json.dumps(outcome),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError, match="committed"):
        await infer(ctx)
    assert planner.calls == 1 and estimator._entry is None


async def test_estimator_failure_or_cancellation_clears_cache_and_recovery_recomputes(tmp_path):
    ctx = await context(tmp_path)
    _, memory, planner, estimator, _ = ctx
    await infer(ctx)
    planner.fail = True
    with pytest.raises(RuntimeError, match="inference failure"):
        await infer(ctx)
    assert estimator._entry is None
    planner.fail = False
    await infer(ctx)
    original = memory.retrieve
    started = asyncio.Event()

    async def blocked(*args):
        started.set()
        await asyncio.Event().wait()

    memory.retrieve = blocked
    task = asyncio.create_task(infer(ctx))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert estimator._entry is None
    memory.retrieve = original
    previous = planner.calls
    await infer(ctx)
    assert planner.calls == previous + 1


async def test_unverifiable_policy_and_mutated_input_use_regular_inference(tmp_path):
    class Undeclared(CountedPlanner):
        def belief_reuse_policy(self):
            raise ValueError("cannot establish purity")

    ctx = list(await context(tmp_path))
    ctx[2] = Undeclared()
    await infer(ctx)
    await infer(ctx)
    assert ctx[2].calls == 2 and ctx[3].counts.bypasses == 2

    class Mutating(CountedPlanner):
        def belief_reuse_policy(self):
            return BeliefReusePolicy("mutating-fixture", True)

        def infer(self, state, records):
            records[0].prediction_ids.clear()
            return super().infer(state, records)

    ctx[2] = Mutating()
    await infer(ctx)
    await infer(ctx)
    assert ctx[2].calls == 2 and ctx[3]._entry is None


async def test_agent_task_failure_closes_episode_and_clears_estimates(tmp_path):
    world = CognitiveQueueWorld(ticks=3)
    planner = CountedPlanner()
    agent = CognitiveAgent(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([]),
        planner,
        [Goal(name="Throughput", metric="delivered", target=85)],
        Policy(search=False),
        reuse_beliefs=True,
    )
    adapter = _CognitiveWorld(agent, world)
    agent.belief_estimator.begin(world.task)
    await adapter.observe()
    world.task.goal = "changed"
    with pytest.raises(ValueError, match="constraints changed"):
        await adapter.observe()
    assert agent.belief is None and agent.belief_estimator._entry is None

    class Broken(CognitiveQueueWorld):
        async def execute(self, action, receipt):
            raise RuntimeError("executor failure")

    world = Broken(ticks=3)
    agent.registry = Registry(
        [QueueBoundVerifier(world.task), QueueBoundVerifier(world.task, future=True)]
    )
    with pytest.raises(RuntimeError, match="executor failure"):
        await agent.run(world, 3)
    assert agent.belief_estimator._task is None and agent.belief is None
    assert agent.memory.run_ids == []


async def test_reuse_keeps_memory_disabled_ablation_and_still_forecasts_every_candidate(tmp_path):
    world = CognitiveQueueWorld(ticks=4, target=5)
    store = Store("sqlite:///:memory:")
    planner = CountedPlanner()
    agent = CognitiveAgent(
        store,
        Artifacts(str(tmp_path)),
        Registry([]),
        planner,
        [Goal(name="Throughput", metric="delivered", target=5)],
        Policy(search=False, calibration=False, max_nodes=3, max_calls=24),
        use_memory=False,
        reuse_beliefs=True,
    )
    forecast = QueueForecast(
        world.task, planner, agent.memory, use_memory=False, belief_estimator=agent.belief_estimator
    )
    agent.registry = Registry(
        [forecast, QueueBoundVerifier(world.task), QueueBoundVerifier(world.task, future=True)]
    )

    async def forbidden(*args):
        raise AssertionError("no-memory ablation must not retrieve")

    agent.memory.retrieve = forbidden
    result = await agent.run(world, 4)
    assert sum(r["calls"] for r in result.rounds) == 36
    assert agent.belief.inferred["service"].value == 3
    assert agent.belief.inferred["service"].source_refs == []
    # Unlike memory-enabled runs, cognitive_update causes no retrieval refresh:
    # next-round initial observations also reuse the post-remember estimate.
    assert agent.belief_estimator.counts.hits == 20 and planner.calls == 9
    assert agent.belief_estimator._entry is None


async def test_clear_during_retrieval_and_episode_end_never_restore_old_cache(tmp_path):
    ctx = await context(tmp_path)
    _, memory, planner, estimator, _ = ctx
    await infer(ctx)
    original = memory.retrieve

    async def resetting(*args):
        estimator.clear()
        return await original(*args)

    memory.retrieve = resetting
    await infer(ctx)
    assert planner.calls == 2 and estimator._entry is None
    memory.retrieve = original
    estimator.end()
    await infer(ctx)
    await infer(ctx)
    assert planner.calls == 4 and estimator._entry is None
    estimator.begin(ctx[0].task)
    await infer(ctx)
    await infer(ctx)
    assert planner.calls == 5 and estimator.counts.hits == 1


async def test_store_replacement_and_memory_manifest_changes_cannot_hit(tmp_path):
    ctx = await context(tmp_path, file_store=True)
    _, memory, planner, estimator, _ = ctx
    await infer(ctx)
    memory.store = Store(str(memory.store.db.url))
    await infer(ctx)
    assert planner.calls == 2
    memory.run_ids = memory.run_ids[:1]
    await infer(ctx)
    assert planner.calls == 3 and estimator.counts.hits == 0


async def test_invalid_or_imagined_state_discards_old_belief(tmp_path):
    ctx = await context(tmp_path)
    state = await ctx[0].observe()
    await infer(ctx, state)
    with pytest.raises(ValueError):
        await infer(ctx, state.model_copy(update={"kind": "predicted"}))
    assert ctx[3]._entry is None
    await infer(ctx, state)
    with pytest.raises(ValueError):
        await infer(ctx, state.model_copy(update={"id": "forged"}))
    assert ctx[3]._entry is None


async def test_adapter_observation_failure_clears_reusable_estimate(tmp_path):
    world = CognitiveQueueWorld(ticks=3)
    agent = CognitiveAgent(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([]),
        CountedPlanner(),
        [],
        reuse_beliefs=True,
    )
    adapter = _CognitiveWorld(agent, world)
    agent.belief_estimator.begin(world.task)
    await adapter.observe()
    assert agent.belief_estimator._entry is not None

    async def broken():
        raise asyncio.CancelledError()

    world.observe = broken
    with pytest.raises(asyncio.CancelledError):
        await adapter.observe()
    assert agent.belief is None and agent.belief_estimator._entry is None


@pytest.mark.parametrize("condition", ["cognitive", "no_memory", "no_adaptation"])
async def test_real_episode_matches_disabled_actions_gate_receipts_and_learning(
    tmp_path, condition
):
    from scripts.bench_belief_reuse import episode

    config = {"ticks": 5, "target": 5, "seed": 1, "shift_tick": 2}
    policy = {"search": False, "calibration": False, "max_nodes": 3, "max_calls": 24}
    before, expected = await episode(config, condition, False, tmp_path / "before", policy)
    after, actual = await episode(config, condition, True, tmp_path / "after", policy)
    assert actual == expected and before["semantic_sha256"] == after["semantic_sha256"]
    assert before["engine_calls"] == after["engine_calls"] == 45
    assert before["counts"]["world_observation"] == after["counts"]["world_observation"] == 36
    assert before["counts"]["adapter_observation"] == after["counts"]["adapter_observation"] == 21
    assert before["counts"]["proposal"] == after["counts"]["proposal"] == 5
    assert before["counts"].get("memory_retrieval", 0) == after["counts"].get("memory_retrieval", 0)
    assert before["counts"]["inference"] == 36 and after["counts"]["inference"] < 36
    # Comparison retains the meaningful results, even while random IDs are normalized.
    changed = copy.deepcopy(actual)
    next(e for e in changed["events"] if e["kind"] == "authorization")["data"]["decision"] = (
        "abstain"
    )
    assert changed != expected
    changed = copy.deepcopy(actual)
    changed["learning"][0]["label"] = 1 - changed["learning"][0]["label"]
    assert changed != expected


async def test_agent_cancellation_discards_estimate_and_cannot_restart_episode(tmp_path):
    entered = asyncio.Event()

    class BlockingWorld(CognitiveQueueWorld):
        calls = 0

        async def observe(self):
            self.calls += 1
            if self.calls == 2:
                entered.set()
                await asyncio.Event().wait()
            return await super().observe()

    world = BlockingWorld(ticks=3)
    agent = CognitiveAgent(
        Store("sqlite:///:memory:"),
        Artifacts(str(tmp_path)),
        Registry([]),
        CountedPlanner(),
        [],
        Policy(search=False, calibration=False),
        reuse_beliefs=True,
    )
    agent.registry = Registry(
        [
            QueueForecast(
                world.task, agent.planner, agent.memory, belief_estimator=agent.belief_estimator
            ),
            QueueBoundVerifier(world.task),
            QueueBoundVerifier(world.task, future=True),
        ]
    )
    task = asyncio.create_task(agent.run(world, 3))
    await entered.wait()
    assert agent.belief_estimator._entry is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert agent.belief is None and agent.belief_estimator._task is None
    assert agent.belief_estimator._entry is None and world.executed == []
    with pytest.raises(ValueError, match="fresh cognitive agent"):
        await agent.run(world, 3)


async def test_timestamp_algorithm_without_explicit_declaration_is_never_reused(tmp_path):
    class TimeDependent(QueuePlanner):
        def infer(self, state, records):
            result = super().infer(state, records)
            result.inferred["service"].value = float(len(state.timestamp))
            return result

    ctx = list(await context(tmp_path))
    ctx[2] = TimeDependent()
    state = (await ctx[0].observe()).model_copy(update={"timestamp": "1"})
    before = await infer(ctx, state)
    after = await infer(ctx, state.model_copy(update={"timestamp": "12"}))
    assert before.inferred["service"].value == 1 and after.inferred["service"].value == 2
    assert ctx[3].counts.bypasses == 2 and ctx[3].counts.hits == 0


async def test_fresh_agent_after_world_reset_has_no_episode_cache_or_memory(tmp_path):
    world = CognitiveQueueWorld(ticks=3, target=3)
    store = Store("sqlite:///:memory:")
    previous_runs = set()
    actions = None
    for _ in range(2):
        world.reset()
        planner = CountedPlanner()
        agent = CognitiveAgent(
            store,
            Artifacts(str(tmp_path)),
            Registry([]),
            planner,
            [Goal(name="Throughput", metric="delivered", target=3)],
            Policy(search=False, calibration=False),
            reuse_beliefs=True,
        )
        assert agent.memory.run_ids == [] and agent.belief_estimator._entry is None
        agent.registry = Registry(
            [
                QueueForecast(
                    world.task, planner, agent.memory, belief_estimator=agent.belief_estimator
                ),
                QueueBoundVerifier(world.task),
                QueueBoundVerifier(world.task, future=True),
            ]
        )
        result = await agent.run(world, 3)
        assert not previous_runs.intersection(result.run_ids)
        assert agent.belief_estimator._entry is None and agent.belief_estimator._task is None
        assert all(
            e.run_id in result.run_ids for e in await agent.memory.retrieve(world.task.domain)
        )
        observed_actions = [a.payload for a in world.executed]
        if actions is not None:
            assert actions == observed_actions
        actions = observed_actions
        previous_runs.update(result.run_ids)


async def test_manifest_change_misses_even_when_selected_experiences_do_not_change(tmp_path):
    ctx = await context(tmp_path)
    _, memory, planner, estimator, _ = ctx
    await infer(ctx)
    generation = memory.generation
    expected = await memory.retrieve(ctx[0].task.domain)
    memory.run_ids.append(memory.run_ids[-1])
    assert await memory.retrieve(ctx[0].task.domain) == expected
    assert memory.generation == generation
    await infer(ctx)
    assert planner.calls == 2 and estimator.counts.hits == 0


async def test_inference_cannot_replace_or_modify_authoritative_observed_facts(tmp_path):
    class Altering(CountedPlanner):
        def belief_reuse_policy(self):
            return BeliefReusePolicy("invalid-observed-output", True)

        def infer(self, state, records):
            result = super().infer(state, records)
            return result.model_copy(
                update={
                    "observed": result.observed.model_copy(
                        update={"provenance": "prediction-as-fact"}
                    )
                }
            )

    ctx = list(await context(tmp_path))
    await infer(ctx)
    ctx[2] = Altering()
    with pytest.raises(ValueError, match="alter observed"):
        await infer(ctx)
    assert ctx[3]._entry is None
