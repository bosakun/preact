"""Receipt-backed flat qualification experiment; raw data stays in private output."""

import argparse
import asyncio
import json
import resource
import sys
import time
from dataclasses import asdict
from pathlib import Path
from statistics import fmean

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.queue_model import MonitoredQueueSource, QueueModelPlanner
from preact.cognition.queue_multi import QueueMultiPlanner
from preact.core.models import Policy, Prediction, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_admission_fixture import AdmissionFixtureVerifier, AdmissionQueueFixture
from preact.domains.queue_model_selection import QueueSelectionWorld
from preact.domains.queue_multi_selection import QueueMultiSelectionWorld
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning.drift import DriftConfig
from scripts.benchmark_queue_model_selection import (
    diagnostic,
    fit_model,
    recover_model,
    selection_source_hash,
)
from scripts.benchmark_queue_temporal import FixedPlanner, summarize

ROOT = Path(__file__).resolve().parents[1]


async def prepare(world, sequence, store, artifacts, policy):
    """Every preparation action uses the fixture's actual mandatory verifier."""
    immediate = (
        AdmissionFixtureVerifier(world.task)
        if isinstance(world, AdmissionQueueFixture)
        else InformationBoundVerifier(world.task)
    )
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry([immediate, InformationBoundVerifier(world.task, future=True)]),
        FixedPlanner(sequence, world.payload["tick"]),
        [Goal(name="Deliver", metric="delivered", target=world.target)],
        Policy(**policy),
    )
    result = await agent.run(world, len(sequence))
    if result.unsafe or sum(r["steps"] for r in result.rounds) != len(sequence):
        raise ValueError("Unsafe or incomplete gated preparation")
    return result.run_ids


