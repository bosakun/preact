"""Receipt-backed recovery protocol. Raw databases/artifacts remain in a new private directory."""

import argparse
import asyncio
import json
import platform
import resource
import sys
import time
from dataclasses import asdict
from pathlib import Path

from preact.core.models import identity
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_recovery import score
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery, comparison_data
from preact.learning import TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import (
    CandidateEvaluation,
    EvaluationBranch,
    EvaluationCase,
    PromotionPolicy,
    RecoveryRejected,
)
from scripts.benchmark_queue_temporal import execute_sequence

ROOT = Path(__file__).resolve().parents[1]


def read_record(lifecycle, digest, record_type):
    return record_type.model_validate(json.loads(lifecycle.artifacts.read(digest)))


def world(protocol, seed, *, high, shift=None, noise=0):
    return InformationQueueWorld(
        seed=seed,
        ticks=protocol["episode_ticks"],
        target=protocol["target"],
        capacity=protocol["capacity"],
        high_first=high,
        shift_tick=protocol["episode_ticks"] if shift is None else shift,
        noise=noise,
    )


async def evaluation_cases(lifecycle, candidate, protocol, *, high, noise=0):
    cases, details = [], []
    for seed in protocol["comparison_seeds"]:
        for label, workload in protocol["workloads"].items():
            worlds, initials, prefixes = [], [], []
            for amount in (3, 1, 0):
                # Public fixture condition: low-capacity preparation, then evaluation regime.
                # Only the actual executor/auditor sees the generated private service schedule.
                instance = world(
                    protocol, seed, high=False, shift=len(workload) if high else None, noise=noise
                )
                initials.append(await instance.observe())
                prefixes.append(
                    await execute_sequence(
                        instance, workload, lifecycle.store, lifecycle.artifacts, protocol["policy"]
                    )
                )
                worlds.append(instance)
            state = await worlds[0].observe()
            actions = [worlds[0].action(state, n) for n in (3, 1, 0)]
            forecast = await lifecycle.forecast_candidate(candidate, worlds[0].task, state, actions)
            branches = []
            for i, amount in enumerate((3, 1, 0)):
                suffix = await execute_sequence(
                    worlds[i],
                    [amount, 0, 0],
                    lifecycle.store,
                    lifecycle.artifacts,
                    protocol["policy"],
                )
                branches.append(
                    EvaluationBranch(
                        episode_id=f"evaluation:{seed}:{label}:{amount}",
                        initial=initials[i],
                        runs=prefixes[i] + suffix,
                    )
                )
            cases.append(EvaluationCase(forecast_artifact=forecast, branches=branches))
            details.append({"seed": seed, "label": label, "high": high, "noise": noise})
    return cases, details


