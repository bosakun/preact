"""Frozen receipt-backed drift evaluation. Publish aggregates, keep raw data private."""

import argparse
import asyncio
import hashlib
import json
import platform
import time
from dataclasses import asdict
from pathlib import Path
from statistics import fmean

from preact.core.models import identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.drift import DriftConfig, ServiceSample, replay_health
from preact.learning.transitions import require_disjoint
from scripts.benchmark_queue_temporal import execute_sequence

ROOT = Path(__file__).resolve().parents[1]


def source_hash():
    paths = [
        *sorted((ROOT / "src/preact").rglob("*.py")),
        Path(__file__),
        ROOT / "scripts/audit_queue_drift.py",
    ]
    return identity(
        {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    )


def semantic(cases):
    return identity(
        [
            {
                "environment": c["environment"],
                "seed": c["seed"],
                "truth": c["trace"],
                "forecasts": [
                    {
                        "tick": q["state"]["payload"]["tick"],
                        "fixed": q["fixed"]["vectors"],
                        "guarded": q["guarded"]["vectors"],
                        "health": q["health"]["status"],
                        "history": [
                            {
                                k: h[k]
                                for k in ("look", "tick", "effect", "p_value", "alpha", "status")
                            }
                            for h in q["health"]["history"]
                        ],
                    }
                    for q in c["forecasts"]
                ],
            }
            for c in cases
        ]
    )


def mean(values):
    return fmean(values) if values else None


def scores(cases, environments):
    result = {}
    for name, spec in environments.items():
        selected = [c for c in cases if c["environment"] == name]
        probes = [q for c in selected for q in c["forecasts"]]
        timings = {
            mode: {
                key: sum(q["timing"][mode][key] for q in probes)
                for key in ("cpu_seconds", "wall_seconds")
            }
            for mode in ("fixed", "guarded")
        }
        stats = {}
        for mode in ("fixed", "guarded", "all_unknown"):
            supported, errors, post_errors, pre_errors = 0, [], [], []
            for c in selected:
                for q in c["forecasts"]:
                    p = q[mode] if mode != "all_unknown" else {"raw": {"status": "unknown"}}
                    if p["raw"]["status"] != "estimated":
                        continue
                    supported += 1
                    tick = q["state"]["payload"]["tick"]
                    for j in range(3):
                        for metric in ("queue", "delivered"):
                            error = abs(p["vectors"][metric][j] - c["trace"][tick + j][metric])
                            errors.append(error)
                            (post_errors if tick >= spec["change_tick"] else pre_errors).append(
                                error
                            )
            stats[mode] = {
                "predictions": len(probes),
                "supported": supported,
                "coverage": supported / len(probes),
                "unknown_rate": 1 - supported / len(probes),
                "state_mae": mean(errors),
                "pre_change_mae": mean(pre_errors),
                "post_change_mae": mean(post_errors),
            }
        common = []
        suppressions = 0
        delays_tick, delays_effective, first_cuts = [], [], []
        detections = 0
        for c in selected:
            last = c["final_health"]
            first = next((h for h in last["history"] if h["status"] == "invalidated"), None)
            detections += first is not None
            if first is not None:
                delays_tick.append(max(0, first["tick"] + 1 - spec["change_tick"]))
                before = sum(s["tick"] < spec["change_tick"] for s in c["samples"])
                delays_effective.append(first["effective_count"] - before)
                first_cuts.append(
                    next(
                        (
                            q["state"]["payload"]["tick"]
                            for q in c["forecasts"]
                            if q["health"]["status"] == "invalidated"
                        ),
                        None,
                    )
                )
            for q in c["forecasts"]:
                if q["health"]["status"] == "invalidated":
                    if q["guarded"]["raw"]["status"] != "unknown" or q["guarded"]["vectors"]:
                        raise ValueError("Invalid model forecast was not suppressed")
                    suppressions += 1
                if q["guarded"]["raw"]["status"] == "estimated":
                    tick = q["state"]["payload"]["tick"]
                    for j in range(3):
                        for metric in ("queue", "delivered"):
                            common.append(
                                abs(q["fixed"]["vectors"][metric][j] - c["trace"][tick + j][metric])
                            )
        result[name] = {
            "episodes": len(selected),
            "detected": detections,
            "detection_rate": detections / len(selected),
            "false_alarm_rate": detections / len(selected) if spec["stable"] else None,
            "missed_rate": 1 - detections / len(selected) if not spec["stable"] else None,
            "delay_ticks": delays_tick if not spec["stable"] else [],
            "delay_effective_samples": delays_effective if not spec["stable"] else [],
            "first_suppressed_cutoffs": first_cuts,
            "suppressed_predictions": suppressions,
            "scores": stats,
            "fixed_mae_on_guarded_support": mean(common),
            "timing": timings,
        }
    return result


async def benchmark(protocol_path: Path, output: Path):
    protocol = json.loads(protocol_path.read_text())
    seeds = [set(protocol[k]) for k in ("training_seeds", "pilot_seeds", "evaluation_seeds")]
    if any(seeds[i] & seeds[j] for i in range(3) for j in range(i + 1, 3)):
        raise ValueError("Training, pilot and evaluation seeds must be disjoint")
    if output.exists():
        raise ValueError("Use a new output directory")
    output.mkdir(parents=True)
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts = Artifacts(str(output / "artifacts"))
    adapter = QueueServiceAdapter()
    config = DriftConfig(**protocol["detector"])
    start_cpu, start_wall = time.process_time(), time.perf_counter()
    manifest = {"training": {}, "evaluation": {}}
    models, training_sources, digests = {}, {}, {}
    for regime in ("low", "high"):
        sources = {}
        for seed in protocol["training_seeds"]:
            world = InformationQueueWorld(
                seed=seed,
                ticks=protocol["training_ticks"],
                capacity=12,
                target=999,
                high_first=regime == "high",
                shift_tick=protocol["training_ticks"],
                noise=protocol["training_noise"],
            )
            pattern = protocol["training_actions"]
            sources[f"train:{regime}:{seed}"] = await execute_sequence(
                world,
                [pattern[t % len(pattern)] for t in range(protocol["training_ticks"])],
                store,
                artifacts,
                protocol["policy"],
            )
        manifest["training"].update(sources)
        training_sources[regime] = sources
        model = await TabularDynamicsTrainer().fit(
            TransitionDataset(store, sources), adapter, TrainingConfig(min_samples=4, seed=17)
        )
        models[regime], digests[regime] = model, model.save(artifacts)
    cases = []
    for env, spec in protocol["environments"].items():
        for seed in protocol["evaluation_seeds"]:
            episode = f"monitor:{env}:{seed}"
            world = InformationQueueWorld(
                seed=seed,
                ticks=protocol["evaluation_ticks"],
                capacity=12,
                target=999,
                **spec["world"],
            )
            initial = await world.observe()
            regime = spec["training_regime"]
            model = models[regime]
            guard = QueueTemporalGuard(
                store,
                world.task,
                model,
                training_sources[regime],
                episode_id=episode,
                initial=initial,
                config=config,
            )
            engine = QueueTemporalEngine(world.task, model)
            runs, forecasts = [], []
            analysis_run = store.create_run({"analysis": "queue-drift/v1", "episode": episode})
            pattern = spec["actions"]
            for tick in range(protocol["evaluation_ticks"]):
                if tick % 4 == 1 and tick + 3 <= protocol["evaluation_ticks"]:
                    state = await world.observe()
                    action = world.action(state, pattern[tick % len(pattern)])
                    timings, outputs = {}, {}
                    for mode in ("fixed", "guarded"):
                        c, w = time.process_time(), time.perf_counter()
                        result = (
                            await guard.compare(state, [action], runs)
                            if mode == "guarded"
                            else await compare_actions(Registry([engine]), engine, state, [action])
                        )
                        timings[mode] = {
                            "cpu_seconds": time.process_time() - c,
                            "wall_seconds": time.perf_counter() - w,
                        }
                        outputs[mode] = result
                    entry = {
                        "state": state.model_dump(),
                        "action": action.model_dump(),
                        "prefix_runs": list(runs),
                        "health": outputs["guarded"]["health"],
                        "health_version": outputs["guarded"]["health_version"],
                        "view_version": outputs["guarded"]["engine_view_version"],
                        "fixed": outputs["fixed"]["predictions"][0].model_dump(),
                        "guarded": outputs["guarded"]["predictions"][0].model_dump(),
                        "timing": timings,
                    }
                    store.append(analysis_run, "monitored_comparison", entry)
                    forecasts.append(entry)
                runs += await execute_sequence(
                    world, [pattern[tick % len(pattern)]], store, artifacts, protocol["policy"]
                )
            manifest["evaluation"][episode] = runs
            snapshot = await TransitionDataset(store, {episode: runs}).snapshot()
            training = await TransitionDataset(store, training_sources[regime]).snapshot()
            require_disjoint(training, snapshot)
            state = await world.observe()
            # At terminal tick only health matters; h1 request returns unknown as episode is over.
            final = await guard.health(state, runs)
            samples = []
            for row in snapshot.transitions:
                t = adapter.targets(row)
                if t[0]:
                    samples.append(
                        ServiceSample(
                            row.receipt,
                            row.outcome_reference,
                            row.before.payload["tick"],
                            bool(t[1]),
                            "probe" if t[2] else "ordinary",
                        )
                    )
            counts = [adapter.targets(r) for r in training.transitions]
            sensitivity = {}
            for setting in protocol["sensitivity"]:
                cfg = DriftConfig(
                    **{
                        **protocol["detector"],
                        **{k: v for k, v in setting.items() if k != "training_transition_limit"},
                    }
                )
                selected_counts = counts[: setting.get("training_transition_limit", len(counts))]
                h = replay_health(
                    model_version=model.version,
                    dataset_hash=model.dataset_hash,
                    scope=episode,
                    basis_hash=final.basis_hash,
                    training_count=sum(int(t[0]) for t in selected_counts),
                    training_high=sum(int(t[1]) for t in selected_counts),
                    samples=tuple(samples),
                    cutoff_tick=state.payload["tick"],
                    config=cfg,
                )
                sensitivity[identity(setting)] = {
                    "setting": setting,
                    "training_count": h.training_count,
                    "training_high": h.training_high,
                    "status": h.status,
                    "first_invalid": next(
                        (asdict(c) for c in h.history if c.status == "invalidated"), None
                    ),
                }
            cases.append(
                {
                    "environment": env,
                    "seed": seed,
                    "episode": episode,
                    "initial": initial.model_dump(),
                    "task": world.task.model_dump(),
                    "regime": regime,
                    "runs": runs,
                    "analysis_run": analysis_run,
                    "trace": [r.after.state.payload for r in snapshot.transitions],
                    "samples": [asdict(s) for s in samples],
                    "forecasts": forecasts,
                    "final_state": state.model_dump(),
                    "final_health": asdict(final),
                    "sensitivity": sensitivity,
                }
            )
            print(f"collected {episode}: {final.status}", flush=True)
    evaluation = await TransitionDataset(store, manifest["evaluation"]).snapshot()
    summary = {
        "schema_version": "1",
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": source_hash(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "models": {
            r: {
                "version": m.version,
                "dataset_hash": m.dataset_hash,
                "artifact_hash": digests[r],
                "transitions": len(m.receipts),
                "informative_count": sum(round(c.count * c.mean[0]) for c in m.cells),
                "high_count": sum(round(c.count * c.mean[1]) for c in m.cells),
            }
            for r, m in models.items()
        },
        "scores": scores(cases, protocol["environments"]),
        "sensitivity": {
            env: [
                {
                    "setting": setting,
                    "episodes": len(protocol["evaluation_seeds"]),
                    "detected": sum(
                        c["sensitivity"][identity(setting)]["status"] == "invalidated"
                        for c in cases
                        if c["environment"] == env
                    ),
                    "first_invalid_ticks": [
                        c["sensitivity"][identity(setting)]["first_invalid"]["tick"] + 1
                        for c in cases
                        if c["environment"] == env
                        and c["sensitivity"][identity(setting)]["first_invalid"] is not None
                    ],
                    "training_counts": [
                        c["sensitivity"][identity(setting)]["training_count"]
                        for c in cases
                        if c["environment"] == env
                    ],
                }
                for setting in protocol["sensitivity"]
            ]
            for env in protocol["environments"]
        },
        "unsafe": sum(r.after.unsafe for r in evaluation.transitions),
        "execution_transitions": len(evaluation.transitions),
        "engine_calls": sum(
            e["kind"] == "prediction"
            for runs in manifest["evaluation"].values()
            for run in runs
            for e in store.read_events(run)
        ),
        "semantic_hash": semantic(cases),
        "total_cpu_seconds": time.process_time() - start_cpu,
        "total_wall_seconds": time.perf_counter() - start_wall,
        "limits": protocol["limits"],
    }
    for name, value in (
        ("manifest", manifest),
        ("models", digests),
        ("cases", cases),
        ("summary", summary),
    ):
        (output / f"{name}.json").write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    store.db.dispose()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=ROOT / "benchmarks/queue-drift-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol.resolve(), args.output.resolve()))
