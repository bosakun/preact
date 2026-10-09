"""Audit private Ledger/model artifacts and recompute published prediction scores."""

import argparse
import asyncio
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import fmean, pvariance

from preact.cognition import EpisodicMemory
from preact.cognition.information import InformationQueuePlanner
from preact.core.models import PredictionRequest, identity
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_features import QueueDynamicsAdapter
from preact.engines.information_queue import InformationForecast
from preact.learning import DynamicsModel, TransitionDataset
from preact.learning.transitions import require_disjoint


async def audit(output: Path, protocol_path: Path):
    protocol = json.loads(protocol_path.read_text())
    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    digests = json.loads((output / "models.json").read_text())
    if summary["protocol_sha256"] != hashlib.sha256(protocol_path.read_bytes()).hexdigest():
        raise ValueError("Benchmark protocol changed")
    root = Path(__file__).resolve().parents[1]
    sources = [
        *sorted((root / "src/preact").rglob("*.py")),
        root / "scripts/benchmark_learned_dynamics.py",
    ]
    if summary["source_sha256"] != identity(
        {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    ):
        raise ValueError("Benchmark source code changed")
    store, artifacts = (
        Store("sqlite:///" + str(output / "ledger.db")),
        Artifacts(str(output / "artifacts")),
    )
    training = await TransitionDataset(store, manifest["training"]).snapshot()
    evaluation = await TransitionDataset(store, manifest["evaluation"]).snapshot()
    require_disjoint(training, evaluation)
    if (
        training.dataset_hash != summary["training_snapshot_hash"]
        or evaluation.dataset_hash != summary["evaluation_snapshot_hash"]
        or len(training.transitions) != summary["training_transitions"]
        or len(evaluation.transitions) != summary["evaluation_transitions"]
    ):
        raise ValueError("Benchmark source observations changed")
    adapter = QueueDynamicsAdapter()
    models = {}
    for key, digest in digests.items():
        model = DynamicsModel.load(artifacts, digest, adapter)
        rows = training.transitions[: int(key)]
        if (
            model.version != summary["models"][key]["version"]
            or model.dataset_hash != identity([r.model_dump() for r in rows])
            or model.receipts != tuple(r.receipt for r in rows)
            or model.outcome_references != tuple(r.outcome_reference for r in rows)
        ):
            raise ValueError("Model training source mismatch")
        groups = defaultdict(list)
        for row in rows:
            groups[adapter.features(row.before, row.action)].append(adapter.targets(row))
        if len(model.cells) != len(groups):
            raise ValueError("Model feature cells mismatch")
        for cell in model.cells:
            values = groups[cell.features]
            columns = list(zip(*values))
            if (
                cell.count != len(values)
                or cell.mean != tuple(fmean(col) for col in columns)
                or cell.variance != tuple(pvariance(col) for col in columns)
                or cell.minimum != tuple(min(col) for col in columns)
                or cell.maximum != tuple(max(col) for col in columns)
            ):
                raise ValueError("Model statistics do not derive from observed targets")
        models[key] = model
    for name in protocol["evaluation_environments"]:
        rows = [r for r in evaluation.transitions if r.episode_id.startswith(f"eval:{name}:")]
        for condition, recorded in summary["predictions"][name].items():
            errors, supported_errors, covered, widths = [], [], [], []
            previous = None
            for row in rows:
                if row.episode_id != previous:
                    memory = EpisodicMemory(store)
                    forecast = InformationForecast(
                        InformationQueueWorld().task, InformationQueuePlanner("no_probe"), memory
                    )
                    previous = row.episode_id
                amount = 0 if row.action.kind == "probe_service" else row.action.payload["amount"]
                _, _, value = transition(row.before.payload, amount, 2)
                bounds, supported = None, False
                if condition == "existing_online":
                    prediction = await forecast.predict(
                        PredictionRequest(state=row.before, actions=[row.action])
                    )
                    value, bounds, supported = (
                        prediction.metrics["processed"],
                        prediction.metric_intervals["processed"],
                        True,
                    )
                elif condition in models:
                    cell = models[condition].lookup(adapter.features(row.before, row.action))
                    if cell is not None:
                        value, bounds, supported = (
                            cell.mean[1],
                            (cell.minimum[1], cell.maximum[1]),
                            True,
                        )
                actual = row.after.state.payload["delivered"] - row.before.payload["delivered"]
                errors.append(abs(value - actual))
                if supported:
                    supported_errors.append(errors[-1])
                    covered.append(float(bounds[0] <= actual <= bounds[1]))
                    widths.append(bounds[1] - bounds[0])
                memory.run_ids.append(row.run_id)
            recomputed = {
                "transitions": len(rows),
                "supported": len(supported_errors),
                "coverage": len(supported_errors) / len(rows),
                "mae_with_explicit_midpoint_fallback": fmean(errors),
                "supported_only_mae": fmean(supported_errors) if supported_errors else None,
                "range_coverage_supported": fmean(covered) if covered else None,
                "mean_range_width_supported": fmean(widths) if widths else None,
            }
            for metric, value in recomputed.items():
                if value is None:
                    valid = recorded[metric] is None
                else:
                    valid = math.isclose(value, recorded[metric], abs_tol=1e-12)
                if not valid:
                    raise ValueError(f"Prediction score mismatch: {name}/{condition}/{metric}")
    semantics = []
    for name, runs in manifest["runtime"].items():
        snapshot = await TransitionDataset(store, {name: runs}).snapshot()
        actual = [
            {
                "before": r.before.payload,
                "action": {"kind": r.action.kind, "payload": r.action.payload},
                "after": r.after.state.payload,
                "checks": r.after.checks,
                "metrics": r.after.metrics,
                "success": r.after.success,
                "unsafe": r.after.unsafe,
            }
            for r in snapshot.transitions
        ]
        semantics.append(actual)
        recorded = summary["runtime"][name]
        if (
            identity(actual) != recorded["semantic_hash"]
            or len(actual) != recorded["steps"]
            or any(r.after.unsafe for r in snapshot.transitions) != recorded["unsafe"]
            or not math.isclose(
                sum(r.after.metrics["reward"] for r in snapshot.transitions), recorded["reward"]
            )
        ):
            raise ValueError("Runtime observation/receipt summary mismatch")
        calls = 0
        for run in runs:
            events = store.read_events(run)
            calls += sum(e["kind"] == "prediction" for e in events)
            if sum(e["kind"] == "authorization" for e in events) != 1:
                raise ValueError("Missing per-action Gate authorization")
            for event in events:
                if (
                    event["kind"] == "prediction"
                    and event["data"]["prediction"]["engine_id"] == "learned-dynamics"
                ):
                    prediction = event["data"]["prediction"]
                    if (
                        prediction["evidence"] != "inference"
                        or prediction["mandatory_checks"]
                        or prediction["success"]["measured"]
                        or prediction["risk"]["measured"]
                    ):
                        raise ValueError("Learned forecast promoted to safety evidence")
        if calls != recorded["engine_calls"]:
            raise ValueError("Runtime engine call count mismatch")
    if not semantics[0] == semantics[1] == semantics[2]:
        raise ValueError("Runtime semantic comparison mismatch")
    store.db.dispose()
    report = {
        "status": "passed-receipt-backed-dynamics-audit",
        "audit_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "verified_training_transitions": len(training.transitions),
        "verified_evaluation_transitions": len(evaluation.transitions),
        "models": len(models),
        "runtime_semantics_equal": True,
        "scope": "Receipt/source alignment, fitted statistics, prediction scores and Runtime observations; timing is measured, not independently reproducible exactly",
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks/learned-dynamics-v1.json",
    )
    args = parser.parse_args()
    asyncio.run(audit(args.output.resolve(), args.protocol.resolve()))