async def recovery_case(protocol, name, seed, output):
    output.mkdir(parents=True)
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts = Artifacts(str(output / "artifacts"))
    lifecycle = await QueueModelRecovery.create(store, artifacts)
    spec = protocol["conditions"][name]
    stages, cpu, wall = {}, time.process_time(), time.perf_counter()

    async def measured(label, operation):
        start_cpu, start_wall = time.process_time(), time.perf_counter()
        try:
            return await operation
        finally:
            stages[label] = {
                "cpu_seconds": time.process_time() - start_cpu,
                "wall_seconds": time.perf_counter() - start_wall,
            }

    parent_world = world(
        protocol,
        protocol["parent_training_seeds"][int(spec["parent_high"])],
        high=spec["parent_high"],
    )
    training = {
        "parent-training": await execute_sequence(
            parent_world,
            ["probe"] * protocol["parent_training_ticks"],
            store,
            artifacts,
            protocol["policy"],
        )
    }
    parent = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter(), TrainingConfig(min_samples=16)
    )
    source = world(
        protocol,
        seed,
        high=spec["parent_high"],
        shift=protocol["shift_tick"] if spec["shift"] else None,
        noise=spec["noise"],
    )
    initial = await source.observe()
    guard = QueueTemporalGuard(
        store,
        source.task,
        parent,
        training,
        episode_id="source",
        initial=initial,
        config=DriftConfig(**protocol["detector"]),
    )
    runs, origin = [], None
    record = {
        "condition": name,
        "seed": seed,
        "journal_run": lifecycle.journal_run,
        "parent_artifact": parent.save(artifacts),
        "training": training,
        "source_initial": initial.model_dump(),
        "source_task": source.task.model_dump(),
        "status": "not_invalidated",
        "promotion": None,
        "evaluation": None,
        "evaluation_details": [],
        "monitoring": [],
    }
    for _ in range(protocol["shift_tick"] + 48):
        runs += await execute_sequence(source, ["probe"], store, artifacts, protocol["policy"])
        state = await source.observe()
        health = await guard.health(state, runs)
        if health.usable and "parent_forecast_before" not in record:
            before = await guard.compare(state, [source.action(state, n) for n in (3, 1, 0)], runs)
            record["parent_forecast_before"] = {
                "state": state.model_dump(),
                "runs": list(runs),
                "result": comparison_data(before),
            }
        if health.status == "invalidated":
            suppressed = await guard.compare(
                state, [source.action(state, n) for n in (3, 1, 0)], runs
            )
            if suppressed["status"] != "unknown" or any(
                p.vectors for p in suppressed["predictions"]
            ):
                raise ValueError("Parent invalidation did not suppress predictions")
            record["parent_forecast_after"] = {
                "state": state.model_dump(),
                "runs": list(runs),
                "result": comparison_data(suppressed),
            }
            origin = await lifecycle.begin(
                parent,
                training,
                episode_id="source",
                task=source.task,
                initial=initial,
                cutoff=state,
                runs=runs,
                config=DriftConfig(**protocol["detector"]),
            )
            record.update(
                origin=origin, invalidation_tick=state.payload["tick"], invalidation=asdict(health)
            )
            break
    record["source_runs"] = list(runs)
    if origin is not None:
        extra = spec.get("training_ticks", protocol["recovery_training_ticks"])
        runs += await execute_sequence(
            source, ["probe"] * extra, store, artifacts, protocol["policy"]
        )
        record["source_runs"] = list(runs)
        try:
            candidate = await measured(
                "training", lifecycle.prepare_candidate(origin, await source.observe(), runs)
            )
            record["candidate"] = candidate
            new_high = not spec["parent_high"]
            cases, details = await measured(
                "forecast_and_collect_evaluation",
                evaluation_cases(
                    lifecycle,
                    candidate,
                    protocol,
                    high=spec.get("evaluation_high", new_high),
                    noise=spec["noise"],
                ),
            )
            evaluation = await measured(
                "evaluation",
                lifecycle.evaluate_candidate(candidate, cases, (await source.observe()).timestamp),
            )
            report = read_record(lifecycle, evaluation, CandidateEvaluation)
            record.update(
                evaluation=evaluation,
                evaluation_details=details,
                scores=report.scores,
                evaluation_reasons=report.reasons,
            )
            if not report.passed:
                raise RecoveryRejected("evaluation_rejected:" + ",".join(report.reasons))
            monitor_worlds, monitor_initials, monitor_runs = [], [], []
            pattern = spec.get("monitoring_actions", ["probe"] * protocol["monitoring_ticks"])
            for amount in (3, 1, 0):
                instance = world(protocol, seed + 1000, high=new_high, noise=spec["noise"])
                monitor_initials.append(await instance.observe())
                prefix = pattern + [3, 3]
                monitor_runs.append(
                    await execute_sequence(instance, prefix, store, artifacts, protocol["policy"])
                )
                monitor_worlds.append(instance)
            monitor = monitor_worlds[0]
            state = await monitor.observe()
            record["monitoring"] = [
                {
                    "episode_id": f"monitor:{amount}",
                    "initial": i.model_dump(),
                    "runs": r,
                    "task": w.task.model_dump(),
                }
                for amount, i, r, w in zip(
                    (3, 1, 0), monitor_initials, monitor_runs, monitor_worlds
                )
            ]
            promotion = await measured(
                "promotion",
                lifecycle.promote(
                    evaluation,
                    episode_id="monitor:3",
                    task=monitor.task,
                    initial=monitor_initials[0],
                    state=state,
                    runs=monitor_runs[0],
                ),
            )
            actions = [monitor.action(state, n) for n in (3, 1, 0)]
            comparison = await measured(
                "recovered_comparison",
                lifecycle.compare(await monitor.observe(), actions, monitor_runs[0]),
            )
            restarted = QueueModelRecovery(store, artifacts, lifecycle.journal_run)
            await measured("restore", restarted.restore(await monitor.observe(), monitor_runs[0]))
            replay = await restarted.compare(await monitor.observe(), actions, monitor_runs[0])
            if comparison["differences"] != replay["differences"]:
                raise ValueError("Restart changed recovered comparison")
            actual = []
            for i, amount in enumerate((3, 1, 0)):
                suffix = await execute_sequence(
                    monitor_worlds[i], [amount, 0, 0], store, artifacts, protocol["policy"]
                )
                monitor_runs[i] += suffix
                rows = (
                    await TransitionDataset(
                        store, {f"monitor:{amount}": monitor_runs[i]}
                    ).snapshot()
                ).transitions
                actual.append([r.after.state.payload for r in rows[-3:]])
                record["monitoring"][i]["runs"] = monitor_runs[i]
            from preact.core.registry import Registry
            from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions

            old = QueueTemporalEngine(monitor.task, parent)
            prior = QueueTemporalEngine(monitor.task, prior=0.5)
            record["post_scores"] = {
                "candidate": score([comparison_data(comparison)], [actual]),
                "parent_unmanaged": score(
                    [comparison_data(await compare_actions(Registry([old]), old, state, actions))],
                    [actual],
                ),
                "prior": score(
                    [
                        comparison_data(
                            await compare_actions(Registry([prior]), prior, state, actions)
                        )
                    ],
                    [actual],
                ),
                "invalidated_no_recovery": score([{"status": "unknown"}], [actual]),
                "all_unknown": score([{"status": "unknown"}], [actual]),
            }
            record.update(
                status="promoted",
                promotion=promotion,
                recovery_ticks={
                    "source_after_invalidation": extra,
                    "evaluation_executions": sum(len(b.runs) for c in cases for b in c.branches),
                    "monitoring_before_promotion": len(pattern) + 2,
                    "total": extra
                    + sum(len(b.runs) for c in cases for b in c.branches)
                    + 3 * (len(pattern) + 2),
                    "semantics": "Sequential experiment execution across different episodes, not continuous-world latency",
                },
                post_comparison={
                    "state": state.model_dump(),
                    "result": comparison_data(comparison),
                },
            )
        except RecoveryRejected as error:
            record.update(status="rejected", rejection=str(error))
    record["source_cutoff"] = (await source.observe()).model_dump()
    record["parent_prediction_stopped"] = "parent_forecast_after" in record
    roles = {"parent": training, "source": {"source": runs}}
    if record["evaluation"]:
        report = read_record(lifecycle, record["evaluation"], CandidateEvaluation)
        roles["evaluation"] = {b.episode_id: b.runs for c in report.cases for b in c.branches}
    if record["monitoring"]:
        roles["monitoring"] = {m["episode_id"]: m["runs"] for m in record["monitoring"]}
    actual_semantics = {}
    for role, manifest in roles.items():
        snapshot = await TransitionDataset(store, manifest).snapshot()
        actual_semantics[role] = [
            {
                "episode": r.episode_id,
                "kind": r.action.kind,
                "action": r.action.payload,
                "before": r.before.payload,
                "after": r.after.state.payload,
                "metrics": r.after.metrics,
                "checks": r.after.checks,
                "unsafe": r.after.unsafe,
            }
            for r in snapshot.transitions
        ]
    record["actual_semantic_hash"] = identity(actual_semantics)
    record["executed_actions"] = sum(len(rows) for rows in actual_semantics.values())
    record["unsafe_outcomes"] = sum(r["unsafe"] for rows in actual_semantics.values() for r in rows)
    recorded_runs = list(
        dict.fromkeys(
            r for manifest in roles.values() for runs_ in manifest.values() for r in runs_
        )
    )
    record["runtime_engine_calls"] = 0
    for r in recorded_runs:
        record["runtime_engine_calls"] += sum(
            e["kind"] == "prediction" for e in await store.call("read_events", r)
        )
    record["timing"] = {
        "cpu_seconds": time.process_time() - cpu,
        "wall_seconds": time.perf_counter() - wall,
        "stages": stages,
    }
    record["peak_process_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if sys.platform == "darwin" else 1024
    )
    (output / "case.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def semantic(records):
    return identity(
        [
            {
                k: c.get(k)
                for k in (
                    "condition",
                    "seed",
                    "status",
                    "rejection",
                    "invalidation_tick",
                    "scores",
                    "post_scores",
                    "recovery_ticks",
                    "actual_semantic_hash",
                    "executed_actions",
                    "unsafe_outcomes",
                    "runtime_engine_calls",
                    "parent_prediction_stopped",
                )
            }
            for c in records
        ]
    )


async def benchmark(protocol_path: Path, output: Path, *, pilot=False):
    protocol = json.loads(protocol_path.read_text())
    if protocol["promotion"] != PromotionPolicy().model_dump(exclude={"schema_version"}):
        raise ValueError("Frozen promotion policy mismatch")
    sets = [
        set(protocol[k])
        for k in ("pilot_seeds", "evaluation_seeds", "comparison_seeds", "parent_training_seeds")
    ]
    if any(a & b for i, a in enumerate(sets) for b in sets[i + 1 :]):
        raise ValueError("Protocol seed roles overlap")
    if output.exists():
        raise ValueError("Use a new private output path")
    output.mkdir(parents=True)
    records = []
    seeds = protocol["pilot_seeds"] if pilot else protocol["evaluation_seeds"]
    conditions = (
        ("low_to_high", "high_to_low", "insufficient_training") if pilot else protocol["conditions"]
    )
    for condition in conditions:
        for seed in seeds:
            case = await recovery_case(protocol, condition, seed, output / f"{condition}-{seed}")
            records.append(case)
            print(
                json.dumps(
                    {
                        "condition": condition,
                        "seed": seed,
                        "status": case["status"],
                        "reason": case.get("rejection"),
                    }
                ),
                flush=True,
            )
    summary = {
        "protocol_hash": identity(protocol),
        "pilot": pilot,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "semantic_hash": semantic(records),
        "cases": [
            {
                k: c.get(k)
                for k in (
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
            }
            for c in records
        ],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=ROOT / "benchmarks/queue-recovery-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol, args.output, pilot=args.pilot))
