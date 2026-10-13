"""Fixed common-pipeline comparisons. Private Store bundles, public aggregate JSON."""

import argparse
import asyncio
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

from preact.cognition import CognitiveAgent, Goal, WorldPlanner
from preact.cognition.deliberation import DeliberationPlanner
from preact.cognition.lifecycle import LearningCoordinator
from preact.cognition.selection import SelectionWorld
from preact.core.models import Policy, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.datasets.programs import LOGIC
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.program import ProgramWorld
from preact.domains.program_learning import ProgramLearningBackend
from preact.domains.queue_learning import QueueLearningBackend
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.local import LocalHeuristic, LocalVerifier
from preact.engines.queue_continuous import ContinuousQueueAdaptation
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from scripts.benchmark_queue_recovery import evaluation_cases
from scripts.benchmark_queue_temporal import execute_sequence

ROOT = Path(__file__).resolve().parents[1]


def agent(world, store, artifacts, coordinator, episode, *, observer=None):
    engines = (
        [LocalHeuristic(world), LocalVerifier(world)]
        if world.task.domain == "software"
        else [
            InformationBoundVerifier(world.task),
            InformationBoundVerifier(world.task, future=True),
        ]
    )
    metric = "goal_complete" if world.task.domain == "software" else "delivered"
    target = 1 if world.task.domain == "software" else world.target
    return CognitiveAgent(
        store,
        artifacts,
        Registry(engines),
        WorldPlanner(world),
        [Goal(name="Task", metric=metric, target=target)],
        Policy(
            search=False,
            width=2 if metric == "goal_complete" else 3,
            max_nodes=3,
            max_calls=256,
            max_seconds=120,
            calibration=False,
        ),
        learning_coordinator=coordinator,
        episode_id=episode,
        round_observer=observer,
    )


def summarize(rows, store, runs):
    events = [e for run in runs for e in store.read_events(run)]
    traces = [e["data"] for e in events if e["kind"] == "deliberation"]
    predictions = [
        r["prediction"] for t in traces for f in t["simulation"]["candidates"] for r in f["records"]
    ]
    node_predictions = [
        p for e in events if e["kind"] == "prediction" for p in [e["data"].get("prediction", {})]
    ]
    return {
        "receipts": len(rows),
        "successes": sum(r.after.state.payload.get("goal_complete", False) for r in rows),
        "unsafe": sum(r.after.unsafe for r in rows),
        "task_actions": sum(r.action.kind != "probe_service" for r in rows),
        "probe_actions": sum(r.action.kind == "probe_service" for r in rows),
        "delivered": rows[-1].after.state.payload.get("delivered") if rows else None,
        "reward": sum(r.after.metrics.get("reward", 0) for r in rows),
        "holding_loss": sum(
            r.after.state.payload.get("queue", 0) * r.before.payload.get("holding_cost", 0)
            for r in rows
        ),
        "local_prediction_calls": sum(t["local_calls"] for t in traces),
        "runtime_prediction_events": len(node_predictions),
        "learned_decisions": sum(
            bool(t["simulation"].get("model_version"))
            or (
                isinstance(t["simulation"].get("health"), dict)
                and t["simulation"]["health"].get("status") == "available"
            )
            for t in traces
        ),
        "prediction_records": len(predictions),
        "abstain": sum(
            not any(e["kind"] == "outcome" for e in store.read_events(run)) for run in runs
        ),
        "external_api_cost_usd": 0.0,
        "local_compute_dollar_cost": None,
    }


