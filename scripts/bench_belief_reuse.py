"""Paired CPU queue/micro measurements; raw ledgers stay in a new private directory."""

import argparse
import asyncio
import hashlib
import json
import platform
import re
import statistics
import time
from collections import Counter, defaultdict
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import event

from preact.cognition.belief import BeliefEstimator
from preact.cognition.loop import CognitiveAgent, _CognitiveWorld
from preact.cognition.memory import EpisodicMemory
from preact.cognition.models import BeliefReusePolicy, Goal
from preact.cognition.queue import QueuePlanner
from preact.core.models import Action, ClaimInstance, Policy, Prediction, identity
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import CognitiveQueueWorld
from preact.engines.cognitive_queue import QueueBoundVerifier, QueueForecast
from tests.fixtures.memory_ledger import append_execution

PHASE = ContextVar("belief_benchmark_phase", default="other")


class Measurements:
    def __init__(self):
        self.counts = Counter()
        self.seconds = Counter()
        self.phases = defaultdict(Counter)

    def count(self, name):
        self.counts[name] += 1
        self.phases[PHASE.get()][name] += 1

    def timed(self, name, wall, cpu):
        self.seconds[name + "_wall"] += time.perf_counter() - wall
        self.seconds[name + "_cpu"] += time.process_time() - cpu

    def dump(self):
        return {
            "counts": dict(self.counts),
            "seconds": dict(self.seconds),
            "phases": {k: dict(v) for k, v in self.phases.items()},
        }


class MeasuredPlanner(QueuePlanner):
    def __init__(self, stats, **kwargs):
        super().__init__(**kwargs)
        self.stats = stats

    def belief_reuse_policy(self):
        # Counters are passive instrumentation, not part of the estimator algorithm.
        return BeliefReusePolicy(
            identity(
                {
                    "algorithm": "uncensored-ema/v1",
                    "adaptation": self.adaptation,
                    "prior": self.prior,
                }
            ),
            True,
        )

    def infer(self, *args):
        self.stats.count("inference")
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            return super().infer(*args)
        finally:
            self.stats.timed("inference", wall, cpu)

    def propose(self, *args):
        self.stats.count("proposal")
        return super().propose(*args)


class MeasuredWorld(CognitiveQueueWorld):
    def __init__(self, stats, **kwargs):
        self.stats = stats
        super().__init__(**kwargs)

    async def observe(self):
        self.stats.count("world_observation")
        return await super().observe()

    async def execute(self, *args):
        token = PHASE.set("executor_internal")
        try:
            return await super().execute(*args)
        finally:
            PHASE.reset(token)


class MeasuredForecast(QueueForecast):
    async def predict(self, request):
        token = PHASE.set("forecast")
        try:
            return await super().predict(request)
        finally:
            PHASE.reset(token)


def instrument_memory(memory, stats):
    original = memory.retrieve

    async def retrieve(*args, **kwargs):
        stats.count("memory_retrieval")
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            return await original(*args, **kwargs)
        finally:
            stats.timed("retrieval", wall, cpu)

    memory.retrieve = retrieve


