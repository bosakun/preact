import asyncio

import pytest
from sqlalchemy import update

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.models import Belief
from preact.core.interfaces import EngineFailure
from preact.core.models import Policy, PredictionRequest
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TransitionDataset


class SequencePlanner:
    def __init__(self, sequence, start=0):
        self.sequence, self.start = sequence, start

    def infer(self, state, experiences):
        return Belief(observed=state, unknown=["current_service"])

    def propose(self, belief, goals, width):
        state = belief.observed
        value = self.sequence[state.payload["tick"] - self.start]
        return [
            InformationQueueWorld.probe(state)
            if value == "probe"
            else InformationQueueWorld.action(state, value)
        ][:width]


async def collect(tmp_path, sequence, *, world=None, extra=None, policy=None):
    world = world or InformationQueueWorld(
        ticks=24, target=999, capacity=12, high_first=False, shift_tick=24, noise=0
    )
    store = Store("sqlite:///:memory:")
    engines = [
        InformationBoundVerifier(world.task),
        InformationBoundVerifier(world.task, future=True),
    ]
    if extra is not None:
        engines.insert(0, extra)
    agent = CognitiveAgent(
        store,
        Artifacts(str(tmp_path)),
        Registry(engines),
        SequencePlanner(sequence),
        [Goal(name="Deliver", metric="delivered", target=999)],
        policy or Policy(search=False, calibration=False, width=1, max_nodes=1, max_calls=32),
    )
    result = await agent.run(world, len(sequence))
    return world, store, result, TransitionDataset(store, {"episode": result.run_ids})


async def fitted(tmp_path, sequence=None):
    world, store, result, dataset = await collect(tmp_path, sequence or ["probe"] * 6)
    adapter = QueueServiceAdapter()
    model = await TabularDynamicsTrainer().fit(dataset, adapter)
    return world, store, result, dataset, model


async def test_censored_count_is_not_ability_count_and_unknown(tmp_path):
    world, _, _, dataset, model = await fitted(tmp_path, [0] * 8)
    assert model.cells[0].count == 8 and model.cells[0].mean == (0, 0, 0, 0)
    engine = QueueTemporalEngine(world.task, model)
    state = await world.observe()
    result = await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
    assert result["status"] == "unknown" and not result["differences"]
    mixed = await fitted(tmp_path / "mixed", ["probe", 0, 0, 0, 0, 0, 0, 0])
    engine = QueueTemporalEngine(mixed[0].task, mixed[-1])
    state = await mixed[0].observe()
    result = await compare_actions(Registry([engine]), engine, state, [mixed[0].action(state, 3)])
    prediction = result["predictions"][0]
    assert prediction.raw["reason"] == "insufficient_informative"
    assert prediction.raw["informative_count"] == 1 and prediction.raw["training_count"] == 8


async def test_probe_and_uncensored_ordinary_sources_separated(tmp_path):
    _, _, _, dataset, model = await fitted(tmp_path, [3, 0, 0, "probe"] * 3)
    rows = (await dataset.snapshot()).transitions
    targets = [QueueServiceAdapter().targets(r) for r in rows]
    assert sum(t[2] for t in targets) == 3
    assert sum(t[3] for t in targets) == 3
    assert sum(t[0] for t in targets) == 6 < model.cells[0].count
    assert all(t[1] == 0 for t in targets)


async def test_learning_reproducible_save_load_and_changes_prediction(tmp_path):
    world, _, _, dataset, low = await fitted(tmp_path)
    high_world = InformationQueueWorld(
        ticks=24, target=999, high_first=True, shift_tick=24, noise=0
    )
    _, _, _, high_data = await collect(tmp_path / "high", ["probe"] * 6, world=high_world)
    trainer, adapter = TabularDynamicsTrainer(), QueueServiceAdapter()
    high = await trainer.fit(high_data, adapter)
    assert low == await trainer.fit(dataset, adapter)
    artifacts = Artifacts(str(tmp_path / "models"))
    assert DynamicsModel.load(artifacts, low.save(artifacts), adapter) == low
    state = await world.observe()
    values = []
    for model in (low, high):
        engine = QueueTemporalEngine(world.task, model)
        result = await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
        values.append(result["predictions"][0].vectors["delivered"])
    assert values == [[0, 1, 2], [0, 3, 3]]


