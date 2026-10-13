"""Outcome-aligned supplementary metrics; never label unexecuted alternatives actual."""

import argparse
import asyncio
import json
from pathlib import Path
from statistics import fmean

from preact.core.models import identity
from preact.core.store import Artifacts, Store
from preact.learning import TransitionDataset
from preact.learning.recovery import CandidateEvaluation


async def measure_case(folder):
    metadata = json.loads((folder / "case.json").read_text())
    store = Store("sqlite:///" + str(folder / "ledger.db"))
    snapshot = await TransitionDataset(store, metadata["episodes"]).snapshot()
    rows = {r.run_id: r for r in snapshot.transitions}
    errors = []
    learned, fallback, readiness, refused, local, verifiers, external = 0, 0, 0, 0, 0, 0, 0
    versions = {}
    for runs in metadata["episodes"].values():
        for run in runs:
            events = store.read_events(run)
            predictions = [
                e["data"]["prediction"]
                for e in events
                if e["kind"] == "prediction" and not e["data"]["cached"]
            ]
            verifiers += sum(p["evidence"] != "inference" for p in predictions)
            traces = [e["data"] for e in events if e["kind"] == "deliberation"]
            row = rows.get(run)
            qualifications = [
                e["data"]["batch"] for e in events if e["kind"] == "candidate_qualification"
            ]
            refused += sum(
                c["status"] == "rejected" for b in qualifications for c in b["candidates"]
            )
            if not traces or not row:
                continue
            trace = traces[0]
            local += trace["local_calls"]
            future = next(
                f
                for f in trace["simulation"]["candidates"]
                if f["fingerprint"] == row.action.fingerprint
            )
            health = trace["simulation"].get("health")
            basis = future.get("basis")
            used = basis == "learned_observed_goal" or (
                metadata["domain"] == "queue"
                and row.action.kind == "submit"
                and isinstance(health, dict)
                and health["status"] == "available"
                and basis != "fixed_prior_fallback"
            )
            learned += used
            readiness += basis in {"readiness_not_learned_value", "bounded_receipt_collection"}
            fallback += not used and basis not in {
                "readiness_not_learned_value",
                "bounded_receipt_collection",
            }
            version = trace["simulation"].get("model_version") or (
                health.get("model_version") if isinstance(health, dict) else None
            )
            if used:
                versions[version] = versions.get(version, 0) + 1
            if metadata["domain"] == "program":
                if basis in {"learned_observed_goal", "fixed_goal_prior"}:
                    errors.append(
                        abs(future["value"] - float(row.after.state.payload["goal_complete"]))
                    )
            elif future["records"]:
                pred = future["records"][0]["prediction"]
                if pred.get("vectors"):
                    errors.append(
                        sum(
                            abs(pred["vectors"][k][0] - row.after.state.payload[k])
                            for k in ("queue", "delivered")
                        )
                        / 2
                    )
    sources = store.read_events(metadata["journal"])
    checkpoints = [e["data"] for e in sources if e["kind"] == "learning_checkpoint"]
    preparation = metadata.get("preparation_runs", [])
    independent = []
    training_count = None
    evaluation_scores = None
    if metadata["domain"] == "program" and metadata["backend_state"].get("candidate"):
        artifacts = Artifacts(str(folder / "artifacts"))
        candidate = json.loads(artifacts.read(metadata["backend_state"]["candidate"]))
        teacher = await TransitionDataset(store, candidate["training"]).snapshot()
        training_count = len(teacher.transitions)
        evaluation = json.loads(artifacts.read(metadata["backend_state"]["evaluation"]))
        independent = [r for rs in evaluation["episodes"].values() for r in rs]
        evaluation_scores = evaluation["scores"]
    elif metadata["domain"] == "queue":
        artifacts = Artifacts(str(folder / "artifacts"))
        latest = metadata["backend_state"]
        report = latest.get("evaluation")
        if latest["epoch"].get("promotion_artifact"):
            promotion = json.loads(artifacts.read(latest["epoch"]["promotion_artifact"]))
            report = promotion["evaluation_artifact"]
        if report:
            evaluation = CandidateEvaluation.model_validate(json.loads(artifacts.read(report)))
            evaluation_scores = evaluation.scores
            independent = [
                r for case in evaluation.cases for branch in case.branches for r in branch.runs
            ]
    eval_events = [e for r in independent for e in store.read_events(r)]
    return {
        "case": folder.name,
        "executed_actions": len(rows),
        "learned_executions": learned,
        "fallback_executions": fallback,
        "readiness_executions": readiness,
        "model_versions_used": versions,
        "executed_h1_mae": fmean(errors) if errors else None,
        "executed_forecast_coverage": len(errors) / len(rows) if rows else 0,
        "executed_forecast_unknown_rate": 1 - len(errors) / len(rows) if rows else 1,
        "action_difference_mae": None,
        "action_difference_limit": "Unexecuted alternatives have no actual labels in closed-loop tasks; independent Queue H3 comparison scores are separate",
        "independent_evaluation_scores": evaluation_scores,
        "training_receipts": training_count,
        "preparation_actions": len(preparation),
        "independent_evaluation_actions": sum(e["kind"] == "outcome" for e in eval_events),
        "independent_evaluation_verifier_calls": sum(
            e["kind"] == "prediction"
            and not e["data"]["cached"]
            and e["data"]["prediction"]["evidence"] != "inference"
            for e in eval_events
        ),
        "task_verifier_calls": verifiers,
        "local_proposal_prediction_calls": local,
        "qualification_rejections": refused,
        "external_api_calls": external,
        "external_api_cost_usd": 0.0,
        "local_compute_dollar_cost": None,
        "learning_control_cpu_seconds": sum(c["cpu_seconds"] for c in checkpoints),
        "learning_control_wall_seconds": sum(c["wall_seconds"] for c in checkpoints),
        "unsafe": sum(r.after.unsafe for r in rows.values()),
        "task_reward": sum(r.after.metrics["reward"] for r in rows.values())
        if metadata["domain"] == "queue"
        else None,
        "task_goal_progress_sum": sum(
            float(r.after.state.payload.get("goal_complete", False)) for r in rows.values()
        )
        if metadata["domain"] == "program"
        else None,
    }


async def summarize(bundle, output):
    cases = [
        await measure_case(folder)
        for folder in sorted(bundle.iterdir())
        if (folder / "case.json").exists()
    ]
    report = {
        "cases": cases,
        "case_manifest_hash": identity([c["case"] for c in cases]),
        "limits": [
            "H1 MAE only for actually executed actions with forecasts; coverage denominator is all actual actions including probes",
            "Independent H3 comparison and receding-horizon task outcomes are separate",
            "Program outcome memory is closed-vocabulary; no generalization claim",
            "No Software reward exists; goal progress is reported instead of inventing zero reward",
            "Preparation and independent evaluation costs are not hidden in task-only metrics",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(summarize(args.bundle, args.output))


if __name__ == "__main__":
    main()
