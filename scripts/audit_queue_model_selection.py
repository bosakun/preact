"""Independent selection-to-receipt audit; no saved ranking grants execution authority."""

import argparse
import asyncio
import hashlib
import itertools
import json
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import fmean

from preact.cognition import EpisodicMemory, Goal
from preact.cognition.information import InformationQueuePlanner
from preact.core.decision import evaluate, gate
from preact.core.models import Action, Policy, State, Task, TreeNode, identity
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld, bounded_safe
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import (
    CandidateEvaluation,
    CandidateModel,
    ForecastRecord,
    ModelPromotion,
    RecoveryOrigin,
    RecoveryRejected,
)
from preact.learning.transitions import require_disjoint
from scripts.audit_queue_drift import audit_fisher
from scripts.audit_queue_recovery import (
    check,
    verify_distribution,
    verify_final_observation,
    verify_trace,
)

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.loads(json.dumps(value, allow_nan=False))


def same(left, right):
    return left.model_dump(exclude={"timestamp"}) == right.model_dump(exclude={"timestamp"})


def expected_value(state, amount, probability, target):
    value = 0.0
    for services in itertools.product((1, 3), repeat=3):
        weight = math.prod(probability if s == 3 else 1 - probability for s in services)
        payload, holding = state.payload, 0
        for t, service in enumerate(services):
            payload, _, _ = transition(payload, amount if t == 0 else 0, service)
            holding += payload["queue"]
        value += weight * (
            min(
                max(0, target - state.payload["delivered"]),
                payload["delivered"] - state.payload["delivered"],
            )
            - state.payload["holding_cost"] * holding
            - state.payload["submission_cost"] * amount
        )
    return value


