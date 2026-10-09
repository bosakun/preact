import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import update

from preact.cognition import CognitiveAgent, EpisodicMemory, Goal, WorldPlanner
from preact.cognition.models import Belief
from preact.core.models import Policy, State
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.datasets.programs import LOGIC
from preact.domains.program import ProgramWorld
from preact.domains.software import BASE, PREPARE, SAFE, SHORTCUT, SoftwareWorld
from preact.domains.software import probe as software_probe
from preact.engines.local import LocalHeuristic, LocalVerifier


def agent_for(world, tmp_path, *, planner=None, engines=None, policy=None, **kwargs):
    return CognitiveAgent(
        Store("sqlite:///" + str(tmp_path / "ledger.db")),
        Artifacts(str(tmp_path / "artifacts")),
        Registry(engines if engines is not None else [LocalHeuristic(world), LocalVerifier(world)]),
        planner if planner is not None else WorldPlanner(world),
        [Goal(name="Repair", metric="goal_progress", target=1)],
        policy if policy is not None else Policy(calibration=False),
        **kwargs,
    )


async def test_software_multiple_rounds_preserve_native_search_gate_and_receipts(tmp_path):
    world = SoftwareWorld()
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 4)
    assert result.success and not result.unsafe
    assert [r["steps"] for r in result.rounds] == [1, 1]
    records = await agent.memory.retrieve("software")
    assert [r.action.payload["files"]["checkout.py"] for r in records] == [PREPARE, SAFE]
    assert all(r.observation.state.kind == "observed" for r in records)
    for run_id in result.run_ids:
        events = agent.store.read_events(run_id)
        assert sum(e["kind"] == "authorization" for e in events) == 1
        assert sum(e["kind"] == "outcome" for e in events) == 1
        intent = next(e for e in events if e["kind"] == "execution_intent")
        outcome = next(e for e in events if e["kind"] == "outcome")
        assert intent["seq"] < outcome["seq"]
        assert agent.store.execution_record(intent["data"]["receipt"])["status"] == "complete"
        assert not any(e["data"].get("direct") for e in events if e["kind"] == "decision")
    assert any(
        e["kind"] == "node" and e["data"]["node"]["depth"] >= 2
        for e in agent.store.read_events(result.run_ids[0])
    )
    assert len(records) < sum(
        e["kind"] == "prediction"
        for run_id in result.run_ids
        for e in agent.store.read_events(run_id)
    )


async def test_program_world_uses_same_public_cognitive_interface(tmp_path):
    world = ProgramWorld(LOGIC[0], evaluation_seed=19)
    result = await agent_for(world, tmp_path).run(world, 2)
    assert result.success and not result.unsafe
    assert result.final_state.payload["goal_complete"]


@pytest.mark.parametrize("memory", [True, False])
async def test_flat_and_no_memory_remain_available(tmp_path, memory):
    world = SoftwareWorld()
    agent = agent_for(world, tmp_path, policy=Policy(search=False), use_memory=memory)
    result = await agent.run(world, 4)
    assert result.success and not result.unsafe and len(result.rounds) == 2


@pytest.mark.parametrize("engines,budget", [(False, 24), (True, 1)])
async def test_missing_verifier_or_budget_abstains_without_patch(tmp_path, engines, budget):
    world = SoftwareWorld()
    agent = agent_for(
        world,
        tmp_path,
        engines=[LocalHeuristic(world)] if not engines else None,
        policy=Policy(max_calls=budget),
    )
    result = await agent.run(world, 4)
    assert not result.success and result.status == "abstained"
    assert world.payload["files"]["checkout.py"] == BASE and not world.receipts
    assert agent.memory.run_ids == []


async def test_injected_candidate_generator_does_not_bypass_native_validation(tmp_path):
    world = SoftwareWorld()

    class Untrusted:
        async def propose(self, state, width):
            action = (await world.propose(state, width))[0]
            action.payload["files"]["checkout.py"] = "import os; os.system('echo unsafe')"
            return [action]

    agent = agent_for(world, tmp_path, planner=WorldPlanner(world, Untrusted()))
    result = await agent.run(world, 1)
    assert result.status == "abstained"
    assert not world.receipts and not agent.memory.run_ids


