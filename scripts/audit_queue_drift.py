"""Independent receipt, Fisher probability, prediction suppression and score audit."""

import argparse
import asyncio
import hashlib
import json
import math
import random
from dataclasses import asdict
from pathlib import Path
from statistics import fmean

from scipy.stats import hypergeom

from preact.core.models import Action, State, Task, identity
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.drift import DriftConfig, ServiceSample, replay_health
from preact.learning.transitions import require_disjoint
from scripts.benchmark_queue_drift import semantic, source_hash


def check(condition, message):
    if not condition:
        raise ValueError(message)


def normalized_prediction(prediction):
    return json.loads(
        json.dumps({k: v for k, v in prediction.items() if k not in {"id", "latency_ms"}})
    )


def audit_fisher(history, config):
    for i, h in enumerate(history, 1):
        n, a, m, b = h["training_count"], h["training_high"], h["recent_count"], h["recent_high"]
        distribution = hypergeom(n + m, a + b, n)
        lo, hi = distribution.support()
        observed = distribution.pmf(a)
        p = sum(
            distribution.pmf(x)
            for x in range(int(lo), int(hi) + 1)
            if distribution.pmf(x) <= observed * (1 + 1e-12)
        )
        check(math.isclose(float(p), h["p_value"], abs_tol=1e-12), "Fisher probability changed")
        check(
            h["look"] == i and h["alpha"] == config.alpha / (i * (i + 1)),
            "Sequential spending changed",
        )
        check(math.isclose(h["effect"], abs(b / m - a / n)), "Effect changed")


