import asyncio

import pytest

from preact.cognition import CognitiveAgent, EpisodicMemory, Goal
from preact.cognition.information import InformationModel, InformationQueuePlanner
from preact.core.decision import evaluate, gate
from preact.core.evidence import bind_claims
from preact.core.models import Decision, Policy, PredictionRequest, State
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import CognitiveQueueWorld
from preact.domains.information_queue import InformationQueueWorld
from preact.engines.information_queue import InformationBoundVerifier, InformationForecast


def agent_for(world, path, *, strategy="periodic", future=True, max_calls=32, **kwargs):
    planner = InformationQueuePlanner(strategy)
    agent = CognitiveAgent(
        Store("sqlite:///:memory:"),
        Artifacts(str(path)),
        Registry([]),
        planner,
        [Goal(name="Jobs", metric="delivered", target=world.target)],
        Policy(search=False, calibration=False, width=4, max_nodes=4, max_calls=max_calls),
        **kwargs,
    )
    engines = [InformationForecast(world.task, planner, agent.memory, use_memory=agent.use_memory)]
    engines.append(InformationBoundVerifier(world.task))
    if future:
        engines.append(InformationBoundVerifier(world.task, future=True))
    agent.registry = Registry(engines)
    return agent


async def sample(tmp_path, **kwargs):
    world = InformationQueueWorld(ticks=8, target=20, noise=0, **kwargs)
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 1)
    records = await agent.memory.retrieve(world.task.domain)
    return world, agent, result, records


async def test_probe_contract_tick_cost_and_drain_dynamics(tmp_path):
    world, agent, result, records = await sample(tmp_path, probe_cost=0.7)
    record = records[0]
    assert record.action.kind == "probe_service" and record.action.payload == {}
    assert record.action.duration == 1 and world.payload["tick"] == 1
    assert record.observation.metrics["measurement_tick"] == 0
    assert record.observation.metrics["probe_cost"] == 0.7 and world.reward == -0.7
    assert record.observation.checks["probe_success"] is True
    assert record.observation.state == agent.belief.observed.model_copy(
        update={"timestamp": record.observation.state.timestamp}
    )
    events = agent.store.read_events(result.run_ids[0])
    kinds = [e["kind"] for e in events]
    assert kinds.index("authorization") < kinds.index("execution_intent") < kinds.index("outcome")
    assert kinds.count("authorization") == kinds.count("outcome") == 1
    execution = agent.store.execution_record(record.observation.receipt)
    assert execution["status"] == "complete"
    assert execution["receipt"] == record.observation.model_dump()
    assert execution["action_hash"] == record.action.fingerprint
    for change in ({"payload": {"amount": 0}}, {"duration": 2}, {"state_id": "stale"}):
        with pytest.raises(ValueError, match="Invalid"):
            world.validate(record.input_state, record.action.model_copy(update=change))
    with pytest.raises(ValueError):
        CognitiveQueueWorld().validate(record.input_state, record.action)


async def test_probe_processes_pending_work_as_drain(tmp_path):
    # Controlled domain-level comparison, with real durable Store intents.
    observations = []
    for probe in (False, True):
        world = InformationQueueWorld(noise=0, probe_cost=0.6)
        world.payload.update(queue=1, pending=[{"due": 1, "amount": 3}])
        state = await world.observe()
        action = world.probe(state) if probe else world.action(state, 0)
        store = Store("sqlite:///:memory:")
        run = store.create_run({})
        receipt = store.intent(run, state.id, action.fingerprint)
        obs = await world.execute(action, receipt)
        store.complete_execution(receipt, obs.model_dump())
        observations.append(obs)
    drain, probe = observations
    assert probe.state.payload == drain.state.payload
    assert probe.metrics["processed"] == drain.metrics["processed"] == 3
    assert probe.metrics["reward"] == pytest.approx(drain.metrics["reward"] - 0.6)
    assert "measured_service" not in drain.metrics


async def test_v2_observation_deep_copy_cannot_mutate_environment():
    world = InformationQueueWorld()
    state = await world.observe()
    state.payload["service_values"].clear()
    state.payload["service_bounds"].clear()
    state.payload["pending"].append({"due": 1, "amount": 999})
    actual = await world.observe()
    assert actual.payload["service_values"] == actual.payload["service_bounds"] == [1, 3]
    assert actual.payload["pending"] == []