async def test_async_generator_mutation_is_rejected_and_belief_cleared(tmp_path):
    world = SoftwareWorld()

    class Mutating:
        async def propose(self, state, width):
            state.payload["files"]["checkout.py"] = SHORTCUT
            return await world.propose(state, width)

    agent = agent_for(world, tmp_path, planner=WorldPlanner(world, Mutating()))
    with pytest.raises(ValueError, match="mutated"):
        await agent.run(world, 1)
    assert agent.belief is None and not world.receipts
    assert world.payload["files"]["checkout.py"] == BASE


@pytest.mark.parametrize("factory", [SoftwareWorld, lambda: ProgramWorld(LOGIC[0])])
async def test_observation_materialization_and_receipt_do_not_share_nested_data(factory):
    world = factory()
    state = await world.observe()
    original = state.model_copy(deep=True)
    filename = next(iter(state.payload["files"]))
    state.payload["files"][filename] = "changed"
    assert (await world.observe()).payload == original.payload
    action = (await world.propose(original, 2))[-1]
    future = world.materialize(original, action)
    future.payload["files"][filename] = "changed future"
    assert action.payload["files"][filename] != "changed future"
    observation = await world.execute(action, "owned-unit-receipt")
    observation.state.payload["files"][filename] = "changed receipt"
    replay = await world.execute(action, "owned-unit-receipt")
    assert replay.state.payload == (await world.observe()).payload
    assert replay.state.payload["files"][filename] != "changed receipt"


async def test_stale_observation_abstains_before_side_effect(tmp_path):
    class Changing(SoftwareWorld):
        observations = 0

        async def observe(self):
            self.observations += 1
            if self.observations == 2:
                self.payload["stage"] = "external-writer"
            return await super().observe()

    world = Changing()
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 1)
    assert result.status == "abstained" and not world.receipts
    assert not agent.memory.run_ids


async def test_task_change_cannot_relax_required_checks(tmp_path):
    class Changing(SoftwareWorld):
        async def propose(self, state, width):
            actions = await super().propose(state, width)
            self.task.required_checks.clear()
            return actions

    world = Changing()
    agent = agent_for(world, tmp_path)
    with pytest.raises(ValueError, match="constraints changed"):
        await agent.run(world, 1)
    assert not world.receipts and agent.belief is None


async def test_planner_cannot_relax_caller_or_agent_policy(tmp_path):
    world = SoftwareWorld()
    policy = Policy(max_calls=1)

    class Changing(WorldPlanner):
        def infer(self, state, experience):
            policy.max_calls = 100
            agent.policy.max_calls = 100
            agent.policy.max_risk = 1
            return super().infer(state, experience)

    agent = agent_for(world, tmp_path, planner=Changing(world), policy=policy)
    result = await agent.run(world, 1)
    assert result.status == "abstained" and not world.receipts
    assert agent.policy.max_calls == 1


async def test_cancelled_execution_keeps_pending_intent_and_restart_cannot_learn(tmp_path):
    entered = asyncio.Event()

    class Blocking(SoftwareWorld):
        async def execute(self, action, receipt):
            entered.set()
            await asyncio.Event().wait()

    world = Blocking()
    agent = agent_for(world, tmp_path, reuse_beliefs=True)
    running = asyncio.create_task(agent.run(world, 4))
    await asyncio.wait_for(entered.wait(), 10)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert agent.belief is None and not agent.memory.run_ids and not world.receipts
    run = agent.store.list_runs()[0]
    assert agent.store.pending_execution(run["id"])
    restarted = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    restarted.recover()
    memory = EpisodicMemory(restarted)
    await memory.remember(run["id"])
    assert not await memory.retrieve("software")
    receipt = next(e for e in agent.store.read_events(run["id"]) if e["kind"] == "execution_intent")
    pending = restarted.execution_record(receipt["data"]["receipt"])
    with pytest.raises(RuntimeError):
        restarted.intent(run["id"], pending["state_id"], pending["action_hash"])
    with pytest.raises(ValueError, match="fresh cognitive agent"):
        await agent.run(SoftwareWorld(), 1)


