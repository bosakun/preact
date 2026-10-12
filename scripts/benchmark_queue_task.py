"""Paired task-aware Queue lifecycle experiments; raw authority stays in private bundles."""

import argparse
import asyncio
import hashlib
import json
import random
import resource
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.queue_task import (
    QueueTaskPlanner,
    TaskInformationModel,
    lifecycle_recommendation,
)
from preact.core.models import Policy, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_multi_selection import QueueMultiSelectionWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_continuous import ContinuousQueueAdaptation
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import CandidateEvaluation, CandidateModel
from scripts.benchmark_queue_recovery import evaluation_cases, read_record
from scripts.benchmark_queue_temporal import execute_sequence

ROOT = Path(__file__).resolve().parents[1]


def source_identity():
    return identity(
        {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                ROOT / "src/preact/cognition/queue_task.py",
                ROOT / "src/preact/cognition/scheduler.py",
                Path(__file__),
            )
        }
    )


def make_world(protocol, spec, seed):
    world = InformationQueueWorld(
        seed=seed,
        ticks=protocol["episode_ticks"],
        target=protocol["goal"],
        capacity=protocol["capacity"],
        noise=0,
        shift_tick=protocol["episode_ticks"],
        submission_cost=spec.get("submission_cost", protocol["submission_cost"]),
        probe_cost=spec.get("probe_cost", protocol["probe_cost"]),
    )
    # Schedule construction is owned experimental environment setup, never passed
    # to planner/scheduler. Each policy receives the same exogenous realization.
    rng = random.Random(seed)
    schedule = []
    for tick in range(protocol["episode_ticks"]):
        regime = next(r for r in spec["regimes"] if r["start"] <= tick < r["end"])
        high = regime["service"] == 3
        if rng.random() < spec.get("noise", 0):
            high = not high
        schedule.append(3 if high else 1)
    world._service = schedule
    return world


async def decision(
    world, source, epoch_runs, all_runs, store, artifacts, protocol, context, strategy
):
    planner = QueueTaskPlanner(
        source,
        prefix_runs=epoch_runs,
        context=context,
        strategy=strategy,
        episode_memory_runs=all_runs[-12:],
        information_model=TaskInformationModel(**protocol["information_model"]),
    )
    wrapper = QueueMultiSelectionWorld(world, planner)
    registry = Registry(
        [InformationBoundVerifier(world.task), InformationBoundVerifier(world.task, future=True)]
    )
    agent = CognitiveAgent(
        store,
        artifacts,
        registry,
        planner,
        [Goal(name="continuous-task", metric="delivered", target=world.target)],
        Policy(**protocol["policy"]),
        candidate_selector=planner,
    )
    # Existing Memory supports restoring an explicit receipt-backed run manifest.
    agent.memory.run_ids = list(all_runs[-12:])
    result = await agent.run(wrapper, 1)
    return result.run_ids[0], result.rounds[0]


