"""Independently rebuild continuous Queue lineage, executed decisions and outcomes."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from preact.cognition.models import Goal
from preact.cognition.queue_model import candidate_value
from preact.core.models import Action, EvidenceKind, Prediction, State, identity
from preact.core.store import Artifacts, Store
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.continuous import MonitoringEpoch
from preact.learning.drift import DriftConfig
from preact.learning.recovery import CandidateEvaluation, CandidateModel, ModelPromotion
from preact.learning.transitions import require_disjoint


def verify(condition, reason):
    if not condition:
        raise ValueError(reason)


async def audit(bundle: Path) -> dict:
    result_path = bundle / "continuous-result.json"
    result = json.loads(result_path.read_text())
    store = Store("sqlite:///" + str(bundle / "ledger.db"))
    artifacts = Artifacts(str(bundle / "artifacts"))
    verify(result["world_was_reset"] is False, "Protocol records a World reset")
    verify(result["single_world_instance"] is True, "Protocol does not retain a single World")
    verify(result["status"] == "completed", "Continuous scenario did not complete")

    lifecycle_events = await store.call("read_events", result["continuous_journal"])
    epochs = [
        e
        for e in lifecycle_events
        if e["kind"] in {"continuous_epoch_started", "continuous_epoch_activated"}
    ]
    verify(len(epochs) == 3, "Expected exactly Model A/B/C epoch records")
    epoch_records = [
        MonitoringEpoch.model_validate(json.loads(artifacts.read(e["data"]["artifact"])))
        for e in epochs
    ]
    verify([epoch.generation for epoch in epoch_records] == [0, 1, 2], "Generation order changed")
    verify(
        all(epoch.world_episode_id == result["world_episode_id"] for epoch in epoch_records),
        "Model epochs are not bound to one World episode",
    )
    verify(
        [epoch.parent_epoch for epoch in epoch_records]
        == [None, epoch_records[0].epoch_id, epoch_records[1].epoch_id],
        "Model epoch parent chain is broken",
    )

    models = []
    for epoch in epoch_records:
        model = DynamicsModel.load(artifacts, epoch.model_artifact, QueueServiceAdapter())
        verify(model.version == epoch.model_version, "Epoch model version mismatch")
        verify(model.dataset_hash == epoch.dataset_hash, "Epoch Dataset hash mismatch")
        refit = await TabularDynamicsTrainer().fit(
            TransitionDataset(store, epoch.training),
            QueueServiceAdapter(),
            TrainingConfig(min_samples=model.min_samples, seed=model.seed),
            limit=len(model.receipts),
        )
        verify(refit == model, "Model cannot be reproduced from its receipt dataset")
        models.append(model)

    # Validate explicit B/C promotions and the epoch-specific fresh Health basis.
    promotions = []
    for index, epoch in enumerate(epoch_records[1:], start=1):
        recovery = QueueModelRecovery(store, artifacts, epoch.recovery_journal)
        journal = await recovery._journal()
        committed = [e for e in journal if e["kind"] == "model_promotion_committed"]
        approved = [e for e in journal if e["kind"] == "model_promotion_approved"]
        verify(len(committed) == len(approved) == 1, "Promotion is not explicitly committed once")
        digest = committed[0]["data"]["artifact"]
        verify(digest == epoch.promotion_artifact, "Epoch does not bind committed promotion")
        promotion = recovery._load(digest, ModelPromotion)
        report = recovery._load(promotion.evaluation_artifact, CandidateEvaluation)
        candidate = recovery._load(promotion.candidate_artifact, CandidateModel)
        verify(report.passed, "A candidate with failed independent evaluation was promoted")
        verify(promotion.model_version == epoch.model_version, "Promotion Model version mismatch")
        verify(promotion.monitoring_epoch == epoch.epoch_id, "Promotion epoch binding mismatch")
        verify(promotion.continuous_episode, "Promotion did not use continuous-episode contract")
        verify(
            promotion.episode_id == result["world_episode_id"], "Promotion World episode mismatch"
        )
        verify(
            promotion.runs == epoch.monitor_runs,
            "Promotion monitor prefix differs from immutable epoch record",
        )
        verify(
            candidate.model_artifact == epoch.model_artifact,
            "Promotion candidate model artifact mismatch",
        )
        verify(
            report.candidate_artifact == promotion.candidate_artifact,
            "Promotion candidate binding mismatch",
        )

        monitor = await TransitionDataset(
            store, {epoch.world_episode_id: promotion.runs}
        ).snapshot()
        candidate_training = await TransitionDataset(store, candidate.training).snapshot()
        evaluation_snapshot = await report_snapshot(store, report)
        require_disjoint(candidate_training, evaluation_snapshot)
        verify(
            not (
                {row.receipt for row in candidate_training.transitions}
                & {row.receipt for row in monitor.transitions}
            ),
            "Training receipt reused in its monitoring epoch",
        )
        verify(
            not (
                {row.run_id for row in candidate_training.transitions}
                & {row.run_id for row in monitor.transitions}
            ),
            "Training run reused in its monitoring epoch",
        )
        anchor = State.model_validate(epoch.anchor.model_dump())
        guard = QueueTemporalGuard(
            store,
            epoch.task,
            models[index],
            epoch.training,
            episode_id=epoch.world_episode_id,
            initial=anchor,
            config=DriftConfig(**epoch.drift_config),
            monitoring_epoch=epoch.epoch_id,
            allow_same_episode=True,
        )
        health = await guard.health(promotion.cutoff, promotion.runs)
        verify(
            health.status == "available", "Promoted model was not available at its Health cutoff"
        )
        verify(health.version == promotion.health_version, "Promotion Health evidence changed")
        promotions.append(
            {
                "generation": index,
                "model_version": models[index].version,
                "health_version": health.version,
                "evaluation": report.scores,
                "effective_training": candidate.effective_count,
            }
        )

    # The ordered transition manifest is the audit proof of no reset and exactly one
    # completed Runtime action between every adjacent observed tick.
    run_ids = result["world_run_ids"]
    snapshot = await TransitionDataset(store, {result["world_episode_id"]: run_ids}).snapshot()
    snapshot.verify_identity()
    verify(
        snapshot.dataset_hash == result["transition_dataset_hash"], "Transition manifest changed"
    )
    rows = snapshot.transitions
    verify(len(rows) == len(run_ids), "A World run lacks exactly one confirmed transition")
    verify(rows[0].before.payload["tick"] == 0, "Continuous World did not begin at tick zero")
    for previous, current in zip(rows, rows[1:]):
        verify(
            previous.after.state.payload["tick"] == current.before.payload["tick"]
            and previous.after.state.model_dump(exclude={"timestamp"})
            == current.before.model_dump(exclude={"timestamp"}),
            "World transition chain contains reset, gap or state discontinuity",
        )
    for row in rows:
        execution = await store.call("execution_record", row.receipt)
        verify(execution["status"] == "complete", "Non-complete receipt entered actual experience")
        verify(row.after.receipt == row.receipt, "Receipt/outcome binding mismatch")
    verify(
        rows[-1].after.state.model_dump(exclude={"timestamp"})
        == State.model_validate(result["world_final_state"]).model_dump(exclude={"timestamp"}),
        "Final observation differs from final receipt transition",
    )

    # Rebuild every claimed model-grounded decision through stored Runtime evidence.
    decisions = []
    for expected_label, expected_model, claim in zip(
        ("model-A", "model-B", "model-C"), models, result["decisions"], strict=True
    ):
        run_id = claim["run_id"]
        events = await store.call("read_events", run_id)
        by_kind = {
            kind: [e for e in events if e["kind"] == kind]
            for kind in (
                "cognitive_prediction",
                "candidate_qualification",
                "candidate_selection",
                "authorization",
                "execution_intent",
                "decision",
                "outcome",
                "observed",
            )
        }
        verify(
            all(len(by_kind[k]) == 1 for k in by_kind), "Decision run lacks unique Runtime proof"
        )
        prediction = by_kind["cognitive_prediction"][0]["data"]
        batch_data = by_kind["candidate_qualification"][0]["data"]
        selection = by_kind["candidate_selection"][0]["data"]
        batch = batch_data["batch"]
        verify(prediction["health"]["status"] == "available", "Decision used unavailable model")
        verify(
            prediction["health"]["model_version"] == expected_model.version,
            "Decision used the wrong model generation",
        )
        verify(
            prediction["health"]["dataset_hash"] == expected_model.dataset_hash
            and prediction["health"]["cutoff_tick"] == prediction["state"]["payload"]["tick"]
            and set(prediction["monitor_runs"]).issubset(set(prediction["source_runs"]))
            and prediction["evaluation_function"] == "capped-progress-holding-root-cost/v1"
            and prediction["horizon"] == 3,
            "Decision used a stale Health, source manifest or changed objective",
        )
        verify(
            prediction["mode"] == "learned_model" or prediction["basis"] == "learned_model",
            "Decision was a fallback rather than a world-model choice",
        )
        prediction_state = State.model_validate(prediction["state"])
        goal = Goal.model_validate(prediction["goals"][0])
        candidates = prediction["candidates"]
        verify(
            len(candidates) == 3 and {c["amount"] for c in candidates} == {0, 1, 3},
            "Expected submit(3), submit(1) and drain predictions",
        )
        by_fingerprint = {}
        for candidate in candidates:
            action = Action.model_validate(candidate["action"])
            forecast = Prediction.model_validate(candidate["prediction"])
            verify(
                action.fingerprint == candidate["fingerprint"]
                and action.state_id == prediction_state.id
                and candidate["amount"] == action.payload["amount"],
                "Predicted candidate Action binding changed",
            )
            verify(
                forecast.evidence == EvidenceKind.INFERENCE
                and forecast.state_id == prediction_state.id
                and forecast.action_ids == [action.id]
                and forecast.horizon == 3
                and forecast.raw.get("base_model_version") == expected_model.version
                and forecast.raw.get("model_health", {}).get("status") == "available"
                and forecast.raw.get("engine_view_version") == prediction["engine_view_version"],
                "Prediction provenance or authority changed",
            )
            recomputed_value = candidate_value(
                forecast.model_dump(), prediction_state, goal, candidate["amount"]
            )
            verify(
                abs(recomputed_value - candidate["value"]) < 1e-10,
                "Saved candidate value does not match its forecast and fixed objective",
            )
            by_fingerprint[action.fingerprint] = candidate
        ranked = sorted(candidates, key=lambda c: (-c["value"], c["amount"]))
        expected_rank = [c["fingerprint"] for c in ranked]
        verify(
            prediction["ranked_fingerprints"] == expected_rank
            and prediction["first_choice_fingerprint"] == expected_rank[0],
            "World-model candidate ranking cannot be reconstructed",
        )
        verify(batch_data["complete"] is True, "Runtime did not finish all candidate checks")
        verify(
            batch_data["qualified_ids"]
            == [c["node_id"] for c in batch["candidates"] if c["status"] == "qualified"],
            "Qualified-set manifest differs from candidate assessments",
        )
        verify(
            all(
                c["status"] in {"qualified", "rejected", "invalid_contract"}
                for c in batch["candidates"]
            ),
            "Unresolved candidate was treated as selectable",
        )
        for item in batch["candidates"]:
            if item["status"] == "qualified":
                verify(
                    item["verification_performed"]
                    and item["evaluation"] is not None
                    and item["gate"] is not None
                    and item["gate"]["decision"] == "execute"
                    and item["gate"]["state_id"] == batch["state"]["id"]
                    and item["gate"]["action_hash"]
                    == Action.model_validate(item["action"]).fingerprint,
                    "Qualified candidate lacks a bound executed Gate assessment",
                )
            elif item["status"] == "rejected":
                verify(
                    item["verification_performed"]
                    and item["gate"] is not None
                    and item["gate"]["decision"] != "execute",
                    "Rejected candidate lacks an explicit non-execute Gate result",
                )
        chosen_node = selection["node_id"]
        chosen = next(c for c in batch["candidates"] if c["node_id"] == chosen_node)
        verify(chosen["status"] == "qualified", "Runtime selected an unqualified candidate")
        ranked_qualified = [
            fingerprint
            for fingerprint in expected_rank
            if any(
                c["status"] == "qualified"
                and c["action"]["id"] == by_fingerprint[fingerprint]["action"]["id"]
                for c in batch["candidates"]
            )
        ]
        verify(
            ranked_qualified
            and chosen["action"]["id"] == by_fingerprint[ranked_qualified[0]]["action"]["id"],
            "Runtime selector did not choose the highest-ranked qualified prediction",
        )
        chosen_action = Action.model_validate(chosen["action"])
        verify(
            selection["action_fingerprint"] == chosen_action.fingerprint,
            "Final selection does not bind the selected Action",
        )
        verify(
            selection["state_id"] == batch["state"]["id"] == prediction["state"]["id"],
            "Prediction/qualification State mismatch",
        )
        verify(
            prediction["run_id"] == run_id and batch["run_id"] == run_id,
            "Prediction and Runtime run association mismatch",
        )
        authorization = by_kind["authorization"][0]["data"]
        verify(
            authorization["decision"] == "execute"
            and authorization["state_id"] == batch["state"]["id"]
            and authorization["action_hash"] == chosen_action.fingerprint,
            "Final authorization does not bind chosen Action",
        )
        intent = by_kind["execution_intent"][0]["data"]
        verify(intent["receipt"], "Missing durable execution intent receipt")
        actual = next(row for row in rows if row.run_id == run_id)
        verify(
            actual.action.fingerprint == chosen_action.fingerprint,
            "Executed Action differs from qualified model choice",
        )
        verify(
            actual.receipt == intent["receipt"] == claim["receipt"],
            "Intent/complete receipt does not match actual transition",
        )
        verify(
            actual.after.model_dump() == claim["actual_observation"],
            "Recorded actual observation differs from receipt-backed observation",
        )
        verify(
            actual.action.fingerprint
            == Action.model_validate(claim["executed_action"]).fingerprint,
            "Published action summary changed",
        )
        decisions.append(
            {
                "label": expected_label,
                "model_version": expected_model.version,
                "state_id": actual.before.id,
                "action": actual.action.kind,
                "amount": actual.action.payload.get("amount"),
                "receipt": actual.receipt,
                "qualified_count": sum(c["status"] == "qualified" for c in batch["candidates"]),
            }
        )

    verification = {
        "status": "passed",
        "world_episode_id": result["world_episode_id"],
        "continuous_transitions": len(rows),
        "model_epochs": [epoch.epoch_id for epoch in epoch_records],
        "promotions": promotions,
        "executed_world_model_decisions": decisions,
        "receipt_complete_count": len(rows),
        "unsafe_count": sum(row.after.unsafe for row in rows),
        "actual_reward": sum(float(row.after.metrics.get("reward", 0.0)) for row in rows),
        "holding_loss": sum(float(row.after.metrics.get("holding_loss", 0.0)) for row in rows),
        "auditor_code_hash": identity(
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (
                    Path(__file__),
                    Path(__file__).parents[1] / "src/preact/learning/transitions.py",
                )
            }
        ),
    }
    store.db.dispose()
    return verification


async def report_snapshot(store: Store, report: CandidateEvaluation):
    episodes = {}
    for case in report.cases:
        for branch in case.branches:
            episodes[branch.episode_id] = branch.runs
    return await TransitionDataset(store, episodes).snapshot()


async def main_async():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = await audit(args.bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main_async())
