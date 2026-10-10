import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

from preact.core.models import State
from preact.core.store import Artifacts, Store
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import TransitionDataset
from preact.learning.recovery import (
    CandidateEvaluation,
    CandidateModel,
    ModelPromotion,
    RecoveryRejected,
)
from scripts.benchmark_queue_recovery import recovery_case

PROTOCOL = Path(__file__).parents[1] / "benchmarks/queue-recovery-v1.json"


@pytest_asyncio.fixture(scope="module")
async def successful_recovery(tmp_path_factory):
    directory = tmp_path_factory.mktemp("recovery") / "case"
    record = await recovery_case(json.loads(PROTOCOL.read_text()), "low_to_high", 201, directory)
    assert record["status"] == "promoted"
    return directory


@pytest.fixture
def prepared(successful_recovery, tmp_path):
    directory = tmp_path / "copy"
    shutil.copytree(successful_recovery, directory)
    record = json.loads((directory / "case.json").read_text())
    store = Store("sqlite:///" + str(directory / "ledger.db"))
    artifacts = Artifacts(str(directory / "artifacts"))
    lifecycle = QueueModelRecovery(store, artifacts, record["journal_run"])
    return lifecycle, record


async def latest(lifecycle, record):
    m = record["monitoring"][0]
    rows = (
        await TransitionDataset(lifecycle.store, {m["episode_id"]: m["runs"]}).snapshot()
    ).transitions
    return rows[-1].after.state.model_copy(
        update={"timestamp": datetime.now(timezone.utc).isoformat()}
    ), m["runs"]


async def restored(lifecycle, record):
    state, runs = await latest(lifecycle, record)
    await lifecycle.restore(state, runs)
    return state, runs


async def comparison(lifecycle, state, runs):
    from preact.domains.information_queue import InformationQueueWorld

    return await lifecycle.compare(
        state, [InformationQueueWorld.action(state, n) for n in (3, 1, 0)], runs
    )


async def test_recovery_restores_new_model_and_preserves_parent_invalidation(prepared):
    lifecycle, c = prepared
    state, runs = await restored(lifecycle, c)
    result = await comparison(lifecycle, state, runs)
    assert result["status"] == "estimated"
    assert result["health"]["status"] == "available"
    origin = await lifecycle._origin(
        lifecycle._load(c["candidate"], CandidateModel).origin_artifact
    )
    parent = await lifecycle._guard(origin).health(
        State.model_validate(c["source_cutoff"]), c["source_runs"]
    )
    assert parent.status == "invalidated"
    assert result["promotion"]["model_version"] != parent.model_version
    assert all(
        p.evidence.value == "inference" and not p.mandatory_checks and p.success.value is None
        for p in result["predictions"]
    )
    result["promotion"]["model_version"] = "forged"
    assert (await comparison(lifecycle, state, runs))["promotion"]["model_version"] != "forged"


async def test_candidate_and_evaluation_do_not_activate_a_model(prepared):
    lifecycle, c = prepared
    state, runs = await latest(lifecycle, c)
    assert (await comparison(lifecycle, state, runs))["reason"] == "not_promoted"


@pytest.mark.parametrize("status", ["pending", "aborted"])
async def test_bad_receipt_clears_active_and_restart_cannot_restore(prepared, status):
    lifecycle, c = prepared
    state, runs = await restored(lifecycle, c)
    candidate = lifecycle._load(c["candidate"], CandidateModel)
    snapshot = await TransitionDataset(lifecycle.store, candidate.training).snapshot()
    with lifecycle.store.db.begin() as conn:
        conn.execute(
            update(lifecycle.store.executions)
            .where(lifecycle.store.executions.c.id == snapshot.transitions[0].receipt)
            .values(status=status)
        )
    with pytest.raises(ValueError):
        await comparison(lifecycle, state, runs)
    assert lifecycle._active is None
    assert (await comparison(lifecycle, state, runs))["status"] == "unknown"
    with pytest.raises(ValueError):
        await lifecycle.restore(state, runs)
    assert lifecycle._active is None


async def test_forged_outcome_rejected_before_reuse(prepared):
    lifecycle, c = prepared
    state, runs = await restored(lifecycle, c)
    evaluation = lifecycle._load(c["evaluation"], CandidateEvaluation)
    run = evaluation.cases[0].branches[0].runs[-1]
    data = next(e["data"] for e in lifecycle.store.read_events(run) if e["kind"] == "outcome")
    data["observation"]["metrics"]["processed"] = 999
    lifecycle.store.append(run, "outcome", data)
    with pytest.raises(ValueError, match="committed"):
        await comparison(lifecycle, state, runs)
    assert lifecycle._active is None