def measurements(store, prefix, runs, target):
    outcomes = [
        e["data"]["observation"]
        for r in [*prefix, *runs]
        for e in store.read_events(r)
        if e["kind"] == "outcome"
    ]
    delivered = outcomes[-1]["state"]["payload"]["delivered"] if outcomes else 0
    holding = sum(o["metrics"]["holding_loss"] for o in outcomes)
    submission = sum(o["metrics"]["submission_cost"] for o in outcomes)
    probe = sum(o["metrics"]["probe_cost"] for o in outcomes)
    result = dict(
        delivered=delivered,
        holding_cost=holding,
        submission_cost=submission,
        probe_cost=probe,
        reward=delivered - holding - submission - probe,
        goal_utility=min(delivered, target) - holding - submission - probe,
        goal_success=delivered >= target,
        goal_tick=next(
            (
                o["state"]["payload"]["tick"]
                for o in outcomes
                if o["state"]["payload"]["delivered"] >= target
            ),
            None,
        ),
        unsafe=sum(o["unsafe"] for o in outcomes),
        preparation_ticks=len(prefix),
        decisions=len(runs),
        actions=[],
        qualified_counts=[],
        unresolved_counts=[],
        top_rejected=0,
        alternatives_exist=0,
        alternatives_executed=0,
        complete_qualification=0,
        unresolved_abstain=0,
        abstain=0,
        model_decisions=0,
        fallback_decisions=0,
        local_prediction_calls=0,
        local_revalidation_calls=0,
        verifier_calls=0,
        external_api_calls=0,
        api_cost_usd=0,
        compute_dollar_cost=None,
        escalations=0,
        gate_rejected=0,
    )
    errors = []
    for run in runs:
        events = store.read_events(run)
        record = next(
            (
                e["data"]
                for e in events
                if e["kind"] in {"cognitive_prediction", "cognitive_selection"}
            ),
            None,
        )
        outcome = next((e["data"]["observation"] for e in events if e["kind"] == "outcome"), None)
        execution = next(
            (
                e["data"]
                for e in events
                if e["kind"] == "decision" and e["data"].get("decision") == "execute"
            ),
            None,
        )
        batch = next((e["data"] for e in events if e["kind"] == "candidate_qualification"), None)
        if batch:
            cs = batch["batch"]["candidates"]
            qualified = [c for c in cs if c["status"] == "qualified"]
            unresolved = [c for c in cs if c["status"] in {"unresolved", "unexpanded"}]
            result["qualified_counts"].append(len(qualified))
            result["unresolved_counts"].append(len(unresolved))
            result["complete_qualification"] += int(batch["complete"])
            result["unresolved_abstain"] += int(bool(unresolved) and outcome is None)
            result["gate_rejected"] += sum(c["status"] == "rejected" for c in cs)
            first = next(
                (
                    c
                    for c in cs
                    if record
                    and c["action"]
                    and identity(
                        {
                            "kind": c["action"]["kind"],
                            "payload": c["action"]["payload"],
                            "duration": c["action"]["duration"],
                            "state": c["action"]["state_id"],
                        }
                    )
                    == record["first_choice_fingerprint"]
                ),
                None,
            )
            rejected = first is not None and first["status"] == "rejected"
            result["top_rejected"] += int(rejected)
            result["alternatives_exist"] += int(rejected and bool(qualified))
            result["alternatives_executed"] += int(
                rejected and bool(qualified) and outcome is not None
            )
        else:
            previews = [e["data"]["gate"] for e in events if e["kind"] == "gate_preview"]
            result["complete_qualification"] += int(bool(previews))
            result["top_rejected"] += int(bool(previews) and previews[0]["decision"] != "execute")
            result["gate_rejected"] += sum(g["decision"] != "execute" for g in previews)
        result["abstain"] += int(outcome is None)
        if record:
            result["model_decisions"] += int(
                record["basis"] == "learned_model"
                and record.get("selection_rule") != "core_utility_control"
            )
            result["fallback_decisions"] += int(record["basis"] == "fallback")
            result["local_prediction_calls"] += record["local_prediction_calls"]
            result["local_revalidation_calls"] += record["local_revalidation_calls"]
        if execution and outcome:
            action = execution["action"]
            result["actions"].append(action["payload"]["amount"])
            if record and record["basis"] in {"learned_model", "fixed_prior"}:
                fingerprint = identity(
                    {
                        "kind": action["kind"],
                        "payload": action["payload"],
                        "duration": action["duration"],
                        "state": action["state_id"],
                    }
                )
                prediction = next(
                    c["prediction"] for c in record["candidates"] if c["fingerprint"] == fingerprint
                )
                for metric in ("queue", "delivered"):
                    errors.append(
                        abs(prediction["vectors"][metric][0] - outcome["state"]["payload"][metric])
                    )
        saved = store.get_run(run)["result"]
        result["verifier_calls"] += saved["calls"]
        result["api_cost_usd"] += saved["cost_usd"]
        result["escalations"] += sum(e["kind"] == "escalation" for e in events)
    result.update(
        one_tick_state_mae=fmean(errors) if errors else None,
        scored_state_values=len(errors),
        forecast_coverage=len(errors) / (2 * max(1, len(result["actions"]))),
        unknown_rate=1 - len(errors) / (2 * max(1, len(result["actions"]))),
        preparation_verifier_calls=sum(store.get_run(r)["result"]["calls"] for r in prefix),
    )
    return result