async def test_delayed_action_comparison_and_no_hidden_schedule_read():
    world = InformationQueueWorld(ticks=24, target=999)
    state = await world.observe()

    class Forbidden:
        def __getitem__(self, key):
            raise AssertionError("Inference read the private schedule")

    world._service = Forbidden()
    engine = QueueTemporalEngine(world.task, prior=0.25)
    actions = [world.action(state, n) for n in (3, 1, 0)]
    result = await compare_actions(Registry([engine]), engine, state, actions)
    assert result["status"] == "estimated"
    a, b, c = result["predictions"]
    assert a.vectors["delivered"] == [0, 1.5, 2.4375]
    assert a.vectors["queue"] == [0, 1.5, 0.5625]
    assert b.vectors["delivered"] == [0, 1, 1] and c.vectors["delivered"] == [0, 0, 0]
    assert a.vectors["pending_work"] == [3, 0, 0]
    assert result["differences"][0]["vectors"]["delivered"] == [0, 0.5, 1.4375]
    assert all(
        p.evidence == "inference"
        and not p.mandatory_checks
        and not p.claim_results
        and not p.future_states
        and not p.success.measured
        for p in result["predictions"]
    )
    assert state.payload == (await world.observe()).payload and not world.executed


async def test_registry_requires_claims_and_no_intervention_conditions():
    world = InformationQueueWorld()
    state = await world.observe()
    engine = QueueTemporalEngine(world.task, prior=0.5)
    action = world.action(state, 3)
    with pytest.raises(EngineFailure, match="explicit continuation"):
        await Registry([engine]).predict(
            engine, PredictionRequest(state=state, actions=[action], horizon=3)
        )
    result = await compare_actions(Registry([engine]), engine, state, [action])
    p = result["predictions"][0]
    claim = p.requested_claims[0].model_copy(update={"conditions": {}})
    with pytest.raises(EngineFailure, match="dynamics"):
        await Registry([engine]).predict(
            engine, PredictionRequest(state=state, actions=[action], horizon=3, claims=[claim])
        )


@pytest.mark.parametrize(
    "change", [{"service_values": [1, 2]}, {"arrival_delay": 3}, {"queue": 0.5}]
)
async def test_outside_domain_contract_is_unknown(change):
    from preact.core.models import State

    world = InformationQueueWorld()
    state = await world.observe()
    state = State.create(state.domain, {**state.payload, **change}, state.provenance)
    engine = QueueTemporalEngine(world.task, prior=0.5)
    result = await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
    assert result["status"] == "unknown"


@pytest.mark.parametrize("status", ["pending", "aborted"])
async def test_uncommitted_probe_is_rejected_by_training(tmp_path, status):
    _, store, _, dataset, model = await fitted(tmp_path)
    with store.db.begin() as conn:
        conn.execute(
            update(store.executions)
            .where(store.executions.c.id == model.receipts[0])
            .values(status=status)
        )
    with pytest.raises(ValueError, match="committed"):
        await TabularDynamicsTrainer().fit(dataset, QueueServiceAdapter())


async def test_forged_probe_duplicate_is_rejected_before_dedup(tmp_path):
    _, store, result, dataset, _ = await fitted(tmp_path)
    import copy

    run = result.run_ids[0]
    data = copy.deepcopy(next(e["data"] for e in store.read_events(run) if e["kind"] == "outcome"))
    data["observation"]["metrics"]["measured_service"] = 3
    store.append(run, "outcome", data)
    with pytest.raises(ValueError, match="committed"):
        await TabularDynamicsTrainer().fit(dataset, QueueServiceAdapter())


async def test_bad_probe_time_failed_probe_and_censored_labels(tmp_path):
    _, _, _, dataset, _ = await fitted(tmp_path)
    row = (await dataset.snapshot()).transitions[0]
    row.after.metrics["measurement_tick"] += 1
    with pytest.raises(ValueError, match="Misaligned"):
        QueueServiceAdapter().targets(row)
    row.after.metrics["measurement_tick"] -= 1
    row.after.checks["probe_success"] = False
    assert QueueServiceAdapter().targets(row) == (0, 0, 0, 0)