async def audit_case(store, artifacts, protocol, case):
    model = DynamicsModel.load(artifacts, case["model_artifact"], QueueServiceAdapter())
    training = await TransitionDataset(store, case["training"]).snapshot()
    refit = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, case["training"]),
        QueueServiceAdapter(),
        TrainingConfig(min_samples=model.min_samples, seed=model.seed),
        limit=len(model.receipts),
    )
    check(refit == model, "Model does not match receipt-backed training")
    settings = case["settings"]
    fixture = InformationQueueWorld(
        seed=case["seed"],
        ticks=protocol["episode_ticks"],
        target=protocol["target"],
        capacity=protocol["capacity"],
        holding_cost=protocol["holding_cost"],
        submission_cost=protocol["submission_cost"],
        probe_cost=protocol["probe_cost"],
        **{k: settings[k] for k in ("high_first", "shift_tick", "noise")},
    )
    initial, task = State.model_validate(case["initial"]), Task.model_validate(case["task"])
    check(
        initial.payload == (await fixture.observe()).payload and initial.payload["tick"] == 0,
        "False episode initial state",
    )
    check(
        identity(fixture.task.model_dump()) == identity(case["task"]),
        "Owned World task representation changed",
    )
    # Preserve the actual Runtime task representation (including integer stakes).
    task = fixture.task.model_copy(deep=True)
    all_runs = case["prefix_runs"] + case["runs"]
    snapshot = await verify_trace(store, {case["episode_id"]: all_runs}, fixture)
    require_disjoint(training, snapshot)
    await verify_final_observation(
        store,
        snapshot,
        State.model_validate(case["final_observation"]),
        case["final_observation"]["timestamp"],
    )
    guard = QueueTemporalGuard(
        store,
        task,
        model,
        case["training"],
        episode_id=case["episode_id"],
        initial=initial,
        config=DriftConfig(**protocol["detector"]),
    )
    preparations = case["preparation_health"]
    check(
        len(preparations) == len(case["prefix_runs"]) + 1
        and preparations[0]["run_id"] is None
        and preparations[0]["state"] == case["initial"],
        "Preparation health history changed",
    )
    for i, entry in enumerate(preparations):
        if i:
            run_id = case["prefix_runs"][i - 1]
            check(
                entry["run_id"] == run_id
                and entry["state"] == store.get_run(run_id)["result"]["final_state"],
                "Preparation observation is not the real Runtime final observation",
            )
            events = store.read_events(run_id)
            finished = [e for e in events if e["kind"] == "finished"]
            check(
                len(finished) == 1 and finished[0]["data"]["final_state"] == entry["state"],
                "Preparation final observation record changed",
            )
            outcome = next(e for e in events if e["kind"] == "outcome")
            check(
                datetime.fromisoformat(outcome["timestamp"])
                < datetime.fromisoformat(entry["state"]["timestamp"]),
                "Preparation observation predates receipt outcome commit",
            )
        verdict = await guard.health(State.model_validate(entry["state"]), case["prefix_runs"][:i])
        check(
            canonical(asdict(verdict)) == canonical(entry["health"]),
            "Preparation health replay changed",
        )
    recovered = (
        QueueModelRecovery(store, artifacts, case["journal_run"]) if case["promotion"] else None
    )
    dependencies = [r for runs in case["training"].values() for r in runs]
    if case["promotion"]:

        def load(digest, kind):
            return kind.model_validate(json.loads(artifacts.read(digest)))

        promotion = load(case["promotion"], ModelPromotion)
        candidate = load(promotion.candidate_artifact, CandidateModel)
        origin = load(candidate.origin_artifact, RecoveryOrigin)
        report = load(promotion.evaluation_artifact, CandidateEvaluation)
        check(
            candidate.training == case["training"]
            and candidate.model_artifact == case["model_artifact"],
            "Promoted model/train source mismatch",
        )
        dependencies = list(
            dict.fromkeys(
                [
                    case["journal_run"],
                    *[r for runs in origin.training.values() for r in runs],
                    *candidate.source_runs,
                    *[r for c in report.cases for b in c.branches for r in b.runs],
                ]
            )
        )
    selections, actions, errors, results = [], [], [], []
    deactivation = None
    for index, run_id in enumerate(case["runs"]):
        run = store.get_run(run_id)
        check(
            run["config"]["policy"] == protocol["policy"]
            or all(run["config"]["policy"].get(k) == v for k, v in protocol["policy"].items()),
            "Runtime policy changed",
        )
        events = store.read_events(run_id)
        selection_events = [e for e in events if e["kind"] == "cognitive_selection"]
        check(len(selection_events) == 1, "Missing/duplicate selection")
        event = selection_events[0]
        record = event["data"]
        state = State.model_validate(record["state"])
        check(
            record["run_id"] == run_id and record["mode"] == case["mode"],
            "Selection run/mode mismatch",
        )
        check(
            record["monitor_runs"] == case["prefix_runs"] + case["runs"][:index],
            "Selection manifest chronology changed",
        )
        check(record["goals"] == run["config"]["cognitive_goals"], "Selection goal changed")
        check(
            record["evaluation_function"] == "capped-progress-holding-root-cost/v1"
            and record["horizon"] == 3,
            "Selection function/horizon changed",
        )
        check(
            len(record["goals"]) == 1 and record["goals"][0]["target"] == protocol["target"],
            "Goal differs from protocol",
        )
        observed = [e for e in events if e["kind"] == "observed" and e["seq"] < event["seq"]]
        check(
            observed and observed[-1]["data"]["state"] == record["state"],
            "Selection does not use Runtime authoritative observation",
        )
        check(
            datetime.fromisoformat(state.timestamp) <= datetime.fromisoformat(event["timestamp"]),
            "Prediction cutoff exceeds selection event",
        )
        expected_sources = list(dict.fromkeys([*dependencies, *record["monitor_runs"]]))
        check(
            set(record["source_runs"]) == set(expected_sources)
            and len(record["source_runs"]) == len(expected_sources),
            "Incomplete selection source dependencies",
        )
        current_heads = store.run_heads(record["source_runs"])
        check(
            {r: asdict(h) for r, h in current_heads.items()} == record["source_heads"],
            "Selection authority fence mismatch",
        )
        health = await guard.health(state, record["monitor_runs"])
        if record["health"] is not None:
            check(canonical(asdict(health)) == record["health"], "Health basis changed")
        audit_fisher(canonical(asdict(health))["history"], DriftConfig(**protocol["detector"]))
        if recovered:
            if record["reason"] == "not_promoted":
                check(
                    deactivation is not None
                    and record["statistical_deactivation"] == deactivation
                    and record["basis"] == "fallback",
                    "Inactive recovery fallback has no validated statistical history",
                )
            # Revalidation is explicit; a saved promotion alone does not activate a forecast.
            try:
                restored = await recovered.restore(state, record["monitor_runs"])
                check(restored == case["promotion"], "Promotion mismatch")
            except RecoveryRejected as exc:
                check(
                    health.status != "available"
                    and record["basis"] == "fallback"
                    and str(exc) == "monitor_health_" + health.status,
                    "Unexpected promotion restore refusal",
                )
                if record["reason"] != "not_promoted":
                    check(record["reason"] == str(exc), "Recovery rejection reason changed")
                    deactivation = str(exc)
            check(
                record["statistical_deactivation"] == deactivation,
                "Recovery deactivation history changed",
            )
        candidates = record["candidates"]
        replay_count = (
            9 * len(report.cases) if recovered and record["reason"] != "not_promoted" else 0
        )
        local_count = sum(c["prediction"] is not None for c in candidates)
        usage = [e["data"] for e in events if e["kind"] == "agent_usage"]
        check(
            record["local_prediction_calls"] == local_count
            and record["local_revalidation_calls"] == replay_count
            and record["local_total_prediction_calls"] == local_count + replay_count
            and len(usage) == local_count + replay_count
            and run["result"]["agent_calls"] == len(usage),
            "Local selection/revalidation call accounting changed",
        )
        check(
            all(
                u["cost_known"] and u["cost_usd"] == 0 and u["external_api_calls"] == 0
                for u in usage
            )
            and sum(u["operation_kind"] == "local_promotion_revalidation" for u in usage)
            == replay_count,
            "Local calls forged external usage or omitted recovery replay",
        )
        if replay_count:
            expected_usage = [
                (
                    c.forecast_artifact,
                    p["engine_id"],
                    p["engine_version"],
                    p["state_id"],
                    p["action_ids"],
                )
                for c in report.cases
                for comparison in load(c.forecast_artifact, ForecastRecord).forecasts.values()
                for p in comparison["predictions"]
            ]
            check(
                [
                    (
                        u["forecast_artifact"],
                        u["engine_id"],
                        u["engine_version"],
                        u["state_id"],
                        u["action_ids"],
                    )
                    for u in usage
                    if u["operation_kind"] == "local_promotion_revalidation"
                ]
                == expected_usage,
                "Promotion replay usage provenance changed",
            )
        check([c["amount"] for c in candidates] == [3, 1, 0], "Candidate set changed")
        for c in candidates:
            action = Action.model_validate(c["action"])
            check(
                action.fingerprint == c["fingerprint"] and action.state_id == state.id,
                "Candidate fingerprint/state mismatch",
            )
            check(
                c["public_bound_safe"] == bounded_safe(state.payload, c["amount"]),
                "Public filter changed",
            )
        if record["basis"] in {"learned_model", "fixed_prior"}:
            check(
                health.status == "available"
                and all(c["prediction"] is not None for c in candidates),
                "Model selected without available health/all forecasts",
            )
            probability = (
                protocol["fixed_prior"]
                if record["basis"] == "fixed_prior"
                else health.training_high / health.training_count
            )
            comparison = {
                "status": "estimated",
                "predictions": [c["prediction"] for c in candidates],
            }
            verify_distribution(comparison, state, probability)
            for c in candidates:
                p = c["prediction"]
                action = Action.model_validate(c["action"])
                check(
                    p["state_id"] == state.id
                    and p["action_ids"] == [action.id]
                    and p["horizon"] == 3,
                    "Forecast binding mismatch",
                )
                check(p["raw"]["p_high"] == probability, "Model probability changed")
                check(
                    math.isclose(
                        c["value"],
                        expected_value(state, c["amount"], probability, protocol["target"]),
                        abs_tol=1e-10,
                    ),
                    "Candidate score changed",
                )
                if record["basis"] == "learned_model":
                    check(
                        p["raw"]["base_model_version"] == model.version
                        and p["raw"]["training_dataset_hash"] == model.dataset_hash,
                        "Model provenance mismatch",
                    )
                    check(
                        p["engine_version"] == record["engine_view_version"]
                        and p["raw"]["health_version"] == health.version,
                        "Health/Engine view mismatch",
                    )
            ranked = sorted(
                [c for c in candidates if c["public_bound_safe"]],
                key=lambda c: (-c["value"], c["amount"]),
            )
            check(
                record["ranked_fingerprints"] == [c["fingerprint"] for c in ranked],
                "Ranking changed",
            )
            chosen = Action.model_validate(ranked[0]["action"]) if ranked else None
        elif case["mode"] == "fixed":
            ranked = sorted(
                [c for c in candidates if c["public_bound_safe"]],
                key=lambda c: (c["amount"] != 1, c["amount"]),
            )
            chosen = Action.model_validate(ranked[0]["action"]) if ranked else None
        else:
            memory = EpisodicMemory(store)
            memory.run_ids.extend(record["monitor_runs"])
            fallback = InformationQueuePlanner("no_probe")
            belief = fallback.infer(state, await memory.retrieve(state.domain))
            proposals = fallback.propose(
                belief, [Goal.model_validate(g) for g in record["goals"]], 3
            )
            chosen = next(
                (a for a in proposals if bounded_safe(state.payload, a.payload["amount"])), None
            )
            # IDs/names are volatile; only semantic action equality is required here.
        check(
            (chosen.fingerprint if chosen else None) == record["selected_fingerprint"],
            "Selected action does not follow score/fallback",
        )
        check(
            record["selected_fingerprint"]
            == (
                Action.model_validate(record["selected"]).fingerprint
                if record["selected"]
                else None
            ),
            "Selected action forged",
        )
        nodes = [
            TreeNode.model_validate(e["data"]["node"])
            for e in events
            if e["kind"] == "node_updated"
        ]
        check(
            all(
                n.action.fingerprint == record["selected_fingerprint"] and n.state == state
                for n in nodes
                if n.action is not None
            ),
            "Runtime evaluated a different action/state",
        )
        check(
            all(not p.engine_id.startswith("queue-temporal") for n in nodes for p in n.predictions),
            "Learned scores entered Gate evidence",
        )
        outcomes = [e for e in events if e["kind"] == "outcome"]
        results.append(run["result"])
        selections.append(record)
        if outcomes:
            check(len(outcomes) == 1 and run["result"]["steps"] == 1, "Not one-action execution")
            outcome = outcomes[0]
            observation = outcome["data"]["observation"]
            execution = store.execution_record(observation["receipt"])
            check(
                execution["status"] == "complete"
                and execution["run_id"] == run_id
                and execution["state_id"] == state.id
                and execution["action_hash"] == record["selected_fingerprint"],
                "Receipt selection binding mismatch",
            )
            auth = [e for e in events if e["kind"] == "authorization"]
            dispatch = [e for e in events if e["kind"] == "cognitive_dispatch_binding"]
            intents = [e for e in events if e["kind"] == "execution_intent"]
            check(
                len(auth) == len(dispatch) == len(intents) == 1
                and event["seq"]
                < auth[0]["seq"]
                < intents[0]["seq"]
                < dispatch[0]["seq"]
                < outcome["seq"],
                "Selection/Gate/intent/dispatch/outcome chronology",
            )
            fresh = State.model_validate(dispatch[0]["data"]["fresh_state"])
            check(
                same(fresh, state)
                and datetime.fromisoformat(fresh.timestamp)
                >= datetime.fromisoformat(intents[0]["timestamp"])
                and datetime.fromisoformat(fresh.timestamp)
                <= datetime.fromisoformat(dispatch[0]["timestamp"]),
                "Dispatch fresh observation differs",
            )
            check(
                dispatch[0]["data"]["receipt"] == observation["receipt"], "Dispatch receipt changed"
            )
            trust = next(e["data"]["engines"] for e in events if e["kind"] == "trust_snapshot")
            node = next(n for n in reversed(nodes) if n.id == outcome["data"]["node_id"])
            assessment = evaluate(
                node.predictions, task, trust, state=node.state, action=node.action
            )
            verdict = gate(
                assessment,
                state.id,
                node.action.fingerprint,
                Policy.model_validate(run["config"]["policy"]),
                False,
            )
            check(verdict.decision == "execute", "Independent Gate would reject execution")
            check(
                auth[0]["data"]["decision"] == "execute"
                and auth[0]["data"]["evidence_hash"] == identity(assessment.model_dump()),
                "Gate evidence differs from authorization",
            )
            actions.append(record["selected"]["payload"]["amount"])
            if record["basis"] in {"learned_model", "fixed_prior"}:
                p = next(
                    c["prediction"]
                    for c in candidates
                    if c["fingerprint"] == record["selected_fingerprint"]
                )
                errors.extend(
                    abs(p["vectors"][key][0] - observation["state"]["payload"][key])
                    for key in ("queue", "delivered")
                )
        else:
            check(
                not any(e["kind"] == "cognitive_dispatch_binding" for e in events),
                "Unexecuted selection recorded as dispatch",
            )
    observations = [r.after for r in snapshot.transitions]
    delivered = observations[-1].state.payload["delivered"]
    holding = sum(o.metrics["holding_loss"] for o in observations)
    submission = sum(o.metrics["submission_cost"] for o in observations)
    probes = sum(o.metrics["probe_cost"] for o in observations)
    m = case["metrics"]
    expected = {
        "goal_achieved": delivered >= protocol["target"],
        "delivered": delivered,
        "holding_cost": holding,
        "submission_cost": submission,
        "probe_cost": probes,
        "reward": sum(o.metrics["reward"] for o in observations),
        "goal_utility": min(protocol["target"], delivered) - holding - submission - probes,
        "unsafe": sum(o.unsafe for o in observations),
        "executed_actions": actions,
        "executed_ticks": len(observations),
        "preparation_ticks": len(case["prefix_runs"]),
        "setup_local_revalidation_calls": 9 * len(report.cases) if recovered else 0,
        "first_available_tick": next(
            (
                p["state"]["payload"]["tick"]
                for p in preparations
                if p["health"]["status"] == "available"
            ),
            None,
        ),
        "decisions": len(selections),
        "learned_decisions": sum(s["basis"] == "learned_model" for s in selections),
        "fixed_prior_decisions": sum(s["basis"] == "fixed_prior" for s in selections),
        "fallback_decisions": sum(s["basis"] == "fallback" for s in selections),
        "local_prediction_calls": sum(s["local_prediction_calls"] for s in selections),
        "local_revalidation_calls": sum(s["local_revalidation_calls"] for s in selections),
        "local_total_prediction_calls": sum(s["local_total_prediction_calls"] for s in selections),
        "runtime_agent_calls": sum(r["agent_calls"] for r in results),
        "runtime_engine_calls": sum(r["calls"] for r in results),
        "abstain": sum(r["steps"] == 0 for r in results),
        "one_tick_state_mae": fmean(errors) if errors else None,
        "submitted_jobs": sum(r.action.payload.get("amount", 0) for r in snapshot.transitions),
        "excess_submitted_jobs": max(
            0,
            sum(r.action.payload.get("amount", 0) for r in snapshot.transitions)
            - protocol["target"],
        ),
        "submit_actions_after_goal": sum(
            r.action.payload.get("amount", 0) > 0
            and r.before.payload["delivered"] >= protocol["target"]
            for r in snapshot.transitions
        ),
        "runtime_execution_calls": sum(r["execution_calls"] for r in results),
        "preparation_engine_calls": sum(
            store.get_run(r)["result"]["calls"] for r in case["prefix_runs"]
        ),
        "external_api_calls": 0,
        "api_cost_usd": sum(r["cost_usd"] for r in results),
        "compute_dollar_cost": None,
    }
    for k, v in expected.items():
        check(
            m[k] == v or isinstance(v, (int, float)) and math.isclose(m[k], v, abs_tol=1e-10),
            "Metric changed: " + k,
        )
    check(
        m["local_total_prediction_calls"] == m["runtime_agent_calls"],
        "Local prediction usage not charged by Runtime",
    )
    return len(selections), len(actions)


