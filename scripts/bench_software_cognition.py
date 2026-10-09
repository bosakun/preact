"""Reproduce bounded Software integration comparisons and audit original receipts."""

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from preact.cognition import CognitiveAgent, EpisodicMemory, Goal, WorldPlanner
from preact.core.models import Action, Policy, identity
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.datasets.programs import LOGIC
from preact.domains.program import ProgramWorld
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic, LocalVerifier


def canonical(value):
    """Drop only volatile execution identities/time after authority checks."""
    if isinstance(value, dict):
        return {
            k: canonical(v)
            for k, v in value.items()
            if k not in {"id", "receipt", "timestamp", "prediction_id", "latency_ms"}
        }
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


async def audit_ledger(store, run_ids):
    records, events = [], []
    memory = EpisodicMemory(store)
    for run_id in run_ids:
        items = store.read_events(run_id)
        events.extend(items)
        records.extend(await memory.read(run_id))
        nodes = {
            e["data"]["node"]["id"]: e["data"]["node"]
            for e in items
            if e["kind"] in {"node", "node_updated"}
        }
        policy = Policy.model_validate(store.get_run(run_id)["config"]["policy"]).model_dump()
        for e in items:
            data = e["data"]
            if e["kind"] == "gate_preview":
                evaluation = nodes[data["node_id"]]["evaluation"]
                assert data["gate"]["evidence_hash"] == identity(evaluation)
                assert data["gate"]["policy_hash"] == identity(
                    {"policy": policy, "stakes": evaluation["stakes"]}
                )
            if e["kind"] != "outcome":
                continue
            node = nodes[data["node_id"]]
            action = Action.model_validate(node["action"])
            auth = [
                a
                for a in items
                if a["kind"] == "authorization"
                and a["data"]["action_hash"] == action.fingerprint
                and a["seq"] < e["seq"]
            ][-1]
            intent = next(
                i
                for i in items
                if i["kind"] == "execution_intent"
                and i["data"]["receipt"] == data["observation"]["receipt"]
            )
            assert auth["seq"] < intent["seq"] < e["seq"]
            assert auth["data"]["evidence_hash"] == identity(node["evaluation"])
            assert auth["data"]["policy_hash"] == identity(
                {"policy": policy, "stakes": node["evaluation"]["stakes"]}
            )
            assert auth["data"]["state_id"] == node["state"]["id"]
            assert auth["data"]["decision"] == "execute"
            assert not any(i["data"].get("direct") for i in items if i["kind"] == "decision")
    labels = []
    predictions = {
        e["data"]["prediction"]["id"]: e["data"] for e in events if e["kind"] == "prediction"
    }
    actual_nodes = {e["data"]["node_id"] for e in events if e["kind"] == "outcome"}
    for label in store.error_rows():
        source = predictions[label["prediction_id"]]
        assert source["node_id"] in actual_nodes and source["prediction"]["horizon"] == 1
        labels.append(canonical(label))
    return records, events, labels