async def test_failed_execution_keeps_pending_intent_without_learning(tmp_path):
    class Broken(SoftwareWorld):
        async def execute(self, action, receipt):
            raise RuntimeError("Injected executor failure")

    world = Broken()
    agent = agent_for(world, tmp_path)
    with pytest.raises(RuntimeError, match="executor failure"):
        await agent.run(world, 1)
    assert agent.belief is None and not agent.memory.run_ids
    assert agent.store.pending_execution(agent.store.list_runs()[0]["id"])


async def test_external_forged_outcome_invalidates_software_memory(tmp_path):
    world = SoftwareWorld()
    agent = agent_for(world, tmp_path, reuse_beliefs=True)
    result = await agent.run(world, 1)
    run_id = result.run_ids[0]
    outcome = next(e for e in agent.store.read_events(run_id) if e["kind"] == "outcome")
    forged = {**outcome["data"], "observation": {**outcome["data"]["observation"], "success": True}}
    external = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    external.append(run_id, "outcome", forged)
    with pytest.raises(ValueError, match="committed"):
        await agent.memory.retrieve("software")


@pytest.mark.parametrize("status", ["pending", "aborted"])
async def test_receipt_only_external_change_invalidates_software_memory(tmp_path, status):
    world = SoftwareWorld()
    agent = agent_for(world, tmp_path, reuse_beliefs=True)
    await agent.run(world, 1)
    record = (await agent.memory.retrieve("software"))[0]
    external = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    # Fault injection models a corrupted external writer, not an admitted transition.
    with external.db.begin() as connection:
        connection.execute(
            update(external.executions)
            .where(external.executions.c.id == record.observation.receipt)
            .values(status=status)
        )
    with pytest.raises(ValueError, match="committed"):
        await agent.memory.retrieve("software")


async def test_cancelled_trusted_probe_kills_and_reaps_child(monkeypatch):
    events = []

    class Process:
        async def communicate(self):
            raise asyncio.CancelledError

        def kill(self):
            events.append("kill")

        async def wait(self):
            events.append("wait")

    async def spawn(*args, **kwargs):
        assert "-I" in args
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(asyncio.CancelledError):
        await software_probe(BASE)
    assert events == ["kill", "wait"]


async def test_fresh_software_episode_does_not_share_belief_or_memory(tmp_path):
    first_world = SoftwareWorld()
    first = agent_for(first_world, tmp_path / "first", reuse_beliefs=True)
    await first.run(first_world, 2)
    second_world = SoftwareWorld()
    second = agent_for(second_world, tmp_path / "second", reuse_beliefs=True)
    assert second.belief is None and not second.memory.run_ids
    await second.run(second_world, 1)
    assert second.belief.observed.payload["files"]["checkout.py"] == PREPARE
    assert not second.belief.inferred
    assert set(second.memory.run_ids).isdisjoint(first.memory.run_ids)
    assert second.belief_estimator.counts.hits == 0


async def test_hypothetical_proposals_never_use_or_create_authoritative_belief(tmp_path):
    world = SoftwareWorld()

    class Watching(WorldPlanner):
        hypotheses = 0

        async def propose_hypothetical(self, state, width):
            assert state.kind == "hypothetical"
            self.hypotheses += 1
            return await super().propose_hypothetical(state, width)

        def infer(self, state, experience):
            assert state.kind == "observed"
            assert all(r.observation.state.kind == "observed" for r in experience)
            return super().infer(state, experience)

    planner = Watching(world)
    agent = agent_for(world, tmp_path, planner=planner, reuse_beliefs=True)
    await agent.run(world, 2)
    assert planner.hypotheses > 0
    assert agent.belief_estimator.counts.hits == 0
    assert agent.belief.observed == (await world.observe()).model_copy(
        update={"timestamp": agent.belief.observed.timestamp}
    )


async def test_search_requires_explicit_planner_support(tmp_path):
    class ObservedOnly:
        def infer(self, state, experience):
            return Belief(observed=state)

        def propose(self, belief, goals, width):
            return []

    with pytest.raises(ValueError, match="hypothetical proposal support"):
        agent_for(SoftwareWorld(), tmp_path, planner=ObservedOnly())


