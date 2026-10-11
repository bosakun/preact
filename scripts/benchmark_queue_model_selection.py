"""Fixed paired closed-loop selection experiment; raw receipts stay in private output."""

import argparse
import asyncio
import json
import platform
import resource
import time
from dataclasses import asdict
from pathlib import Path
from statistics import fmean

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.queue_model import MonitoredQueueSource, QueueModelPlanner
from preact.core.models import Policy, State, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_model_selection import QueueSelectionWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import CandidateModel
from scripts.benchmark_queue_recovery import evaluation_cases
from scripts.benchmark_queue_temporal import execute_sequence, summarize

ROOT = Path(__file__).resolve().parents[1]


def selection_source_hash():
    import hashlib

    files = [
        *sorted((ROOT / "src/preact").rglob("*.py")),
        *[
            p
            for p in sorted((ROOT / "scripts").glob("*queue*.py"))
            if not p.name.startswith("audit_")
        ],
    ]
    return identity(
        {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    )


def make_world(protocol, seed, settings, *, target=None, ticks=None):
    return InformationQueueWorld(
        seed=seed,
        ticks=ticks or protocol["episode_ticks"],
        target=target or protocol["target"],
        capacity=protocol["capacity"],
        holding_cost=protocol["holding_cost"],
        submission_cost=protocol["submission_cost"],
        probe_cost=protocol["probe_cost"],
        **{k: settings[k] for k in ("high_first", "shift_tick", "noise")},
    )


async def fit_model(store, artifacts, protocol, high):
    fixture = make_world(
        protocol,
        protocol["training_seeds"][int(high)],
        {"high_first": high, "shift_tick": 128, "noise": 0},
        target=999,
        ticks=128,
    )
    training = {
        f"training:{high}": await execute_sequence(
            fixture, ["probe"] * protocol["training_ticks"], store, artifacts, protocol["policy"]
        )
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter(), TrainingConfig(min_samples=16)
    )
    return model, training, model.save(artifacts)


async def recover_model(store, artifacts, protocol, parent, training, seed, direction):
    cpu, wall = time.process_time(), time.perf_counter()
    high = direction == "high_to_low"
    settings = {"high_first": high, "shift_tick": 8, "noise": 0}
    world = make_world(protocol, seed, settings, target=999, ticks=128)
    initial = await world.observe()
    guard = QueueTemporalGuard(
        store,
        world.task,
        parent,
        training,
        episode_id=f"recovery-source:{direction}:{seed}",
        initial=initial,
    )
    lifecycle = await QueueModelRecovery.create(store, artifacts)
    runs, origin = [], None
    for _ in range(56):
        runs += await execute_sequence(world, ["probe"], store, artifacts, protocol["policy"])
        state = await world.observe()
        health = await guard.health(state, runs)
        if health.status == "invalidated":
            origin = await lifecycle.begin(
                parent,
                training,
                episode_id=guard._episode,
                task=world.task,
                initial=initial,
                cutoff=state,
                runs=runs,
            )
            break
    if origin is None:
        raise ValueError("Frozen deterministic recovery fixture did not invalidate")
    runs += await execute_sequence(world, ["probe"] * 16, store, artifacts, protocol["policy"])
    candidate_digest = await lifecycle.prepare_candidate(origin, await world.observe(), runs)
    evaluation_protocol = {
        **protocol,
        "episode_ticks": 128,
        "target": 999,
        "comparison_seeds": protocol["diagnostic_seeds"],
        "workloads": {"pending": [3], "queue_pending": [3, 3]},
    }
    cases, _ = await evaluation_cases(
        lifecycle, candidate_digest, evaluation_protocol, high=not high
    )
    evaluation = await lifecycle.evaluate_candidate(
        candidate_digest, cases, (await world.observe()).timestamp
    )
    candidate = CandidateModel.model_validate(json.loads(artifacts.read(candidate_digest)))
    from preact.learning import DynamicsModel

    model = DynamicsModel.load(artifacts, candidate.model_artifact, QueueServiceAdapter())
    preparatory = [*runs, *[r for c in cases for b in c.branches for r in b.runs]]
    preparation_rows = (
        await TransitionDataset(
            store,
            {guard._episode: runs, **{b.episode_id: b.runs for c in cases for b in c.branches}},
        ).snapshot()
    ).transitions
    return (
        lifecycle,
        evaluation,
        model,
        candidate.training,
        candidate.model_artifact,
        {
            "source_runs": runs,
            "origin": origin,
            "candidate": candidate_digest,
            "evaluation": evaluation,
            "preparation_runs": preparatory,
            "preparation_ticks": len(preparatory),
            "preparation_probe_cost": sum(r.after.metrics["probe_cost"] for r in preparation_rows),
            "preparation_submission_cost": sum(
                r.after.metrics["submission_cost"] for r in preparation_rows
            ),
            "preparation_holding_cost": sum(
                r.after.metrics["holding_loss"] for r in preparation_rows
            ),
            "preparation_local_prediction_calls": 18 * len(cases),
            "preparation_runtime_engine_calls": sum(
                store.get_run(r)["result"]["calls"] for r in preparatory
            ),
            "preparation_cpu_seconds": time.process_time() - cpu,
            "preparation_wall_seconds": time.perf_counter() - wall,
            "invalidation_tick": health.cutoff_tick,
        },
    )


def metrics(store, runs, prefix, target):
    all_runs = [*prefix, *runs]
    rows = [e for r in all_runs for e in store.read_events(r) if e["kind"] == "outcome"]
    obs = [e["data"]["observation"] for e in rows]
    selections = [
        e["data"] for r in runs for e in store.read_events(r) if e["kind"] == "cognitive_selection"
    ]
    results = [store.get_run(r)["result"] for r in runs]
    delivered = obs[-1]["state"]["payload"]["delivered"] if obs else 0
    holding = sum(o["metrics"]["holding_loss"] for o in obs)
    submission = sum(o["metrics"]["submission_cost"] for o in obs)
    probes = sum(o["metrics"]["probe_cost"] for o in obs)
    errors = []
    submitted, wasted = 0, 0
    for run in all_runs:
        events = store.read_events(run)
        outcome = next((e["data"]["observation"] for e in events if e["kind"] == "outcome"), None)
        if outcome is None:
            continue
        decision = next(
            e["data"]
            for e in events
            if e["kind"] == "decision" and e["data"].get("decision") == "execute"
        )
        action = decision["action"]
        amount = action["payload"].get("amount", 0)
        submitted += amount
        before = next(e["data"]["state"] for e in events if e["kind"] == "observed")
        wasted += int(amount > 0 and before["payload"]["delivered"] >= target)
    actions, goal_ticks = [], []
    for run in runs:
        events = store.read_events(run)
        selection = next((e["data"] for e in events if e["kind"] == "cognitive_selection"), None)
        outcome = next((e["data"]["observation"] for e in events if e["kind"] == "outcome"), None)
        if selection and outcome:
            actions.append(selection["selected"]["payload"]["amount"])
            predicted = next(
                c["prediction"]
                for c in selection["candidates"]
                if c["fingerprint"] == selection["selected_fingerprint"]
            )
            if selection["basis"] in {"learned_model", "fixed_prior"}:
                for metric in ("queue", "delivered"):
                    errors.append(
                        abs(predicted["vectors"][metric][0] - outcome["state"]["payload"][metric])
                    )
        if outcome and outcome["state"]["payload"]["delivered"] >= target:
            goal_ticks.append(outcome["state"]["payload"]["tick"])
    gate_rejected = sum(
        any(
            e["kind"] == "gate_preview" and e["data"]["gate"]["decision"] == "abstain"
            for e in store.read_events(r)
        )
        for r in runs
    )
    return {
        "goal_achieved": delivered >= target,
        "delivered": delivered,
        "holding_cost": holding,
        "submission_cost": submission,
        "probe_cost": probes,
        "reward": sum(o["metrics"]["reward"] for o in obs),
        "goal_utility": min(target, delivered) - holding - submission - probes,
        "unsafe": sum(o["unsafe"] for o in obs),
        "executed_actions": actions,
        "executed_ticks": len(obs),
        "preparation_ticks": len(prefix),
        "submitted_jobs": submitted,
        "excess_submitted_jobs": max(0, submitted - target),
        "submit_actions_after_goal": wasted,
        "runtime_execution_calls": sum(r["execution_calls"] for r in results),
        "preparation_engine_calls": sum(store.get_run(r)["result"]["calls"] for r in prefix),
        "external_api_calls": 0,
        "api_cost_usd": sum(r["cost_usd"] for r in results),
        "compute_dollar_cost": None,
        "goal_tick": min(goal_ticks) if goal_ticks else None,
        "abstain": sum(r["steps"] == 0 for r in results),
        "gate_rejected": gate_rejected,
        "learned_decisions": sum(s["basis"] == "learned_model" for s in selections),
        "fixed_prior_decisions": sum(s["basis"] == "fixed_prior" for s in selections),
        "fallback_decisions": sum(s["basis"] == "fallback" for s in selections),
        "decisions": len(selections),
        "local_prediction_calls": sum(s["local_prediction_calls"] for s in selections),
        "local_revalidation_calls": sum(s["local_revalidation_calls"] for s in selections),
        "local_total_prediction_calls": sum(s["local_total_prediction_calls"] for s in selections),
        "runtime_engine_calls": sum(r["calls"] for r in results),
        "runtime_agent_calls": sum(r["agent_calls"] for r in results),
        "one_tick_state_mae": fmean(errors) if errors else None,
        "forecast_coverage": sum(s["basis"] in {"learned_model", "fixed_prior"} for s in selections)
        / len(selections)
        if selections
        else 0,
        "selection_cpu_seconds": sum(s["cpu_seconds"] for s in selections),
        "selection_wall_seconds": sum(s["wall_seconds"] for s in selections),
        "unknown_rate": 1
        - sum(s["basis"] in {"learned_model", "fixed_prior"} for s in selections) / len(selections)
        if selections
        else 1,
        "escalations": sum(e["kind"] == "escalation" for r in runs for e in store.read_events(r)),
        "abstain_rate": sum(r["steps"] == 0 for r in results) / len(results) if results else 0,
        "fallback_rate": sum(s["basis"] == "fallback" for s in selections) / len(selections)
        if selections
        else 0,
        "first_model_tick": next(
            (s["state"]["payload"]["tick"] for s in selections if s["basis"] == "learned_model"),
            None,
        ),
    }


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
    *,
    lifecycle=None,
    evaluation=None,
):
    world = make_world(protocol, seed, settings)
    initial = await world.observe()
    guard = QueueTemporalGuard(
        store,
        world.task,
        model,
        training,
        episode_id=f"selection:{name}:{seed}:{mode}",
        initial=initial,
        config=DriftConfig(**protocol["detector"]),
    )
    preparation_cpu, preparation_wall = time.process_time(), time.perf_counter()
    preparation_health = [
        {
            "run_id": None,
            "state": initial.model_dump(),
            "health": asdict(await guard.health(initial, [])),
        }
    ]
    prefix = await execute_sequence(
        world, settings.get("warmup", protocol["warmup"]), store, artifacts, protocol["policy"]
    )
    for index, run_id in enumerate(prefix):
        # Runtime's saved final_state is its real post-receipt World.observe,
        # not the earlier receipt State with a rewritten timestamp.
        state = State.model_validate(store.get_run(run_id)["result"]["final_state"])
        preparation_health.append(
            {
                "run_id": run_id,
                "state": state.model_dump(),
                "health": asdict(await guard.health(state, prefix[: index + 1])),
            }
        )
    preparation_timing = {
        "preparation_cpu_seconds": time.process_time() - preparation_cpu,
        "preparation_wall_seconds": time.perf_counter() - preparation_wall,
    }
    setup_cpu, setup_wall = time.process_time(), time.perf_counter()
    promotion = None
    if lifecycle is not None and mode == "learned":
        promotion = await lifecycle.promote(
            evaluation,
            episode_id=guard._episode,
            task=world.task,
            initial=initial,
            state=await world.observe(),
            runs=prefix,
        )
        source = MonitoredQueueSource.promoted(lifecycle, promotion)
    else:
        source = MonitoredQueueSource(store, guard, training)
    planner = QueueModelPlanner(
        source,
        prefix_runs=prefix,
        mode=mode,
        prior=protocol["fixed_prior"] if mode == "prior" else None,
    )
    wrapper = QueueSelectionWorld(world, planner)
    agent = CognitiveAgent(
        store,
        artifacts,
        Registry(
            [
                InformationBoundVerifier(world.task),
                InformationBoundVerifier(world.task, future=True),
            ]
        ),
        planner,
        [Goal(name="Deliver", metric="delivered", target=protocol["target"])],
        Policy(**protocol["policy"]),
    )
    agent.memory.run_ids.extend(prefix)
    retrieves = 0
    retrieve = agent.memory.retrieve

    async def measured_retrieve(*args, **kwargs):
        nonlocal retrieves
        retrieves += 1
        return await retrieve(*args, **kwargs)

    agent.memory.retrieve = measured_retrieve
    setup_timing = {
        "setup_cpu_seconds": time.process_time() - setup_cpu,
        "setup_wall_seconds": time.perf_counter() - setup_wall,
        "setup_local_revalidation_calls": source.revalidation_calls if promotion else 0,
    }
    cpu, wall = time.process_time(), time.perf_counter()
    result = await agent.run(wrapper, protocol["episode_ticks"] - len(prefix))
    record = {
        "id": f"{name}:{seed}:{mode}",
        "environment": name,
        "seed": seed,
        "mode": mode,
        "settings": settings,
        "initial": initial.model_dump(),
        "task": world.task.model_dump(),
        "model_artifact": digest,
        "training": training,
        "episode_id": guard._episode,
        "prefix_runs": prefix,
        "preparation_health": preparation_health,
        "runs": result.run_ids,
        "final_observation": (await world.observe()).model_dump(),
        "promotion": promotion,
        "journal_run": lifecycle.journal_run if promotion else None,
        "metrics": {
            **metrics(store, result.run_ids, prefix, protocol["target"]),
            **preparation_timing,
            **setup_timing,
            "first_available_tick": next(
                (
                    p["state"]["payload"]["tick"]
                    for p in preparation_health
                    if p["health"]["status"] == "available"
                ),
                None,
            ),
            "cpu_seconds": time.process_time() - cpu,
            "wall_seconds": time.perf_counter() - wall,
            "total_episode_cpu_seconds": time.process_time() - preparation_cpu,
            "total_episode_wall_seconds": time.perf_counter() - preparation_wall,
            "observation_calls": wrapper.observation_calls,
            "memory_retrieval_calls": retrieves,
        },
    }
    return record


