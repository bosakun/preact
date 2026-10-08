"""Reproducible CPU experiments with real Runtime execution and raw evidence."""

import argparse
import asyncio
import hashlib
import json
import platform
import random
import statistics
import time
from pathlib import Path

from preact.core.models import Policy, State
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import CognitiveQueueWorld
from preact.engines.cognitive_queue import QueueBoundVerifier, QueueForecast

from .loop import CognitiveAgent
from .memory import EpisodicMemory
from .models import Goal
from .queue import QueuePlanner


class _ReactiveWorld(CognitiveQueueWorld):
    async def propose(self, state, width):
        amount = 0 if state.payload["queue"] >= state.payload["capacity"] - 2 else 3
        return [self.action(state, amount)][:width]


def ece(pairs: list[tuple[float, float]], bins: int) -> float | None:
    if not pairs:
        return None
    error = 0.0
    for bucket in range(bins):
        rows = [(p, y) for p, y in pairs if min(bins - 1, int(p * bins)) == bucket]
        if rows:
            error += len(rows) / len(pairs) * abs(statistics.mean(p - y for p, y in rows))
    return error


def summarize(
    events: list[dict], world: CognitiveQueueWorld, rounds: list[dict], analysis: dict
) -> dict:
    comparisons = [e["data"] for e in events if e["kind"] == "comparison"]
    predictions = {
        e["data"]["prediction"]["id"]: e["data"]["prediction"]
        for e in events
        if e["kind"] == "prediction"
    }
    observations = {
        (e["run_id"], e["data"]["node_id"]): e["data"]["observation"]
        for e in events
        if e["kind"] == "outcome"
    }
    model_pairs, errors, estimates = [], [], []
    for event in events:
        if event["kind"] != "comparison":
            continue
        comparison = event["data"]
        prediction = predictions[comparison["prediction_id"]]
        if prediction["engine_id"] != "queue-forecast":
            continue
        observation = observations[(event["run_id"], comparison["node_id"])]
        metrics = observation["metrics"]
        model_pairs.append(
            (
                prediction["raw"]["all_processed_probability"],
                float(metrics["processed"] == metrics["available_work"]),
            )
        )
        errors.append(comparison["metrics"]["processed"]["absolute_error"])
        estimates.append(
            (observation["state"]["payload"]["tick"] - 1, prediction["raw"]["service_estimate"])
        )
    mean_after = 3 - 2 * world.noise if not world.high_first else 1 + 2 * world.noise
    recovery = None
    consecutive = analysis["recovery_consecutive_ticks"]
    for index in range(len(estimates) - consecutive + 1):
        window = estimates[index : index + consecutive]
        if window[0][0] >= world.shift_tick and all(
            abs(value - mean_after) <= analysis["recovery_tolerance"] for _, value in window
        ):
            recovery = window[0][0] - world.shift_tick
            break
    immediate_scores = [c["success"]["brier"] for c in comparisons if "success" in c]
    risk_scores = [c["risk"]["brier"] for c in comparisons if "risk" in c]
    previews = [e["data"]["gate"] for e in events if e["kind"] == "gate_preview"]
    executed = {(e["run_id"], e["data"]["node_id"]) for e in events if e["kind"] == "outcome"}
    alternative_calls = sum(
        (e["run_id"], e["data"]["node_id"]) not in executed
        for e in events
        if e["kind"] == "verification_requested"
    )
    return {
        "success": world.complete(State.create(world.task.domain, world.payload, "evaluation"))
        and not world.unsafe,
        "unsafe": world.unsafe,
        "ticks": world.payload["tick"],
        "delivered": world.payload["delivered"],
        "reward": world.reward,
        "calls": sum(r["calls"] for r in rounds),
        "execution_calls": sum(r["execution_calls"] for r in rounds),
        "cost_usd": sum(r["cost_usd"] for r in rounds),
        "cost_known": all(r["cost_known"] for r in rounds),
        "latency_ms": sum(r["latency_ms"] for r in rounds),
        "model_processed_mae": statistics.mean(errors) if errors else None,
        "model_all_processed_brier": statistics.mean((p - y) ** 2 for p, y in model_pairs)
        if model_pairs
        else None,
        "model_all_processed_ece": ece(model_pairs, analysis["ece_bins"]),
        "model_score_n": len(model_pairs),
        "immediate_success_brier": statistics.mean(immediate_scores) if immediate_scores else None,
        "immediate_risk_brier": statistics.mean(risk_scores) if risk_scores else None,
        "adaptation_ticks": recovery,
        "adaptation_censored": recovery is None,
        "verify_escalations": sum(e["kind"] == "escalation" for e in events),
        "abstained_rounds": sum(r["status"] == "abstained" for r in rounds),
        "hard_vetoes": sum(
            any("Mandatory check failed" in reason for reason in g["reasons"]) for g in previews
        ),
        "alternative_verification_calls": alternative_calls,
        "cached_prediction_calls": sum(
            bool(e["data"]["cached"]) for e in events if e["kind"] == "prediction"
        ),
    }


