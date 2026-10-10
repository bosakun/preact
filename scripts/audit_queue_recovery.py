"""Revalidate recovery authority, fixture traces, independent scores and durable promotion."""

import argparse
import asyncio
import itertools
import json
from dataclasses import asdict
from pathlib import Path
from statistics import fmean

from preact.core.models import State, Task, identity
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import DynamicsModel, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import (
    CandidateEvaluation,
    ForecastRecord,
)
from preact.learning.transitions import require_disjoint
from scripts.audit_queue_drift import audit_fisher
from scripts.benchmark_queue_recovery import semantic, world


def check(condition, message):
    if not condition:
        raise ValueError(message)


async def verify_trace(store, episodes, fixture):
    """Fixture oracle is confined to this auditor, never the learner or lifecycle."""
    snapshot = await TransitionDataset(store, episodes).snapshot()
    check(not snapshot.unlabelled_receipts, "Unlabelled audit execution")
    for row in snapshot.transitions:
        before = await fixture.observe()
        check(before.payload == row.before.payload, "Fixture input state mismatch")
        check(before.provenance == row.before.provenance, "Fixture provenance mismatch")
        outcome = await fixture.execute(row.action, row.receipt)
        check(outcome.state.payload == row.after.state.payload, "Fixture outcome state mismatch")
        check(
            outcome.metrics == row.after.metrics and outcome.checks == row.after.checks,
            "Fixture outcome metrics mismatch",
        )
        check(outcome.unsafe == row.after.unsafe and not outcome.unsafe, "Unsafe audit outcome")
    return snapshot


def independent_scores(comparisons, actual):
    state_errors, difference_errors, supported = [], [], 0
    for comparison, truth in zip(comparisons, actual, strict=True):
        if comparison["status"] != "estimated":
            continue
        supported += 1
        for a in range(3):
            for t in range(3):
                for metric in ("queue", "delivered"):
                    state_errors.append(
                        abs(
                            comparison["predictions"][a]["vectors"][metric][t] - truth[a][t][metric]
                        )
                    )
        for a, b in itertools.combinations(range(3), 2):
            for t in range(3):
                for metric in ("queue", "delivered"):
                    predicted = (
                        comparison["predictions"][a]["vectors"][metric][t]
                        - comparison["predictions"][b]["vectors"][metric][t]
                    )
                    difference_errors.append(
                        abs(predicted - (truth[a][t][metric] - truth[b][t][metric]))
                    )
    n = len(comparisons)
    return {
        "comparisons": n,
        "supported": supported,
        "coverage": supported / n,
        "unknown_rate": 1 - supported / n,
        "state_mae": fmean(state_errors) if state_errors else None,
        "difference_mae": fmean(difference_errors) if difference_errors else None,
    }


def verify_distribution(comparison, state, probability):
    check(comparison["status"] == "estimated", "Unexpected unsupported evaluation")
    for amount, forecast in zip((3, 1, 0), comparison["predictions"], strict=True):
        check(forecast["evidence"] == "inference", "Forecast gained observation authority")
        check(
            not forecast["mandatory_checks"]
            and forecast["success"]["value"] is None
            and forecast["risk"]["value"] is None,
            "Forecast gained safety authority",
        )
        for metric in ("queue", "delivered"):
            expected = [0.0, 0.0, 0.0]
            for service_path in itertools.product((1, 3), repeat=3):
                weight = 1.0
                payload = state.payload
                for t, service in enumerate(service_path):
                    weight *= probability if service == 3 else 1 - probability
                for t, service in enumerate(service_path):
                    payload, _, _ = transition(payload, amount if t == 0 else 0, service)
                    expected[t] += weight * payload[metric]
            check(
                all(
                    abs(a - b) < 1e-10
                    for a, b in zip(expected, forecast["vectors"][metric], strict=True)
                ),
                "Independent distribution forecast mismatch",
            )