async def diagnostic(store, artifacts, protocol, models, seed, high):
    settings = {"high_first": False, "shift_tick": 5 if high else 48, "noise": 0}
    worlds = [make_world(protocol, seed, settings, target=999) for _ in range(3)]
    prefixes = [
        await execute_sequence(
            w, protocol["diagnostic_prefix"], store, artifacts, protocol["policy"]
        )
        for w in worlds
    ]
    state = await worlds[0].observe()
    comparisons = {}
    for mode in ("learned", "prior"):
        engine = (
            QueueTemporalEngine(worlds[0].task, models[high][0])
            if mode == "learned"
            else QueueTemporalEngine(worlds[0].task, prior=protocol["fixed_prior"])
        )
        comparison = await compare_actions(
            Registry([engine]), engine, state, [worlds[0].action(state, n) for n in (3, 1, 0)]
        )
        comparisons[mode] = {
            **comparison,
            "predictions": [p.model_dump() for p in comparison["predictions"]],
        }
    branches = []
    for w, n, prefix in zip(worlds, (3, 1, 0), prefixes):
        runs = await execute_sequence(w, [n, 0, 0], store, artifacts, protocol["policy"])
        rows = (
            await TransitionDataset(store, {f"diagnostic:{seed}:{high}:{n}": runs}).snapshot()
        ).transitions
        branches.append(
            {
                "amount": n,
                "prefix_runs": prefix,
                "runs": runs,
                "trace": [r.after.state.payload for r in rows],
            }
        )
    return {
        "seed": seed,
        "high": high,
        "state": state.model_dump(),
        "branches": branches,
        "comparisons": comparisons,
        "model_artifact": models[high][2],
        "training": models[high][1],
    }