async def test_artifact_integrity_mismatch_cannot_fallback(prepared):
    lifecycle, c = prepared
    state, runs = await restored(lifecycle, c)
    candidate = lifecycle._load(c["candidate"], CandidateModel)
    (lifecycle.artifacts.root / candidate.model_artifact).write_bytes(b"{}")
    with pytest.raises(ValueError, match="integrity"):
        await comparison(lifecycle, state, runs)
    assert lifecycle._active is None


async def test_train_evaluation_and_monitor_actual_runs_cannot_be_relabeled(prepared):
    lifecycle, c = prepared
    candidate = lifecycle._load(c["candidate"], CandidateModel)
    report = lifecycle._load(c["evaluation"], CandidateEvaluation)
    case = report.cases[0]
    branch = case.branches[0].model_copy(
        update={"episode_id": "renamed", "runs": next(iter(candidate.training.values()))}
    )
    case = case.model_copy(update={"branches": [branch, *case.branches[1:]]})
    with pytest.raises(ValueError, match="disjoint"):
        await lifecycle.evaluate_candidate(
            c["candidate"], [case], datetime.now(timezone.utc).isoformat()
        )
    promotion = lifecycle._load(c["promotion"], ModelPromotion)
    evaluation_branch = report.cases[0].branches[0]
    changed = promotion.model_copy(
        update={"episode_id": "relabeled", "runs": evaluation_branch.runs}
    )
    state, _ = await latest(lifecycle, c)
    with pytest.raises(ValueError, match="disjoint"):
        await lifecycle._promotion_guard(changed, state, evaluation_branch.runs)


async def test_historical_cutoff_future_state_and_prefix_replacement_rejected(prepared):
    lifecycle, c = prepared
    state, runs = await latest(lifecycle, c)
    with pytest.raises(ValueError):
        await lifecycle.restore(
            state.model_copy(update={"timestamp": "2100-01-01T00:00:00+00:00"}), runs
        )
    with pytest.raises(ValueError, match="prefix"):
        await lifecycle.restore(state, runs[1:])
    with pytest.raises(ValueError):
        await lifecycle.restore(state.model_copy(update={"kind": "hypothetical"}), runs)
    assert lifecycle._active is None


async def test_changed_store_during_restore_and_compare_fails_closed(prepared, monkeypatch):
    lifecycle, c = prepared
    state, runs = await restored(lifecycle, c)
    original = lifecycle.store.call
    changed = False

    async def updating(method, *args, **kwargs):
        nonlocal changed
        value = await original(method, *args, **kwargs)
        if method == "run_heads" and not changed:
            lifecycle.store.append(runs[0], "external_update", {})
            changed = True
        return value

    monkeypatch.setattr(lifecycle.store, "call", updating)
    with pytest.raises(ValueError, match="changed"):
        await comparison(lifecycle, state, runs)
    assert lifecycle._active is None