async def test_runtime_opt_in_preserves_actions_gate_receipts_and_no_verifier_no_execution(
    tmp_path,
):
    from scripts.benchmark_learned_dynamics import semantic

    _, _, _, _, model = await fitted(tmp_path / "train")
    results = []
    for enabled in (False, True):
        world = InformationQueueWorld(
            ticks=24, target=999, high_first=False, shift_tick=24, noise=0
        )
        engine = QueueTemporalEngine(world.task, model) if enabled else None
        _, store, result, dataset = await collect(
            tmp_path / str(enabled), [3, 0, 0], world=world, extra=engine
        )
        assert len(result.run_ids) == 3
        for run in result.run_ids:
            events = store.read_events(run)
            assert sum(e["kind"] == "authorization" for e in events) == 1
            assert sum(e["kind"] == "outcome" for e in events) == 1
        results.append(semantic(await dataset.snapshot()))
    assert results[0] == results[1]
    world = InformationQueueWorld(target=999)
    engine = QueueTemporalEngine(world.task, prior=1)
    store = Store("sqlite:///:memory:")
    agent = CognitiveAgent(
        store,
        Artifacts(str(tmp_path / "unsafe")),
        Registry([engine]),
        SequencePlanner([3]),
        [Goal(name="Deliver", metric="delivered", target=999)],
    )
    result = await agent.run(world, 1)
    assert result.rounds[0]["steps"] == 0 and world.payload["tick"] == 0
    assert not (await TransitionDataset(store, {"missing": result.run_ids}).snapshot()).transitions


async def test_failure_unknown_and_cancellation_no_fallback(monkeypatch):
    world = InformationQueueWorld()
    state = await world.observe()
    engine = QueueTemporalEngine(world.task, prior=0.5)
    monkeypatch.setattr(
        engine.adapter, "features", lambda *a: (_ for _ in ()).throw(ValueError("fail"))
    )
    result = await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
    assert result["status"] == "unknown"

    async def cancel(request):
        raise asyncio.CancelledError()

    monkeypatch.setattr(engine, "predict", cancel)
    with pytest.raises(asyncio.CancelledError):
        await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
    assert not world.executed


async def test_optimistic_temporal_model_cannot_override_overflow_verifier(tmp_path):
    world = InformationQueueWorld(target=999, capacity=3)
    world.payload.update(queue=3, pending=[{"due": 1, "amount": 3}])
    engine = QueueTemporalEngine(world.task, prior=1)
    _, _, result, dataset = await collect(tmp_path, [3], world=world, extra=engine)
    assert result.rounds[0]["steps"] == 0 and not world.executed
    assert not (await dataset.snapshot()).transitions


async def test_bounded_budget_provenance_and_changed_model_fail_closed(tmp_path):
    from preact.core.evidence import bind_claims

    world, _, _, _, model = await fitted(tmp_path)
    state = await world.observe()
    action = world.action(state, 3)
    engine = QueueTemporalEngine(world.task, model)
    claim = bind_claims(world.task, state.id, [action])[-1]
    prediction, _ = await Registry([engine]).predict(
        engine,
        PredictionRequest(
            state=state, actions=[action], horizon=3, claims=[claim], sample_budget=1
        ),
    )
    assert prediction.raw["status"] == "unknown"
    wrong = state.model_copy(update={"provenance": "inferred"})
    result = await compare_actions(Registry([engine]), engine, wrong, [action])
    assert result["status"] == "unknown"
    engine.model = model.model_copy(update={"seed": 1})
    result = await compare_actions(Registry([engine]), engine, state, [action])
    assert result["status"] == "unknown"


