"""Receipt-grounded paired deliberation evaluation; private bundles stay local."""

import argparse
import asyncio
import hashlib
import json
import random
import resource
import time
from pathlib import Path

from preact.cognition import CognitiveAgent, Goal, WorldPlanner
from preact.cognition.deliberation import DeliberationPlanner
from preact.cognition.information import InformationQueuePlanner
from preact.cognition.queue_model import MonitoredQueueSource
from preact.core.models import Continuation, Policy, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_deliberation import QueueGoalAdapter, QueuePredictionAdapter
from preact.domains.queue_multi_selection import QueueMultiSelectionWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.domains.software import SoftwareWorld
from preact.domains.software_deliberation import (
    SoftwareGoalAdapter,
    SoftwarePredictionAdapter,
    WorkflowSoftwareWorld,
)
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.local import LocalHeuristic, LocalVerifier
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from scripts.benchmark_queue_temporal import execute_sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [*sorted((ROOT / "src/preact").rglob("*.py")), Path(__file__)]


def source_identity():
    return identity(
        {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES}
    )


def actual_rows(store, runs):
    rows = []
    for run in runs:
        events = store.read_events(run)
        nodes = {
            e["data"]["node"]["id"]: e["data"]["node"]
            for e in events
            if e["kind"] == "node_updated"
        }
        for event in events:
            if event["kind"] == "outcome":
                outcome = event["data"]
                node = nodes[outcome["node_id"]]
                rows.append(
                    {
                        "run_id": run,
                        "action": node["action"],
                        "before": node["state"],
                        "observation": outcome["observation"],
                    }
                )
    return rows


def metrics(store, result, world, goals, *, domain):
    rows = actual_rows(store, result.run_ids)
    deliberations = [
        e["data"]
        for r in result.run_ids
        for e in store.read_events(r)
        if e["kind"] == "deliberation"
    ]
    qualified = [
        e["data"]["batch"]
        for r in result.run_ids
        for e in store.read_events(r)
        if e["kind"] == "candidate_qualification"
    ]
    runtime_predictions = [
        e["data"]
        for run in result.run_ids
        for e in store.read_events(run)
        if e["kind"] == "prediction" and not e["data"]["cached"]
    ]
    errors = []
    labelled = 0
    for row in rows:
        trace = next((r for r in deliberations if r["run_id"] == row["run_id"]), None)
        if trace and trace["status"] == "estimated":
            f = next(
                c
                for c in trace["simulation"]["candidates"]
                if c["fingerprint"]
                == identity(
                    {
                        "kind": row["action"]["kind"],
                        "payload": row["action"]["payload"],
                        "duration": row["action"]["duration"],
                        "state": row["action"]["state_id"],
                    }
                )
            )
            if domain == "software":
                prediction = next(
                    r["prediction"] for r in f["records"] if r["prediction"]["horizon"] == 1
                )
                estimate = prediction["successor"]["payload"][goals[0].metric]
                error = abs(estimate - row["observation"]["state"]["payload"][goals[0].metric])
            else:
                p = f["records"][0]["prediction"]
                after = row["observation"]["state"]["payload"]
                error = sum(abs(p["vectors"][k][0] - after[k]) for k in ("queue", "delivered")) / 2
            errors.append(error)
            labelled += 1
    reward = (
        world.reward
        if domain == "queue"
        else goals[0].progress(result.final_state) - 0.05 * len(rows)
    )
    return {
        "goal_achieved": goals[0].progress(result.final_state) >= goals[0].target,
        "world_complete": world.complete(result.final_state),
        "actions": [r["action"] for r in rows],
        "actual_steps": len(rows),
        "unsafe": sum(r["observation"]["unsafe"] for r in rows),
        "abstain": sum(r["steps"] == 0 for r in result.rounds),
        "gate_rejections": sum(
            c["status"] == "rejected" for b in qualified for c in b["candidates"]
        ),
        "unresolved": sum(
            c["status"] in ("unresolved", "unexpanded") for b in qualified for c in b["candidates"]
        ),
        "reward": reward,
        "goal_capped_utility": reward
        - max(0, goals[0].progress(result.final_state) - goals[0].target),
        "goal_progress": goals[0].progress(result.final_state),
        "holding_cost": sum(r["observation"]["metrics"].get("holding_loss", 0) for r in rows),
        "mae_first_executed_step": sum(errors) / len(errors) if errors else None,
        "coverage_decision_rounds": labelled / len(result.rounds),
        "unknown_rounds": sum(r["status"] == "unknown" for r in deliberations),
        "local_prediction_calls": sum(r["agent_calls"] for r in result.rounds),
        "runtime_engine_calls": sum(r["calls"] for r in result.rounds),
        "verifier_calls": sum(
            p["prediction"]["evidence"] != "inference" for p in runtime_predictions
        ),
        "runtime_inference_calls": sum(
            p["prediction"]["evidence"] == "inference" for p in runtime_predictions
        ),
        "external_api_calls": 0,
        "api_cost_usd": sum(r["cost_usd"] for r in result.rounds),
        "compute_dollar_cost": None,
    }