async def program_case(folder, strategy, seed, protocol, *, order="native"):
    if order not in {"native", "reverse"}:
        raise ValueError("Undeclared public proposal order")

    class OrderedProgramWorld(ProgramWorld):
        async def propose(self, state, width):
            proposals = await super().propose(state, width)
            return list(reversed(proposals)) if order == "reverse" else proposals

    folder.mkdir(parents=True, exist_ok=False)
    store, artifacts = (
        Store("sqlite:///" + str(folder / "ledger.db")),
        Artifacts(str(folder / "artifacts")),
    )
    backend = ProgramLearningBackend(
        store,
        artifacts,
        ProgramWorld(LOGIC[0]),
        lambda i: ProgramWorld(LOGIC[0], seed=100 + i, evaluation_seed=seed * 100 + i),
    )
    coordinator = await LearningCoordinator.create(backend)
    preparation = []
    if strategy in {"learned", "no_promotion"}:
        for index in range(protocol["program"]["max_preparation_actions"]):
            world = ProgramWorld(LOGIC[0], seed=seed, evaluation_seed=seed * 1000 + index)
            result = await agent(world, store, artifacts, coordinator, f"preparation:{index}").run(
                world, 1
            )
            preparation.extend(result.run_ids)
            if backend.candidate:
                break
    runs, episodes = [], {}
    task_metadata = []
    if strategy == "learned":
        fresh = ProgramWorld(LOGIC[0], seed=seed, evaluation_seed=seed)
        await coordinator.promote(backend.candidate, fresh)
        # New backend/Agent validates durable state rather than trusting in-memory flags.
        restored = ProgramLearningBackend(
            store,
            artifacts,
            fresh,
            lambda i: ProgramWorld(LOGIC[0], evaluation_seed=seed * 100 + i),
        )
        coordinator = await LearningCoordinator.restore(restored, coordinator.journal_run, fresh)
        backend = restored
    for index in range(protocol["program"]["evaluation_tasks"]):
        world = OrderedProgramWorld(LOGIC[0], seed=seed, evaluation_seed=seed * 10000 + index)
        episode = f"task:{index}"
        if strategy == "existing":
            runner = agent(world, store, artifacts, None, episode)
        elif strategy == "fixed_prior":
            # Same candidates, pure selector and Runtime; scalar prior is a soft value.
            checkpoint = backend.state()
            source = backend.binding(world, checkpoint).planner.source
            old = source.forecast

            async def fixed(state, actions, rs):
                result = await old(state, actions, rs)
                for f in result["candidates"]:
                    f["value"], f["basis"] = 0.5, "fixed_goal_prior"
                return result

            source.forecast = fixed
            planner = DeliberationPlanner(
                world, source, backend.binding(world, checkpoint).planner.value
            )
            runner = agent(world, store, artifacts, None, episode)
            runner.planner, runner.candidate_selector = planner, planner
            result = await runner.run(SelectionWorld(world, planner), 1)
            episodes[episode] = result.run_ids
            runs.extend(result.run_ids)
            task_metadata.append(
                {
                    "episode": episode,
                    "initial": next(
                        e["data"]["state"]
                        for e in store.read_events(result.run_ids[0])
                        if e["kind"] == "observed"
                    ),
                }
            )
            continue
        else:
            runner = agent(world, store, artifacts, coordinator, episode)
        result = await runner.run(world, 1)
        episodes[episode] = result.run_ids
        runs.extend(result.run_ids)
        task_metadata.append({"episode": episode, "success": result.success})
    snapshot = await TransitionDataset(store, episodes).snapshot()
    metadata = {
        "domain": "program",
        "order": order,
        "strategy": strategy,
        "seed": seed,
        "episodes": episodes,
        "preparation_runs": preparation,
        "journal": coordinator.journal_run,
        "backend_state": backend.state(),
        "tasks": task_metadata,
    }
    (folder / "case.json").write_text(json.dumps(metadata, indent=2))
    result = summarize(snapshot.transitions, store, runs)
    result.update(
        goal_rate=result["successes"] / protocol["program"]["evaluation_tasks"],
        preparation_actions=len(preparation),
        evaluation_actions=backend.evaluation["evaluation_actions"] if backend.evaluation else 0,
        evaluation_scores=backend.evaluation["scores"] if backend.evaluation else None,
        promotions=int(backend.active_promotion is not None),
        action_codes=[
            identity(r.action.payload["files"]["program.py"]) for r in snapshot.transitions
        ],
    )
    return result