async def episode(
    store,
    artifacts,
    protocol,
    model,
    training,
    digest,
    name,
    seed,
    mode,
    settings,
    lifecycle=None,
    evaluation=None,
):
    start_cpu, start_wall = time.process_time(), time.perf_counter()
    cls = AdmissionQueueFixture if settings.get("fixture") else InformationQueueWorld
    world = cls(
        seed=seed,
        ticks=protocol["episode_ticks"],
        target=protocol["target"],
        capacity=protocol["capacity"],
        holding_cost=protocol["holding_cost"],
        submission_cost=protocol["submission_cost"],
        probe_cost=protocol["probe_cost"],
        **{k: settings[k] for k in ("high_first", "shift_tick", "noise")},
    )
    initial = await world.observe()
    scope = f"multi:{name}:{seed}:{mode}"
    guard = QueueTemporalGuard(
        store,
        world.task,
        model,
        training,
        episode_id=scope,
        initial=initial,
        config=DriftConfig(**protocol["detector"]),
    )
    prefix = await prepare(
        world, settings.get("warmup", protocol["warmup"]), store, artifacts, protocol["policy"]
    )
    ready = asdict(await guard.health(await world.observe(), prefix))
    promotion = None
    if lifecycle is not None and mode in {"multi_learned", "single_learned"}:
        promotion = await lifecycle.promote(
            evaluation,
            episode_id=scope,
            task=world.task,
            initial=initial,
            state=await world.observe(),
            runs=prefix,
        )
        source = MonitoredQueueSource.promoted(lifecycle, promotion)
    else:
        source = MonitoredQueueSource(store, guard, training)
    planner_mode = {
        "multi_learned": "learned",
        "single_learned": "learned",
        "multi_prior": "prior",
        "existing": "existing",
        "fixed": "fixed",
        "core_utility_control": "learned",
    }[mode]
    single = mode == "single_learned"
    planner = (
        QueueModelPlanner(source, prefix_runs=prefix, mode=planner_mode)
        if single
        else QueueMultiPlanner(
            source,
            prefix_runs=prefix,
            mode=planner_mode,
            prior=protocol["fixed_prior"] if planner_mode == "prior" else None,
            selection_rule="core_utility_control"
            if mode == "core_utility_control"
            else "model_value",
        )
    )
    wrapper = (
        QueueSelectionWorld(world, planner) if single else QueueMultiSelectionWorld(world, planner)
    )
    policy = {**protocol["policy"], **({"width": 1, "max_nodes": 1} if single else {})}
    immediate = (
        AdmissionFixtureVerifier(world.task)
        if settings.get("fixture")
        else InformationBoundVerifier(world.task)
    )
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry([immediate, InformationBoundVerifier(world.task, future=True)]),
        planner,
        [Goal(name="Deliver", metric="delivered", target=protocol["target"])],
        Policy(**policy),
        candidate_selector=None if single else planner,
    )
    agent.memory.run_ids.extend(prefix)
    retrievals = 0
    retrieve = agent.memory.retrieve

    async def measured(*args, **kwargs):
        nonlocal retrievals
        retrievals += 1
        return await retrieve(*args, **kwargs)

    agent.memory.retrieve = measured
    cpu, wall = time.process_time(), time.perf_counter()
    result = await agent.run(wrapper, protocol["episode_ticks"] - len(prefix))
    return {
        "id": f"{name}:{seed}:{mode}",
        "environment": name,
        "seed": seed,
        "mode": mode,
        "settings": settings,
        "initial": initial.model_dump(),
        "task": world.task.model_dump(),
        "episode_id": scope,
        "model_artifact": digest,
        "training": training,
        "prefix_runs": prefix,
        "runs": result.run_ids,
        "readiness": ready,
        "promotion": promotion,
        "journal_run": lifecycle.journal_run if promotion else None,
        "final_observation": (await world.observe()).model_dump(),
        "metrics": {
            **measurements(store, prefix, result.run_ids, protocol["target"]),
            "cpu_seconds": time.process_time() - cpu,
            "wall_seconds": time.perf_counter() - wall,
            "total_cpu_seconds": time.process_time() - start_cpu,
            "total_wall_seconds": time.perf_counter() - start_wall,
            "observation_calls": wrapper.observation_calls if single else wrapper.observations,
            "memory_retrieval_calls": retrievals,
            "setup_local_revalidation_calls": source.revalidation_calls if promotion else 0,
        },
    }


