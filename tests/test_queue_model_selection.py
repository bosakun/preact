import asyncio

import pytest

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.queue_model import MonitoredQueueSource, QueueModelPlanner
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_model_selection import QueueSelectionWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_temporal import QueueTemporalEngine
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from scripts.benchmark_queue_temporal import execute_sequence

POLICY = dict(search=False, calibration=False, width=1, max_nodes=1, max_calls=32)


async def setup(tmp_path, *, warmup=8, mode="learned", engines=True, max_calls=32):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    training_world = InformationQueueWorld(
        seed=11, ticks=48, target=999, high_first=False, shift_tick=48, noise=0, capacity=12
    )
    training = {
        "training": await execute_sequence(training_world, ["probe"] * 16, store, artifacts, POLICY)
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    world = InformationQueueWorld(
        seed=101, ticks=48, target=30, high_first=False, shift_tick=48, noise=0, capacity=12
    )
    initial = await world.observe()
    guard = QueueTemporalGuard(
        store, world.task, model, training, episode_id="monitor", initial=initial
    )
    prefix = await execute_sequence(world, ["probe"] * warmup, store, artifacts, POLICY)
    source = MonitoredQueueSource(store, guard, training)
    planner = QueueModelPlanner(
        source, prefix_runs=prefix, mode=mode, prior=0.5 if mode == "prior" else None
    )
    wrapper = QueueSelectionWorld(world, planner)
    registry = Registry(
        [InformationBoundVerifier(world.task), InformationBoundVerifier(world.task, future=True)]
        if engines
        else []
    )
    agent = CognitiveAgent(
        store,
        artifacts,
        registry,
        planner,
        [Goal(name="Deliver", metric="delivered", target=30)],
        Policy(**{**POLICY, "max_calls": max_calls}),
        reuse_beliefs=True,
    )
    return store, world, planner, wrapper, agent


async def test_model_selection_executes_exact_selected_action_and_complete_receipt(tmp_path):
    store, world, planner, wrapper, agent = await setup(tmp_path)
    result = await agent.run(wrapper, 3)
    assert len(planner.records) == 3
    assert all(
        r["basis"] == "learned_model" and r["health"]["status"] == "available"
        for r in planner.records
    )
    for record, run in zip(planner.records, result.run_ids):
        assert record["run_id"] == run
        events = store.read_events(run)
        assert len([e for e in events if e["kind"] == "authorization"]) == 1
        assert len([e for e in events if e["kind"] == "cognitive_dispatch_binding"]) == 1
        outcome = next(e["data"]["observation"] for e in events if e["kind"] == "outcome")
        execution = store.execution_record(outcome["receipt"])
        assert execution["status"] == "complete"
        assert execution["action_hash"] == record["selected_fingerprint"]
        assert execution["state_id"] == record["state"]["id"]
    assert wrapper.observation_calls >= 3 * 4
    assert all(r["agent_calls"] == 3 and r["cost_known"] for r in result.rounds)
    assert planner.pending is None
    assert world.payload["tick"] == 11


@pytest.mark.parametrize("mode", ["existing", "fixed", "unavailable"])
async def test_baselines_use_same_gate_and_no_fake_prediction_calls(tmp_path, mode):
    _, _, planner, wrapper, agent = await setup(tmp_path, mode=mode)
    result = await agent.run(wrapper, 1)
    assert result.rounds[0]["steps"] == 1
    assert result.rounds[0]["agent_calls"] == 0
    assert planner.records[0]["basis"] != "learned_model"


async def test_partial_ordinary_experience_can_remain_insufficient(tmp_path):
    _, _, planner, wrapper, agent = await setup(tmp_path, warmup=0)
    await agent.run(wrapper, 24)
    assert planner.records[0]["health"]["status"] == "insufficient_data"
    assert planner.records[0]["basis"] == "fallback"
    assert all(r["basis"] == "fallback" for r in planner.records)
    assert all(r["health"]["effective_count"] < 8 for r in planner.records)


@pytest.mark.parametrize("engines,max_calls", [(False, 32), (True, 2)])
async def test_missing_verifier_or_budget_never_dispatches(tmp_path, engines, max_calls):
    store, world, _, wrapper, agent = await setup(tmp_path, engines=engines, max_calls=max_calls)
    tick = world.payload["tick"]
    result = await agent.run(wrapper, 1)
    assert result.rounds[0]["steps"] == 0 and world.payload["tick"] == tick
    assert not any(e["kind"] == "outcome" for e in store.read_events(result.run_ids[0]))


@pytest.mark.parametrize("change", ["provenance", "uncertainty", "payload", "source", "record"])
async def test_changes_after_selection_stop_before_side_effect(tmp_path, monkeypatch, change):
    store, world, planner, wrapper, agent = await setup(tmp_path)
    original = wrapper.observe
    changed = False

    async def observe():
        nonlocal changed
        if planner.pending is not None and not changed:
            changed = True
            if change == "source":
                store.append(planner.prefix_runs[0], "external_update", {})
            elif change == "record":
                planner.pending["basis"] = "forged"
            else:
                real = await world.observe()
                updates = (
                    {"provenance": "forged"} if change == "provenance" else {"uncertainty": 0.5}
                )
                if change == "payload":
                    real.payload["queue"] += 1
                    updates = {"payload": real.payload}

                async def altered():
                    return real.model_copy(update=updates)

                monkeypatch.setattr(world, "observe", altered)
        return await original()

    monkeypatch.setattr(wrapper, "observe", observe)
    with pytest.raises(ValueError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8 and planner.pending is None


async def test_update_after_durable_intent_stops_before_dispatch(tmp_path, monkeypatch):
    store, world, planner, wrapper, agent = await setup(tmp_path)
    original = store.call

    async def changing(method, *args, **kwargs):
        value = await original(method, *args, **kwargs)
        if method == "intent":
            store.append(planner.prefix_runs[0], "external_update", {})
        return value

    monkeypatch.setattr(store, "call", changing)
    with pytest.raises(ValueError, match="authority changed"):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8


@pytest.mark.parametrize("exception", [asyncio.CancelledError, RuntimeError])
async def test_inference_failure_clears_selection_without_fallback(
    tmp_path, monkeypatch, exception
):
    _, world, planner, wrapper, agent = await setup(tmp_path)

    async def failed(*args):
        raise exception("inference failed")

    monkeypatch.setattr(QueueTemporalEngine, "predict", failed)
    with pytest.raises(exception):
        await agent.run(wrapper, 1)
    assert planner.pending is None and world.payload["tick"] == 8


async def test_policy_and_episode_ownership_are_explicit(tmp_path):
    _, world, planner, wrapper, agent = await setup(tmp_path)
    with pytest.raises(ValueError, match="one actual"):
        QueueSelectionWorld(world, planner)
    with pytest.raises(ValueError, match="width=1"):
        planner.begin_round("wrong", Policy(search=False, width=2), ())
    await agent.run(wrapper, 1)
    with pytest.raises(ValueError, match="fresh cognitive"):
        await agent.run(wrapper, 1)


async def test_two_receipt_trained_models_change_actual_actions_at_same_public_state(tmp_path):
    outcomes = []
    states = []
    for label, services in (("low", [3, 3] + [1] * 14), ("mixed", [3, 1] * 8)):
        directory = tmp_path / label
        directory.mkdir()
        store = Store("sqlite:///" + str(directory / "ledger.db"))
        artifacts = Artifacts(str(directory / "artifacts"))
        training_world = InformationQueueWorld(seed=11, ticks=48, target=999, capacity=12, noise=0)
        # Owned executor fixture, not model parameter replacement or a prediction oracle.
        training_world._service = services + [1] * 32
        training = {
            label: await execute_sequence(training_world, ["probe"] * 16, store, artifacts, POLICY)
        }
        model = await TabularDynamicsTrainer().fit(
            TransitionDataset(store, training), QueueServiceAdapter()
        )
        world = InformationQueueWorld(seed=101, ticks=48, target=20, capacity=12, noise=0)
        world._service = [3, 3] + [1] * 46
        initial = await world.observe()
        guard = QueueTemporalGuard(
            store, world.task, model, training, episode_id="monitor", initial=initial
        )
        prefix = await execute_sequence(
            world, ["probe"] * 8 + [3, 3, 0, 0, 0], store, artifacts, POLICY
        )
        states.append((await world.observe()).model_dump(exclude={"timestamp"}))
        planner = QueueModelPlanner(
            MonitoredQueueSource(store, guard, training), prefix_runs=prefix
        )
        wrapper = QueueSelectionWorld(world, planner)
        agent = CognitiveAgent(
            store,
            artifacts,
            Registry(
                [
                    InformationBoundVerifier(world.task),
                    InformationBoundVerifier(world.task, future=True),
                ]
            ),
            planner,
            [Goal(name="Deliver", metric="delivered", target=20)],
            Policy(**POLICY),
        )
        result = await agent.run(wrapper, 1)
        record = planner.records[0]
        assert record["basis"] == "learned_model" and record["health"]["status"] == "available"
        assert result.rounds[0]["steps"] == 1
        row = (await TransitionDataset(store, {"actual": result.run_ids}).snapshot()).transitions[0]
        assert row.action.fingerprint == record["selected_fingerprint"]
        outcomes.append(row.action.payload["amount"])
    assert states[0] == states[1]
    assert outcomes == [1, 3]


@pytest.mark.parametrize("status", ["pending", "aborted"])
async def test_bad_source_receipt_does_not_become_normal_fallback(tmp_path, status):
    from sqlalchemy import update

    store, world, planner, wrapper, agent = await setup(tmp_path)
    row = (
        await TransitionDataset(store, {"monitor": list(planner.prefix_runs)}).snapshot()
    ).transitions[0]
    with store.db.begin() as conn:
        conn.execute(
            update(store.executions)
            .where(store.executions.c.id == row.receipt)
            .values(status=status)
        )
    with pytest.raises(ValueError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8 and not planner.records


async def test_forged_source_and_unexecuted_selection_are_not_experience(tmp_path):
    store, world, planner, wrapper, agent = await setup(tmp_path)
    event = next(e for e in store.read_events(planner.prefix_runs[0]) if e["kind"] == "outcome")
    event["data"]["observation"]["metrics"]["measured_service"] = 3
    store.append(planner.prefix_runs[0], "outcome", event["data"])
    with pytest.raises(ValueError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8


async def test_gate_rejects_high_model_score_without_trying_safe_alternative(tmp_path):
    from preact.core.models import Prediction
    from preact.engines.cognitive_queue import exact

    store, world, planner, wrapper, agent = await setup(tmp_path)
    verifier = InformationBoundVerifier(world.task)
    original = verifier.predict

    async def reject(request):
        p = await original(request)
        if request.actions[0].payload["amount"] == 3:
            return Prediction.model_validate(
                {
                    **p.model_dump(),
                    "risk": exact(True).model_dump(),
                    "mandatory_checks": {"capacity": False},
                }
            )
        return p

    verifier.predict = reject
    agent.registry = Registry([verifier, InformationBoundVerifier(world.task, future=True)])
    result = await agent.run(wrapper, 1)
    assert planner.records[0]["selected"]["payload"]["amount"] == 3
    assert planner.records[0]["candidates"][2]["public_bound_safe"]
    assert result.rounds[0]["steps"] == 0 and world.payload["tick"] == 8
    assert not (
        await TransitionDataset(store, {"unexecuted": result.run_ids}).snapshot()
    ).transitions


async def test_cancellation_after_intent_never_executes_or_learns(tmp_path, monkeypatch):
    store, world, planner, wrapper, agent = await setup(tmp_path)
    original = store.call

    async def cancelled(method, *args, **kwargs):
        value = await original(method, *args, **kwargs)
        if method == "intent":
            raise asyncio.CancelledError
        return value

    monkeypatch.setattr(store, "call", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8 and planner.pending is None
    run = planner.records[0]["run_id"]
    snapshot = await TransitionDataset(store, {"cancelled": [run]}).snapshot()
    assert not snapshot.transitions and store.pending_execution(run)


async def test_cached_observation_after_intent_is_rejected_even_with_same_state_id(
    tmp_path, monkeypatch
):
    from preact.core.models import State

    store, world, planner, wrapper, agent = await setup(tmp_path)
    original = store.call

    async def caching(method, *args, **kwargs):
        value = await original(method, *args, **kwargs)
        if method == "intent":
            cached = State.model_validate(planner.pending["state"])

            async def stale():
                return cached.model_copy(deep=True)

            monkeypatch.setattr(world, "observe", stale)
        return value

    monkeypatch.setattr(store, "call", caching)
    with pytest.raises(ValueError, match="fresh after the durable intent"):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8 and planner.pending is None
    assert store.pending_execution(planner.records[0]["run_id"])
    run = planner.records[0]["run_id"]
    snapshot = await TransitionDataset(store, {"stale": [run]}).snapshot()
    assert not snapshot.transitions and store.pending_execution(run)


async def test_forecast_deadline_never_reuses_previous_selection(tmp_path, monkeypatch):
    _, world, planner, wrapper, agent = await setup(tmp_path)

    async def slow(*args):
        await asyncio.sleep(10)

    monkeypatch.setattr(QueueTemporalEngine, "predict", slow)
    agent._policy = Policy(**{**POLICY, "max_seconds": 0.02})
    with pytest.raises(TimeoutError):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8
    assert planner.pending is None and not planner.records


async def test_small_closed_loop_auditor_rejects_selection_and_receipt_trace_tampering(tmp_path):
    import json
    from pathlib import Path

    from scripts.audit_queue_model_selection import audit
    from scripts.benchmark_queue_model_selection import benchmark

    protocol = json.loads(Path("benchmarks/queue-model-selection-v2.json").read_text())
    protocol["evaluation_seeds"] = [101]
    protocol["environments"] = {"stable_low": protocol["environments"]["stable_low"]}
    protocol["modes"] = ["learned", "prior"]
    protocol["recovery_directions"] = []
    protocol["diagnostic_seeds"] = [301]
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    output = tmp_path / "run"
    summary = await benchmark(path, output)
    assert (await audit(path, output))["passed"]
    cases = json.loads((output / "cases.json").read_text())
    left, right = [c["metrics"]["executed_actions"] for c in cases]
    assert left != right
    assert summary["scores"]["stable_low/learned"]["learned_decisions"] > 0
    store = Store("sqlite:///" + str(output / "ledger.db"))
    run = cases[0]["runs"][0]
    event = next(e for e in store.read_events(run) if e["kind"] == "cognitive_selection")
    store.append(run, "cognitive_selection", event["data"])
    with pytest.raises(ValueError, match="duplicate selection"):
        await audit(path, output)


async def test_trace_hook_cannot_mutate_runtime_policy(tmp_path, monkeypatch):
    store, _, planner, wrapper, agent = await setup(tmp_path)
    original = planner.begin_round
    async_calls = []

    def altered(run, policy, previous):
        original(run, policy, previous)
        policy.max_calls = 99999
        policy.min_success = 0
        async_calls.append(policy)

    monkeypatch.setattr(planner, "begin_round", altered)
    result = await agent.run(wrapper, 1)
    actual = store.get_run(result.run_ids[0])["config"]["policy"]
    assert actual["max_calls"] == 32 and actual["min_success"] == agent.policy.min_success
    assert async_calls[0].max_calls == 99999


async def test_world_reset_cannot_reuse_monitored_episode(tmp_path):
    _, world, planner, wrapper, agent = await setup(tmp_path)
    world.reset()
    with pytest.raises(ValueError):
        await agent.run(wrapper, 1)
    assert not planner.records and planner.pending is None


async def test_authorization_expiry_during_extra_dispatch_checks_never_executes(
    tmp_path, monkeypatch
):
    store, world, _, wrapper, agent = await setup(tmp_path)
    original = store.call

    async def delayed(method, *args, **kwargs):
        value = await original(method, *args, **kwargs)
        if method == "append" and args[1] == "cognitive_dispatch_binding":
            monkeypatch.setattr(
                "preact.domains.queue_model_selection.wall_time", lambda: float("inf")
            )
        return value

    monkeypatch.setattr(store, "call", delayed)
    with pytest.raises(ValueError, match="authorization expired"):
        await agent.run(wrapper, 1)
    assert world.payload["tick"] == 8


@pytest.mark.parametrize("direction", ["low_to_high", "high_to_low"])
async def test_explicitly_promoted_model_selects_and_executes_in_actual_new_episode(
    tmp_path, monkeypatch, direction
):
    import json
    from pathlib import Path

    from scripts.audit_queue_model_selection import audit_case
    from scripts.benchmark_queue_model_selection import episode, fit_model, recover_model

    protocol = json.loads(Path("benchmarks/queue-model-selection-v2.json").read_text())
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    parent, training, _ = await fit_model(store, artifacts, protocol, direction == "high_to_low")
    lifecycle, evaluation, model, new_training, digest, preparation = await recover_model(
        store, artifacts, protocol, parent, training, 201, direction
    )
    # Run the real episode through loss of recent samples and later fallback
    # observations; statistical deactivation must not become a stale score.
    protocol["episode_ticks"] = 48
    predict = QueueTemporalEngine.predict
    propose = QueueModelPlanner.propose
    actual_calls = []
    count = 0

    async def counted_predict(self, request):
        nonlocal count
        count += 1
        return await predict(self, request)

    async def counted_propose(self, *args):
        before = count
        value = await propose(self, *args)
        actual_calls.append(count - before)
        return value

    monkeypatch.setattr(QueueTemporalEngine, "predict", counted_predict)
    monkeypatch.setattr(QueueModelPlanner, "propose", counted_propose)
    case = await episode(
        store,
        artifacts,
        protocol,
        model,
        new_training,
        digest,
        "recovered_" + direction,
        201,
        "learned",
        {"high_first": direction == "low_to_high", "shift_tick": 48, "noise": 0},
        lifecycle=lifecycle,
        evaluation=evaluation,
    )
    assert case["promotion"] and case["metrics"]["learned_decisions"] > 0
    assert case["metrics"]["executed_ticks"] == 48
    records = [
        e["data"]
        for r in case["runs"]
        for e in store.read_events(r)
        if e["kind"] == "cognitive_selection"
    ]
    assert actual_calls[0] == 75 and 72 in actual_calls and 0 in actual_calls
    assert actual_calls == [r["local_total_prediction_calls"] for r in records]
    assert any(
        r["reason"] == "not_promoted"
        and r["statistical_deactivation"] is not None
        and r["basis"] == "fallback"
        for r in records
    )
    assert case["metrics"]["local_total_prediction_calls"] == sum(actual_calls)
    assert case["metrics"]["runtime_agent_calls"] == sum(actual_calls)
    assert preparation["preparation_ticks"] == len(preparation["preparation_runs"])
    assert (await audit_case(store, artifacts, protocol, case))[1] == 35
    # A cleared recovery handle (for example after a failed comparison) is not
    # ordinary statistical data shortage and cannot silently select a fallback.
    from preact.core.models import State

    first = next(r for r in records if r["basis"] == "learned_model")
    state, manifest = State.model_validate(first["state"]), first["monitor_runs"]
    # Explicitly reconstruct the real historical available cutoff, as the read-only
    # auditor does. This is not a fresh State for executing an action now.
    await lifecycle.restore(state, manifest)
    source = MonitoredQueueSource.promoted(lifecycle, case["promotion"])
    lifecycle._active = None
    with pytest.raises(ValueError, match="lost its explicitly validated active model"):
        await source.compare(
            state,
            [InformationQueueWorld.action(state, n) for n in (3, 1, 0)],
            manifest,
        )
    # Exercise only call admission with a fresh real World, without attempting
    # a second promotion in the same one-promotion recovery journal.
    from preact.core.runtime import Runtime

    limited = InformationQueueWorld(seed=202, ticks=16, noise=0)
    limited.proposal_calls = QueueModelPlanner(source).proposal_calls
    assert limited.proposal_calls == 75

    async def forbidden(*args):
        raise AssertionError("Insufficient call budget must prevent proposal dispatch")

    monkeypatch.setattr(limited, "propose", forbidden)
    runtime = Runtime(store, artifacts, Registry([]), Policy(**{**POLICY, "max_calls": 32}))
    result = await runtime.run(limited)
    assert result["steps"] == result["agent_calls"] == 0
    assert limited.payload["tick"] == 0
    assert any(e["kind"] == "proposal_skipped" for e in store.read_events(runtime.run_id))
