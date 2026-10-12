"""Reproducible Low→High→Low recovery inside one receipt-backed Queue World episode."""

import argparse
import asyncio
import json
import resource
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.queue_multi import QueueMultiPlanner
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_multi_selection import QueueMultiSelectionWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_continuous import ContinuousQueueAdaptation
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import CandidateEvaluation
from scripts.benchmark_queue_recovery import evaluation_cases, read_record
from scripts.benchmark_queue_recovery import world as recovery_world
from scripts.benchmark_queue_temporal import execute_sequence

ROOT = Path(__file__).resolve().parents[1]
COLLECT_POLICY = {
    "search": False,
    "calibration": False,
    "width": 1,
    "max_nodes": 1,
    "max_calls": 128,
}


def _protocol(path: Path) -> dict:
    return json.loads(path.read_text())


async def _decision(world, lifecycle, monitor_runs, store, artifacts, label):
    source = lifecycle.selection_source()
    planner = QueueMultiPlanner(source, prefix_runs=monitor_runs, mode="learned")
    wrapper = QueueMultiSelectionWorld(world, planner)
    registry = Registry(
        [InformationBoundVerifier(world.task), InformationBoundVerifier(world.task, future=True)]
    )
    agent = CognitiveAgent(
        store,
        artifacts,
        registry,
        planner,
        [Goal(name="deliver-long-horizon", metric="delivered", target=world.target)],
        Policy(**json.loads(json.dumps(PROTOCOL["decision"]["policy"]))),
        candidate_selector=planner,
    )
    result = await agent.run(wrapper, 1)
    if result.unsafe or result.rounds[0]["steps"] != 1:
        raise ValueError("Model-grounded action failed Runtime execution")
    record = planner.records[-1]
    return {
        "label": label,
        "run_id": result.run_ids[0],
        "model_version": record["health"]["model_version"],
        "health": record["health"]["status"],
        "ranked_amounts": [
            c["amount"]
            for c in sorted(
                record["candidates"],
                key=lambda c: record["ranked_fingerprints"].index(c["fingerprint"]),
            )
        ],
        "first_choice_fingerprint": record["first_choice_fingerprint"],
        "prediction_hash": planner._seal,
        "cpu_seconds": record["cpu_seconds"],
        "wall_seconds": record["wall_seconds"],
        "external_api_calls": record["external_api_calls"],
        "known_local_api_cost_usd": record["local_api_cost_usd"],
    }


async def _probe(world, store, artifacts, count):
    return await execute_sequence(world, ["probe"] * count, store, artifacts, COLLECT_POLICY)


async def _until_invalidated(lifecycle, world, store, artifacts, monitor_runs, maximum):
    start_tick = world.payload["tick"]
    trace = []
    for _ in range(maximum):
        monitor_runs.extend(await _probe(world, store, artifacts, 1))
        state = await world.observe()
        health = await lifecycle.health(state, monitor_runs)
        trace.append(
            {
                "tick": state.payload["tick"],
                "status": health.status,
                "effective_count": health.effective_count,
                "health_version": health.version,
            }
        )
        if health.status == "invalidated":
            return state, trace, state.payload["tick"] - start_tick
    raise ValueError("Predeclared horizon did not produce a model invalidation")


