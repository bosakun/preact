import pytest

from preact.cognition import CognitiveAgent, EpisodicMemory, Goal
from preact.cognition.models import Experience
from preact.cognition.queue import QueuePlanner
from preact.core.decision import evaluate, gate
from preact.core.evidence import bind_claims
from preact.core.models import Decision, Observation, Policy, PredictionRequest, State
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import CognitiveQueueWorld
from preact.engines.cognitive_queue import QueueBoundVerifier, QueueForecast


def agent_for(world, tmp_path, *, adaptation=True, use_memory=True, max_calls=24, future=True):
    store = Store("sqlite:///:memory:")
    planner = QueuePlanner(adaptation=adaptation)
    immediate = QueueBoundVerifier(world.task)
    engines = [immediate]
    if future:
        engines.append(QueueBoundVerifier(world.task, future=True))
    agent = CognitiveAgent(
        store,
        Artifacts(str(tmp_path)),
        Registry(engines),
        planner,
        [Goal(name="Throughput", metric="delivered", target=world.target)],
        Policy(search=False, calibration=False, max_nodes=3, max_calls=max_calls),
        use_memory=use_memory,
    )
    forecast = QueueForecast(world.task, planner, agent.memory, use_memory=use_memory)
    agent.registry = Registry([forecast, *engines])
    return agent


async def test_end_to_end_delayed_partial_world_uses_gate_and_committed_memory(tmp_path):
    world = CognitiveQueueWorld(ticks=16, target=20, shift_tick=8, noise=0)
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 16)
    assert not result.unsafe and len(world.executed) == 16
    assert len(agent.memory.run_ids) == 16
    records = await agent.memory.retrieve(world.task.domain)
    assert len(records) == 12
    assert all(r.observation.state.kind == "observed" for r in records)
    assert agent.belief.observed.payload["tick"] == 16
    assert "service" not in agent.belief.observed.payload
    assert agent.belief.inferred["service"].value < 2
    for run_id in result.run_ids:
        events = agent.store.read_events(run_id)
        assert len([e for e in events if e["kind"] == "outcome"]) == 1
        assert len([e for e in events if e["kind"] == "authorization"]) == 1
        assert not any(e["data"].get("direct") for e in events if e["kind"] == "decision")
        outcomes = [e for e in events if e["kind"] == "outcome"]
        predictions = [e for e in events if e["kind"] == "prediction"]
        chosen_ids = {e["data"]["node_id"] for e in outcomes}
        labeled = {r["prediction_id"] for r in agent.store.error_rows()}
        assert all(
            p["data"]["prediction"]["id"] not in labeled
            for p in predictions
            if p["data"]["node_id"] not in chosen_ids or p["data"]["prediction"]["horizon"] != 1
        )


@pytest.mark.parametrize("max_calls,future", [(1, True), (24, False)])
async def test_cognitive_loop_cannot_execute_without_required_evidence(tmp_path, max_calls, future):
    world = CognitiveQueueWorld(ticks=3, target=2)
    agent = agent_for(world, tmp_path, max_calls=max_calls, future=future)
    result = await agent.run(world, 3)
    assert result.status == "abstained" and not world.executed
    assert agent.memory.run_ids == []
    previews = [
        e["data"]["gate"]["decision"]
        for e in agent.store.read_events(result.run_ids[0])
        if e["kind"] == "gate_preview"
    ]
    assert "abstain" in previews or any(
        e["kind"] == "decision" and e["data"]["decision"] == "abstain"
        for e in agent.store.read_events(result.run_ids[0])
    )


async def test_inference_cannot_become_a_safety_measurement(tmp_path):
    world = CognitiveQueueWorld()
    state = await world.observe()
    action = (await world.propose(state, 1))[0]
    forecast = QueueForecast(
        world.task, QueuePlanner(), EpisodicMemory(Store("sqlite:///:memory:"))
    )
    prediction = await forecast.predict(PredictionRequest(state=state, actions=[action]))
    evaluation = evaluate([prediction], world.task, {}, state=state, action=action)
    assert (
        gate(evaluation, state.id, action.fingerprint, Policy(), True).decision == Decision.VERIFY
    )
    assert not prediction.success.measured
    assert not evaluation.success_lower