async def audit(protocol_path, output):
    protocol = json.loads(Path(protocol_path).read_text())
    cases = json.loads((output / "cases.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    check(summary["protocol_hash"] == identity(protocol), "Protocol changed")
    from scripts.benchmark_queue_model_selection import selection_source_hash

    check(summary["source_hash"] == selection_source_hash(), "Selection source changed")
    seeds = protocol["pilot_seeds"] if summary["pilot"] else protocol["evaluation_seeds"]
    expected = {
        (e, s, m) for e in protocol["environments"] for s in seeds for m in protocol["modes"]
    }
    expected |= {
        ("recovered_" + e, s, m)
        for e in protocol["recovery_directions"]
        for s in (protocol["pilot_seeds"] if summary["pilot"] else protocol["recovery_seeds"])
        for m in protocol["modes"]
    }
    check(
        {(c["environment"], c["seed"], c["mode"]) for c in cases} == expected
        and len(cases) == len(expected),
        "Missing/duplicate conditions",
    )
    store, artifacts = (
        Store("sqlite:///" + str(output / "ledger.db")),
        Artifacts(str(output / "artifacts")),
    )
    episode_runs = [r for c in cases for r in c["prefix_runs"] + c["runs"]]
    check(len(episode_runs) == len(set(episode_runs)), "Actual policy episodes share run sources")
    decisions, executions = 0, 0
    for c in cases:
        d, e = await audit_case(store, artifacts, protocol, c)
        decisions += d
        executions += e
        print("audited", c["id"], flush=True)
    for key, values in summary["scores"].items():
        env, mode = key.split("/")
        group = [c["metrics"] for c in cases if c["environment"] == env and c["mode"] == mode]
        for metric, value in values.items():
            actual = (
                fmean(float(m[metric]) for m in group if m[metric] is not None)
                if any(m[metric] is not None for m in group)
                else None
            )
            check(actual == value, "Aggregate metric changed")
    diagnostics = json.loads((output / "diagnostics.json").read_text())
    diagnostic_seeds = protocol["pilot_seeds"] if summary["pilot"] else protocol["diagnostic_seeds"]
    check(
        {(d["seed"], d["high"]) for d in diagnostics}
        == {(s, h) for s in diagnostic_seeds for h in (False, True)},
        "Diagnostic conditions changed",
    )
    diagnostic_runs = [
        r for d in diagnostics for b in d["branches"] for r in b["prefix_runs"] + b["runs"]
    ]
    check(
        len(diagnostic_runs) == len(set(diagnostic_runs))
        and not set(diagnostic_runs) & set(episode_runs),
        "Diagnostic and policy episodes overlap",
    )
    for d in diagnostics:
        model = DynamicsModel.load(artifacts, d["model_artifact"], QueueServiceAdapter())
        training = await TransitionDataset(store, d["training"]).snapshot()
        targets = [QueueServiceAdapter().targets(r) for r in training.transitions]
        effective = sum(int(t[0]) for t in targets)
        probability = sum(int(t[1]) for t in targets) / effective
        for b in d["branches"]:
            snapshot = await TransitionDataset(
                store, {"diagnostic": b["prefix_runs"] + b["runs"]}
            ).snapshot()
            require_disjoint(training, snapshot)
            check(
                [r.after.state.payload for r in snapshot.transitions[-3:]] == b["trace"],
                "Diagnostic actual trace changed",
            )
            check(
                same(snapshot.transitions[-3].before, State.model_validate(d["state"])),
                "Diagnostic root differs",
            )
            check(
                [r.action.payload["amount"] for r in snapshot.transitions[-3:]]
                == [b["amount"], 0, 0],
                "Diagnostic continuation is not environment-only",
            )
        for mode, comparison in d["comparisons"].items():
            p = probability if mode == "learned" else protocol["fixed_prior"]
            verify_distribution(comparison, State.model_validate(d["state"]), p)
            if mode == "learned":
                check(
                    all(
                        f["raw"]["dataset_hash"] == model.dataset_hash
                        and f["raw"]["model_version"] == model.version
                        for f in comparison["predictions"]
                    ),
                    "Diagnostic model provenance mismatch",
                )
    for high in (False, True):
        selected = [d for d in diagnostics if d["high"] == high]
        for mode in ("learned", "prior"):
            errors, differences = [], []
            for d in selected:
                forecasts = d["comparisons"][mode]["predictions"]
                truth = [b["trace"] for b in d["branches"]]
                for a in range(3):
                    for t in (1, 2):
                        errors.append(
                            abs(forecasts[a]["vectors"]["delivered"][t] - truth[a][t]["delivered"])
                        )
                for a, b in itertools.combinations(range(3), 2):
                    for t in (1, 2):
                        differences.append(
                            abs(
                                forecasts[a]["vectors"]["delivered"][t]
                                - forecasts[b]["vectors"]["delivered"][t]
                                - truth[a][t]["delivered"]
                                + truth[b][t]["delivered"]
                            )
                        )
            score = summary["diagnostics"][str(high)][mode]
            check(
                math.isclose(score["tick_2_3_state_mae"], fmean(errors))
                and math.isclose(score["tick_2_3_action_difference_mae"], fmean(differences))
                and score["coverage"] == 1,
                "Diagnostic MAE/coverage changed",
            )
    for key, pair in summary["paired_action_disagreement"].items():
        env, baseline = key.split("/")
        pairs = []
        for seed in {c["seed"] for c in cases if c["environment"] == env}:
            group = {c["mode"]: c for c in cases if c["environment"] == env and c["seed"] == seed}
            pairs.extend(
                zip(
                    group["learned"]["metrics"]["executed_actions"],
                    group[baseline]["metrics"]["executed_actions"],
                )
            )
        check(
            pair["paired_ticks"] == len(pairs)
            and pair["different_executed_actions"] == sum(a != b for a, b in pairs),
            "Paired actual action comparison changed",
        )
    for preparation in json.loads((output / "recovery.json").read_text()):
        candidate = CandidateModel.model_validate(
            json.loads(artifacts.read(preparation["candidate"]))
        )
        origin = RecoveryOrigin.model_validate(
            json.loads(artifacts.read(candidate.origin_artifact))
        )
        report = CandidateEvaluation.model_validate(
            json.loads(artifacts.read(preparation["evaluation"]))
        )
        episodes = {
            origin.episode_id: candidate.source_runs,
            **{b.episode_id: b.runs for c in report.cases for b in c.branches},
        }
        snapshot = await TransitionDataset(store, episodes).snapshot()
        expected_runs = [
            *candidate.source_runs,
            *[r for c in report.cases for b in c.branches for r in b.runs],
        ]
        check(
            preparation["preparation_runs"] == expected_runs
            and preparation["preparation_ticks"] == len(snapshot.transitions),
            "Recovery preparation count changed",
        )
        check(
            preparation["preparation_local_prediction_calls"] == 18 * len(report.cases)
            and preparation["preparation_runtime_engine_calls"]
            == sum(store.get_run(r)["result"]["calls"] for r in expected_runs),
            "Recovery collection/evaluation local call accounting changed",
        )
        for metric, field in (
            ("probe_cost", "preparation_probe_cost"),
            ("submission_cost", "preparation_submission_cost"),
            ("holding_loss", "preparation_holding_cost"),
        ):
            check(
                math.isclose(
                    sum(r.after.metrics[metric] for r in snapshot.transitions), preparation[field]
                ),
                "Recovery preparation cost changed",
            )
    semantic = [
        {"id": c["id"], "metrics": {k: v for k, v in c["metrics"].items() if "seconds" not in k}}
        for c in cases
    ]
    check(identity(semantic) == summary["semantic_hash"], "Semantic summary changed")
    result = {
        "passed": True,
        "auditor_source_hash": identity(
            {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ROOT / "scripts").glob("audit_queue*.py"))
            }
        ),
        "episodes": len(cases),
        "decisions": decisions,
        "executed_actions": executions,
        "unsafe": sum(c["metrics"]["unsafe"] for c in cases),
        "semantic_hash": summary["semantic_hash"],
        "scope": "Explicit owner manifests, append-only Store; no authentication of arbitrary external World identity",
    }
    (output / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    store.db.dispose()
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--protocol", type=Path, default=ROOT / "benchmarks/queue-model-selection-v2.json"
    )
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    asyncio.run(audit(a.protocol, a.output))
