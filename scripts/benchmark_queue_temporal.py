"""Paired, receipt-backed temporal evaluation; raw evidence stays in a new private path."""

import argparse
import asyncio
import hashlib
import itertools
import json
import platform
import time
from pathlib import Path
from statistics import fmean

from preact.cognition import CognitiveAgent, EpisodicMemory, Goal
from preact.cognition.information import InformationQueuePlanner
from preact.cognition.models import Belief
from preact.core.models import Policy, PredictionRequest, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier, InformationForecast
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.learning import TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.transitions import require_disjoint

ROOT = Path(__file__).resolve().parents[1]


class FixedPlanner:
    def __init__(self, sequence, start):
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


async def execute_sequence(world, sequence, store, artifacts, policy):
    if not sequence:
        return []
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry(
            [
                InformationBoundVerifier(world.task),
                InformationBoundVerifier(world.task, future=True),
            ]
        ),
        FixedPlanner(sequence, world.payload["tick"]),
        [Goal(name="Deliver", metric="delivered", target=world.target)],
        Policy(**policy),
    )
    result = await agent.run(world, len(sequence))
    if result.unsafe or sum(r["steps"] for r in result.rounds) != len(sequence):
        raise ValueError("Unsafe or incomplete gated collection")
    return result.run_ids


