"""Paired v2 sensing study. Raw receipts/schedules stay outside the public report."""

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import time
from collections import Counter
from pathlib import Path

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.information import InformationModel, InformationQueuePlanner
from preact.core.models import Policy, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld, bounded_safe
from preact.engines.information_queue import InformationBoundVerifier, InformationForecast
from scripts.bench_belief_reuse import semantic_trace


class MeasuredWorld(InformationQueueWorld):
    def __init__(self, counts, **kwargs):
        self.counts = counts
        super().__init__(**kwargs)

    async def observe(self):
        self.counts["observations"] += 1
        return await super().observe()


class MeasuredPlanner(InformationQueuePlanner):
    def __init__(self, counts, seconds, *args, **kwargs):
        self.counts, self.seconds = counts, seconds
        super().__init__(*args, **kwargs)

    def infer(self, *args):
        self.counts["inference"] += 1
        return super().infer(*args)

    def propose(self, *args):
        wall, cpu = time.perf_counter(), time.process_time()
        self.counts["proposals"] += 1
        try:
            return super().propose(*args)
        finally:
            self.seconds["proposal_wall"] += time.perf_counter() - wall
            self.seconds["proposal_cpu"] += time.process_time() - cpu


def best_normal(planner, belief, goals):
    decision = planner.decide(belief, goals)
    amounts = [n for n in (3, 1, 0) if bounded_safe(belief.observed.payload, n)]
    if not any(g.progress(belief.observed) < g.target for g in goals):
        return 0
    return max(amounts, key=lambda n: (decision.normal_values[n], n))


async def episode(config, strategy, directory, protocol, *, old_budget=False):
    directory.mkdir(parents=True, exist_ok=False)
    counts, seconds = Counter(), Counter()
    world = MeasuredWorld(counts, **config)
    planner = MeasuredPlanner(
        counts, seconds, strategy, model=InformationModel(**protocol["model"])
    )
    store = Store(f"sqlite:///{directory / 'ledger.sqlite'}")
    policy = protocol["policy"] if not old_budget else protocol["no_probe_budget_control"]
    goals = [Goal(name="Jobs", metric="delivered", target=world.target)]
    agent = CognitiveAgent(
        store,
        Artifacts(str(directory / "artifacts")),
        Registry([]),
        planner,
        goals,
        Policy.model_validate(policy),
        reuse_beliefs=True,
    )
    original = agent.memory.retrieve

    async def retrieve(*args, **kwargs):
        counts["retrieval"] += 1
        return await original(*args, **kwargs)

    agent.memory.retrieve = retrieve
    agent.registry = Registry(
        [
            InformationForecast(
                world.task, planner, agent.memory, belief_estimator=agent.belief_estimator
            ),
            InformationBoundVerifier(world.task),
            InformationBoundVerifier(world.task, future=True),
        ]
    )
    started, cpu = time.perf_counter(), time.process_time()
    try:
        result = await agent.run(world, world.ticks)
        wall, cpu = time.perf_counter() - started, time.process_time() - cpu
        observed_counts = dict(counts)
        events = [e for run in result.run_ids for e in store.read_events(run)]
        trace = semantic_trace(events, result.run_ids, world, result, store)
        # This audit is outside episode timing. It reads authoritative executions,
        # checks ordering and analyzes the private schedule only AFTER all actions.
        records, service_errors, forecasts, counterfactual_changes = [], [], [], 0
        for run in result.run_ids:
            execution = await agent.memory.read(run)
            kinds = [e["kind"] for e in events if e["run_id"] == run]
            assert len(execution) == 1 and kinds.count("outcome") == 1
            assert (
                kinds.index("authorization")
                < kinds.index("execution_intent")
                < kinds.index("outcome")
            )
            record = execution[0]
            state, obs = record.input_state, record.observation
            tick = state.payload["tick"]
            assert obs.state.payload["tick"] == tick + 1
            assert (
                "measured_service" not in state.payload
                and "measured_service" not in obs.state.payload
            )
            assert obs.checks["capacity"] and not obs.unsafe
            is_probe = record.action.kind == "probe_service"
            assert obs.metrics["probe_cost"] == (world.probe_cost if is_probe else 0)
            if is_probe:
                assert obs.metrics["measurement_tick"] == tick
                assert obs.metrics["measured_service"] == world._service[tick]
            else:
                assert "measured_service" not in obs.metrics
            candidates = [
                e["data"] for e in events if e["run_id"] == run and e["kind"] == "prediction"
            ]
            node = next(
                e["data"]["node_id"]
                for e in events
                if e["run_id"] == run and e["kind"] == "outcome"
            )
            forecast = next(
                p["prediction"]
                for p in candidates
                if p["node_id"] == node and p["prediction"]["engine_id"] == "information-forecast"
            )
            assert "measured_service" not in forecast["metrics"]
            value = forecast["raw"]["service_estimate"]
            forecasts.append((tick, value))
            service_errors.append(abs(value - world._service[tick]))
            if records and records[-1].action.kind == "probe_service":
                with_probe = planner.infer(state, records[-12:])
                without = planner.infer(state, [r for r in records[-12:] if r is not records[-1]])
                counterfactual_changes += best_normal(planner, with_probe, goals) != best_normal(
                    planner, without, goals
                )
            records.append(record)
        assert observed_counts["inference"] == observed_counts["retrieval"]
        detection = None
        if 0 < world.shift_tick < world.ticks:
            mean_after = (1 + 2 * world.noise) if world.high_first else (3 - 2 * world.noise)
            for i in range(len(forecasts) - 2):
                window = forecasts[i : i + 3]
                if window[0][0] >= world.shift_tick and all(
                    abs(v - mean_after) <= 0.5 for _, v in window
                ):
                    detection = window[0][0] - world.shift_tick
                    break
        metrics = {
            "success": result.success,
            "unsafe": result.unsafe,
            "reward": world.reward,
            "throughput": world.payload["delivered"],
            "ticks": world.payload["tick"],
            "holding_loss": sum(r.observation.metrics["holding_loss"] for r in records),
            "probe_count": sum(r.action.kind == "probe_service" for r in records),
            "probe_cost": sum(r.observation.metrics["probe_cost"] for r in records),
            "change_detection_delay": detection,
            "service_mae": statistics.mean(service_errors),
            "post_probe_normal_choice_changes": counterfactual_changes,
            "engine_calls": sum(r["calls"] for r in result.rounds),
            "escalations": sum(e["kind"] == "escalation" for e in events),
            "abstains": sum(r["status"] == "abstained" for r in result.rounds),
            "cpu_seconds": cpu,
            "wall_seconds": wall,
            "proposal_seconds": dict(seconds),
            "counts": observed_counts,
            "reuse_hits": agent.belief_estimator.counts.hits,
        }
        assert len(records) == world.ticks and metrics["reuse_hits"] == 0
        (directory / "trace.json").write_text(json.dumps(trace, indent=2) + "\n")
        (directory / "events.json").write_text(json.dumps(events, indent=2) + "\n")
        return {
            "metrics": metrics,
            "semantic_sha256": identity(trace),
            "behavior_sha256": identity(
                {
                    "actions": [(r.action.kind, r.action.payload) for r in records],
                    "observations": [r.observation.state.payload for r in records],
                    "reward": world.reward,
                    "unsafe": result.unsafe,
                }
            ),
        }
    finally:
        store.db.dispose()