def semantic_trace(events, run_ids, world, result, store):
    """Normalize variable identities without dropping actions, truth or Gate reasons.

    Gate hashes are validated against full original evaluations before normalization.
    The hash itself varies with random evidence IDs; normalized evidence membership
    and evaluations are compared instead. Every outcome is checked against Store.
    """
    labels = {run: f"round-{i}" for i, run in enumerate(run_ids)}
    nodes, gates = {}, []
    node_index, pred_index = Counter(), Counter()
    for e in events:
        d, run = e["data"], e["run_id"]
        if e["kind"] in {"node", "node_updated"}:
            node = d["node"]
            nodes[node["id"]] = node
            if node["id"] not in labels:
                labels[node["id"]] = f"{labels[run]}/node-{node_index[run]}"
                node_index[run] += 1
            if node["action"]:
                labels[node["action"]["id"]] = labels[node["id"]] + "/action"
        elif e["kind"] == "prediction":
            p = d["prediction"]
            labels[p["id"]] = f"{labels[run]}/prediction-{pred_index[run]}"
            pred_index[run] += 1
            labels[identity(Prediction.model_validate(p).model_dump())] = (
                labels[p["id"]] + "/digest"
            )
            for claim in p["requested_claims"]:
                instance = ClaimInstance.model_validate(claim)
                labels[instance.key] = (
                    labels[d["node_id"]]
                    + "/claim/"
                    + identity({**claim, "action_ids": [labels[a] for a in claim["action_ids"]]})
                )
        elif e["kind"] == "outcome":
            obs = d["observation"]
            execution = store.execution_record(obs["receipt"])
            node = nodes[d["node_id"]]
            assert execution["status"] == "complete" and execution["run_id"] == run
            assert execution["receipt"] == obs and execution["state_id"] == node["state"]["id"]
            assert execution["action_hash"] == Action.model_validate(node["action"]).fingerprint
            labels[obs["receipt"]] = labels[run] + "/receipt"
        elif e["kind"] == "gate_preview":
            gates.append((d["gate"], d["node_id"]))
        elif e["kind"] == "authorization":
            # The authorization binds the same evaluation as its candidate preview.
            matches = [
                n
                for n, v in nodes.items()
                if v["action"]
                and Action.model_validate(v["action"]).fingerprint == d["action_hash"]
            ]
            assert len(matches) == 1
            gates.append((d, matches[0]))
    for gate, node_id in gates:
        assert gate["evidence_hash"] == identity(nodes[node_id]["evaluation"])
    learning = store.error_rows()
    for index, row in enumerate(learning):
        labels[row["id"]] = f"learning-{index}"
    pattern = re.compile("|".join(re.escape(k) for k in sorted(labels, key=len, reverse=True)))
    volatile = {"timestamp", "expires_at", "latency_ms", "evidence_hash"}

    def normalize(value, field=None):
        if isinstance(value, dict):
            return {k: normalize(v, k) for k, v in value.items() if k not in volatile}
        if isinstance(value, list):
            rows = [normalize(v) for v in value]
            return (
                sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
                if field in {"evidence_ids", "source_ids"}
                else rows
            )
        if isinstance(value, str):
            return pattern.sub(lambda m: labels[m.group()], value)
        return value

    selected = {
        "observed",
        "prediction",
        "gate_preview",
        "authorization",
        "execution_intent",
        "outcome",
        "observation_evidence",
        "comparison",
        "cognitive_update",
        "decision",
        "execution_aborted",
        "escalation",
    }
    trace = []
    for e in events:
        if e["kind"] in selected:
            data = {k: v for k, v in e["data"].items() if k != "artifact"}
            trace.append({"round": labels[e["run_id"]], "kind": e["kind"], "data": normalize(data)})
    return {
        "events": trace,
        "evaluations": normalize([n["evaluation"] for n in nodes.values() if n["evaluation"]]),
        "learning": normalize(learning),
        "actions": normalize([a.model_dump() for a in world.executed]),
        "reward": world.reward,
        "final_state": normalize(result.final_state.model_dump()),
        "success": result.success,
        "unsafe": result.unsafe,
        "status": result.status,
    }


async def episode(config, condition, enabled, directory, policy):
    directory.mkdir()
    stats = Measurements()
    world = MeasuredWorld(stats, **config)
    store = Store(f"sqlite:///{directory / 'ledger.db'}")
    event.listen(store.db, "before_cursor_execute", lambda *args: stats.count("sql"))
    planner = MeasuredPlanner(stats, adaptation=condition != "no_adaptation")
    agent = CognitiveAgent(
        store,
        Artifacts(str(directory / "artifacts")),
        Registry([]),
        planner,
        [Goal(name="Throughput", metric="delivered", target=world.target)],
        Policy.model_validate(policy),
        use_memory=condition != "no_memory",
        reuse_beliefs=enabled,
    )
    instrument_memory(agent.memory, stats)
    agent.registry = Registry(
        [
            MeasuredForecast(
                world.task,
                planner,
                agent.memory,
                use_memory=agent.use_memory,
                belief_estimator=agent.belief_estimator if enabled else None,
            ),
            QueueBoundVerifier(world.task),
            QueueBoundVerifier(world.task, future=True),
        ]
    )
    original_runtime, original_adapter = Runtime.observe, _CognitiveWorld.observe
    round_observations, between = Counter(), set()

    async def runtime_observe(runtime, adapter):
        round_observations[runtime.run_id] += 1
        phase = {1: "runtime_start", 2: "pre_dispatch", 3: "runtime_final"}.get(
            round_observations[runtime.run_id], "unexpected_runtime"
        )
        token = PHASE.set(phase)
        try:
            return await original_runtime(runtime, adapter)
        finally:
            PHASE.reset(token)

    async def adapter_observe(adapter):
        token = None
        if PHASE.get() == "other":
            run = agent.memory.run_ids[-1] if agent.memory.run_ids else None
            phase = "agent_final" if run in between else "round_between"
            between.add(run)
            token = PHASE.set(phase)
        stats.count("adapter_observation")
        try:
            return await original_adapter(adapter)
        finally:
            if token is not None:
                PHASE.reset(token)

    Runtime.observe, _CognitiveWorld.observe = runtime_observe, adapter_observe
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        result = await agent.run(world, config["ticks"])
        stats.timed("episode", wall, cpu)
        snapshot = stats.dump()  # Exclude evaluation/reporting SQL and CPU.
        events = [e for r in result.run_ids for e in store.read_events(r)]
        trace = semantic_trace(events, result.run_ids, world, result, store)
        (directory / "semantic.json").write_text(json.dumps(trace, indent=2) + "\n")
        row = {
            "condition": condition,
            "enabled": enabled,
            "config": config,
            **snapshot,
            "cache": vars(agent.belief_estimator.counts),
            "engine_calls": sum(r["calls"] for r in result.rounds),
            "execution_calls": sum(r["execution_calls"] for r in result.rounds),
            "reward": world.reward,
            "success": result.success,
            "unsafe": result.unsafe,
            "semantic_sha256": identity(trace),
        }
        (directory / "metrics.json").write_text(json.dumps(row, indent=2) + "\n")
        return row, trace
    finally:
        Runtime.observe, _CognitiveWorld.observe = original_runtime, original_adapter
        store.db.dispose()