async def audit(protocol_path: Path, output: Path):
    protocol = json.loads(protocol_path.read_text())
    summary = json.loads((output / "summary.json").read_text())
    check(summary["protocol_hash"] == identity(protocol), "Protocol changed")
    seeds = protocol["pilot_seeds"] if summary["pilot"] else protocol["evaluation_seeds"]
    names = (
        ("low_to_high", "high_to_low", "insufficient_training")
        if summary["pilot"]
        else protocol["conditions"]
    )
    expected = {(name, seed) for name in names for seed in seeds}
    records, transitions, promoted = [], 0, 0
    for name, seed in sorted(expected):
        directory = output / f"{name}-{seed}"
        c = json.loads((directory / "case.json").read_text())
        spec = protocol["conditions"][name]
        store = Store("sqlite:///" + str(directory / "ledger.db"))
        artifacts = Artifacts(str(directory / "artifacts"))
        lifecycle = QueueModelRecovery(store, artifacts, c["journal_run"])
        parent = DynamicsModel.load(artifacts, c["parent_artifact"], QueueServiceAdapter())
        training = await verify_trace(
            store,
            c["training"],
            world(
                protocol,
                protocol["parent_training_seeds"][int(spec["parent_high"])],
                high=spec["parent_high"],
            ),
        )
        source = await verify_trace(
            store,
            {"source": c["source_runs"]},
            world(
                protocol,
                seed,
                high=spec["parent_high"],
                shift=protocol["shift_tick"] if spec["shift"] else None,
                noise=spec["noise"],
            ),
        )
        require_disjoint(training, source)
        transitions += len(training.transitions) + len(source.transitions)
        role_snapshots = {"parent": training, "source": source}
        parent_guard = QueueTemporalGuard(
            store,
            Task.model_validate(c["source_task"]),
            parent,
            c["training"],
            episode_id="source",
            initial=State.model_validate(c["source_initial"]),
            config=DriftConfig(**protocol["detector"]),
        )
        for phase, expected_status in (
            ("parent_forecast_before", "estimated"),
            ("parent_forecast_after", "unknown"),
        ):
            if phase not in c:
                continue
            original = c[phase]
            observed_state = State.model_validate(original["state"])
            actual_comparison = await parent_guard.compare(
                observed_state,
                [InformationQueueWorld.action(observed_state, n) for n in (3, 1, 0)],
                original["runs"],
            )
            check(
                actual_comparison["status"] == original["result"]["status"] == expected_status,
                "Parent prediction lifecycle mismatch",
            )
            check(
                [p.vectors for p in actual_comparison["predictions"]]
                == [p["vectors"] for p in original["result"]["predictions"]],
                "Parent forecast changed",
            )
        check(
            c["parent_prediction_stopped"] == ("parent_forecast_after" in c),
            "Parent suppression omitted",
        )
        if c["status"] == "not_invalidated":
            guard = QueueTemporalGuard(
                store,
                Task.model_validate(c["source_task"]),
                parent,
                c["training"],
                episode_id="source",
                initial=State.model_validate(c["source_initial"]),
                config=DriftConfig(**protocol["detector"]),
            )
            health = await guard.health(State.model_validate(c["source_cutoff"]), c["source_runs"])
            check(
                health.status != "invalidated" and not c["promotion"],
                "Stable model incorrectly promoted",
            )
        else:
            origin = await lifecycle._origin(c["origin"])
            health = await lifecycle._guard(origin).health(origin.cutoff, origin.runs)
            audit_fisher([asdict(h) for h in health.history], DriftConfig(**origin.config))
            first = next(h for h in health.history if h.status == "invalidated")
            check(first.tick + 1 == c["invalidation_tick"], "First invalidation cutoff mismatch")
            check(c["source_runs"][: len(origin.runs)] == origin.runs, "Source prefix changed")
            if "candidate" not in c:
                _, suffix = await lifecycle._training(
                    origin, State.model_validate(c["source_cutoff"]), c["source_runs"]
                )
                count = sum(int(QueueServiceAdapter().targets(r)[0]) for r in suffix.transitions)
                check(
                    count < 16 and c["rejection"] == "insufficient_training",
                    "Invalid insufficient-training rejection",
                )
            else:
                candidate, _, model, b_training = await lifecycle._candidate(c["candidate"])
                check(
                    set(model.receipts).isdisjoint(parent.receipts),
                    "Old training leaked into candidate",
                )
                check(
                    all(
                        r.before.payload["tick"] >= origin.cutoff.payload["tick"]
                        for r in b_training.transitions
                    ),
                    "Pre-invalidation teacher",
                )
                report = lifecycle._load(c["evaluation"], CandidateEvaluation)
                recomputed, evaluation = await lifecycle._evaluate(
                    c["candidate"], report.cases, report.cutoff
                )
                role_snapshots["evaluation"] = evaluation
                check(recomputed == report, "Evaluation record mismatch")
                probabilities = {}
                for key, m, snapshot in (
                    ("candidate", model, b_training),
                    ("parent", parent, training),
                ):
                    labels = [QueueServiceAdapter().targets(r) for r in snapshot.transitions]
                    probabilities[key] = sum(x[1] for x in labels) / sum(x[0] for x in labels)
                probabilities["prior"] = 0.5
                actual, comparisons = [], {k: [] for k in probabilities}
                independent_count = 0
                check(
                    {(d["seed"], d["label"]) for d in c["evaluation_details"]}
                    == {
                        (seed_, label)
                        for seed_ in protocol["comparison_seeds"]
                        for label in protocol["workloads"]
                    },
                    "Evaluation conditions selected or omitted",
                )
                for case, detail in zip(report.cases, c["evaluation_details"], strict=True):
                    forecast = lifecycle._load(case.forecast_artifact, ForecastRecord)
                    truths = []
                    for branch in case.branches:
                        prefix = protocol["workloads"][detail["label"]]
                        fixture = world(
                            protocol,
                            detail["seed"],
                            high=False,
                            shift=len(prefix) if detail["high"] else None,
                            noise=detail["noise"],
                        )
                        snapshot = await verify_trace(
                            store, {branch.episode_id: branch.runs}, fixture
                        )
                        truths.append([r.after.state.payload for r in snapshot.transitions[-3:]])
                        independent_count += sum(
                            int(QueueServiceAdapter().targets(r)[0])
                            for r in snapshot.transitions[-3:]
                        )
                    actual.append(truths)
                    for key, p in probabilities.items():
                        verify_distribution(forecast.forecasts[key], forecast.state, p)
                        comparisons[key].append(forecast.forecasts[key])
                independent = {k: independent_scores(v, actual) for k, v in comparisons.items()}
                check(
                    independent_count == report.effective_count,
                    "Effective evaluation count includes preparation or changed",
                )
                check(
                    independent == report.scores == c["scores"], "Independent MAE/coverage mismatch"
                )
                # Independently enforce the agreed numeric quality policy.
                b, a, prior = (independent[k] for k in ("candidate", "parent", "prior"))
                numeric_pass = (
                    b["coverage"] == 1
                    and a["state_mae"] > 0
                    and b["state_mae"] <= 0.5
                    and b["difference_mae"] <= 0.5
                    and b["state_mae"] <= a["state_mae"] * 0.9
                    and b["difference_mae"] <= a["difference_mae"] + 0.05
                    and all(b[k] <= prior[k] + 0.05 for k in ("state_mae", "difference_mae"))
                )
                check(
                    not report.passed
                    or (numeric_pass and report.effective_count >= 16 and len(report.cases) >= 8),
                    "Unqualified evaluation passed",
                )
                transitions += len(evaluation.transitions)
                if c["monitoring"]:
                    role_snapshots["monitoring"] = await TransitionDataset(
                        store, {m["episode_id"]: m["runs"] for m in c["monitoring"]}
                    ).snapshot()
                for monitor in c["monitoring"]:
                    snapshot = await verify_trace(
                        store,
                        {monitor["episode_id"]: monitor["runs"]},
                        world(
                            protocol, seed + 1000, high=not spec["parent_high"], noise=spec["noise"]
                        ),
                    )
                    require_disjoint(snapshot, evaluation)
                    require_disjoint(snapshot, b_training)
                    require_disjoint(snapshot, source)
                    transitions += len(snapshot.transitions)
                if c["status"] == "promoted":
                    promoted += 1
                    monitor = c["monitoring"][0]
                    rows = (
                        await TransitionDataset(
                            store, {monitor["episode_id"]: monitor["runs"]}
                        ).snapshot()
                    ).transitions
                    fresh = rows[-1].after.state.model_copy(
                        update={"timestamp": c["source_cutoff"]["timestamp"]}
                    )
                    check(
                        await lifecycle.restore(fresh, monitor["runs"]) == c["promotion"],
                        "Promotion restore mismatch",
                    )
                    stored = c["post_comparison"]["result"]
                    check(
                        stored["health"]["status"] == "available"
                        and stored["status"] == "estimated",
                        "Recovery did not produce available prediction",
                    )
                    check(
                        stored["promotion"]["model_version"] == candidate.model_version,
                        "Old model used after promotion",
                    )
                    verify_distribution(
                        stored,
                        State.model_validate(c["post_comparison"]["state"]),
                        probabilities["candidate"],
                    )
                    post_truth = []
                    for m in c["monitoring"]:
                        s = await TransitionDataset(store, {m["episode_id"]: m["runs"]}).snapshot()
                        post_truth.append([r.after.state.payload for r in s.transitions[-3:]])
                    check(
                        independent_scores([stored], [post_truth]) == c["post_scores"]["candidate"],
                        "Post-promotion score mismatch",
                    )
                else:
                    check(
                        not any(
                            e["kind"] == "model_promotion_committed"
                            for e in await lifecycle._journal()
                        ),
                        "Rejected model became active",
                    )
        actual_semantics = {
            role: [
                {
                    "episode": r.episode_id,
                    "kind": r.action.kind,
                    "action": r.action.payload,
                    "before": r.before.payload,
                    "after": r.after.state.payload,
                    "metrics": r.after.metrics,
                    "checks": r.after.checks,
                    "unsafe": r.after.unsafe,
                }
                for r in snapshot.transitions
            ]
            for role, snapshot in role_snapshots.items()
        }
        check(
            identity(actual_semantics) == c["actual_semantic_hash"],
            "Actual execution semantics changed",
        )
        check(
            sum(len(s.transitions) for s in role_snapshots.values()) == c["executed_actions"],
            "Execution count changed",
        )
        check(c["unsafe_outcomes"] == 0, "Unsafe outcome omitted")
        all_runs = list(
            dict.fromkeys(
                r for s in role_snapshots.values() for runs in s.episodes.values() for r in runs
            )
        )
        calls = 0
        for r in all_runs:
            calls += sum(e["kind"] == "prediction" for e in await store.call("read_events", r))
        check(calls == c["runtime_engine_calls"], "Runtime engine calls changed")
        records.append(c)
    ordered = [
        next(c for c in records if (c["condition"], c["seed"]) == (name, seed))
        for name in names
        for seed in seeds
    ]
    check(semantic(ordered) == summary["semantic_hash"], "Semantic summary mismatch")
    check(
        {(c["condition"], c["seed"]) for c in summary["cases"]} == expected, "Summary cases omitted"
    )
    for raw, brief in zip(ordered, summary["cases"], strict=True):
        check(all(brief[k] == raw.get(k) for k in brief), "Summary aggregate changed")
    result = {
        "status": "passed",
        "cases": len(records),
        "promoted": promoted,
        "validated_transitions": transitions,
        "semantic_hash": summary["semantic_hash"],
        "scope": "Receipt/fixture/time/source validation, independent MAE/distribution and promotion replay; timing is not independently certified",
    }
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(audit(args.protocol, args.output))))