def aggregate(rows):
    result = {}
    for case in sorted({r["case"] for r in rows}):
        group = [
            r for r in rows if r["case"] == case and r["repeat"] == 0 and not r["budget_control"]
        ]
        control = {r["seed"]: r for r in group if r["strategy"] == "no_probe"}
        result[case] = {}
        for strategy in sorted({r["strategy"] for r in group}):
            selected = [r for r in group if r["strategy"] == strategy]
            names = [
                "reward",
                "throughput",
                "holding_loss",
                "probe_count",
                "probe_cost",
                "service_mae",
                "post_probe_normal_choice_changes",
                "engine_calls",
                "escalations",
                "abstains",
                "cpu_seconds",
                "wall_seconds",
            ]
            means = {k: statistics.mean(r["metrics"][k] for r in selected) for k in names}
            means.update(
                success_rate=statistics.mean(float(r["metrics"]["success"]) for r in selected),
                unsafe_rate=statistics.mean(float(r["metrics"]["unsafe"]) for r in selected),
                paired_reward_delta=statistics.mean(
                    r["metrics"]["reward"] - control[r["seed"]]["metrics"]["reward"]
                    for r in selected
                ),
                detection_delays=[r["metrics"]["change_detection_delay"] for r in selected],
            )
            result[case][strategy] = means
    return result


async def benchmark(protocol_path, output, report_path):
    protocol = json.loads(protocol_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    source = Path("src/preact")
    hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.rglob("*.py"))
    }
    for path in (Path(__file__), Path("scripts/bench_belief_reuse.py")):
        relative = path.relative_to(Path.cwd()) if path.is_absolute() else path
        hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    rows, reference, controls = [], {}, {}
    for repeat in range(protocol["repeats"]):
        for case, options in protocol["cases"].items():
            for seed in protocol["seeds"]:
                config = protocol["world"] | options | {"seed": seed}
                for strategy in protocol["strategies"]:
                    print(f"repeat={repeat} {case} seed={seed} {strategy}", flush=True)
                    row = await episode(
                        config, strategy, output / f"{repeat}-{case}-{seed}-{strategy}", protocol
                    )
                    key = (case, seed, strategy)
                    if repeat:
                        assert row["semantic_sha256"] == reference[key], (
                            f"Reproduction mismatch: {key}"
                        )
                    else:
                        reference[key] = row["semantic_sha256"]
                    if strategy == "no_probe":
                        controls[(case, seed)] = row["behavior_sha256"]
                    rows.append(
                        row
                        | {
                            "case": case,
                            "seed": seed,
                            "strategy": strategy,
                            "repeat": repeat,
                            "budget_control": False,
                        }
                    )
                    (output / "partial-results.json").write_text(json.dumps(rows, indent=2) + "\n")
                if repeat == 0 and protocol.get("budget_control", False):
                    row = await episode(
                        config,
                        "no_probe",
                        output / f"budget-{case}-{seed}",
                        protocol,
                        old_budget=True,
                    )
                    assert row["behavior_sha256"] == controls[(case, seed)], (
                        "No Probe budget confound"
                    )
                    rows.append(
                        row
                        | {
                            "case": case,
                            "seed": seed,
                            "strategy": "no_probe",
                            "repeat": 0,
                            "budget_control": True,
                        }
                    )
    report = {
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": hashes,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "episodes": rows,
        "aggregate": aggregate(rows),
        "audit": {
            "semantic_reproduction": True,
            "receipt_gate_audit": True,
            "budget_control_matched": bool(protocol.get("budget_control", False)),
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Receipt/Gate audited CPU information-seeking study"
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol, args.output, args.report))


if __name__ == "__main__":
    main()