async def test_small_benchmark_audit_rejects_forged_scores_and_trace(tmp_path):
    import json
    from pathlib import Path

    from scripts.audit_queue_temporal import audit
    from scripts.benchmark_queue_temporal import benchmark

    protocol = json.loads(Path("benchmarks/queue-temporal-v1.json").read_text())
    protocol.update(
        training_seeds=[11],
        evaluation_seeds=[101],
        training_ticks=6,
        training_sizes=[0, 6],
        prefix_ticks=[0],
    )
    protocol["training_environment"]["shift_tick"] = 6
    protocol["evaluation_environments"] = {
        "stable_low": protocol["evaluation_environments"]["stable_low"]
    }
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    output = tmp_path / "run"
    summary = await benchmark(path, output)
    assert (await audit(output, path))["status"] == "passed-queue-temporal-audit"
    assert summary["fit"]["6"]["informative_count"] < 6
    with pytest.raises(ValueError, match="new output"):
        await benchmark(path, output)
    score_path = output / "summary.json"
    saved = score_path.read_text()
    score = json.loads(saved)
    score["scores"]["stable_low"]["fixed_prior"]["tick_2_3_action_difference_mae"] += 1
    score_path.write_text(json.dumps(score))
    with pytest.raises(ValueError, match="Score mismatch"):
        await audit(output, path)
    score_path.write_text(saved)
    cases_path = output / "cases.json"
    cases = json.loads(cases_path.read_text())
    cases[0]["branches"][0]["trace"][1]["delivered"] += 1
    cases_path.write_text(json.dumps(cases))
    with pytest.raises(ValueError, match="Forged evaluation trace"):
        await audit(output, path)


async def test_forecasts_do_not_create_experience_and_cache_returns_copies():
    world = InformationQueueWorld()
    state = await world.observe()
    engine = QueueTemporalEngine(world.task, prior=0.5)
    registry = Registry([engine])
    actions = [world.action(state, n) for n in (3, 1, 0)]
    result = await compare_actions(registry, engine, state, actions)
    original = result["predictions"][0].vectors["queue"][:]
    result["predictions"][0].vectors["queue"][0] = 999
    again = await compare_actions(registry, engine, state, actions)
    assert again["predictions"][0].vectors["queue"] == original
    store = Store("sqlite:///:memory:")
    run = store.create_run({})
    store.append(run, "temporal_comparison", {"prediction": again["predictions"][0].model_dump()})
    assert not (await TransitionDataset(store, {"unexecuted": [run]}).snapshot()).transitions


async def test_existing_pending_work_and_hypothesis_not_observation():
    world = InformationQueueWorld()
    world.payload.update(queue=1, pending=[{"due": 1, "amount": 3}])
    state = await world.observe()
    engine = QueueTemporalEngine(world.task, prior=0)
    result = await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
    p = result["predictions"][0]
    assert p.vectors["queue"] == [3, 5, 4] and p.vectors["delivered"] == [1, 2, 3]
    hypothetical = state.model_copy(update={"kind": "hypothetical"})
    result = await compare_actions(
        Registry([engine]), engine, hypothetical, [world.action(state, 3)]
    )
    assert result["status"] == "unknown"


async def test_mutated_state_identity_and_future_schema_fail_closed():
    world = InformationQueueWorld()
    state = await world.observe()
    action = world.action(state, 3)
    engine = QueueTemporalEngine(world.task, prior=0.5)
    state.payload["queue"] = 1
    with pytest.raises(ValueError, match="identity"):
        await compare_actions(Registry([engine]), engine, state, [action])
    state = (await world.observe()).model_copy(update={"schema_version": "2"})
    result = await compare_actions(Registry([engine]), engine, state, [world.action(state, 3)])
    assert result["status"] == "unknown"