async def test_reset_and_new_episode_do_not_leak_hidden_state_or_experience(tmp_path):
    world = CognitiveQueueWorld(ticks=6, target=2, shift_tick=0, noise=0)
    first = agent_for(world, tmp_path / "first")
    await first.run(world, 6)
    assert first.belief.inferred["service"].value < 3
    world.reset()
    assert (await world.observe()).payload["tick"] == 0
    assert world.executed == [] and world.reward == 0
    second = agent_for(world, tmp_path / "second")
    await second.run(world, 1)
    assert second.belief.inferred["service"].value == 3
    assert second.memory.run_ids != first.memory.run_ids


async def test_execution_failure_keeps_pending_receipt_and_never_learns_success(tmp_path):
    class Broken(CognitiveQueueWorld):
        async def execute(self, action, receipt):
            raise RuntimeError("Injected real executor failure")

    world = Broken()
    agent = agent_for(world, tmp_path)
    with pytest.raises(RuntimeError, match="executor failure"):
        await agent.run(world, 1)
    run = agent.store.list_runs()[0]
    assert run["status"] == "interrupted"
    assert agent.store.pending_execution(run["id"])
    assert agent.memory.run_ids == [] and agent.store.error_rows() == []


async def test_memory_restores_from_ledger_and_rejects_forged_outcome(tmp_path):
    world = CognitiveQueueWorld()
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 2)
    restored = EpisodicMemory(agent.store)
    for run_id in result.run_ids:
        await restored.remember(run_id)
        await restored.remember(run_id)
    assert len(await restored.retrieve(world.task.domain)) == 2
    events = agent.store.read_events(result.run_ids[0])
    outcome = next(e for e in events if e["kind"] == "outcome")
    forged = {**outcome["data"], "observation": {**outcome["data"]["observation"], "unsafe": True}}
    agent.store.append(result.run_ids[0], "outcome", forged)
    with pytest.raises(ValueError, match="committed"):
        await restored.retrieve(world.task.domain)


async def test_observer_and_planner_cannot_replace_actual_facts(tmp_path):
    class PredictiveObserver(CognitiveQueueWorld):
        async def observe(self):
            state = await super().observe()
            return state.model_copy(update={"kind": "hypothetical"})

    world = PredictiveObserver()
    with pytest.raises(ValueError, match="authoritative"):
        await agent_for(world, tmp_path).run(world, 1)

    class MutatingPlanner(QueuePlanner):
        def infer(self, state, experience):
            return super().infer(
                State.create(state.domain, {**state.payload, "queue": 0.1}, state.provenance),
                experience,
            )

    world = CognitiveQueueWorld()
    agent = agent_for(world, tmp_path)
    agent.planner = MutatingPlanner()
    with pytest.raises(ValueError, match="observed facts"):
        await agent.run(world, 1)
    assert not world.executed


async def test_learning_uses_uncensored_actual_samples_and_ablation_disables_it():
    world = CognitiveQueueWorld()
    state = await world.observe()
    action = (await world.propose(state, 1))[0]

    def record(available, processed):
        return Experience(
            reference="real:7",
            run_id="real",
            input_state=state,
            action=action,
            prediction_ids=[],
            observation=Observation(
                state=state,
                success=False,
                unsafe=False,
                receipt="unit-test",
                metrics={"available_work": available, "processed": processed},
            ),
        )

    planner = QueuePlanner()
    assert planner.infer(state, [record(1, 1)]).inferred["service"].value == 3
    learned = planner.infer(state, [record(3, 1)]).inferred["service"]
    assert learned.value == 2 and learned.source_refs == ["real:7"]
    assert (
        QueuePlanner(adaptation=False).infer(state, [record(3, 1)]).inferred["service"].value == 3
    )
    assert QueuePlanner(prior=1).infer(state, [record(3, 3)]).inferred["service"].value == 2