async def benchmark(protocol_path, output, *, pilot=False):
    protocol = json.loads(Path(protocol_path).read_text())
    if output.exists():
        raise ValueError("Use a new output directory")
    output.mkdir(parents=True)
    store, artifacts = (
        Store("sqlite:///" + str(output / "ledger.db")),
        Artifacts(str(output / "artifacts")),
    )
    cpu, wall = time.process_time(), time.perf_counter()
    execution_source_hash = selection_source_hash()
    models = {h: await fit_model(store, artifacts, protocol, h) for h in (False, True)}
    cases = []
    seeds = protocol["pilot_seeds"] if pilot else protocol["evaluation_seeds"]
    for name, settings in protocol["environments"].items():
        model, training, digest = models[settings.get("training_high", settings["high_first"])]
        for seed in seeds:
            for mode in protocol["modes"]:
                cases.append(
                    await episode(
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
                    )
                )
                print(cases[-1]["id"], flush=True)
    recovery = []
    for direction in protocol["recovery_directions"]:
        for seed in protocol["pilot_seeds"] if pilot else protocol["recovery_seeds"]:
            parent, training, _ = models[direction == "high_to_low"]
            lifecycle, evaluation, model, new_training, digest, preparation = await recover_model(
                store, artifacts, protocol, parent, training, seed, direction
            )
            recovery.append({"direction": direction, "seed": seed, **preparation})
            settings = {"high_first": direction == "low_to_high", "shift_tick": 48, "noise": 0}
            for mode in protocol["modes"]:
                cases.append(
                    await episode(
                        store,
                        artifacts,
                        protocol,
                        model,
                        new_training,
                        digest,
                        "recovered_" + direction,
                        seed,
                        mode,
                        settings,
                        lifecycle=lifecycle,
                        evaluation=evaluation,
                    )
                )
                print(cases[-1]["id"], flush=True)
    diagnostics = [
        await diagnostic(store, artifacts, protocol, models, seed, high)
        for seed in (protocol["pilot_seeds"] if pilot else protocol["diagnostic_seeds"])
        for high in (False, True)
    ]
    from preact.core.models import Prediction

    diagnostics_scores = {}
    for high in (False, True):
        selected = [d for d in diagnostics if d["high"] == high]
        diagnostics_scores[str(high)] = {
            m: summarize(
                [
                    {
                        **d["comparisons"][m],
                        "predictions": [
                            Prediction.model_validate(p) for p in d["comparisons"][m]["predictions"]
                        ],
                    }
                    for d in selected
                ],
                [[b["trace"] for b in d["branches"]] for d in selected],
                3,
            )
            for m in ("learned", "prior")
        }
    groups = {}
    for case in cases:
        key = case["environment"] + "/" + case["mode"]
        groups.setdefault(key, []).append(case["metrics"])
    scores = {
        key: {
            metric: fmean(float(m[metric]) for m in values if m[metric] is not None)
            if any(m[metric] is not None for m in values)
            else None
            for metric in values[0]
            if metric != "executed_actions"
        }
        for key, values in groups.items()
    }
    paired = {}
    for name in {c["environment"] for c in cases}:
        by_seed = {}
        for c in cases:
            if c["environment"] == name:
                by_seed.setdefault(c["seed"], {})[c["mode"]] = c
        for baseline in protocol["modes"]:
            if baseline == "learned":
                continue
            differences, total = 0, 0
            for group in by_seed.values():
                for left, right in zip(
                    group["learned"]["metrics"]["executed_actions"],
                    group[baseline]["metrics"]["executed_actions"],
                ):
                    total += 1
                    differences += left != right
            paired[name + "/" + baseline] = {
                "different_executed_actions": differences,
                "paired_ticks": total,
                "rate": differences / total if total else 0,
                "scope": "same seed and ticks; states diverge after different actions",
            }
    semantic = [
        {"id": c["id"], "metrics": {k: v for k, v in c["metrics"].items() if "seconds" not in k}}
        for c in cases
    ]
    summary = {
        "protocol_hash": identity(protocol),
        "source_hash": execution_source_hash,
        "pilot": pilot,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "episodes": len(cases),
        "shared_parent_training": {
            "episodes": 2,
            "runtime_ticks": 2 * protocol["training_ticks"],
            "probe_reward_cost": 2 * protocol["training_ticks"] * protocol["probe_cost"],
            "scope": "formal parent training shared by paired benchmark; not included in closed-loop utility",
        },
        "recovery_preparation": [
            {
                k: v
                for k, v in r.items()
                if k not in {"source_runs", "origin", "candidate", "evaluation", "preparation_runs"}
            }
            for r in recovery
        ],
        "scores": scores,
        "paired_action_disagreement": paired,
        "diagnostics": diagnostics_scores,
        "total_cpu_seconds": time.process_time() - cpu,
        "total_wall_seconds": time.perf_counter() - wall,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
        "semantic_hash": identity(semantic),
        "limits": protocol["limits"],
    }
    for name, data in (
        ("cases", cases),
        ("diagnostics", diagnostics),
        ("recovery", recovery),
        ("summary", summary),
    ):
        (output / (name + ".json")).write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    store.db.dispose()
    if execution_source_hash != selection_source_hash():
        raise ValueError(
            "Collector/production source changed during evaluation; rerun in a new output"
        )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol", type=Path, default=ROOT / "benchmarks/queue-model-selection-v2.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()
    asyncio.run(benchmark(args.protocol, args.output, pilot=args.pilot))