async def _recover_generation(
    protocol, lifecycle, world, store, artifacts, current_runs, direction, generation
):
    cutoff = await world.observe()
    origin = await lifecycle.begin_recovery(cutoff, current_runs)
    # New confirmed interactions only; no synthetic/counterfactual transitions.
    training_count = protocol["recovery"]["effective_training"]
    training_runs = await _probe(world, store, artifacts, training_count)
    current_runs.extend(training_runs)
    training_state = await world.observe()
    candidate = await lifecycle.recovery.prepare_candidate(origin, training_state, current_runs)

    eval_protocol = _protocol(ROOT / "benchmarks/queue-recovery-v1.json")
    high = direction == "low-to-high"
    cases, _details = await evaluation_cases(
        lifecycle.recovery,
        candidate,
        eval_protocol,
        high=high,
        noise=0,
    )
    # Evaluation worlds are newly instantiated independent worlds; give each
    # generated provenance key a generation-specific identity so same textual
    # seed/condition labels cannot masquerade as a reused episode.
    cases = [
        case.model_copy(
            update={
                "branches": [
                    branch.model_copy(
                        update={"episode_id": f"continuous-eval-{generation}:{branch.episode_id}"}
                    )
                    for branch in case.branches
                ]
            }
        )
        for case in cases
    ]
    evaluation = await lifecycle.recovery.evaluate_candidate(
        candidate, cases, datetime.now(timezone.utc).isoformat()
    )
    report = read_record(lifecycle.recovery, evaluation, CandidateEvaluation)
    if not report.passed:
        raise ValueError(
            f"Fixed independent evaluation rejected generation {generation}: {report.reasons}"
        )

    # The source World is paused during independent evaluation. Anchor it freshly,
    # then collect a disjoint, receipt-backed monitoring window in this same episode.
    anchor = await world.observe()
    monitoring_runs = await _probe(
        world, store, artifacts, protocol["recovery"]["monitoring_probes"]
    )
    current = await world.observe()
    active_record = await lifecycle.promote(
        evaluation,
        anchor=anchor,
        state=current,
        monitor_runs=monitoring_runs,
    )
    # Reconstruct the just-activated epoch from durable records and use that
    # restored instance for the next actual decision.
    restored = ContinuousQueueAdaptation(store, artifacts, lifecycle.journal_run)
    restored_observation = await world.observe()
    await restored.restore(restored_observation, monitoring_runs)
    if restored.epoch != lifecycle.epoch:
        raise ValueError("Restarted active epoch differs from the committed promotion")
    epoch = lifecycle.epoch
    if epoch.generation != generation or epoch.promotion_artifact is None:
        raise ValueError("Promotion failed to publish the expected monitoring epoch")
    return (
        {
            "generation": generation,
            "origin_artifact": origin,
            "candidate_artifact": candidate,
            "evaluation_artifact": evaluation,
            "evaluation_scores": report.scores,
            "evaluation_passed": report.passed,
            "promotion_artifact": active_record,
            "model_version": epoch.model_version,
            "dataset_hash": epoch.dataset_hash,
            "monitoring_epoch": epoch.epoch_id,
            "anchor": anchor.model_dump(),
            "training_state": training_state.model_dump(),
            "monitor_state": current.model_dump(),
            "monitor_runs": monitoring_runs,
            "training_run_count": training_count,
            "training_runs": training_runs,
            "recovery_journal": lifecycle.recovery.journal_run,
            "world_tick_before_training": cutoff.payload["tick"],
            "world_tick_after_monitoring": current.payload["tick"],
        },
        monitoring_runs,
        restored,
    )