async def queue_case(folder, condition, strategy, seed, protocol):
    folder.mkdir(parents=True, exist_ok=False)
    store, artifacts = (
        Store("sqlite:///" + str(folder / "ledger.db")),
        Artifacts(str(folder / "artifacts")),
    )
    spec = protocol["queue"]["conditions"][condition]
    ticks = protocol["queue"]["ticks"]
    world = InformationQueueWorld(seed=seed, ticks=ticks + 3, target=999, noise=0)
    # Owned benchmark executor specifies the environment; predictors never receive this schedule.
    world._service = [
        spec["before"] if i < spec["shift"] else spec["after"] for i in range(ticks + 3)
    ]
    teacher = InformationQueueWorld(seed=701, ticks=32, target=999, noise=0)
    teacher._service = [spec["before"]] * 32
    training = {
        "teacher": await execute_sequence(
            teacher,
            ["probe"] * 16,
            store,
            artifacts,
            {"search": False, "calibration": False, "width": 1, "max_nodes": 1, "max_calls": 64},
        )
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, "source")
    initial = await world.observe()
    await lifecycle.start(
        model, training, world_episode_id="source", task=world.task, anchor=initial
    )

    async def factory(recovery, candidate, generation):
        declared = json.loads((ROOT / "benchmarks/queue-recovery-v1.json").read_text())
        cases, _ = await evaluation_cases(recovery, candidate, declared, high=spec["after"] == 3)
        return [
            case.model_copy(
                update={
                    "branches": [
                        b.model_copy(
                            update={"episode_id": f"common-evaluation:{generation}:{b.episode_id}"}
                        )
                        for b in case.branches
                    ]
                }
            )
            for case in cases
        ]

    backend = QueueLearningBackend(lifecycle, factory, recover=strategy == "learned")
    coordinator = await LearningCoordinator.create(backend)
    promotions = []

    async def operator(run_id, belief):
        if strategy == "learned" and backend.phase == "await_promotion":
            health = await backend.pending_guard.health(await world.observe(), backend.monitor_runs)
            if health.status == "available":
                promotions.append(await coordinator.promote(backend.candidate, world))

    runner = agent(world, store, artifacts, coordinator, "source", observer=operator)
    result = await runner.run(world, ticks)
    episodes = {"source": result.run_ids}
    snapshot = await TransitionDataset(store, episodes).snapshot()
    metadata = {
        "domain": "queue",
        "condition": condition,
        "strategy": strategy,
        "seed": seed,
        "episodes": episodes,
        "journal": coordinator.journal_run,
        "backend_state": backend.state(),
        "source_initial": initial.model_dump(),
        "source_task": world.task.model_dump(),
        "training": training,
        "promotions": promotions,
    }
    (folder / "case.json").write_text(json.dumps(metadata, indent=2))
    summary = summarize(snapshot.transitions, store, result.run_ids)
    summary.update(
        promotions=len(promotions),
        goal_rate=float(result.success),
        actions=[
            r.action.kind + ":" + str(r.action.payload.get("amount", "probe"))
            for r in snapshot.transitions
        ],
    )
    return summary


async def run(protocol_path, output, report):
    protocol = json.loads(protocol_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    for seed in protocol["seeds"]:
        configurations = [
            ("program", c, s)
            for c in protocol["program"].get("orders", ["native"])
            for s in protocol["program"]["strategies"]
        ]
        configurations += [
            ("queue", c, s)
            for c in protocol["queue"]["conditions"]
            for s in protocol["queue"]["strategies"]
        ]
        for domain, condition, strategy in configurations:
            folder = output / f"{domain}-{condition}-{strategy}-{seed}"
            cpu, wall = time.process_time(), time.perf_counter()
            summary = (
                await program_case(folder, strategy, seed, protocol, order=condition)
                if domain == "program"
                else await queue_case(folder, condition, strategy, seed, protocol)
            )
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak = peak if sys.platform == "darwin" else peak * 1024
            summary.update(
                cpu_seconds=time.process_time() - cpu,
                wall_seconds=time.perf_counter() - wall,
                peak_process_rss_bytes=peak,
            )
            cases.append({"case": folder.name, **summary})
            print(folder.name, summary["receipts"], summary["unsafe"], flush=True)
    result = {
        "protocol_hash": identity(protocol),
        "source_hash": identity(
            {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ROOT / "src/preact").rglob("*.py"))
            }
        ),
        "cases": cases,
        "limits": [
            "Closed-vocabulary Program outcomes, no unseen-code/task generalization",
            "Queue independent evaluation uses declared operator conditions, not live oracle",
            "Finite deterministic owned fixtures, not broad cognitive superiority",
            "Preparation/evaluation cost is separate from task-only results",
        ],
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2))
    (output / "results.json").write_text(json.dumps(result, indent=2))
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.protocol, args.output, args.report))


if __name__ == "__main__":
    main()