@pytest.mark.parametrize("max_calls,future", [(1, True), (32, False)])
async def test_insufficient_evidence_abstains_without_measurement(tmp_path, max_calls, future):
    world = InformationQueueWorld(ticks=3)
    agent = agent_for(world, tmp_path, max_calls=max_calls, future=future)
    result = await agent.run(world, 3)
    assert result.status == "abstained" and not world.executed
    assert world.payload["tick"] == 0 and world.reward == 0
    assert await agent.memory.retrieve(world.task.domain) == []


async def test_private_schedule_unavailable_before_execution_and_inference_is_not_proof(tmp_path):
    fast, slow = InformationQueueWorld(noise=0), InformationQueueWorld(noise=0, high_first=False)
    states = [await w.observe() for w in (fast, slow)]
    assert states[0].payload == states[1].payload and states[0].id == states[1].id
    assert not {
        "service",
        "measured_service",
        "measurement_tick",
        "high_first",
        "shift_tick",
    } & set(states[0].payload)
    agent = agent_for(fast, tmp_path)
    beliefs = [agent.planner.infer(s, []) for s in states]
    assert beliefs[0].inferred == beliefs[1].inferred

    # Trap any hidden schedule access by perception, proposal or either engine.
    class Forbidden:
        def __getitem__(self, key):
            raise AssertionError("Private schedule read before execution")

    fast._service = Forbidden()
    state = await fast.observe()
    action = fast.probe(state)
    request = PredictionRequest(state=state, actions=[action])
    prediction = await agent.registry.engines[0].predict(request)
    assert "measured_service" not in prediction.metrics and "measurement_tick" not in prediction.raw
    evaluation = evaluate([prediction], fast.task, {}, state=state, action=action)
    assert (
        gate(evaluation, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    )
    assert not prediction.success.measured
    await InformationBoundVerifier(fast.task).predict(request)
    claims = bind_claims(fast.task, state.id, [action])[-1:]
    await InformationBoundVerifier(fast.task, future=True).predict(
        request.model_copy(update={"claims": claims, "horizon": 3})
    )
    agent.planner.propose(beliefs[0], agent.goals, 4)


async def test_probe_learning_exact_past_sample_deduplication_and_decay(tmp_path):
    world, agent, _, records = await sample(tmp_path, high_first=False)
    state = await world.observe()
    belief = agent.planner.infer(state, records + records)
    assert belief.inferred["probe_samples"].value == 1
    assert belief.inferred["service_high_probability"].value == pytest.approx(0.1)
    assert belief.inferred["service"].source_refs == [records[0].reference]
    assert "current_service" in belief.unknown and "measured_service" not in state.payload
    later = State.create(state.domain, {**state.payload, "tick": 7}, state.provenance)
    assert agent.planner.infer(later, records).inferred["service_high_probability"].value > 0.39
    assert agent.planner.infer(state, []).inferred["service_high_probability"].value == 0.5


@pytest.mark.parametrize("mutation", ["pending", "aborted", "forged"])
async def test_memory_rejects_uncommitted_and_forged_probe(tmp_path, mutation):
    world, agent, result, records = await sample(tmp_path)
    receipt = records[0].observation.receipt
    run = result.run_ids[0]
    if mutation == "forged":
        outcome = next(e["data"] for e in agent.store.read_events(run) if e["kind"] == "outcome")
        changed = {
            **outcome["observation"],
            "metrics": {**outcome["observation"]["metrics"], "measured_service": 1},
        }
        agent.store.append(run, "outcome", {**outcome, "observation": changed})
    else:
        from sqlalchemy import update

        with agent.store.db.begin() as conn:
            conn.execute(
                update(agent.store.executions)
                .where(agent.store.executions.c.id == receipt)
                .values(status=mutation)
            )
    with pytest.raises(ValueError, match="committed"):
        await agent.memory.retrieve(world.task.domain)


async def test_unexecuted_probe_and_failed_measurement_are_not_learned(tmp_path):
    world = InformationQueueWorld(ticks=4, target=9)
    agent = agent_for(world, tmp_path, strategy="voi")
    await agent.run(world, 1)
    records = await agent.memory.retrieve(world.task.domain)
    assert records[0].action.kind == "submit" and agent.belief.inferred["probe_samples"].value == 0
    world, agent, _, records = await sample(tmp_path / "executed")
    record = records[0].model_copy(deep=True)
    record.observation.checks["probe_success"] = False
    assert agent.planner.infer(await world.observe(), [record]).inferred["probe_samples"].value == 0
    record.observation.checks["probe_success"] = True
    record.observation.metrics["measurement_tick"] += 1
    with pytest.raises(ValueError, match="future tick"):
        agent.planner.infer(await world.observe(), [record])


async def test_voi_positive_negative_and_periodic_are_distinct():
    goals = [Goal(name="Jobs", metric="delivered", target=35)]
    values = []
    for holding, cost in ((1, 0.05), (1, 2), (0.25, 0.05)):
        state = await InformationQueueWorld(
            ticks=24, target=35, holding_cost=holding, probe_cost=cost
        ).observe()
        planner = InformationQueuePlanner()
        d = planner.decide(planner.infer(state, []), goals)
        assert d.net_voi == pytest.approx(d.information_gain - d.opportunity_cost - cost)
        values.append(d.request_probe)
    assert values == [True, False, False]
    assert d.uncertainty == 1 and d.action_change_probability > 0
    assert InformationQueuePlanner("periodic").decide(planner.infer(state, []), goals).request_probe
    assert not planner.decide(planner.infer(state, []), []).request_probe


async def test_no_memory_reset_reuse_optout_and_configuration_change(tmp_path):
    world = InformationQueueWorld(ticks=8, target=20, noise=0)
    agent = agent_for(world, tmp_path, use_memory=False, reuse_beliefs=True)
    result = await agent.run(world, 2)
    assert agent.belief.inferred["probe_samples"].value == 0
    assert agent.belief_estimator.counts.hits == 0
    assert len(world.executed) == 2 and result.final_state.payload["tick"] == 2
    world.reset()
    fresh = agent_for(world, tmp_path / "new")
    assert fresh.memory.run_ids == [] and fresh.belief is None
    state = await world.observe()
    before = fresh.planner.infer(state, []).inferred["service"].value
    fresh.planner.model = InformationModel(prior_high=0.8)
    assert fresh.planner.infer(state, []).inferred["service"].value != before


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_execution_failure_or_cancellation_leaves_no_sensor_learning(tmp_path, error):
    class Broken(InformationQueueWorld):
        async def execute(self, action, receipt):
            raise error("Executor interrupted")

    world = Broken()
    agent = agent_for(world, tmp_path, reuse_beliefs=True)
    with pytest.raises(error):
        await agent.run(world, 1)
    assert world.payload["tick"] == 0 and not world.executed
    assert agent.memory.run_ids == [] and agent.belief is None


async def test_v1_normal_action_semantics_preserved():
    old, new = CognitiveQueueWorld(seed=7), InformationQueueWorld(seed=7)
    for n in (3, 1, 0, 3, 0):
        a, b = await old.observe(), await new.observe()
        one = await old.execute(old.action(a, n), "domain-test")
        two = await new.execute(new.action(b, n), "a" * 64)
        for key in a.payload:
            assert one.state.payload[key] == two.state.payload[key]
        assert old.reward == new.reward and old.unsafe == new.unsafe
        assert "measured_service" not in one.metrics and "measured_service" not in two.metrics


@pytest.mark.parametrize(
    "options", [{"horizon": 2.5}, {"period": 1.5}, {"persistence": 1}, {"prior_high": 0}]
)
def test_invalid_model_configuration_fails_closed(options):
    with pytest.raises(ValueError):
        InformationModel(**options)


async def test_external_store_forgery_invalidates_warm_probe_memory(tmp_path):
    from sqlalchemy import update

    world = InformationQueueWorld(ticks=8, noise=0)
    agent = agent_for(world, tmp_path)
    disk = Store(f"sqlite:///{tmp_path / 'external.sqlite'}")
    agent.store = disk
    agent.memory = EpisodicMemory(disk)
    agent.registry.engines[0].memory = agent.memory
    result = await agent.run(world, 1)
    record = (await agent.memory.retrieve(world.task.domain))[0]
    other = Store(f"sqlite:///{tmp_path / 'external.sqlite'}")
    with other.db.begin() as conn:
        conn.execute(
            update(other.executions)
            .where(other.executions.c.id == record.observation.receipt)
            .values(status="pending")
        )
    with pytest.raises(ValueError, match="committed"):
        await agent.memory.retrieve(world.task.domain)
    assert len(result.run_ids) == 1
    other.db.dispose()
    disk.db.dispose()


async def test_probe_cannot_override_unsafe_public_bound(tmp_path):
    world = InformationQueueWorld(ticks=8)
    world.payload.update(queue=8, pending=[{"due": 1, "amount": 3}])
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 1)
    assert result.status == "abstained" and world.payload["tick"] == 0
    assert not world.executed and world.reward == 0