async def test_episode_budget_does_not_refill_calls_between_software_rounds(tmp_path):
    world = SoftwareWorld()
    agent = agent_for(world, tmp_path, policy=Policy(max_calls=8), episode_budget=True)
    result = await agent.run(world, 4)
    assert result.status == "abstained" and len(result.rounds) == 1
    assert result.rounds[0]["calls"] == 8 and agent.policy.max_calls == 8
    assert world.payload["files"]["checkout.py"] == PREPARE
    assert any(
        e["kind"] == "cognitive_budget_exhausted"
        for e in agent.store.read_events(result.run_ids[0])
    )


async def test_episode_budget_respects_source_task_action_limit(tmp_path):
    world = SoftwareWorld()
    world.task.max_steps = 1
    agent = agent_for(world, tmp_path, episode_budget=True)
    result = await agent.run(world, 20)
    assert len(result.rounds) == 1 and len(world.receipts) == 1 and not result.success


async def test_generator_external_usage_reservations_are_not_hidden(tmp_path):
    world = SoftwareWorld()

    class Expensive:
        proposal_calls = 3
        usage = []

        async def propose(self, state, width):
            raise AssertionError("Budget must reject before external computation")

    agent = agent_for(
        world,
        tmp_path,
        planner=WorldPlanner(world, Expensive()),
        policy=Policy(max_calls=2),
    )
    result = await agent.run(world, 1)
    assert result.status == "abstained" and not world.receipts


async def test_receipt_memory_reorders_only_actual_unsuccessful_same_state_attempts(tmp_path):
    # A real, safe unsuccessful patch is remembered; restoring the repository makes
    # it a same-state retry. This is an explicit developer intervention, not reset
    # reuse of an Agent or inference from a hypothetical verifier result.
    world = SoftwareWorld()
    world.payload = {"files": {"checkout.py": PREPARE}, "stage": "prepared", "goal_progress": 0.25}

    class Generator:
        proposal_usage_complete = True

        async def propose(self, state, width):
            safe = (await world.propose(state, 2))[0]
            no_progress = safe.model_copy(deep=True)
            no_progress.payload = {"files": {"checkout.py": PREPARE}, "stage": "prepared"}
            return [no_progress, safe][:width]

    planner = WorldPlanner(world, Generator())
    # Native materialization sets progress=.25; execution adds goal_complete=False.
    world.payload["goal_complete"] = False
    original = await world.observe()
    agent = agent_for(world, tmp_path, planner=planner, policy=Policy(search=False, width=1))
    result = await agent.run(world, 1)
    assert not result.success and not result.unsafe
    records = await agent.memory.retrieve("software")
    assert len(records) == 1 and records[0].input_state.id == original.id
    belief = planner.infer(await world.observe(), records)
    actions = await planner.propose(belief, [], 2)
    assert actions[0].payload["files"]["checkout.py"] == SAFE
    assert next(iter(belief.inferred.values())).source_refs == [records[0].reference]
    no_memory = await planner.propose(planner.infer(await world.observe(), []), [], 2)
    assert no_memory[0].payload["files"]["checkout.py"] == PREPARE
    assert not planner.infer(
        State.create("software", original.payload, "different-provenance"), records
    ).inferred


async def test_software_benchmark_is_paired_and_audit_rejects_metric_tampering(tmp_path):
    from scripts.audit_software_cognition import audit
    from scripts.bench_software_cognition import aggregate, benchmark

    protocol = json.loads(Path("benchmarks/software-cognition-v1.json").read_text())
    protocol.update(seeds=[11], repeats=1)
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol))
    report_path, raw = tmp_path / "report.json", tmp_path / "raw"
    report = await benchmark(protocol_path, raw, report_path)
    checked = await audit(protocol_path, report_path, raw)
    assert checked["episodes"] == 9 and checked["raw_authority_checked"]
    assert report["semantic_pairs_equal"]
    assert report["aggregate"]["cognitive"]["memory_retry_signals"] == 0
    report["episodes"][0]["metrics"]["verifications"] += 1
    report["aggregate"] = aggregate(report["episodes"])
    report_path.write_text(json.dumps(report))
    with pytest.raises(AssertionError):
        await audit(protocol_path, report_path, raw)