def source_hash():
    sources = [*sorted((ROOT / "src/preact").rglob("*.py")), Path(__file__)]
    return identity(
        {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    )


def summarize(comparisons, actual, horizon):
    """Scores on exactly the same action/tick pairs, without an unknown fallback."""
    state_errors, differences, covered = [], [], []
    supported = 0
    for result, truth in zip(comparisons, actual):
        if result["status"] != "estimated":
            continue
        supported += 1
        predictions = result["predictions"]
        for prediction, trace in zip(predictions, truth):
            for tick in range(1, horizon):
                state_errors.append(
                    abs(prediction.vectors["delivered"][tick] - trace[tick]["delivered"])
                )
            lo, hi = prediction.metric_intervals["delivered"]
            covered.append(lo <= trace[-1]["delivered"] <= hi)
        for pair in result["differences"]:
            left, right = pair["left"], pair["right"]
            for tick in range(1, horizon):
                observed = truth[left][tick]["delivered"] - truth[right][tick]["delivered"]
                differences.append(abs(pair["vectors"]["delivered"][tick] - observed))
    return {
        "comparisons": len(comparisons),
        "supported": supported,
        "coverage": supported / len(comparisons),
        "tick_2_3_state_mae": fmean(state_errors) if state_errors else None,
        "tick_2_3_action_difference_mae": fmean(differences) if differences else None,
        "support_range_contains_actual": fmean(covered) if covered else None,
    }


async def benchmark(protocol_path: Path, output: Path):
    protocol = json.loads(protocol_path.read_text())
    if set(protocol["training_seeds"]) & set(protocol["evaluation_seeds"]):
        raise ValueError("Train/evaluation seeds overlap")
    if protocol["horizon"] != 3 or protocol["actions"] != [3, 1, 0]:
        raise ValueError("Unsupported protocol semantics")
    if output.exists():
        raise ValueError("Use a new output path")
    output.mkdir(parents=True)
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts = Artifacts(str(output / "artifacts"))
    cpu, wall = time.process_time(), time.perf_counter()
    manifest = {"training": {}, "evaluation": {}}
    for seed in protocol["training_seeds"]:
        world = InformationQueueWorld(
            seed=seed,
            ticks=protocol["training_ticks"],
            capacity=protocol["capacity"],
            target=protocol["target"],
            **protocol["training_environment"],
        )
        pattern = protocol["training_actions"]
        sequence = [pattern[i % len(pattern)] for i in range(protocol["training_ticks"])]
        manifest["training"][f"train:{seed}"] = await execute_sequence(
            world, sequence, store, artifacts, protocol["policy"]
        )
    dataset, adapter = TransitionDataset(store, manifest["training"]), QueueServiceAdapter()
    training = await dataset.snapshot()
    models, digests, fits = {}, {}, {}
    for size in protocol["training_sizes"]:
        start_cpu, start_wall = time.process_time(), time.perf_counter()
        model = await TabularDynamicsTrainer().fit(
            dataset,
            adapter,
            TrainingConfig(min_samples=protocol["min_samples"], seed=protocol["training_seed"]),
            limit=size,
        )
        if len(model.receipts) != size:
            raise ValueError("Requested training size exceeds validated dataset")
        models[str(size)], digests[str(size)] = model, model.save(artifacts)
        fits[str(size)] = {
            "cpu_seconds": time.process_time() - start_cpu,
            "wall_seconds": time.perf_counter() - start_wall,
            "training_count": len(model.receipts),
            "informative_count": sum(round(c.count * c.mean[0]) for c in model.cells),
            "probe_count": sum(round(c.count * c.mean[2]) for c in model.cells),
            "ordinary_count": sum(round(c.count * c.mean[3]) for c in model.cells),
        }
    cases = []
    timings = {
        key: {"cpu_seconds": 0.0, "wall_seconds": 0.0, "engine_calls": 0, "service_paths": 0}
        for key in ("fixed_prior", *models)
    }
    existing_errors = []
    for environment, settings in protocol["evaluation_environments"].items():
        for seed, prefix in itertools.product(
            protocol["evaluation_seeds"], protocol["prefix_ticks"]
        ):
            case = {
                "id": f"eval:{environment}:{seed}:{prefix}",
                "environment": environment,
                "seed": seed,
                "prefix": prefix,
                "branches": [],
                "comparisons": {},
            }
            for amount in protocol["actions"]:
                world = InformationQueueWorld(
                    seed=seed,
                    ticks=protocol["evaluation_ticks"],
                    capacity=protocol["capacity"],
                    target=protocol["target"],
                    **settings,
                )
                prefix_runs = await execute_sequence(
                    world, ["probe"] * prefix, store, artifacts, protocol["policy"]
                )
                state = await world.observe()
                if not case["branches"]:
                    case["initial"] = state.model_dump()
                    analysis_run = store.create_run(world.task.model_dump())
                    store.append(analysis_run, "analysis_observed", {"state": state.model_dump()})
                    case["analysis_run"] = analysis_run
                    actions = [world.action(state, n) for n in protocol["actions"]]
                    case["actions"] = [a.model_dump() for a in actions]
                    for key in timings:
                        engine = (
                            QueueTemporalEngine(world.task, models[key])
                            if key in models
                            else QueueTemporalEngine(world.task, prior=protocol["fixed_prior"])
                        )
                        start_cpu, start_wall = time.process_time(), time.perf_counter()
                        comparison = await compare_actions(
                            Registry([engine]), engine, state, actions
                        )
                        timings[key]["cpu_seconds"] += time.process_time() - start_cpu
                        timings[key]["wall_seconds"] += time.perf_counter() - start_wall
                        timings[key]["engine_calls"] += len(actions)
                        timings[key]["service_paths"] += sum(
                            p.sample_count
                            for p in comparison["predictions"]
                            if p.raw["status"] == "estimated"
                        )
                        case["comparisons"][key] = {
                            **comparison,
                            "predictions": [p.model_dump() for p in comparison["predictions"]],
                        }
                        store.append(
                            analysis_run,
                            "temporal_comparison",
                            {"condition": key, **case["comparisons"][key]},
                        )
                elif state.payload != case["initial"]["payload"]:
                    raise ValueError("Paired initial states differ")
                runs = await execute_sequence(
                    world, [amount, 0, 0], store, artifacts, protocol["policy"]
                )
                episode = f"{case['id']}:{amount}"
                manifest["evaluation"][episode] = prefix_runs + runs
                snapshot = await TransitionDataset(store, {episode: runs}).snapshot()
                trace = [r.after.state.payload for r in snapshot.transitions]
                case["branches"].append(
                    {"amount": amount, "prefix_runs": prefix_runs, "runs": runs, "trace": trace}
                )
                memory = EpisodicMemory(store)
                memory.run_ids.extend(prefix_runs)
                old = InformationForecast(world.task, InformationQueuePlanner("no_probe"), memory)
                for row in snapshot.transitions:
                    p = await old.predict(PredictionRequest(state=row.before, actions=[row.action]))
                    existing_errors.append(
                        abs(p.metrics["processed"] - row.after.metrics["processed"])
                    )
                    memory.run_ids.append(row.run_id)
            cases.append(case)
    evaluation = await TransitionDataset(store, manifest["evaluation"]).snapshot()
    require_disjoint(training, evaluation)
    scores, scores_by_prefix = {}, {}
    from preact.core.models import Prediction

    for environment in protocol["evaluation_environments"]:
        selected = [c for c in cases if c["environment"] == environment]
        actual = [[b["trace"] for b in c["branches"]] for c in selected]
        scores[environment] = {}
        for key in timings:
            comparisons = [
                {
                    **c["comparisons"][key],
                    "predictions": [
                        Prediction.model_validate(p) for p in c["comparisons"][key]["predictions"]
                    ],
                }
                for c in selected
            ]
            scores[environment][key] = summarize(comparisons, actual, protocol["horizon"])
        for prefix in protocol["prefix_ticks"]:
            subset = [c for c in selected if c["prefix"] == prefix]
            label = f"{environment}/prefix:{prefix}"
            scores_by_prefix[label] = {}
            for key in timings:
                comparisons = [
                    {
                        **c["comparisons"][key],
                        "predictions": [
                            Prediction.model_validate(p)
                            for p in c["comparisons"][key]["predictions"]
                        ],
                    }
                    for c in subset
                ]
                scores_by_prefix[label][key] = summarize(
                    comparisons,
                    [[b["trace"] for b in c["branches"]] for c in subset],
                    protocol["horizon"],
                )
    summary = {
        "schema_version": "1",
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": source_hash(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "training_snapshot_hash": training.dataset_hash,
        "evaluation_snapshot_hash": evaluation.dataset_hash,
        "training_transitions": len(training.transitions),
        "evaluation_transitions": len(evaluation.transitions),
        "split_disjoint": True,
        "models": {
            k: {"version": m.version, "artifact_hash": digests[k], "dataset_hash": m.dataset_hash}
            for k, m in models.items()
        },
        "fit": fits,
        "inference": timings,
        "scores": scores,
        "scores_by_prefix": scores_by_prefix,
        "existing_online_one_step_mae": fmean(existing_errors),
        "existing_online_one_step_predictions": len(existing_errors),
        "evaluation_unsafe": sum(r.after.unsafe for r in evaluation.transitions),
        "execution_engine_calls": sum(
            e["kind"] == "prediction"
            for runs in manifest["evaluation"].values()
            for run in runs
            for e in store.read_events(run)
        ),
        "total_cpu_seconds": time.process_time() - cpu,
        "total_wall_seconds": time.perf_counter() - wall,
        "semantic_hash": identity(
            [
                {
                    "initial": c["initial"]["payload"],
                    "environment": c["environment"],
                    "seed": c["seed"],
                    "branches": [b["trace"] for b in c["branches"]],
                    "forecasts": {
                        k: [p["vectors"] for p in c["comparisons"][k]["predictions"]]
                        for k in timings
                    },
                }
                for c in cases
            ]
        ),
        "limits": protocol["limits"],
    }
    for name, content in (
        ("manifest", manifest),
        ("models", digests),
        ("cases", cases),
        ("summary", summary),
    ):
        (output / f"{name}.json").write_text(json.dumps(content, indent=2, allow_nan=False) + "\n")
    store.db.dispose()
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=ROOT / "benchmarks/queue-temporal-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol.resolve(), args.output.resolve()))