async def test_committed_probe_knowledge_cannot_replace_missing_safety_verifier(tmp_path):
    world, first, _, records = await sample(tmp_path)
    second = agent_for(world, tmp_path / "second", future=False)
    # Explicitly restore verified same-world experience to test the evidence boundary.
    second.store, second.memory = first.store, first.memory
    second.registry.engines[0].memory = second.memory
    result = await second.run(world, 1)
    assert second.belief.inferred["service"].source_refs == [records[0].reference]
    assert result.status == "abstained" and world.payload["tick"] == 1
    assert len(world.executed) == 1


async def test_paired_benchmark_reproduction_and_no_probe_budget_control(tmp_path):
    import json
    from pathlib import Path

    from scripts.audit_information_seeking import audit_raw, without_policy_hash
    from scripts.bench_information_seeking import episode

    protocol = json.loads(Path("benchmarks/information-seeking-pilot-v1.json").read_text())
    config = protocol["world"] | {"seed": 31, "ticks": 4, "shift_tick": 2}
    one = await episode(config, "voi", tmp_path / "one", protocol)
    two = await episode(config, "voi", tmp_path / "two", protocol)
    assert one["semantic_sha256"] == two["semantic_sha256"]
    control = await episode(config, "no_probe", tmp_path / "control", protocol)
    budget = await episode(config, "no_probe", tmp_path / "budget", protocol, old_budget=True)
    assert control["behavior_sha256"] == budget["behavior_sha256"]
    assert control["metrics"]["engine_calls"] == budget["metrics"]["engine_calls"]
    normal_trace = audit_raw(control | {"budget_control": False}, protocol, tmp_path / "control")
    budget_trace = audit_raw(budget | {"budget_control": True}, protocol, tmp_path / "budget")
    assert without_policy_hash(normal_trace) == without_policy_hash(budget_trace)
    events_path = tmp_path / "control" / "events.json"
    events = json.loads(events_path.read_text())
    next(e["data"]["gate"] for e in events if e["kind"] == "gate_preview")["policy_hash"] = "forged"
    events_path.write_text(json.dumps(events))
    with pytest.raises(AssertionError):
        audit_raw(control | {"budget_control": False}, protocol, tmp_path / "control")