async def episode(task, seed, condition, directory, protocol):
    directory.mkdir(parents=True, exist_ok=False)
    if task == "checkout-repair":
        world = SoftwareWorld(seed)
    else:
        spec = next(s for s in LOGIC if s.name == task)
        world = ProgramWorld(
            spec, seed=seed, evaluation_seed=seed + protocol["evaluation_seed_offset"]
        )
    counts = Counter()
    observe, propose = world.observe, world.propose

    async def measured_observe():
        counts["observations"] += 1
        return await observe()

    async def measured_propose(*args):
        counts["proposals"] += 1
        return await propose(*args)

    world.observe, world.propose = measured_observe, measured_propose
    store = Store("sqlite:///" + str(directory / "ledger.sqlite"))
    artifacts = Artifacts(str(directory / "artifacts"))
    registry = Registry([LocalHeuristic(world), LocalVerifier(world)])
    policy = Policy.model_validate(protocol["policy"])
    agent = None
    runtime = None
    if condition != "runtime":
        agent = CognitiveAgent(
            store,
            artifacts,
            registry,
            WorldPlanner(world),
            [Goal(name="Repair", metric="goal_progress", target=1)],
            policy,
            use_memory=condition != "cognitive_no_memory",
            episode_budget=True,
        )
        infer, retrieve = agent.planner.infer, agent.memory.retrieve

        def measured_infer(*args):
            counts["inferences"] += 1
            return infer(*args)

        async def measured_retrieve(*args):
            counts["retrievals"] += 1
            return await retrieve(*args)

        agent.planner.infer, agent.memory.retrieve = measured_infer, measured_retrieve
    else:
        runtime = Runtime(store, artifacts, registry, policy)
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        if agent is None:
            outcome = await runtime.run(world)
            run_ids, rounds = [runtime.run_id], [outcome]
            final = outcome["final_state"]
            success, unsafe = outcome["success"], outcome["unsafe"]
        else:
            result = await agent.run(world, protocol["max_rounds"])
            run_ids, rounds = result.run_ids, result.rounds
            final = result.final_state.model_dump()
            success, unsafe = result.success, result.unsafe
        wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
        measured_counts = dict(counts)
        records, events, labels = await audit_ledger(store, run_ids)
        assert sum(r["calls"] + r["agent_calls"] for r in rounds) <= policy.max_calls
        assert len(records) <= world.task.max_steps
        # Random IDs/timestamps are removed; action state_id and all substantive
        # observed payload/provenance/checks/metrics remain in this comparison.
        semantic = {
            "actions": [canonical(r.action.model_dump()) for r in records],
            "observations": [canonical(r.observation.model_dump()) for r in records],
            "final_state": canonical(final),
            "success": success,
            "unsafe": unsafe,
            "learning": labels,
        }
        if agent:
            for run in run_ids:
                assert sum(e["kind"] == "outcome" for e in store.read_events(run)) <= 1
        with (directory / "semantic.json").open("x") as target:
            json.dump(semantic, target, indent=2)
        metrics = {
            "success": success,
            "unsafe": unsafe,
            "executed_actions": len(records),
            "action_fingerprints": [r.action.fingerprint for r in records],
            "abstains": sum(
                e["kind"] == "decision" and e["data"].get("decision") == "abstain" for e in events
            ),
            "engine_calls": sum(r["calls"] for r in rounds),
            "verifications": sum(
                e["kind"] == "prediction" and e["data"]["prediction"]["engine_id"] == "local-tests"
                for e in events
            ),
            "escalations": sum(e["kind"] == "escalation" for e in events),
            "cost_usd": sum(r["cost_usd"] for r in rounds),
            "memory_retry_signals": sum(
                bool(e["data"]["belief"]["inferred"])
                for e in events
                if e["kind"] == "cognitive_update"
            ),
            "cpu_seconds": cpu,
            "wall_seconds": wall,
            "counts": measured_counts,
        }
        return {"metrics": metrics, "semantic_sha256": identity(semantic)}
    finally:
        store.db.dispose()


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["condition"]].append(row["metrics"])
    return {
        name: {
            key: statistics.mean(m[key] for m in metrics)
            for key in (
                "success",
                "unsafe",
                "executed_actions",
                "abstains",
                "engine_calls",
                "verifications",
                "cost_usd",
                "memory_retry_signals",
                "cpu_seconds",
                "wall_seconds",
            )
        }
        for name, metrics in groups.items()
    }


def source_hashes():
    paths = sorted(Path("src/preact").rglob("*.py")) + [
        Path("scripts/bench_software_cognition.py"),
        Path("scripts/audit_software_cognition.py"),
    ]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


async def benchmark(protocol_path, output, report_path):
    protocol = json.loads(protocol_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    sources, rows = source_hashes(), []
    for task in protocol["tasks"]:
        for seed in protocol["seeds"]:
            for repeat in range(protocol["repeats"]):
                conditions = protocol["conditions"]
                rotation = seed % len(conditions)
                order = conditions[rotation:] + conditions[:rotation]
                if repeat % 2:
                    order = list(reversed(order))
                for condition in order:
                    directory = output / f"{task}-{seed}-{condition}-{repeat}"
                    measured = await episode(task, seed, condition, directory, protocol)
                    rows.append(
                        {
                            "task": task,
                            "seed": seed,
                            "condition": condition,
                            "repeat": repeat,
                            **measured,
                        }
                    )
    assert sources == source_hashes(), "Source changed during measurement"
    pairs = defaultdict(list)
    for row in rows:
        pairs[row["task"], row["seed"]].append(row["semantic_sha256"])
    equal = all(len(set(digests)) == 1 for digests in pairs.values())
    report = {
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": sources,
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "episodes": rows,
        "aggregate": aggregate(rows),
        "semantic_pairs_equal": equal,
        "authority_audit": "Original Gate policy/evidence hashes, ordered authorization/intent/outcome, complete receipt, input State/Action and horizon-1 learning checked in every episode",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x") as target:
        json.dump(report, target, indent=2)
        target.write("\n")
    print(
        json.dumps(
            {
                "episodes": len(rows),
                "semantic_pairs_equal": equal,
                "aggregate": report["aggregate"],
            },
            indent=2,
        )
    )
    if not equal:
        raise RuntimeError("Paired Software semantics differ; inspect private semantic traces")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol", type=Path, default=Path("benchmarks/software-cognition-v1.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol, args.output, args.report))


if __name__ == "__main__":
    main()