async def run(protocol_path: Path, output: Path, result_path: Path):
    global PROTOCOL
    PROTOCOL = _protocol(protocol_path)
    output.mkdir(parents=True, exist_ok=False)
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts = Artifacts(str(output / "artifacts"))
    wall, cpu = time.perf_counter(), time.process_time()

    # Model A comes from a separate, explicitly declared pretraining episode.
    pretraining = recovery_world(
        _protocol(ROOT / "benchmarks/queue-recovery-v1.json"),
        701,
        high=PROTOCOL["service_regimes"][0]["service"] == 3,
        noise=0,
    )
    training_runs = await _probe(
        pretraining, store, artifacts, PROTOCOL["model_a_training"]["probes"]
    )
    training = {"model-a-pretraining": training_runs}
    model_a = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )

    world = InformationQueueWorld(
        seed=PROTOCOL["seed"],
        ticks=PROTOCOL["episode_ticks"],
        target=PROTOCOL["target"],
        capacity=PROTOCOL["capacity"],
        noise=0,
        shift_tick=PROTOCOL["episode_ticks"],
        high_first=False,
        submission_cost=PROTOCOL.get("submission_cost", 0.1),
    )
    schedule = [0] * PROTOCOL["episode_ticks"]
    for regime in PROTOCOL["service_regimes"]:
        schedule[regime["start_tick"] : regime["end_tick"]] = [regime["service"]] * (
            regime["end_tick"] - regime["start_tick"]
        )
    world._service = schedule
    episode_id = PROTOCOL["world_episode_id"]
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, episode_id)
    first_anchor = await world.observe()
    initial_epoch = await lifecycle.start(
        model_a,
        training,
        world_episode_id=episode_id,
        task=world.task,
        anchor=first_anchor,
        config=DriftConfig(**PROTOCOL["detection"]),
    )
    epoch_runs = []
    epoch_runs.extend(await _probe(world, store, artifacts, 8))
    state = await world.observe()
    initial_health = await lifecycle.health(state, epoch_runs)
    initial_monitor_state = state.model_dump()
    initial_monitor_runs = list(epoch_runs)
    if initial_health.status != "available":
        raise ValueError(
            "Initial Model A did not become available from its own monitoring receipts"
        )

    decisions = []
    decisions.append(await _decision(world, lifecycle, epoch_runs, store, artifacts, "model-A"))
    epoch_runs.append(decisions[-1]["run_id"])
    await lifecycle.health(await world.observe(), epoch_runs)

    first_cutoff, first_detection, first_delay = await _until_invalidated(
        lifecycle, world, store, artifacts, epoch_runs, 64
    )
    first_direction = (
        "low-to-high"
        if PROTOCOL["service_regimes"][1]["service"] > PROTOCOL["service_regimes"][0]["service"]
        else "high-to-low"
    )
    generation_b, monitor_b, lifecycle = await _recover_generation(
        PROTOCOL, lifecycle, world, store, artifacts, epoch_runs.copy(), first_direction, 1
    )
    # The new epoch owns only its own monitoring receipts; prior recovery runs are
    # training provenance and are never reused as post-promotion health evidence.
    decisions.append(await _decision(world, lifecycle, monitor_b, store, artifacts, "model-B"))
    monitor_b.append(decisions[-1]["run_id"])
    await lifecycle.health(await world.observe(), monitor_b)

    second_cutoff, second_detection, second_delay = await _until_invalidated(
        lifecycle, world, store, artifacts, monitor_b, 64
    )
    second_direction = (
        "low-to-high"
        if PROTOCOL["service_regimes"][2]["service"] > PROTOCOL["service_regimes"][1]["service"]
        else "high-to-low"
    )
    generation_c, monitor_c, lifecycle = await _recover_generation(
        PROTOCOL, lifecycle, world, store, artifacts, monitor_b.copy(), second_direction, 2
    )
    decisions.append(await _decision(world, lifecycle, monitor_c, store, artifacts, "model-C"))
    monitor_c.append(decisions[-1]["run_id"])
    final_state = await world.observe()
    final_health = await lifecycle.health(final_state, monitor_c)

    all_world_runs = (
        epoch_runs
        + generation_b["training_runs"]
        + monitor_b
        + generation_c["training_runs"]
        + monitor_c
    )
    continuous_snapshot = await TransitionDataset(store, {episode_id: all_world_runs}).snapshot()
    continuous_snapshot.verify_identity()
    actual_reward = sum(
        float(row.after.metrics.get("reward", 0.0)) for row in continuous_snapshot.transitions
    )
    holding_loss = sum(
        float(row.after.metrics.get("holding_loss", 0.0)) for row in continuous_snapshot.transitions
    )
    executed_by_run = {row.run_id: row for row in continuous_snapshot.transitions}
    for decision in decisions:
        row = executed_by_run[decision["run_id"]]
        decision["executed_action"] = row.action.model_dump()
        decision["receipt"] = row.receipt
        decision["actual_observation"] = row.after.model_dump()

    # Matched-horizon non-learning control: a separate World, same seed, schedule,
    # task and first N ticks; always submit(1) through the normal Runtime.
    baseline_world = InformationQueueWorld(
        seed=PROTOCOL["seed"],
        ticks=PROTOCOL["episode_ticks"],
        target=PROTOCOL["target"],
        capacity=PROTOCOL["capacity"],
        noise=0,
        shift_tick=PROTOCOL["episode_ticks"],
        high_first=False,
        submission_cost=PROTOCOL.get("submission_cost", 0.1),
    )
    baseline_world._service = list(schedule)
    baseline_timing = time.perf_counter()
    baseline_runs = await execute_sequence(
        baseline_world,
        [1] * final_state.payload["tick"],
        store,
        artifacts,
        COLLECT_POLICY,
    )
    baseline_snapshot = await TransitionDataset(
        store, {"fixed-submit-one-baseline": baseline_runs}
    ).snapshot()
    baseline_observations = [row.after for row in baseline_snapshot.transitions]
    fixed_baseline = {
        "policy": "always-submit-1/v1",
        "seed": PROTOCOL["seed"],
        "matched_ticks": len(baseline_observations),
        "run_count": len(baseline_runs),
        "transition_dataset_hash": baseline_snapshot.dataset_hash,
        "delivered": baseline_observations[-1].state.payload["delivered"],
        "reward": sum(float(row.metrics.get("reward", 0.0)) for row in baseline_observations),
        "holding_loss": sum(
            float(row.metrics.get("holding_loss", 0.0)) for row in baseline_observations
        ),
        "unsafe_executions": sum(row.unsafe for row in baseline_observations),
        "cpu_seconds": None,
        "wall_seconds": time.perf_counter() - baseline_timing,
        "world_episode_id": "fixed-submit-one-baseline",
    }

    elapsed_wall, elapsed_cpu = time.perf_counter() - wall, time.process_time() - cpu
    result = {
        "schema_version": "1",
        "protocol_id": PROTOCOL["protocol_id"],
        "protocol_sha256": __import__("hashlib").sha256(protocol_path.read_bytes()).hexdigest(),
        "seed": PROTOCOL["seed"],
        "status": "completed",
        "continuous_journal": lifecycle.journal_run,
        "world_episode_id": episode_id,
        "single_world_instance": True,
        "world_start_state": first_anchor.model_dump(),
        "world_final_state": final_state.model_dump(),
        "world_was_reset": False,
        "initial_epoch": initial_epoch,
        "initial_model": {"version": model_a.version, "dataset_hash": model_a.dataset_hash},
        "initial_health": asdict(initial_health),
        "initial_monitor_state": initial_monitor_state,
        "initial_monitor_runs": initial_monitor_runs,
        "first_invalidation": {
            "state": first_cutoff.model_dump(),
            "trace": first_detection,
            "ticks_from_wait_start": first_delay,
        },
        "generation_b": generation_b,
        "second_invalidation": {
            "state": second_cutoff.model_dump(),
            "trace": second_detection,
            "ticks_from_wait_start": second_delay,
        },
        "generation_c": generation_c,
        "decisions": decisions,
        "final_health": asdict(final_health),
        "world_run_ids": all_world_runs,
        "transition_dataset_hash": continuous_snapshot.dataset_hash,
        "actual_reward": actual_reward,
        "holding_loss": holding_loss,
        "unsafe_executions": sum(row.after.unsafe for row in continuous_snapshot.transitions),
        "fixed_baseline": fixed_baseline,
        "cost": {
            "cpu_seconds": elapsed_cpu,
            "wall_seconds": elapsed_wall,
            "max_rss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "known_external_api_calls": 0,
            "known_external_api_cost_usd": 0,
            "compute_dollar_cost": None,
        },
        "limits": PROTOCOL["predeclared_limits"],
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    result_path.write_text(encoded)
    (output / "continuous-result.json").write_text(encoded)
    store.db.dispose()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol", type=Path, default=ROOT / "benchmarks/queue-continuous-v1.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--result", type=Path, default=ROOT / "reports/queue-continuous-v1-results.json"
    )
    args = parser.parse_args()
    result = asyncio.run(run(args.protocol, args.output, args.result))
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "world_episode_id",
                    "first_invalidation",
                    "generation_b",
                    "second_invalidation",
                    "generation_c",
                    "decisions",
                    "cost",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