async def test_new_planner_never_reuses_stale_tick_memory_or_changed_settings(tmp_path):
    from preact.cognition.belief import BeliefEstimator

    world, agent, _, records = await sample(tmp_path, high_first=False)
    estimator = BeliefEstimator(enabled=True)
    estimator.begin(world.task)
    state = await world.observe()
    one = await estimator.infer(state, agent.planner, agent.memory, task=world.task)
    assert one.inferred["service_high_probability"].value == pytest.approx(0.1)
    later = State.create(state.domain, {**state.payload, "tick": 2}, state.provenance)
    two = await estimator.infer(later, agent.planner, agent.memory, task=world.task)
    assert two.observed == later and two.inferred["service_high_probability"].value > 0.1
    agent.planner.model = InformationModel(persistence=0.7)
    three = await estimator.infer(later, agent.planner, agent.memory, task=world.task)
    assert three.inferred != two.inferred
    assert estimator.counts.hits == 0 and estimator._entry is None
    assert records[0].observation.metrics["measured_service"] == 1
    estimator.end()


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_inference_failure_never_falls_back_to_old_belief(tmp_path, error):
    class Broken(InformationQueuePlanner):
        def __init__(self):
            super().__init__("periodic")
            self.calls = 0

        def infer(self, *args):
            self.calls += 1
            if self.calls > 1:
                raise error("Fresh inference failed")
            return super().infer(*args)

    world = InformationQueueWorld()
    agent = agent_for(world, tmp_path, reuse_beliefs=True)
    agent.planner = Broken()
    agent.registry.engines[0].planner = agent.planner
    with pytest.raises(error):
        await agent.run(world, 1)
    assert agent.belief is None and agent.belief_estimator._entry is None
    assert not world.executed and world.payload["tick"] == 0