async def run_case(protocol, domain, condition, strategy, seed, folder):
    folder.mkdir(parents=True, exist_ok=False)
    store = Store("sqlite:///" + str(folder / "ledger.db"))
    artifacts = Artifacts(str(folder / "artifacts"))
    cpu, wall = time.process_time(), time.perf_counter()
    metadata = {
        "domain": domain,
        "condition": condition,
        "strategy": strategy,
        "seed": seed,
        "source_hash": source_identity(),
        "training": {},
        "prefix": [],
    }
    if domain == "software":
        world = (
            SoftwareWorld(seed) if condition == "native_checkout" else WorkflowSoftwareWorld(seed)
        )
        goals = [
            Goal(
                name="Software objective",
                metric="goal_progress" if condition == "native_checkout" else "workflow_progress",
                target=1,
            )
        ]
        registry = Registry([LocalHeuristic(world), LocalVerifier(world)])
        if strategy == "core_search":
            planner = WorldPlanner(world)
            wrapper = world
            policy = Policy(
                search=True, max_depth=2, width=2, max_nodes=12, max_calls=64, calibration=False
            )
            selector = None
        else:
            source = SoftwarePredictionAdapter(
                world, store, horizon=1 if strategy == "short" else 2
            )
            planner = DeliberationPlanner(world, source, SoftwareGoalAdapter())
            wrapper = QueueMultiSelectionWorld(world, planner)
            policy = Policy(search=False, width=2, max_nodes=2, max_calls=64, calibration=False)
            selector = planner
        rounds = 4
    else:
        spec = protocol["queue_conditions"][condition]
        collect = {
            "search": False,
            "width": 1,
            "max_nodes": 1,
            "max_calls": 64,
            "calibration": False,
        }
        teacher = InformationQueueWorld(
            seed=seed + 100, ticks=32, target=protocol["queue_goal"], noise=0
        )
        teacher._service = (spec.get("training_pattern") or [spec["training_service"]]) * 32
        teacher._service = teacher._service[:32]
        training = {
            "teacher": await execute_sequence(teacher, ["probe"] * 16, store, artifacts, collect)
        }
        model = await TabularDynamicsTrainer().fit(
            TransitionDataset(store, training), QueueServiceAdapter()
        )
        model_artifact = model.save(artifacts)
        world = InformationQueueWorld(
            seed=seed, ticks=32, target=protocol["queue_goal"], noise=spec.get("noise", 0)
        )
        if spec.get("noise", 0):
            rng = random.Random(seed)
            world._service = (spec.get("warmup_pattern", [3]) * 8)[:8] + [
                1 if rng.random() < spec["noise"] else 3 for _ in range(24)
            ]
        if not spec.get("noise", 0):
            world._service = [spec["actual_service"]] * 32
        if condition == "shift_after_warmup":
            world._service = [3] * 8 + [1] * 24
        anchor = await world.observe()
        prefix = await execute_sequence(
            world, [0 if condition == "partial" else "probe"] * 8, store, artifacts, collect
        )
        guard = QueueTemporalGuard(
            store, world.task, model, training, episode_id="decision", initial=anchor
        )
        monitored = MonitoredQueueSource(store, guard, training)
        metadata.update(
            training=training,
            prefix=prefix,
            anchor=anchor.model_dump(),
            model_artifact=model_artifact,
            training_probe_cost=-teacher.reward,
            queue_spec=spec,
            task=world.task.model_dump(),
        )
        goals = [Goal(name="Deliver", metric="delivered", target=protocol["queue_goal"])]
        registry = Registry(
            [
                InformationBoundVerifier(world.task),
                InformationBoundVerifier(world.task, future=True),
            ]
        )
        if strategy == "existing":
            planner = InformationQueuePlanner("no_probe")
            wrapper = world
            selector = None
        else:
            source = QueuePredictionAdapter(
                monitored,
                prior=0.5 if strategy == "fixed_prior" else None,
                continuation=Continuation.ACTION_SEQUENCE
                if condition == "unsupported"
                else Continuation.ENVIRONMENT_ONLY,
            )
            planner = DeliberationPlanner(
                world,
                source,
                QueueGoalAdapter(
                    projection=1 if strategy == "short" else 3,
                    spread_cost=1.0 if strategy == "conservative" else 0.0,
                ),
                prefix_runs=prefix,
            )
            wrapper = QueueMultiSelectionWorld(world, planner)
            selector = planner
        policy = Policy(search=False, width=3, max_nodes=3, max_calls=64, calibration=False)
        rounds = protocol["queue_rounds"]
    initial = await world.observe()
    agent = CognitiveAgent(
        store, artifacts, registry, planner, goals, policy, candidate_selector=selector
    )
    agent.memory.run_ids = list(metadata["prefix"])
    result = await agent.run(wrapper, rounds)
    summary = metrics(store, result, world, goals, domain=domain)
    summary.update(
        preparation_actions=len(metadata["prefix"]),
        training_actions=sum(len(runs) for runs in metadata["training"].values()),
        training_probe_cost=metadata.get("training_probe_cost", 0),
    )
    metadata.update(
        initial=initial.model_dump(),
        task=world.task.model_dump(),
        policy=policy.model_dump(),
        goals=[g.model_dump() for g in goals],
        runs=result.run_ids,
        result=result.model_dump(),
        summary=summary,
        cpu_seconds=time.process_time() - cpu,
        wall_seconds=time.perf_counter() - wall,
        max_rss_platform_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    (folder / "case.json").write_text(json.dumps(metadata, indent=2, allow_nan=False))
    public = {
        k: metadata[k]
        for k in (
            "domain",
            "condition",
            "strategy",
            "seed",
            "summary",
            "cpu_seconds",
            "wall_seconds",
            "max_rss_platform_units",
        )
    }
    public["summary"] = {
        **summary,
        "actions": [
            {
                "name": a["name"],
                "kind": a["kind"],
                "fingerprint": identity(
                    {
                        "kind": a["kind"],
                        "payload": a["payload"],
                        "duration": a["duration"],
                        "state": a["state_id"],
                    }
                ),
                "amount": a["payload"].get("amount"),
            }
            for a in summary["actions"]
        ],
    }
    return public


async def benchmark(protocol_path, output, report_path):
    data = protocol_path.read_bytes()
    protocol = json.loads(data)
    initial_source = source_identity()
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    for seed in protocol["seeds"]:
        for condition in protocol.get("software_conditions", ["workflow"]):
            for strategy in protocol["software_strategies"]:
                cases.append(
                    await run_case(
                        protocol,
                        "software",
                        condition,
                        strategy,
                        seed,
                        output / f"software-{condition}-{strategy}-{seed}",
                    )
                )
        for condition, spec in protocol["queue_conditions"].items():
            for strategy in spec.get("strategies", protocol["queue_strategies"]):
                cases.append(
                    await run_case(
                        protocol,
                        "queue",
                        condition,
                        strategy,
                        seed,
                        output / f"queue-{condition}-{strategy}-{seed}",
                    )
                )
    if initial_source != source_identity() or data != protocol_path.read_bytes():
        raise ValueError("Source/protocol changed during evaluation")
    report = {
        "protocol_id": protocol["protocol_id"],
        "protocol_hash": hashlib.sha256(data).hexdigest(),
        "source_hash": initial_source,
        "cases": cases,
        "limits": [
            "Owned deterministic Software workflow; known rules, not causal learning",
            "Queue short scoring projects H3 at tick1; no fabricated H1 request",
            "All Queue policies share real 16 training + 8 warmup receipt costs",
            "H3 environment-only is not the subsequent closed-loop trajectory",
            "Only the actually executed first step receives an Outcome label",
            "Single process cumulative RSS; timings are observational, not speed benchmarks",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"cases": len(cases), "unsafe": sum(c["summary"]["unsafe"] for c in cases)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol, args.output, args.report))