async def episode(config: dict, condition: str, output: Path, analysis: dict) -> dict:
    output.mkdir()
    world_type = _ReactiveWorld if condition == "reactive" else CognitiveQueueWorld
    world = world_type(**config)
    store = Store(f"sqlite:///{output / 'ledger.sqlite'}")
    artifacts = Artifacts(str(output / "artifacts"))
    planner = QueuePlanner(
        adaptation=condition
        not in {"no_adaptation", "current_preact", "reactive", "fixed_prediction"}
    )
    memory = EpisodicMemory(store)
    use_memory = condition != "no_memory"
    policy = Policy.model_validate(analysis["policy"])
    if condition == "fixed_prediction":
        engines = [QueueBoundVerifier(world.task, nominal=True, single=True)]
    else:
        engines = [QueueBoundVerifier(world.task), QueueBoundVerifier(world.task, future=True)]
    forecast = QueueForecast(world.task, planner, memory, use_memory=use_memory)
    registry = Registry([forecast, *engines])
    started, cpu = time.monotonic(), time.process_time()
    rounds, run_ids = [], []
    try:
        if condition in {"reactive", "current_preact"}:
            for _ in range(config["ticks"]):
                runtime = Runtime(store, artifacts, registry, policy)
                result = await runtime.run(
                    world, direct=condition == "reactive", forecast_selected=condition == "reactive"
                )
                rounds.append(result)
                run_ids.append(runtime.run_id)
                if result["unsafe"] or result["success"] or result["steps"] == 0:
                    break
        else:
            agent = CognitiveAgent(
                store,
                artifacts,
                registry,
                planner,
                [Goal(name="Throughput", metric="delivered", target=world.target)],
                policy,
                use_memory=use_memory,
            )
            forecast.memory = agent.memory
            result = await agent.run(world, config["ticks"])
            rounds, run_ids = result.rounds, result.run_ids
        elapsed, cpu_seconds = time.monotonic() - started, time.process_time() - cpu
        events = [event for run_id in run_ids for event in store.read_events(run_id)]
        metrics = summarize(events, world, rounds, analysis)
        metrics.update(wall_seconds=elapsed, cpu_seconds=cpu_seconds)
        payload = {
            "condition": condition,
            "config": config,
            "metrics": metrics,
            "rounds": rounds,
            "run_ids": run_ids,
            "events": events,
            "prediction_errors": store.error_rows(),
        }
        (output / "episode.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        return {
            "condition": condition,
            "config": config,
            "metrics": metrics,
            "evidence": str(output / "episode.json"),
        }
    finally:
        store.db.dispose()


def paired_intervals(rows: list[dict], split: str, analysis: dict) -> dict:
    primary = {
        r["config"]["seed"]: r
        for r in rows
        if r["split"] == split and r["condition"] == "cognitive"
    }
    result = {}
    for condition in sorted({r["condition"] for r in rows} - {"cognitive"}):
        control = {
            r["config"]["seed"]: r
            for r in rows
            if r["split"] == split and r["condition"] == condition
        }
        deltas = [
            primary[s]["metrics"]["reward"] - control[s]["metrics"]["reward"]
            for s in primary.keys() & control.keys()
        ]
        if not deltas:
            continue
        rng = random.Random(analysis["paired_bootstrap_seed"])
        means = sorted(
            statistics.mean(rng.choices(deltas, k=len(deltas)))
            for _ in range(analysis["paired_bootstrap_samples"])
        )
        result[condition] = {
            "paired_reward_delta": statistics.mean(deltas),
            "bootstrap_95": [means[int(len(means) * 0.025)], means[int(len(means) * 0.975)]],
            "n_seeds": len(deltas),
        }
    return result


async def benchmark(protocol_path: Path, output: Path, splits: list[str]) -> dict:
    protocol = json.loads(protocol_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1]
    hashes = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source.rglob("*.py"))
    }
    manifest = {
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": hashes,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "splits": splits,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = []
    for split in splits:
        split_config = protocol[split]
        for seed in split_config["seeds"]:
            config = {k: v for k, v in split_config.items() if k != "seeds"} | {"seed": seed}
            for condition in protocol["conditions"]:
                print(f"{split} seed={seed} {condition}", flush=True)
                row = await episode(
                    config,
                    condition,
                    output / f"{split}-{seed}-{condition}",
                    protocol["analysis"] | {"policy": protocol["policy"]},
                )
                rows.append(row | {"split": split})
                (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    intervals = {split: paired_intervals(rows, split, protocol["analysis"]) for split in splits}
    report = {"episodes": rows, "paired_reward_intervals": intervals}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="PreAct認知queue CPU比較実験")
    parser.add_argument("--protocol", type=Path, default=Path("benchmarks/cognitive-queue-v1.json"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", choices=["development", "transfer", "all"], default="all")
    args = parser.parse_args()
    asyncio.run(
        benchmark(
            args.protocol,
            args.output,
            ["development", "transfer"] if args.split == "all" else [args.split],
        )
    )


if __name__ == "__main__":
    main()