async def test_cancelled_prediction_never_returns_previous_available(prepared, monkeypatch):
    from preact.engines.queue_temporal import QueueTemporalEngine

    lifecycle, c = prepared
    state, runs = await restored(lifecycle, c)

    async def cancelled(self, request):
        raise asyncio.CancelledError

    monkeypatch.setattr(QueueTemporalEngine, "predict", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await comparison(lifecycle, state, runs)
    assert lifecycle._active is None


@pytest.mark.parametrize("after_commit", [False, True])
async def test_promotion_interruption_and_safe_explicit_restore(
    prepared, monkeypatch, after_commit
):
    lifecycle, c = prepared
    promotion = lifecycle._load(c["promotion"], ModelPromotion)
    # Isolated test copy at the pre-promotion stage, preserving original forecast times.
    with lifecycle.store.db.begin() as conn:
        conn.execute(
            delete(lifecycle.store.events).where(
                (lifecycle.store.events.c.run_id == lifecycle.journal_run)
                & lifecycle.store.events.c.kind.in_(
                    ["model_promotion_approved", "model_promotion_committed"]
                )
            )
        )
    fresh = QueueModelRecovery(lifecycle.store, lifecycle.artifacts, lifecycle.journal_run)
    original = fresh.store.call

    async def interrupted(method, *args, **kwargs):
        if method == "append" and args[1] == "model_promotion_committed":
            if after_commit:
                await original(method, *args, **kwargs)
            raise asyncio.CancelledError
        return await original(method, *args, **kwargs)

    monkeypatch.setattr(fresh.store, "call", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await fresh.promote(
            c["evaluation"],
            episode_id=promotion.episode_id,
            task=promotion.task,
            initial=promotion.initial,
            state=promotion.cutoff,
            runs=promotion.runs,
        )
    assert fresh._active is None
    monkeypatch.setattr(fresh.store, "call", original)
    state, runs = await latest(lifecycle, c)
    restart = QueueModelRecovery(fresh.store, fresh.artifacts, fresh.journal_run)
    if after_commit:
        await restart.restore(state, runs)
        assert (await comparison(restart, state, runs))["status"] == "estimated"
    else:
        with pytest.raises(RecoveryRejected, match="no_committed"):
            await restart.restore(state, runs)
        assert restart._active is None


async def test_missing_current_evidence_prevents_restore_even_with_durable_commit(prepared):
    lifecycle, c = prepared
    promotion = lifecycle._load(c["promotion"], ModelPromotion)
    with pytest.raises(ValueError):
        await lifecycle.restore(promotion.initial, [])
    assert lifecycle._active is None


async def test_new_episode_health_does_not_borrow_evaluation_samples(prepared):
    lifecycle, c = prepared
    promotion = lifecycle._load(c["promotion"], ModelPromotion)
    waiting = promotion.model_copy(
        update={"cutoff": promotion.initial, "runs": [], "health_version": ""}
    )
    with pytest.raises(RecoveryRejected, match="insufficient_data"):
        await lifecycle._promotion_guard(waiting, waiting.initial, [])


async def test_candidate_evaluation_artifact_cannot_forge_approval(prepared):
    lifecycle, c = prepared
    report = lifecycle._load(c["evaluation"], CandidateEvaluation)
    forged = report.model_copy(update={"scores": {}})
    digest = await lifecycle._record("model_candidate_evaluated", forged)
    promotion = lifecycle._load(c["promotion"], ModelPromotion).model_copy(
        update={"evaluation_artifact": digest}
    )
    state, runs = await latest(lifecycle, c)
    with pytest.raises(ValueError):
        await lifecycle._promotion_guard(promotion, state, runs)


async def test_quality_rejection_and_stable_parent_do_not_commit(tmp_path):
    protocol = json.loads(PROTOCOL.read_text())
    for name in ("quality_rejected", "stable_low"):
        record = await recovery_case(protocol, name, 201, tmp_path / name)
        assert record["status"] in {"rejected", "not_invalidated"}
        assert record["promotion"] is None
    assert (
        "prediction_quality"
        in json.loads((tmp_path / "quality_rejected/case.json").read_text())["rejection"]
    )


async def test_first_invalidation_cutoff_cannot_be_moved_to_later_experience(prepared):
    lifecycle, c = prepared
    candidate, origin, _, _ = await lifecycle._candidate(c["candidate"])
    fresh = await QueueModelRecovery.create(lifecycle.store, lifecycle.artifacts)
    with pytest.raises(ValueError, match="first invalidation"):
        await fresh.begin(
            lifecycle._parent(origin),
            origin.training,
            episode_id=origin.episode_id,
            task=origin.task,
            initial=origin.initial,
            cutoff=candidate.source_cutoff,
            runs=candidate.source_runs,
        )


async def test_retrospective_evaluation_forecast_is_rejected(prepared):
    from preact.learning.recovery import ForecastRecord

    lifecycle, c = prepared
    report = lifecycle._load(c["evaluation"], CandidateEvaluation)
    case = report.cases[0]
    forecast = lifecycle._load(case.forecast_artifact, ForecastRecord)
    forecast = forecast.model_copy(
        update={
            "state": forecast.state.model_copy(
                update={"timestamp": datetime.now(timezone.utc).isoformat()}
            )
        }
    )
    late = await lifecycle._record("model_evaluation_forecast", forecast)
    # A late record of identical semantic forecasts cannot establish a prior prediction.
    with pytest.raises(ValueError, match="root or environment-only"):
        await lifecycle.evaluate_candidate(
            c["candidate"],
            [case.model_copy(update={"forecast_artifact": late})],
            datetime.now(timezone.utc).isoformat(),
        )


async def test_promoted_inference_alone_cannot_authorize_an_action(prepared, tmp_path):
    from preact.cognition import CognitiveAgent, Goal
    from preact.core.registry import Registry
    from preact.domains.information_queue import InformationQueueWorld
    from preact.engines.queue_temporal import QueueTemporalEngine
    from scripts.benchmark_queue_temporal import FixedPlanner

    lifecycle, c = prepared
    _, _, model, _ = await lifecycle._candidate(c["candidate"])
    instance = InformationQueueWorld(ticks=24, target=999, high_first=True, noise=0)
    agent = CognitiveAgent(
        lifecycle.store,
        Artifacts(str(tmp_path / "gate")),
        Registry([QueueTemporalEngine(instance.task, model)]),
        FixedPlanner([3], 0),
        [Goal(name="Deliver", metric="delivered", target=999)],
    )
    result = await agent.run(instance, 1)
    assert result.rounds[0]["steps"] == 0 and not instance.executed
    assert not (
        await TransitionDataset(lifecycle.store, {"unexecuted": result.run_ids}).snapshot()
    ).transitions


async def test_valid_json_model_with_forged_statistics_is_rejected(prepared):
    from preact.domains.queue_service_features import QueueServiceAdapter
    from preact.learning import DynamicsModel

    lifecycle, c = prepared
    candidate = lifecycle._load(c["candidate"], CandidateModel)
    model = DynamicsModel.load(lifecycle.artifacts, candidate.model_artifact, QueueServiceAdapter())
    forged = model.model_copy(update={"seed": 999})
    artifact = forged.save(lifecycle.artifacts)
    bad_candidate = candidate.model_copy(
        update={"model_artifact": artifact, "model_version": forged.version}
    )
    digest = await lifecycle._record("model_candidate_created", bad_candidate)
    with pytest.raises(ValueError, match="training authority"):
        await lifecycle._candidate(digest)


async def test_identical_stage_record_is_idempotent(prepared):
    lifecycle, c = prepared
    candidate = lifecycle._load(c["candidate"], CandidateModel)
    assert await lifecycle._record("model_candidate_created", candidate) == c["candidate"]
    assert (
        len([e for e in await lifecycle._journal() if e["kind"] == "model_candidate_created"]) == 1
    )


async def test_writer_update_during_durable_commit_blocks_publication(prepared, monkeypatch):
    lifecycle, c = prepared
    promotion = lifecycle._load(c["promotion"], ModelPromotion)
    with lifecycle.store.db.begin() as conn:
        conn.execute(
            delete(lifecycle.store.events).where(
                (lifecycle.store.events.c.run_id == lifecycle.journal_run)
                & lifecycle.store.events.c.kind.in_(
                    ["model_promotion_approved", "model_promotion_committed"]
                )
            )
        )
    original = lifecycle.store.call

    async def changed(method, *args, **kwargs):
        result = await original(method, *args, **kwargs)
        if method == "append" and args[1] == "model_promotion_committed":
            lifecycle.store.append(promotion.runs[0], "external_writer", {})
        return result

    monkeypatch.setattr(lifecycle.store, "call", changed)
    with pytest.raises(ValueError, match="durable model switch"):
        await lifecycle.promote(
            c["evaluation"],
            episode_id=promotion.episode_id,
            task=promotion.task,
            initial=promotion.initial,
            state=promotion.cutoff,
            runs=promotion.runs,
        )
    assert lifecycle._active is None


async def test_independent_auditor_rejects_changed_public_result(successful_recovery, tmp_path):
    from preact.core.models import identity
    from scripts.audit_queue_recovery import audit
    from scripts.benchmark_queue_recovery import semantic

    output = tmp_path / "audit"
    output.mkdir()
    shutil.copytree(successful_recovery, output / "low_to_high-201")
    record = json.loads((output / "low_to_high-201/case.json").read_text())
    protocol = json.loads(PROTOCOL.read_text())
    protocol.update(
        pilot_seeds=[901],
        evaluation_seeds=[201],
        conditions={"low_to_high": protocol["conditions"]["low_to_high"]},
    )
    path = tmp_path / "bounded-audit-protocol.json"
    path.write_text(json.dumps(protocol))
    keys = (
        "condition",
        "seed",
        "status",
        "rejection",
        "invalidation_tick",
        "scores",
        "post_scores",
        "recovery_ticks",
        "timing",
        "peak_process_bytes",
        "actual_semantic_hash",
        "executed_actions",
        "unsafe_outcomes",
        "runtime_engine_calls",
        "parent_prediction_stopped",
    )
    summary = {
        "pilot": False,
        "protocol_hash": identity(protocol),
        "semantic_hash": semantic([record]),
        "cases": [{k: record.get(k) for k in keys}],
    }
    (output / "summary.json").write_text(json.dumps(summary))
    result = await audit(path, output)
    assert result["status"] == "passed" and result["promoted"] == 1
    summary["cases"][0]["status"] = "rejected"
    (output / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="aggregate changed"):
        await audit(path, output)