async def test_receipt_backed_nonempty_root_makes_all_actions_service_sensitive(tmp_path):
    world, store, result, dataset = await collect(tmp_path, [3, 3])
    rows = (await dataset.snapshot()).transitions
    state = await world.observe()
    assert len(rows) == len(result.run_ids) == 2
    assert state.payload == rows[-1].after.state.payload
    assert state.payload["queue"] == 2 and state.payload["pending"] == [{"due": 3, "amount": 3}]
    for row in rows:
        events = store.read_events(row.run_id)
        assert sum(e["kind"] == "authorization" for e in events) == 1
        assert sum(e["kind"] == "outcome" for e in events) == 1

    class Forbidden:
        def __getitem__(self, key):
            raise AssertionError("Forecast read the private schedule")

    world._service = Forbidden()
    actions = [world.action(state, n) for n in (3, 1, 0)]
    forecasts = []
    for prior in (0, 1):
        engine = QueueTemporalEngine(world.task, prior=prior)
        comparison = await compare_actions(Registry([engine]), engine, state, actions)
        assert comparison["status"] == "estimated"
        forecasts.append([p.vectors["delivered"] for p in comparison["predictions"]])
    assert forecasts[0] == [[2, 3, 4]] * 3
    assert forecasts[1] == [[4, 7, 9], [4, 7, 7], [4, 6, 6]]
    assert all(a != b for a, b in zip(*forecasts))
    assert len((await dataset.snapshot()).transitions) == 2  # Analysis is not experience.


async def test_nonempty_benchmark_audits_prefix_receipts_and_per_action_scores(tmp_path):
    import json
    from pathlib import Path

    from scripts.audit_queue_temporal import audit
    from scripts.benchmark_queue_temporal import benchmark

    protocol = json.loads(Path("benchmarks/queue-temporal-v1.json").read_text())
    protocol.update(
        training_seeds=[11],
        evaluation_seeds=[101],
        training_ticks=6,
        training_sizes=[0, 6],
        prefix_ticks=[0],
        workload_prefixes={"pending": [3], "queue_and_pending": [3, 3]},
    )
    protocol["training_environment"]["shift_tick"] = 6
    protocol["evaluation_environments"] = {
        "stable_low": {"high_first": False, "shift_tick": 12, "noise": 0}
    }
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    output = tmp_path / "run"
    summary = await benchmark(path, output)
    assert (await audit(output, path))["paired_comparisons"] == 2
    assert summary["initial_states"] == {
        "comparisons": 2,
        "nonempty_queue": 1,
        "nonempty_pending": 2,
        "nonempty_work": 2,
        "receipt_backed_prefix_transitions": 9,
    }
    score = summary["scores"]["stable_low"]["fixed_prior"]
    assert all(a["service_sensitive_comparisons"] == 2 for a in score["per_action"].values())
    assert all(a["tick_2_3_delivered_mae"] > 0 for a in score["per_action"].values())
    assert score["tick_2_3_action_difference_mae"] != 2 * score["tick_2_3_state_mae"]
    score_path = output / "summary.json"
    saved = score_path.read_text()
    summary["scores"]["stable_low"]["fixed_prior"]["per_action"]["0"]["tick_2_3_queue_mae"] += 1
    score_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="Per-action score mismatch"):
        await audit(output, path)
    score_path.write_text(saved)
    cases_path = output / "cases.json"
    cases = json.loads(cases_path.read_text())
    original_cases = cases_path.read_text()
    cases[0]["prefix_actions"] = [0]
    cases_path.write_text(json.dumps(cases))
    with pytest.raises(ValueError, match="Prefix specification changed"):
        await audit(output, path)
    cases_path.write_text(original_cases)
    # A completed root cannot rescue an uncommitted preparation receipt.
    store = Store("sqlite:///" + str(output / "ledger.db"))
    prefix_run = cases[0]["branches"][0]["prefix_runs"][0]
    with store.db.begin() as conn:
        conn.execute(
            update(store.executions)
            .where(store.executions.c.run_id == prefix_run)
            .values(status="pending")
        )
    with pytest.raises(ValueError, match="committed"):
        await audit(output, path)
    store.db.dispose()


@pytest.mark.parametrize("workload", [[True], [2], ["probe"], [3] * 12])
async def test_invalid_workload_prefix_rejected_before_collection(tmp_path, workload):
    import json
    from pathlib import Path

    from scripts.benchmark_queue_temporal import benchmark

    protocol = json.loads(Path("benchmarks/queue-temporal-v1.json").read_text())
    protocol["workload_prefixes"] = {"invalid": workload}
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="Unsupported initial condition"):
        await benchmark(path, output)
    assert not output.exists()
