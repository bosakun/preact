"""Revalidate receipts, models, paired action traces and independently rescore predictions."""

import argparse
import asyncio
import hashlib
import itertools
import json
import math
from pathlib import Path
from statistics import fmean

from preact.cognition import EpisodicMemory
from preact.cognition.information import InformationQueuePlanner
from preact.core.models import Action, Prediction, PredictionRequest, State, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld, action_amount
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationForecast
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.transitions import require_disjoint


def check(condition, message):
    if not condition:
        raise ValueError(message)


async def audit(output: Path, protocol_path: Path):
    protocol = json.loads(protocol_path.read_text())
    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    cases = json.loads((output / "cases.json").read_text())
    digests = json.loads((output / "models.json").read_text())
    check(
        summary["protocol_sha256"] == hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "Protocol changed",
    )
    root = Path(__file__).resolve().parents[1]
    sources = [
        *sorted((root / "src/preact").rglob("*.py")),
        root / "scripts/benchmark_queue_temporal.py",
    ]
    check(
        summary["source_sha256"]
        == identity(
            {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        ),
        "Prediction source changed",
    )
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts = Artifacts(str(output / "artifacts"))
    dataset = TransitionDataset(store, manifest["training"])
    training = await dataset.snapshot()
    evaluation = await TransitionDataset(store, manifest["evaluation"]).snapshot()
    require_disjoint(training, evaluation)
    check(
        training.dataset_hash == summary["training_snapshot_hash"]
        and evaluation.dataset_hash == summary["evaluation_snapshot_hash"],
        "Authority changed",
    )
    check(
        len(training.transitions) == summary["training_transitions"]
        and len(evaluation.transitions) == summary["evaluation_transitions"],
        "Dataset size changed",
    )
    models = {}
    adapter = QueueServiceAdapter()
    check(
        set(digests) == set(summary["models"]) == {str(n) for n in protocol["training_sizes"]},
        "Model manifest changed",
    )
    for size in protocol["training_sizes"]:
        key = str(size)
        model = DynamicsModel.load(artifacts, digests[key], adapter)
        refit = await TabularDynamicsTrainer().fit(
            dataset,
            adapter,
            TrainingConfig(min_samples=protocol["min_samples"], seed=protocol["training_seed"]),
            limit=size,
        )
        check(
            model == refit and model.version == summary["models"][key]["version"],
            "Model not derived from verified teachers",
        )
        check(
            summary["models"][key]["artifact_hash"] == digests[key]
            and summary["models"][key]["dataset_hash"] == model.dataset_hash,
            "Model source identifiers changed",
        )
        # Independently count masks; total rows must not substitute for ability samples.
        targets = [adapter.targets(r) for r in training.transitions[:size]]
        fit = summary["fit"][key]
        check(
            fit["training_count"] == size
            and fit["informative_count"] == sum(t[0] for t in targets)
            and fit["probe_count"] == sum(t[2] for t in targets)
            and fit["ordinary_count"] == sum(t[3] for t in targets),
            "Effective sample counts changed",
        )
        models[key] = model
    expected_cases = {
        f"eval:{env}:{seed}:{prefix}"
        for env, seed, prefix in itertools.product(
            protocol["evaluation_environments"],
            protocol["evaluation_seeds"],
            protocol["prefix_ticks"],
        )
    }
    check(
        {c["id"] for c in cases} == expected_cases and len(cases) == len(expected_cases),
        "Missing or duplicate cases",
    )
    scores = {}
    calls, old_errors, semantics = 0, [], []
    for case in cases:
        world = InformationQueueWorld(
            seed=case["seed"],
            ticks=protocol["evaluation_ticks"],
            capacity=protocol["capacity"],
            target=protocol["target"],
            **protocol["evaluation_environments"][case["environment"]],
        )
        state = State.model_validate(case["initial"])
        analysis_events = store.read_events(case["analysis_run"])
        check(
            analysis_events[0]["kind"] == "analysis_observed"
            and analysis_events[0]["data"]["state"] == case["initial"],
            "Unbound analysis observation",
        )
        actual = []
        for branch, amount in zip(case["branches"], protocol["actions"]):
            check(
                branch["amount"] == amount
                and len(branch["runs"]) == 3
                and len(branch["prefix_runs"]) == case["prefix"],
                "Action/tick budget mismatch",
            )
            episode = f"{case['id']}:{amount}"
            check(
                manifest["evaluation"][episode] == branch["prefix_runs"] + branch["runs"],
                "Episode manifest mismatch",
            )
            rows = [r for r in evaluation.transitions if r.episode_id == episode]
            roots = rows[case["prefix"] :]
            check(
                len(roots) == 3
                and roots[0].before.payload == state.payload
                and roots[0].before.provenance == state.provenance,
                "Paired state mismatch",
            )
            check(
                all(r.continuous_from_previous is not False for r in rows), "Discontinuous episode"
            )
            check(
                all(r.action.kind == "probe_service" for r in rows[: case["prefix"]]),
                "Prefix semantics changed",
            )
            trace = []
            memory = EpisodicMemory(store)
            memory.run_ids.extend(branch["prefix_runs"])
            old = InformationForecast(world.task, InformationQueuePlanner("no_probe"), memory)
            for tick, row in enumerate(roots):
                check(
                    action_amount(row.before, row.action) == (amount if tick == 0 else 0),
                    "Unexpected follow-up intervention",
                )
                check(
                    row.after.state.payload["tick"] == state.payload["tick"] + tick + 1,
                    "Misaligned tick",
                )
                trace.append(row.after.state.payload)
                p = await old.predict(PredictionRequest(state=row.before, actions=[row.action]))
                old_errors.append(abs(p.metrics["processed"] - row.after.metrics["processed"]))
                memory.run_ids.append(row.run_id)
            check(trace == branch["trace"], "Forged evaluation trace")
            actual.append(trace)
            for row in rows:
                events = store.read_events(row.run_id)
                check(
                    sum(e["kind"] == "authorization" for e in events) == 1
                    and sum(e["kind"] == "outcome" for e in events) == 1,
                    "Missing Gate or multiple actions",
                )
                calls += sum(e["kind"] == "prediction" for e in events)
        actions = [Action.model_validate(a) for a in case["actions"]]
        check(
            [a.payload for a in actions] == [{"amount": n} for n in protocol["actions"]],
            "Prediction interventions changed",
        )
        for key, recorded in case["comparisons"].items():
            engine = (
                QueueTemporalEngine(world.task, models[key])
                if key in models
                else QueueTemporalEngine(world.task, prior=protocol["fixed_prior"])
            )
            regenerated = await compare_actions(Registry([engine]), engine, state, actions)
            check(
                regenerated["status"] == recorded["status"]
                and regenerated["differences"] == recorded["differences"],
                "Comparison changed",
            )
            predictions = [Prediction.model_validate(p) for p in recorded["predictions"]]
            for p, regenerated_p in zip(predictions, regenerated["predictions"]):
                check(
                    p.model_dump(mode="json", exclude={"id", "latency_ms"})
                    == regenerated_p.model_dump(mode="json", exclude={"id", "latency_ms"}),
                    "Prediction changed",
                )
                check(
                    p.evidence == "inference"
                    and not p.claim_results
                    and not p.mandatory_checks
                    and not p.success.measured
                    and not p.risk.measured
                    and not p.future_states,
                    "Promoted forecast authority",
                )
            matching = [
                e
                for e in analysis_events
                if e["kind"] == "temporal_comparison" and e["data"]["condition"] == key
            ]
            check(
                len(matching) == 1 and matching[0]["data"] == {"condition": key, **recorded},
                "Analysis Ledger mismatch",
            )
            for scope in (case["environment"], f"{case['environment']}/prefix:{case['prefix']}"):
                group = scores.setdefault(scope, {}).setdefault(
                    key,
                    {"count": 0, "supported": 0, "errors": [], "differences": [], "covered": []},
                )
                group["count"] += 1
                if recorded["status"] == "estimated":
                    group["supported"] += 1
                    for p, trace in zip(predictions, actual):
                        group["errors"].extend(
                            abs(p.vectors["delivered"][t] - trace[t]["delivered"]) for t in (1, 2)
                        )
                        lo, hi = p.metric_intervals["delivered"]
                        group["covered"].append(lo <= trace[-1]["delivered"] <= hi)
                    # Recompute differences directly; do not trust stored differences.
                    for left, right in itertools.combinations(range(3), 2):
                        group["differences"].extend(
                            abs(
                                (
                                    predictions[left].vectors["delivered"][t]
                                    - predictions[right].vectors["delivered"][t]
                                )
                                - (actual[left][t]["delivered"] - actual[right][t]["delivered"])
                            )
                            for t in (1, 2)
                        )
        semantics.append(
            {
                "initial": state.payload,
                "environment": case["environment"],
                "seed": case["seed"],
                "branches": actual,
                "forecasts": {
                    k: [p["vectors"] for p in case["comparisons"][k]["predictions"]]
                    for k in summary["inference"]
                },
            }
        )
    for environment, conditions in scores.items():
        for key, group in conditions.items():
            recomputed = {
                "comparisons": group["count"],
                "supported": group["supported"],
                "coverage": group["supported"] / group["count"],
                "tick_2_3_state_mae": fmean(group["errors"]) if group["errors"] else None,
                "tick_2_3_action_difference_mae": fmean(group["differences"])
                if group["differences"]
                else None,
                "support_range_contains_actual": fmean(group["covered"])
                if group["covered"]
                else None,
            }
            for metric, value in recomputed.items():
                section = (
                    "scores"
                    if environment in protocol["evaluation_environments"]
                    else "scores_by_prefix"
                )
                stored = summary[section][environment][key][metric]
                check(
                    stored is None if value is None else math.isclose(stored, value, abs_tol=1e-12),
                    f"Score mismatch: {environment}/{key}/{metric}",
                )
    check(identity(semantics) == summary["semantic_hash"], "Semantic summary changed")
    check(calls == summary["execution_engine_calls"], "Execution call count changed")
    check(
        math.isclose(fmean(old_errors), summary["existing_online_one_step_mae"]),
        "Existing baseline score changed",
    )
    check(
        summary["evaluation_unsafe"] == sum(r.after.unsafe for r in evaluation.transitions) == 0,
        "Unsafe or forged outcomes",
    )
    store.db.dispose()
    report = {
        "status": "passed-queue-temporal-audit",
        "training_transitions": len(training.transitions),
        "evaluation_transitions": len(evaluation.transitions),
        "paired_comparisons": len(cases),
        "models": len(models),
        "audit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Revalidated receipts, authority, fit, paired forecasts and scores; timing not independently certified",
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "benchmarks/queue-temporal-v1.json",
    )
    args = parser.parse_args()
    asyncio.run(audit(args.output.resolve(), args.protocol.resolve()))