async def test_delayed_overflow_bound_refutes_action_that_is_immediately_safe():
    world = CognitiveQueueWorld()
    state = State.create(world.task.domain, {**world.payload, "queue": 8}, "test-actual")
    action = world.action(state, 3)
    immediate = await QueueBoundVerifier(world.task).predict(
        PredictionRequest(state=state, actions=[action])
    )
    assert immediate.mandatory_checks["capacity"] is True
    claims = bind_claims(world.task, state.id, [action])[-1:]
    future = await QueueBoundVerifier(world.task, future=True).predict(
        PredictionRequest(state=state, actions=[action], horizon=3, claims=claims)
    )
    assert future.claim_results[0].check is False


async def test_no_memory_disables_cross_round_learning_even_though_ledger_is_retained(tmp_path):
    world = CognitiveQueueWorld(ticks=8, target=20, shift_tick=0, noise=0)
    agent = agent_for(world, tmp_path, use_memory=False)
    await agent.run(world, 8)
    assert agent.memory.run_ids
    assert agent.belief.inferred["service"].value == 3
    assert agent.belief.inferred["service"].source_refs == []


async def test_hidden_regime_is_not_available_to_planner_or_engines():
    fast = CognitiveQueueWorld(high_first=True, noise=0)
    slow = CognitiveQueueWorld(high_first=False, noise=0)
    assert fast._service != slow._service
    first, second = await fast.observe(), await slow.observe()
    assert first.payload == second.payload and first.id == second.id
    planner = QueuePlanner()
    goals = [Goal(name="Throughput", metric="delivered", target=85)]
    assert [a.payload for a in planner.propose(planner.infer(first, []), goals, 3)] == [
        a.payload for a in planner.propose(planner.infer(second, []), goals, 3)
    ]
    assert "shift_tick" not in first.payload and "high_first" not in first.payload


async def test_correlated_service_forecasts_cannot_strengthen_gate_evidence():
    world = CognitiveQueueWorld()
    state = await world.observe()
    action = (await world.propose(state, 1))[0]
    memory = EpisodicMemory(Store("sqlite:///:memory:"))
    predictions = [
        await QueueForecast(world.task, QueuePlanner(), memory, engine_id=name).predict(
            PredictionRequest(state=state, actions=[action])
        )
        for name in ["biased-model-a", "biased-model-b"]
    ]
    first = evaluate(predictions[:1], world.task, {}, state=state, action=action)
    repeated = evaluate(predictions, world.task, {}, state=state, action=action)
    assert repeated.success == first.success
    assert repeated.success_lower == 0 and repeated.risk_upper == 1
    assert (
        gate(repeated, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN
    )


async def test_observed_outcomes_change_candidate_ranking_without_any_safety_override(tmp_path):
    world = CognitiveQueueWorld(ticks=18, target=85, shift_tick=0, noise=0)
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 18)
    amounts = [a.payload["amount"] for a in world.executed]
    assert amounts[0] == 3
    assert any(amount < 3 for amount in amounts[4:])
    assert not result.unsafe
    assert any(
        e["data"]["prediction"].get("raw", {}).get("memory_refs")
        for run_id in result.run_ids
        for e in agent.store.read_events(run_id)
        if e["kind"] == "prediction"
    )


async def test_agent_lifecycle_rejects_reuse_after_environment_reset(tmp_path):
    world = CognitiveQueueWorld(ticks=4, target=2)
    agent = agent_for(world, tmp_path)
    await agent.run(world, 4)
    world.reset()
    with pytest.raises(ValueError, match="fresh cognitive agent"):
        await agent.run(world, 4)
    assert not world.executed


async def test_domain_task_change_is_detected_through_wrapper_before_execution(tmp_path):
    class ChangingTask(CognitiveQueueWorld):
        async def observe(self):
            state = await super().observe()
            self.task.required_checks.clear()
            return state

    world = ChangingTask()
    agent = agent_for(world, tmp_path)
    with pytest.raises(ValueError, match="constraints changed"):
        await agent.run(world, 1)
    assert not world.executed


async def test_repeated_outcome_source_is_one_memory_item(tmp_path):
    world = CognitiveQueueWorld()
    agent = agent_for(world, tmp_path)
    result = await agent.run(world, 1)
    events = agent.store.read_events(result.run_ids[0])
    outcome = next(e for e in events if e["kind"] == "outcome")
    agent.store.append(result.run_ids[0], "outcome", outcome["data"])
    assert len(await agent.memory.retrieve(world.task.domain)) == 1