async def run_case(protocol, label, strategy, seed, output):
    spec = protocol["conditions"][label]
    output.mkdir(parents=True, exist_ok=False)
    store, artifacts = (
        Store("sqlite:///" + str(output / "ledger.db")),
        Artifacts(str(output / "artifacts")),
    )
    cpu, wall = time.process_time(), time.perf_counter()
    world = make_world(protocol, spec, seed)
    initial = await world.observe()
    episode = f"task:{label}:{strategy}:{seed}"
    record = {
        "condition": label,
        "strategy": strategy,
        "seed": seed,
        "episode": episode,
        "initial": initial.model_dump(),
        "runs": [],
        "epochs": [],
        "recoveries": [],
        "health": [],
        "failures": [],
        "stages": [],
    }
    lifecycle, source = None, None
    epoch_runs, monitor_runs, origin, pending_guard, anchor = [], [], None, None, None
    effective, tasks_in_epoch = 0, 0
    phase = "monitor"
    if strategy != "fixed_task":
        pretrain = InformationQueueWorld(seed=701, ticks=32, target=999, noise=0)
        pretrain._service = [spec["regimes"][0]["service"]] * 32
        training = {
            f"pretrain:{episode}": await execute_sequence(
                pretrain, ["probe"] * 16, store, artifacts, protocol["collection_policy"]
            )
        }
        model = await TabularDynamicsTrainer().fit(
            TransitionDataset(store, training), QueueServiceAdapter()
        )
        lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, episode)
        initial = await world.observe()
        record["initial"] = initial.model_dump()
        digest = await lifecycle.start(
            model,
            training,
            world_episode_id=episode,
            task=world.task,
            anchor=initial,
            config=DriftConfig(**protocol["detection"]),
        )
        record.update(journal=lifecycle.journal_run, initial_model=model.version)
        record["epochs"].append({"artifact": digest, "generation": 0})
        source = lifecycle.selection_source()

    for _ in range(spec.get("execution_ticks", protocol["execution_ticks"])):
        state = await world.observe()
        if world.complete(state):
            break
        if lifecycle is not None:
            health = await lifecycle.health(state, epoch_runs)
            record["health"].append(
                {"state": state.model_dump(), "runs": list(epoch_runs), "health": asdict(health)}
            )
            if (
                health.status == "invalidated"
                and origin is None
                and phase == "monitor"
                and strategy != "no_recovery"
            ):
                origin = await lifecycle.begin_recovery(state, epoch_runs)
                phase, effective = "collect", 0
                record["recoveries"].append(
                    {
                        "origin": origin,
                        "cutoff": state.model_dump(),
                        "journal": lifecycle.recovery.journal_run,
                        "training_ordinary": 0,
                        "training_probe": 0,
                    }
                )

            recommendation = lifecycle_recommendation(
                phase, effective, state.payload["episode_ticks"] - state.payload["tick"]
            )
            if recommendation == "train_evaluate" and state.payload["delivered"] < world.target:
                await store.call(
                    "append",
                    lifecycle.journal_run,
                    "cognitive_lifecycle_schedule",
                    {
                        "state": state.model_dump(),
                        "recommendation": recommendation,
                        "effective_training": effective,
                        "monitoring_epoch": lifecycle.epoch.epoch_id,
                    },
                )
                start_cpu, start_wall = time.process_time(), time.perf_counter()
                candidate = await lifecycle.recovery.prepare_candidate(origin, state, epoch_runs)
                independent_protocol = json.loads(
                    (ROOT / "benchmarks/queue-recovery-v1.json").read_text()
                )
                # Independent evaluation environment is declared by the protocol
                # operator, not inferred by reading the source World's schedule.
                regime = next(
                    r for r in spec["regimes"] if r["start"] <= state.payload["tick"] < r["end"]
                )
                eval_high = spec.get("evaluation_high", regime["service"] == 3)
                cases, details = await evaluation_cases(
                    lifecycle.recovery,
                    candidate,
                    independent_protocol,
                    high=eval_high,
                    noise=spec.get("noise", 0),
                )
                generation = lifecycle.epoch.generation + 1
                cases = [
                    c.model_copy(
                        update={
                            "branches": [
                                b.model_copy(
                                    update={
                                        "episode_id": f"{episode}:evaluation:{generation}:{b.episode_id}"
                                    }
                                )
                                for b in c.branches
                            ]
                        }
                    )
                    for c in cases
                ]
                evaluation = await lifecycle.recovery.evaluate_candidate(
                    candidate, cases, datetime.now(timezone.utc).isoformat()
                )
                report = read_record(lifecycle.recovery, evaluation, CandidateEvaluation)
                record["recoveries"][-1].update(
                    candidate=candidate,
                    evaluation=evaluation,
                    evaluation_passed=report.passed,
                    evaluation_reasons=report.reasons,
                    scores=report.scores,
                    evaluation_conditions=details,
                )
                record["stages"].append(
                    {
                        "kind": "training_evaluation",
                        "cpu_seconds": time.process_time() - start_cpu,
                        "wall_seconds": time.perf_counter() - start_wall,
                    }
                )
                if not report.passed:
                    phase = "rejected"
                else:
                    anchor = await world.observe()
                    candidate_record = read_record(lifecycle.recovery, candidate, CandidateModel)
                    model = DynamicsModel.load(
                        artifacts, candidate_record.model_artifact, QueueServiceAdapter()
                    )
                    epoch = identity(
                        {
                            "generation": generation,
                            "model": model.version,
                            "episode": episode,
                            "anchor": anchor.model_dump(),
                        }
                    )
                    pending_guard = QueueTemporalGuard(
                        store,
                        world.task,
                        model,
                        candidate_record.training,
                        episode_id=episode,
                        initial=anchor,
                        monitoring_epoch=epoch,
                        allow_same_episode=True,
                        config=DriftConfig(**protocol["detection"]),
                    )
                    phase, monitor_runs = "await_promotion", []
            if phase == "await_promotion":
                state = await world.observe()
                candidate_health = await pending_guard.health(state, monitor_runs)
                record["recoveries"][-1].update(
                    monitor_anchor=anchor.model_dump(),
                    monitor_runs=list(monitor_runs),
                    monitor_state=state.model_dump(),
                    monitor_health=asdict(candidate_health),
                )
                if (
                    candidate_health.status == "available"
                    and state.payload["delivered"] < world.target
                ):
                    # This is a deliberate harness instruction, not automatic
                    # promotion by the production scheduler or execution Runtime.
                    await store.call(
                        "append",
                        lifecycle.recovery.journal_run,
                        "harness_promotion_requested",
                        {"evaluation": evaluation, "state": state.model_dump()},
                    )
                    digest = await lifecycle.promote(
                        evaluation, anchor=anchor, state=state, monitor_runs=monitor_runs
                    )
                    restored = ContinuousQueueAdaptation(store, artifacts, lifecycle.journal_run)
                    await restored.restore(await world.observe(), monitor_runs)
                    lifecycle, source = restored, restored.selection_source()
                    record["epochs"].append(
                        {"artifact": digest, "generation": lifecycle.epoch.generation}
                    )
                    record["recoveries"][-1]["promotion"] = digest
                    epoch_runs = list(monitor_runs)
                    phase, origin, effective, tasks_in_epoch = "monitor", None, 0, 0
                elif candidate_health.status == "invalidated":
                    phase = "rejected"
                    record["recoveries"][-1]["promotion_rejected"] = "monitor_invalidated"

        before = await world.observe()
        if strategy == "fixed_task" or (
            strategy == "probe_centric" and (health.status != "available" or tasks_in_epoch > 0)
        ):
            run = (
                await execute_sequence(
                    world,
                    [1 if strategy == "fixed_task" else "probe"],
                    store,
                    artifacts,
                    protocol["collection_policy"],
                )
            )[0]
            outcome = {"steps": 1}
            if strategy == "probe_centric":
                await store.call(
                    "append",
                    run,
                    "benchmark_collection",
                    {
                        "reason": "declared_pr14_probe_control",
                        "stage": phase,
                        "monitoring_epoch": lifecycle.epoch.epoch_id,
                    },
                )
        else:
            context = {
                "stage": phase,
                "effective_training": effective,
                "missing_monitor": max(
                    0, 8 - (candidate_health.effective_count if phase == "await_promotion" else 0)
                ),
                "tasks_in_epoch": tasks_in_epoch,
                "monitoring_epoch": lifecycle.epoch.epoch_id,
            }
            run, outcome = await decision(
                world,
                source,
                epoch_runs,
                record["runs"],
                store,
                artifacts,
                protocol,
                context,
                strategy,
            )
        record["runs"].append(run)
        if outcome["steps"] != 1:
            record["failures"].append(
                {"tick": before.payload["tick"], "reason": "runtime_abstained"}
            )
            break
        snapshot = await TransitionDataset(store, {episode: [run]}).snapshot()
        if len(snapshot.transitions) != 1 or snapshot.unlabelled_receipts:
            raise ValueError("One formal complete receipt required per task tick")
        row = snapshot.transitions[0]
        targets = QueueServiceAdapter().targets(row)
        if strategy != "fixed_task":
            epoch_runs.append(run)
            tasks_in_epoch += row.action.kind == "submit"
            if phase == "collect":
                effective += int(targets[0])
                record["recoveries"][-1]["training_probe"] += int(targets[2])
                record["recoveries"][-1]["training_ordinary"] += int(targets[3])
            elif phase == "await_promotion":
                monitor_runs.append(run)

    final = await world.observe()
    if phase in {"collect", "await_promotion"}:
        record["failures"].append(
            {
                "tick": final.payload["tick"],
                "reason": "goal_met_recovery_deferred"
                if final.payload["delivered"] >= world.target
                else (
                    "insufficient_effective_training"
                    if phase == "collect"
                    else "promotion_health_unavailable"
                ),
                "effective_training": effective,
            }
        )
    snapshot = await TransitionDataset(store, {episode: record["runs"]}).snapshot()
    rows = snapshot.transitions
    counters = {
        "task_actions": 0,
        "probe_actions": 0,
        "ordinary_informative": 0,
        "probe_informative": 0,
        "fallback_task_actions": 0,
        "learned_task_actions": 0,
        "verifier_calls": 0,
        "local_prediction_calls": 0,
        "gate_rejections": 0,
        "unresolved": 0,
        "model_unavailable_decisions": 0,
    }
    model_actions, goal_model_actions, amounts, errors = {}, {}, [], []
    advancing_actions = 0
    reference_disagreements = 0
    for row in rows:
        teacher = QueueServiceAdapter().targets(row)
        counters["task_actions"] += row.action.kind == "submit"
        counters["probe_actions"] += row.action.kind == "probe_service"
        counters["ordinary_informative"] += int(teacher[3])
        counters["probe_informative"] += int(teacher[2])
        advancing_actions += row.after.metrics["processed"] > 0 or (
            row.action.kind == "submit" and row.action.payload["amount"] > 0
        )
        amounts.append(
            "probe" if row.action.kind == "probe_service" else row.action.payload["amount"]
        )
        events = await store.call("read_events", row.run_id)
        counters["verifier_calls"] += sum(e["kind"] == "prediction" for e in events)
        forecasts = [e["data"] for e in events if e["kind"] == "cognitive_prediction"]
        for forecast in forecasts:
            counters["model_unavailable_decisions"] += (
                forecast.get("health", {}).get("status") != "available"
            )
            counters["local_prediction_calls"] += forecast["local_total_prediction_calls"]
            counters["local_prediction_calls"] += 6
            if row.action.kind == "submit":
                learned = forecast["basis"] == "learned_model"
                counters["learned_task_actions"] += learned
                counters["fallback_task_actions"] += not learned
                if learned:
                    version = forecast["health"]["model_version"]
                    model_actions[version] = model_actions.get(version, 0) + 1
                    if row.before.payload["delivered"] < world.target:
                        goal_model_actions[version] = goal_model_actions.get(version, 0) + 1
                    schedule = next(e["data"] for e in events if e["kind"] == "cognitive_schedule")
                    low = schedule["reference_forecasts"]["low"]
                    low_amount = min(
                        (3, 1, 0),
                        key=lambda n: (
                            -sum(
                                b["weight"]
                                * (
                                    min(
                                        max(0, world.target - row.before.payload["delivered"]),
                                        b["delivered"][-1] - row.before.payload["delivered"],
                                    )
                                    - row.before.payload["holding_cost"] * sum(b["queue"])
                                    - row.before.payload["submission_cost"] * n
                                )
                                for b in low[(3, 1, 0).index(n)]["raw"]["branches"]
                            ),
                            n,
                        ),
                    )
                    reference_disagreements += row.action.payload["amount"] != low_amount
                    candidate = next(
                        c
                        for c in forecast["candidates"]
                        if c["fingerprint"] == row.action.fingerprint
                    )
                    branches = candidate["prediction"]["raw"]["branches"]
                    errors.extend(
                        abs(
                            sum(b["weight"] * b[key][0] for b in branches)
                            - row.after.state.payload[key]
                        )
                        for key in ("queue", "delivered")
                    )
        for event in events:
            if event["kind"] == "candidate_qualification":
                counters["gate_rejections"] += sum(
                    c["status"] == "rejected" for c in event["data"]["batch"]["candidates"]
                )
                counters["unresolved"] += sum(
                    c["status"] == "unresolved" for c in event["data"]["batch"]["candidates"]
                )
    record["final"] = final.model_dump()
    record["dataset_hash"] = snapshot.dataset_hash
    record["summary"] = {
        **counters,
        "executed_ticks": len(rows),
        "delivered": final.payload["delivered"],
        "goal_success": world.complete(final),
        "goal_tick": next(
            (
                r.after.state.payload["tick"]
                for r in rows
                if r.after.state.payload["delivered"] >= world.target
            ),
            None,
        ),
        "goal_threshold_met": final.payload["delivered"] >= world.target,
        "abstain_decisions": sum(f["reason"] == "runtime_abstained" for f in record["failures"]),
        "reward": sum(r.after.metrics["reward"] for r in rows),
        "holding_loss": sum(r.after.metrics["holding_loss"] for r in rows),
        "goal_capped_utility": min(world.target, final.payload["delivered"])
        - sum(
            r.after.metrics["holding_loss"]
            + r.after.metrics["submission_cost"]
            + (world.probe_cost if r.action.kind == "probe_service" else 0)
            for r in rows
        ),
        "probe_cost": counters["probe_actions"] * world.probe_cost,
        "unsafe": sum(r.after.unsafe for r in rows),
        "model_task_actions": model_actions,
        "model_goal_active_actions": goal_model_actions,
        "work_advancing_actions": advancing_actions,
        "learned_vs_low_reference_action_differences": reference_disagreements,
        "action_sequence": amounts,
        "promotions": len(record["epochs"]) - 1 if lifecycle else 0,
        "invalidations": len(
            {
                entry["health"]["model_version"]
                for entry in record["health"]
                if entry["health"]["status"] == "invalidated"
            }
        ),
        "task_rate": counters["task_actions"] / len(rows) if rows else 0,
        "probe_rate": counters["probe_actions"] / len(rows) if rows else 0,
        "failures": record["failures"],
        "one_step_state_mae": sum(errors) / len(errors) if errors else None,
        "one_step_prediction_coverage": len(errors) / (2 * len(rows)) if rows else 0,
        "cpu_seconds": time.process_time() - cpu,
        "wall_seconds": time.perf_counter() - wall,
        "max_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "external_api_calls": 0,
        "known_api_cost_usd": 0,
        "compute_dollar_cost": None,
    }
    (output / "task-result.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    store.db.dispose()
    return record


async def run(protocol_path, output, report):
    source_hash = source_identity()
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for label in protocol["conditions"]:
        for seed in protocol["seeds"]:
            for strategy in protocol["conditions"][label].get("strategies", protocol["strategies"]):
                record = await run_case(
                    protocol, label, strategy, seed, output / f"{label}-{seed}-{strategy}"
                )
                results.append(
                    {
                        "condition": label,
                        "seed": seed,
                        "strategy": strategy,
                        **record["summary"],
                        "recoveries": [
                            {
                                k: v
                                for k, v in r.items()
                                if k
                                in {
                                    "training_probe",
                                    "training_ordinary",
                                    "evaluation_passed",
                                    "evaluation_reasons",
                                    "scores",
                                }
                            }
                            | {
                                "invalidation_tick": r["cutoff"]["payload"]["tick"],
                                "promotion_tick": r.get("monitor_state", {})
                                .get("payload", {})
                                .get("tick")
                                if "promotion" in r
                                else None,
                            }
                            for r in record["recoveries"]
                        ],
                    }
                )
                print(
                    label,
                    seed,
                    strategy,
                    record["summary"]["delivered"],
                    record["summary"]["promotions"],
                    flush=True,
                )
    if source_identity() != source_hash or protocol_path.read_bytes() != protocol_bytes:
        raise ValueError("Benchmark source/protocol changed during execution")
    summary = {
        "source_hash": source_hash,
        "protocol_id": protocol["protocol_id"],
        "protocol_hash": hashlib.sha256(protocol_bytes).hexdigest(),
        "results": results,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.protocol, args.output, args.report))


if __name__ == "__main__":
    main()
