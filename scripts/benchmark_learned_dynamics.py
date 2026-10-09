"""Reproducible CPU learning evaluation from real gated queue-v2 executions.

Raw Ledger, snapshots and artifacts remain in a new ignored output directory.
The shareable summary contains aggregates/hashes, not raw evidence or model data.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import random
import time
from pathlib import Path
from statistics import fmean

from preact.cognition import CognitiveAgent, EpisodicMemory, Goal
from preact.cognition.information import InformationQueuePlanner
from preact.cognition.models import Belief
from preact.core.models import Policy, PredictionRequest, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_features import QueueDynamicsAdapter
from preact.engines.information_queue import InformationBoundVerifier, InformationForecast
from preact.engines.learned_dynamics import LearnedDynamicsEngine
from preact.learning import TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.transitions import require_disjoint

ROOT = Path(__file__).resolve().parents[1]


class CollectionPlanner:
    """Seed/tick-only candidate order, independent of learned model and service."""

    def __init__(self, seed):
        self.seed = seed

    def infer(self, state, experience):
        return Belief(observed=state, unknown=["current_service"])

    def propose(self, belief, goals, width):
        state = belief.observed
        candidates = [InformationQueueWorld.action(state, amount) for amount in (3, 1, 0)]
        candidates.append(InformationQueueWorld.probe(state))
        random.Random(self.seed * 1000 + state.payload["tick"]).shuffle(candidates)
        return candidates[:width]


async def collect(store, artifacts, protocol, seed, environment, engine=None):
    world = InformationQueueWorld(
        seed=seed,
        ticks=protocol["ticks"],
        capacity=protocol["capacity"],
        target=protocol["target"],
        **environment,
    )
    engines = [
        InformationBoundVerifier(world.task),
        InformationBoundVerifier(world.task, future=True),
    ]
    if engine is not None:
        engines.insert(0, engine(world.task))
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry(engines),
        CollectionPlanner(seed),
        [Goal(name="Deliver", metric="delivered", target=protocol["target"])],
        Policy(**protocol["policy"]),
    )
    cpu, wall = time.process_time(), time.perf_counter()
    result = await agent.run(world, protocol["ticks"])
    elapsed = {"cpu_seconds": time.process_time() - cpu, "wall_seconds": time.perf_counter() - wall}
    if result.unsafe or world.payload["tick"] != protocol["ticks"]:
        raise ValueError("Collector unsafe or incomplete; do not report a completed protocol")
    return world, result, elapsed


def semantic(snapshot):
    return [
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


def summarize(records):
    supported = [r for r in records if r["supported"]]
    return {
        "transitions": len(records),
        "supported": len(supported),
        "coverage": len(supported) / len(records),
        "mae_with_explicit_midpoint_fallback": fmean(r["error"] for r in records),
        "supported_only_mae": fmean(r["error"] for r in supported) if supported else None,
        "range_coverage_supported": fmean(r["covered"] for r in supported) if supported else None,
        "mean_range_width_supported": fmean(r["width"] for r in supported) if supported else None,
        "inference_cpu_seconds": sum(r["cpu"] for r in records),
        "inference_wall_seconds": sum(r["wall"] for r in records),
    }


async def benchmark(protocol_path: Path, output: Path):
    protocol = json.loads(protocol_path.read_text())
    train_seeds, eval_seeds = protocol["training_seeds"], protocol["evaluation_seeds"]
    if (
        set(train_seeds) & set(eval_seeds)
        or len(set(train_seeds)) != len(train_seeds)
        or len(set(eval_seeds)) != len(eval_seeds)
    ):
        raise ValueError("Training and evaluation seeds must be distinct")
    if output.exists():
        raise ValueError("Use a new output path; historical results must not be overwritten")
    output.mkdir(parents=True)
    store, artifacts = (
        Store("sqlite:///" + str(output / "ledger.db")),
        Artifacts(str(output / "artifacts")),
    )
    manifest = {"training": {}, "evaluation": {}, "runtime": {}}
    collector_times = []
    for seed in protocol["training_seeds"]:
        _, result, elapsed = await collect(
            store, artifacts, protocol, seed, protocol["training_environment"]
        )
        manifest["training"][f"train:{seed}"] = result.run_ids
        collector_times.append(elapsed)
    for name, environment in protocol["evaluation_environments"].items():
        for seed in protocol["evaluation_seeds"]:
            _, result, elapsed = await collect(store, artifacts, protocol, seed, environment)
            manifest["evaluation"][f"eval:{name}:{seed}"] = result.run_ids
            collector_times.append(elapsed)
    training = TransitionDataset(store, manifest["training"])
    evaluation = TransitionDataset(store, manifest["evaluation"])
    training_snapshot, evaluation_snapshot = await training.snapshot(), await evaluation.snapshot()
    require_disjoint(training_snapshot, evaluation_snapshot)
    adapter, trainer = QueueDynamicsAdapter(), TabularDynamicsTrainer()
    config = TrainingConfig(min_samples=protocol["min_samples"], seed=protocol["training_seed"])
    models, fits, model_digests = {}, {}, {}
    for size in protocol["training_sizes"]:
        cpu, wall = time.process_time(), time.perf_counter()
        model = await trainer.fit(training, adapter, config, limit=size)
        fits[str(size)] = {
            "cpu_seconds": time.process_time() - cpu,
            "wall_seconds": time.perf_counter() - wall,
        }
        if len(model.receipts) != size:
            raise ValueError("Training size was not satisfied")
        models[str(size)] = model
        model_digests[str(size)] = model.save(artifacts)
    task = InformationQueueWorld().task
    predictions = {}
    for name in protocol["evaluation_environments"]:
        scenario = [
            r for r in evaluation_snapshot.transitions if r.episode_id.startswith(f"eval:{name}:")
        ]
        scenario_results = {}
        for condition in ["public_midpoint", "existing_online", *models]:
            records = []
            previous_episode = None
            learned_engine = (
                LearnedDynamicsEngine(task, adapter, models[condition])
                if condition in models
                else None
            )
            for row in scenario:
                if row.episode_id != previous_episode:
                    memory = EpisodicMemory(store)
                    old_engine = InformationForecast(
                        task, InformationQueuePlanner("no_probe"), memory
                    )
                    previous_episode = row.episode_id
                request = PredictionRequest(state=row.before, actions=[row.action])
                cpu, wall = time.process_time(), time.perf_counter()
                amount = 0 if row.action.kind == "probe_service" else row.action.payload["amount"]
                _, available, midpoint = transition(row.before.payload, amount, 2)
                if condition == "existing_online":
                    prediction = await old_engine.predict(request)
                elif condition == "public_midpoint":
                    prediction = None
                else:
                    prediction = await learned_engine.predict(request)
                elapsed_cpu, elapsed_wall = time.process_time() - cpu, time.perf_counter() - wall
                supported = prediction is not None and "processed" in prediction.metrics
                predicted = prediction.metrics["processed"] if supported else midpoint
                bounds = (
                    prediction.metric_intervals["processed"]
                    if supported
                    else (min(available, 1), min(available, 3))
                )
                actual = row.after.state.payload["delivered"] - row.before.payload["delivered"]
                # Conservation makes processed MAE equal next queue and delivered MAE.
                records.append(
                    {
                        "supported": supported,
                        "error": abs(predicted - actual),
                        "covered": float(bounds[0] <= actual <= bounds[1]),
                        "width": bounds[1] - bounds[0],
                        "cpu": elapsed_cpu,
                        "wall": elapsed_wall,
                    }
                )
                memory.run_ids.append(row.run_id)
            scenario_results[condition] = summarize(records)
        predictions[name] = scenario_results
    runtime = {}
    paired = []
    for condition in ("existing_runtime", "untrained_runtime", "learned_runtime"):

        def make_engine(runtime_task):
            key = "0" if condition == "untrained_runtime" else str(max(protocol["training_sizes"]))
            return LearnedDynamicsEngine(runtime_task, adapter, models[key])

        world, result, elapsed = await collect(
            store,
            artifacts,
            protocol,
            protocol["runtime_seed"],
            protocol["evaluation_environments"]["low_to_high"],
            engine=None if condition == "existing_runtime" else make_engine,
        )
        manifest["runtime"][condition] = result.run_ids
        snapshot = await TransitionDataset(store, {condition: result.run_ids}).snapshot()
        paired.append(semantic(snapshot))
        runtime[condition] = {
            **elapsed,
            "steps": len(snapshot.transitions),
            "unsafe": result.unsafe,
            "reward": world.reward,
            "engine_calls": sum(r["calls"] for r in result.rounds),
            "abstain": sum(r["steps"] == 0 for r in result.rounds),
            "semantic_hash": identity(paired[-1]),
        }
    if not paired[0] == paired[1] == paired[2]:
        raise ValueError("Runtime semantic equivalence failed")
    sources = [*sorted((ROOT / "src/preact").rglob("*.py")), Path(__file__)]
    summary = {
        "schema_version": "1",
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": identity(
            {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        ),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "training_transitions": len(training_snapshot.transitions),
        "evaluation_transitions": len(evaluation_snapshot.transitions),
        "split_disjoint": True,
        "training_snapshot_hash": training_snapshot.dataset_hash,
        "evaluation_snapshot_hash": evaluation_snapshot.dataset_hash,
        "models": {
            key: {
                "version": m.version,
                "dataset_hash": m.dataset_hash,
                "artifact_hash": model_digests[key],
                "cells": len(m.cells),
            }
            for key, m in models.items()
        },
        "fit": fits,
        "predictions": predictions,
        "runtime": runtime,
        "runtime_semantics_equal": True,
        "collection_cpu_seconds": sum(t["cpu_seconds"] for t in collector_times),
        "collection_wall_seconds": sum(t["wall_seconds"] for t in collector_times),
        "limits": [
            "Frozen low-service supervised training",
            "Existing Bayesian baseline uses online past observations",
            "Empirical ranges are not calibrated intervals",
            "No decision improvement claimed; runtime baseline uses only existing verifiers",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "models.json").write_text(json.dumps(model_digests, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, indent=2))
    store.db.dispose()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol", type=Path, default=ROOT / "benchmarks/learned-dynamics-v1.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol.resolve(), args.output.resolve()))