async def benchmark(protocol_path, output, pilot=False):
    protocol = json.loads(protocol_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts = Artifacts(str(output / "artifacts"))
    cpu, wall = time.process_time(), time.perf_counter()
    execution_source_hash = selection_source_hash()
    models = {high: await fit_model(store, artifacts, protocol, high) for high in (False, True)}
    cases = []
    seeds = protocol["pilot_seeds"] if pilot else protocol["evaluation_seeds"]
    for name, settings in protocol["environments"].items():
        for seed in seeds:
            model, training, digest = models[settings.get("training_high", settings["high_first"])]
            for mode in protocol["modes"]:
                case = await episode(
                    store, artifacts, protocol, model, training, digest, name, seed, mode, settings
                )
                cases.append(case)
                (output / "partial.json").write_text(json.dumps(cases, indent=2))
                print(
                    case["id"],
                    case["metrics"]["goal_success"],
                    case["metrics"]["alternatives_executed"],
                    flush=True,
                )
    recovery = []
    for direction in protocol["recovery_directions"]:
        for seed in protocol["pilot_seeds"] if pilot else protocol["recovery_seeds"]:
            parent, training, _ = models[direction == "high_to_low"]
            for mode in protocol["modes"]:
                lifecycle, evaluation, model, training_b, digest, preparation = await recover_model(
                    store,
                    artifacts,
                    {
                        **protocol,
                        "diagnostic_seeds": protocol.get(
                            "recovery_comparison_seeds", protocol["diagnostic_seeds"]
                        ),
                    },
                    parent,
                    training,
                    seed,
                    direction,
                )
                settings = {"high_first": direction == "low_to_high", "shift_tick": 48, "noise": 0}
                case = await episode(
                    store,
                    artifacts,
                    protocol,
                    model,
                    training_b,
                    digest,
                    "recovered_" + direction,
                    seed,
                    mode,
                    settings,
                    lifecycle,
                    evaluation,
                )
                case["recovery_preparation"] = preparation
                cases.append(case)
                recovery.append(preparation)
                (output / "partial.json").write_text(json.dumps(cases, indent=2))
                print(case["id"], case["metrics"]["goal_success"], flush=True)
    diagnostics = [
        await diagnostic(store, artifacts, protocol, models, seed, high)
        for seed in (protocol["pilot_seeds"] if pilot else protocol["diagnostic_seeds"])
        for high in (False, True)
    ]
    manifest = {
        "schema_version": "1",
        "protocol": protocol,
        "protocol_hash": identity(protocol),
        "source_hash": execution_source_hash,
        "pilot": pilot,
        "cases": cases,
        "diagnostics": diagnostics,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    groups = {}
    for case in cases:
        groups.setdefault(case["environment"] + ":" + case["mode"], []).append(case["metrics"])
    summary = {
        "schema_version": "1",
        "protocol_hash": identity(protocol),
        "source_hash": manifest["source_hash"],
        "pilot": pilot,
        "episodes": len(cases),
        "groups": {
            key: {
                k: fmean(r[k] for r in rows)
                for k in rows[0]
                if isinstance(rows[0][k], (int, float)) and all(r[k] is not None for r in rows)
            }
            for key, rows in groups.items()
        },
        "cases": [
            {
                "environment": c["environment"],
                "seed": c["seed"],
                "mode": c["mode"],
                "metrics": c["metrics"],
                "recovery_preparation_metrics": {
                    k: v
                    for k, v in c.get("recovery_preparation", {}).items()
                    if isinstance(v, (int, float))
                },
            }
            for c in cases
        ],
        "cpu_seconds": time.process_time() - cpu,
        "wall_seconds": time.perf_counter() - wall,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
        "limitations": protocol["limits"],
        "diagnostics": {
            f"{high}:{mode}": summarize(
                [
                    {
                        **d["comparisons"][mode],
                        "predictions": [
                            Prediction.model_validate(p)
                            for p in d["comparisons"][mode]["predictions"]
                        ],
                    }
                    for d in diagnostics
                    if d["high"] == high
                ],
                [[b["trace"] for b in d["branches"]] for d in diagnostics if d["high"] == high],
                3,
                detailed=True,
            )
            for high in (False, True)
            for mode in ("learned", "prior")
        },
    }
    if selection_source_hash() != execution_source_hash:
        raise ValueError(
            "Implementation changed during benchmark; keep diagnostic output and rerun"
        )
    summary["semantic_hash"] = identity(
        [
            {
                "environment": c["environment"],
                "seed": c["seed"],
                "mode": c["mode"],
                "metrics": {k: v for k, v in c["metrics"].items() if "seconds" not in k},
            }
            for c in cases
        ]
    )
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    store.db.dispose()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol", type=Path, default=ROOT / "benchmarks/queue-multi-selection-v2.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol, args.output, args.pilot))