async def audit(protocol_path: Path, output: Path):
    protocol = json.loads(protocol_path.read_text())
    summary = json.loads((output / "summary.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    cases = json.loads((output / "cases.json").read_text())
    digests = json.loads((output / "models.json").read_text())
    check(
        summary["protocol_sha256"] == hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "Protocol changed",
    )
    check(summary["source_sha256"] == source_hash(), "Source changed")
    expected = {
        (env, seed) for env in protocol["environments"] for seed in protocol["evaluation_seeds"]
    }
    check(
        {(c["environment"], c["seed"]) for c in cases} == expected and len(cases) == len(expected),
        "Cases missing or duplicated",
    )
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts, adapter = Artifacts(str(output / "artifacts")), QueueServiceAdapter()
    training = await TransitionDataset(store, manifest["training"]).snapshot()
    evaluation = await TransitionDataset(store, manifest["evaluation"]).snapshot()
    require_disjoint(training, evaluation)
    models = {}
    for regime, digest in digests.items():
        sources = {
            k: v for k, v in manifest["training"].items() if k.startswith(f"train:{regime}:")
        }
        model = DynamicsModel.load(artifacts, digest, adapter)
        refit = await TabularDynamicsTrainer().fit(
            TransitionDataset(store, sources), adapter, TrainingConfig(min_samples=4, seed=17)
        )
        check(
            model == refit and model.version == summary["models"][regime]["version"],
            "Training model mismatch",
        )
        check(
            model.dataset_hash == summary["models"][regime]["dataset_hash"]
            and digest == summary["models"][regime]["artifact_hash"],
            "Model provenance changed",
        )
        targets = [adapter.targets(r) for r in training.transitions if r.episode_id in sources]
        check(
            sum(t[0] for t in targets) == summary["models"][regime]["informative_count"]
            and sum(t[1] for t in targets) == summary["models"][regime]["high_count"]
            and len(targets) == summary["models"][regime]["transitions"],
            "Training effective count changed",
        )
        models[regime] = model
    config = DriftConfig(**protocol["detector"])
    errors = {env: {mode: [] for mode in ("fixed", "guarded")} for env in protocol["environments"]}
    phase_errors = {
        env: {mode: {phase: [] for phase in ("pre", "post")} for mode in ("fixed", "guarded")}
        for env in protocol["environments"]
    }
    common_errors = {env: [] for env in protocol["environments"]}
    support = {env: {mode: 0 for mode in ("fixed", "guarded")} for env in protocol["environments"]}
    counts = {env: 0 for env in protocol["environments"]}
    detected = {env: 0 for env in protocol["environments"]}
    suppressed = {env: 0 for env in protocol["environments"]}
    checks = 0
    for case in cases:
        env, regime, episode = case["environment"], case["regime"], case["episode"]
        check(case["runs"] == manifest["evaluation"][episode], "Episode manifest changed")
        sources = {
            k: v for k, v in manifest["training"].items() if k.startswith(f"train:{regime}:")
        }
        snapshot = await TransitionDataset(store, {episode: case["runs"]}).snapshot()
        check(
            case["trace"] == [r.after.state.payload for r in snapshot.transitions],
            "Actual trace forged",
        )
        check(len(snapshot.transitions) == protocol["evaluation_ticks"], "Execution budget changed")
        pattern = protocol["environments"][env]["actions"]
        world_spec = protocol["environments"][env]["world"]
        fixture = InformationQueueWorld(
            seed=case["seed"],
            ticks=protocol["evaluation_ticks"],
            capacity=12,
            target=999,
            **world_spec,
        )
        check(fixture.task.model_dump() == case["task"], "Task/fixture changed")
        check(fixture.payload == case["initial"]["payload"], "Initial fixture changed")
        # Evaluation oracle only: independently reproduce the owned seeded fixture.
        # This schedule is never supplied to a guard, learner or predictor.
        rng = random.Random(case["seed"])
        for i, row in enumerate(snapshot.transitions):
            high = (
                world_spec["high_first"]
                if i < world_spec["shift_tick"]
                else not world_spec["high_first"]
            )
            if rng.random() < world_spec["noise"]:
                high = not high
            expected_state, available, processed = transition(
                row.before.payload,
                0 if row.action.kind == "probe_service" else row.action.payload["amount"],
                3 if high else 1,
            )
            check(
                expected_state == row.after.state.payload
                and row.after.metrics["processed"] == processed
                and row.after.metrics["available_work"] == available,
                "Seeded fixture outcome mismatch",
            )
            if row.action.kind == "probe_service":
                check(
                    row.after.metrics["measured_service"] == (3 if high else 1),
                    "Seeded probe measurement mismatch",
                )
            expected_kind = "probe_service" if pattern[i % len(pattern)] == "probe" else "submit"
            check(row.action.kind == expected_kind, "Action sequence changed")
            if expected_kind == "submit":
                check(
                    row.action.payload == {"amount": pattern[i % len(pattern)]},
                    "Submission sequence changed",
                )
        samples = []
        for row in snapshot.transitions:
            t = adapter.targets(row)
            if t[0]:
                samples.append(
                    {
                        "receipt": row.receipt,
                        "reference": row.outcome_reference,
                        "tick": row.before.payload["tick"],
                        "high": bool(t[1]),
                        "source": "probe" if t[2] else "ordinary",
                    }
                )
        check(samples == case["samples"], "Monitoring sample labels changed")
        guard = QueueTemporalGuard(
            store,
            Task.model_validate(case["task"]),
            models[regime],
            sources,
            episode_id=episode,
            initial=State.model_validate(case["initial"]),
            config=config,
        )
        events = store.read_events(case["analysis_run"])
        stored = [e["data"] for e in events if e["kind"] == "monitored_comparison"]
        check(stored == case["forecasts"], "Prediction archive changed")
        expected_ticks = list(range(1, protocol["evaluation_ticks"] - 2, 4))
        check(
            [q["state"]["payload"]["tick"] for q in case["forecasts"]] == expected_ticks,
            "Cutoffs missing",
        )
        for q in case["forecasts"]:
            state, action = State.model_validate(q["state"]), Action.model_validate(q["action"])
            tick = state.payload["tick"]
            check(q["prefix_runs"] == case["runs"][:tick], "Future receipts in cutoff")
            check(
                action.fingerprint == snapshot.transitions[tick].action.fingerprint,
                "Scored action differs from executed action",
            )
            result = await guard.compare(state, [action], q["prefix_runs"])
            check(json.loads(json.dumps(result["health"])) == q["health"], "Health replay changed")
            check(
                result["health_version"] == q["health_version"]
                and result["engine_view_version"] == q["view_version"],
                "Health/view provenance changed",
            )
            check(
                normalized_prediction(result["predictions"][0].model_dump())
                == normalized_prediction(q["guarded"]),
                "Guarded forecast changed",
            )
            audit_fisher(q["health"]["history"], config)
            counts[env] += 1
            if q["health"]["status"] == "invalidated":
                check(
                    q["guarded"]["raw"]["status"] == "unknown" and not q["guarded"]["vectors"],
                    "Failed suppression",
                )
                suppressed[env] += 1
            for mode in ("fixed", "guarded"):
                p = q[mode]
                check(
                    p["evidence"] == "inference"
                    and not p["mandatory_checks"]
                    and not p["claim_results"]
                    and p["success"]["value"] is None
                    and p["risk"]["value"] is None,
                    "Inference promoted to safety evidence",
                )
                if p["raw"]["status"] == "estimated":
                    check(
                        p["raw"]["p_high"]
                        == summary["models"][regime]["high_count"]
                        / summary["models"][regime]["informative_count"],
                        "Fixed model distribution changed",
                    )
                    support[env][mode] += 1
                    # Independent moment integration from the declared p_high.
                    from preact.engines.queue_temporal import paths

                    branches = paths(state.payload, action.payload["amount"], 3, p["raw"]["p_high"])
                    check(branches == p["raw"]["branches"], "Branch weights changed")
                    for j in range(3):
                        for metric in ("queue", "delivered"):
                            expected = sum(b["weight"] * b[metric][j] for b in branches)
                            check(
                                math.isclose(expected, p["vectors"][metric][j]),
                                "Forecast moment changed",
                            )
                            error = abs(
                                expected
                                - snapshot.transitions[tick + j].after.state.payload[metric]
                            )
                            errors[env][mode].append(error)
                            phase = (
                                "post"
                                if tick >= protocol["environments"][env]["change_tick"]
                                else "pre"
                            )
                            phase_errors[env][mode][phase].append(error)
                            if mode == "fixed" and q["guarded"]["raw"]["status"] == "estimated":
                                common_errors[env].append(error)
            checks += 1
        final = await guard.health(State.model_validate(case["final_state"]), case["runs"])
        check(
            json.loads(json.dumps(asdict(final))) == case["final_health"],
            "Terminal history changed",
        )
        detected[env] += final.status == "invalidated"
        labels = [adapter.targets(r) for r in training.transitions if r.episode_id in sources]
        check(
            set(case["sensitivity"]) == {identity(setting) for setting in protocol["sensitivity"]},
            "Sensitivity conditions changed",
        )
        for setting in protocol["sensitivity"]:
            cfg = DriftConfig(
                **{
                    **protocol["detector"],
                    **{k: v for k, v in setting.items() if k != "training_transition_limit"},
                }
            )
            teachers = labels[: setting.get("training_transition_limit", len(labels))]
            h = replay_health(
                model_version=models[regime].version,
                dataset_hash=models[regime].dataset_hash,
                scope=episode,
                basis_hash=final.basis_hash,
                training_count=sum(int(t[0]) for t in teachers),
                training_high=sum(int(t[1]) for t in teachers),
                samples=tuple(ServiceSample(**x) for x in samples),
                cutoff_tick=protocol["evaluation_ticks"],
                config=cfg,
            )
            audit_fisher([asdict(c) for c in h.history], cfg)
            expected_sensitivity = {
                "setting": setting,
                "training_count": h.training_count,
                "training_high": h.training_high,
                "status": h.status,
                "first_invalid": next(
                    (asdict(c) for c in h.history if c.status == "invalidated"), None
                ),
            }
            check(
                json.loads(json.dumps(expected_sensitivity))
                == case["sensitivity"][identity(setting)],
                "Sensitivity verdict changed",
            )
    for env in protocol["environments"]:
        s = summary["scores"][env]
        check(
            s["detected"] == detected[env] and s["suppressed_predictions"] == suppressed[env],
            "Detection/suppression scores changed",
        )
        selected = [c for c in cases if c["environment"] == env]
        check(
            s["episodes"] == len(selected) and s["detection_rate"] == detected[env] / len(selected),
            "Detection rate changed",
        )
        stable = protocol["environments"][env]["stable"]
        check(
            s["false_alarm_rate"] == (detected[env] / len(selected) if stable else None),
            "False alarm rate changed",
        )
        check(
            s["missed_rate"] == (1 - detected[env] / len(selected) if not stable else None),
            "Missed rate changed",
        )
        delays, sample_delays, first_cuts = [], [], []
        for c in selected:
            first = next(
                (h for h in c["final_health"]["history"] if h["status"] == "invalidated"), None
            )
            if first:
                first_cuts.append(
                    next(
                        (
                            q["state"]["payload"]["tick"]
                            for q in c["forecasts"]
                            if q["health"]["status"] == "invalidated"
                        ),
                        None,
                    )
                )
                if not stable:
                    change_tick = protocol["environments"][env]["change_tick"]
                    delays.append(max(0, first["tick"] + 1 - change_tick))
                    sample_delays.append(
                        first["effective_count"]
                        - sum(t["tick"] < change_tick for t in c["samples"])
                    )
        check(
            s["delay_ticks"] == delays
            and s["delay_effective_samples"] == sample_delays
            and s["first_suppressed_cutoffs"] == first_cuts,
            "Detection/application delay changed",
        )
        check(
            s["fixed_mae_on_guarded_support"]
            == (fmean(common_errors[env]) if common_errors[env] else None),
            "Common-support MAE changed",
        )
        for mode in ("fixed", "guarded"):
            scored = s["scores"][mode]
            check(
                scored["predictions"] == counts[env]
                and scored["supported"] == support[env][mode]
                and scored["coverage"] == support[env][mode] / counts[env],
                "Coverage changed",
            )
            value = fmean(errors[env][mode]) if errors[env][mode] else None
            check(scored["state_mae"] == value, "MAE changed")
            check(
                scored["unknown_rate"] == 1 - support[env][mode] / counts[env],
                "Unknown rate changed",
            )
            for phase in ("pre", "post"):
                values = phase_errors[env][mode][phase]
                check(
                    scored[f"{phase}_change_mae"] == (fmean(values) if values else None),
                    "Phase MAE changed",
                )
        check(
            s["scores"]["all_unknown"]
            == {
                "predictions": counts[env],
                "supported": 0,
                "coverage": 0.0,
                "unknown_rate": 1.0,
                "state_mae": None,
                "pre_change_mae": None,
                "post_change_mae": None,
            },
            "All-unknown baseline changed",
        )
        expected_sensitivity = []
        for setting in protocol["sensitivity"]:
            key = identity(setting)
            expected_sensitivity.append(
                {
                    "setting": setting,
                    "episodes": len(selected),
                    "detected": sum(
                        c["sensitivity"][key]["status"] == "invalidated" for c in selected
                    ),
                    "first_invalid_ticks": [
                        c["sensitivity"][key]["first_invalid"]["tick"] + 1
                        for c in selected
                        if c["sensitivity"][key]["first_invalid"] is not None
                    ],
                    "training_counts": [c["sensitivity"][key]["training_count"] for c in selected],
                }
            )
        check(summary["sensitivity"][env] == expected_sensitivity, "Sensitivity summary changed")
    check(
        summary["unsafe"] == sum(r.after.unsafe for r in evaluation.transitions) == 0,
        "Unsafe results changed",
    )
    check(
        summary["execution_transitions"] == len(evaluation.transitions), "Execution count changed"
    )
    check(
        summary["engine_calls"]
        == sum(
            e["kind"] == "prediction"
            for runs in manifest["evaluation"].values()
            for run in runs
            for e in store.read_events(run)
        ),
        "Engine calls changed",
    )
    check(summary["semantic_hash"] == semantic(cases), "Semantic hash changed")
    result = {
        "passed": True,
        "comparisons": checks,
        "validated_transitions": len(training.transitions) + len(evaluation.transitions),
        "fisher_independently_checked": True,
        "suppression_checked": True,
        "scores_independently_checked": True,
        "timings_certified": False,
    }
    store.db.dispose()
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(audit(args.protocol.resolve(), args.output.resolve())), indent=2))