async def micro(directory, count, iterations):
    directory.mkdir()
    store = Store(f"sqlite:///{directory / 'ledger.db'}")
    world = CognitiveQueueWorld(ticks=20)
    memory = EpisodicMemory(store)
    for i in range(count):
        run = store.create_run({}, run_id=f"micro-{i}")
        await append_execution(store, world, run)
        await memory.remember(run)
    result = {"experiences": count, "iterations": iterations, "modes": {}}
    expected = None
    for enabled in (False, True):
        stats = Measurements()
        planner = MeasuredPlanner(stats)
        estimator = BeliefEstimator(enabled=enabled)
        estimator.begin(world.task)
        original_retrieve = memory.retrieve
        instrument_memory(memory, stats)
        await memory.retrieve(world.task.domain)  # Warm receipt index, outside timing.
        stats = planner.stats = Measurements()
        memory.retrieve = original_retrieve
        instrument_memory(memory, stats)
        wall, cpu = time.perf_counter(), time.process_time()
        values = []
        for _ in range(iterations):
            state = await world.observe()  # Actual re-observation retained in both modes.
            if enabled:
                belief = await estimator.infer(state, planner, memory, task=world.task)
            else:
                belief = planner.infer(state, await memory.retrieve(state.domain))
            values.append(
                {
                    "inferred": {k: v.model_dump() for k, v in belief.inferred.items()},
                    "unknown": belief.unknown,
                }
            )
        stats.timed("episode", wall, cpu)
        if expected is not None:
            assert values == expected
        expected = values
        result["modes"][str(enabled).lower()] = stats.dump() | {"cache": vars(estimator.counts)}
        memory.retrieve = original_retrieve
        estimator.end()
    store.db.dispose()
    return result


async def benchmark(protocol_path, output):
    protocol = json.loads(protocol_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    sources = list((root / "src/preact").rglob("*.py")) + [
        Path(__file__).resolve(),
        root / "tests/fixtures/memory_ledger.py",
    ]
    report = {
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(sources)
        },
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "episodes": [],
        "micro": [],
    }
    for enabled in (False, True):
        await episode(
            protocol["warmup"],
            "cognitive",
            enabled,
            output / f"warmup-{int(enabled)}",
            protocol["policy"],
        )
    for split, split_config in protocol["splits"].items():
        for seed in split_config["seeds"]:
            config = {k: v for k, v in split_config.items() if k != "seeds"} | {"seed": seed}
            for condition in protocol["conditions"]:
                rows, traces = [], []
                order = (False, True) if seed % 2 == 0 else (True, False)
                for enabled in order:
                    name = f"{split}-{seed}-{condition}-{int(enabled)}"
                    print(name, flush=True)
                    row, trace = await episode(
                        config, condition, enabled, output / name, protocol["policy"]
                    )
                    rows.append(row | {"split": split})
                    traces.append(trace)
                if traces[0] != traces[1]:
                    raise ValueError(f"Semantic mismatch: {split}/{seed}/{condition}")
                for row in rows:
                    row["paired_semantic_equality"] = True
                report["episodes"].extend(rows)
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    for count in protocol["micro"]["experience_counts"]:
        report["micro"].append(
            await micro(output / f"micro-{count}", count, protocol["micro"]["iterations"])
        )
    report["summary"] = {}
    for condition in protocol["conditions"]:
        report["summary"][condition] = {}
        for enabled in (False, True):
            rows = [
                r
                for r in report["episodes"]
                if r["condition"] == condition and r["enabled"] == enabled
            ]
            report["summary"][condition][str(enabled).lower()] = {
                "n": len(rows),
                "mean_seconds": {
                    k: statistics.mean(r["seconds"].get(k, 0) for r in rows)
                    for k in sorted({k for r in rows for k in r["seconds"]})
                },
                "counts_per_episode": rows[0]["counts"],
                "all_equal": all(r["paired_semantic_equality"] for r in rows),
            }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=Path("benchmarks/belief-reuse-v1.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol, args.output))


if __name__ == "__main__":
    main()
