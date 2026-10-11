"""Independent qualification/value/authorization/receipt audit, never flag-based."""

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

from preact.cognition.information import InformationQueuePlanner
from preact.cognition.memory import EpisodicMemory
from preact.cognition.models import Goal
from preact.core.decision import evaluate, gate
from preact.core.models import (
    Action,
    Decision,
    Policy,
    Prediction,
    PredictionRequest,
    State,
    identity,
)
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld, bounded_safe
from preact.domains.queue_admission_fixture import AdmissionFixtureVerifier, AdmissionQueueFixture
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.drift import DriftConfig
from preact.learning.recovery import CandidateEvaluation, ModelPromotion
from scripts.audit_queue_model_selection import verify_distribution
from scripts.benchmark_queue_model_selection import selection_source_hash

ROOT = Path(__file__).resolve().parents[1]
AUDITOR_FILES = [Path(__file__), ROOT / "scripts/audit_queue_model_selection.py"]
AUDITOR_HASH = identity({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in AUDITOR_FILES})


def check(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.loads(json.dumps(value))


def paths(state, amount, probability):
    """Independent exhaustive calculation; no new Planner/value function import."""
    rows = []
    for services in itertools.product((1, 3), repeat=3):
        current = state.payload
        qs, ds, ps = [], [], []
        for index, service in enumerate(services):
            current, _, _ = transition(current, amount if index == 0 else 0, service)
            qs.append(current["queue"])
            ds.append(current["delivered"])
            ps.append(sum(p["amount"] for p in current["pending"]))
        rows.append(
            dict(
                service=list(services),
                weight=math.prod(probability if s == 3 else 1 - probability for s in services),
                queue=qs,
                delivered=ds,
                pending_work=ps,
            )
        )
    return rows


def value(state, rows, amount, target):
    return sum(
        r["weight"]
        * (
            min(
                max(0, target - state.payload["delivered"]),
                r["delivered"][-1] - state.payload["delivered"],
            )
            - state.payload["holding_cost"] * sum(r["queue"])
            - state.payload["submission_cost"] * amount
        )
        for r in rows
    )


async def verified_predictions(store, artifacts, events, node_id, state, action, task):
    entries = [
        e["data"] for e in events if e["kind"] == "prediction" and e["data"]["node_id"] == node_id
    ]
    predictions = []
    for entry in entries:
        saved = Prediction.model_validate(entry["prediction"])
        check(
            json.loads(artifacts.read(entry["artifact"])) == entry["prediction"],
            "Verifier artifact changed",
        )
        check(
            saved.state_id == state.id and saved.action_ids == [action.id],
            "Verifier State/Action binding changed",
        )
        engine = (
            InformationBoundVerifier(task, future=True)
            if saved.horizon > 1
            else (
                AdmissionFixtureVerifier(task)
                if "submission_permit" in task.required_checks
                else InformationBoundVerifier(task)
            )
        )
        predicted, _ = await Registry([engine]).predict(
            engine,
            PredictionRequest(
                state=state,
                actions=[action],
                claims=saved.requested_claims,
                horizon=saved.horizon,
                sample_budget=1,
            ),
        )
        check(
            saved.engine_id == predicted.engine_id
            and saved.engine_version == predicted.engine_version
            and saved.family == predicted.family
            and saved.evidence == predicted.evidence,
            "Verifier provenance changed",
        )
        check(
            saved.success == predicted.success
            and saved.risk == predicted.risk
            and saved.mandatory_checks == predicted.mandatory_checks
            and saved.claim_results == predicted.claim_results,
            "Executable verification does not reproduce",
        )
        predictions.append(saved)
    return predictions


async def audit_case(store, artifacts, protocol, case, *, execution_stats=None):
    model = DynamicsModel.load(artifacts, case["model_artifact"], QueueServiceAdapter())
    dataset = TransitionDataset(store, case["training"])
    refit = await TabularDynamicsTrainer().fit(
        dataset,
        QueueServiceAdapter(),
        TrainingConfig(min_samples=model.min_samples, seed=model.seed),
        limit=len(model.receipts),
    )
    check(refit == model, "Model does not match complete receipt training")
    cls = AdmissionQueueFixture if case["settings"].get("fixture") else InformationQueueWorld
    fixture = cls(
        seed=case["seed"],
        ticks=protocol["episode_ticks"],
        target=protocol["target"],
        capacity=protocol["capacity"],
        holding_cost=protocol["holding_cost"],
        submission_cost=protocol["submission_cost"],
        probe_cost=protocol["probe_cost"],
        **{k: case["settings"][k] for k in ("high_first", "shift_tick", "noise")},
    )
    task = fixture.task.model_copy(deep=True)
    check(
        identity(task.model_dump()) == identity(case["task"]), "Owned task representation changed"
    )
    initial = State.model_validate(case["initial"])
    check(initial.payload == (await fixture.observe()).payload, "False initial authoritative State")
    guard = QueueTemporalGuard(
        store,
        task,
        model,
        case["training"],
        episode_id=case["episode_id"],
        initial=initial,
        config=DriftConfig(**protocol["detector"]),
    )
    if case["promotion"]:
        recovery = QueueModelRecovery(store, artifacts, case["journal_run"])
        promotion = ModelPromotion.model_validate(json.loads(artifacts.read(case["promotion"])))
        journal = store.read_events(case["journal_run"])
        approved = [
            e
            for e in journal
            if e["kind"] == "model_promotion_approved"
            and e["data"]["artifact"] == case["promotion"]
        ]
        committed = [
            e
            for e in journal
            if e["kind"] == "model_promotion_committed"
            and e["data"]["artifact"] == case["promotion"]
        ]
        check(
            len(approved) == len(committed) == 1
            and approved[0]["seq"] < committed[0]["seq"]
            and datetime.fromisoformat(promotion.cutoff.timestamp)
            <= datetime.fromisoformat(approved[0]["timestamp"])
            <= datetime.fromisoformat(committed[0]["timestamp"]),
            "Promotion has no ordered durable approval/commit",
        )
        check(
            promotion.episode_id == case["episode_id"] and promotion.initial == initial,
            "Promotion monitoring scope changed",
        )
        await recovery._promotion_guard(promotion, promotion.cutoff, promotion.runs)
    monitoring = await TransitionDataset(
        store, {case["episode_id"]: [*case["prefix_runs"], *case["runs"]]}
    ).snapshot()
    check(not monitoring.unlabelled_receipts, "Monitoring includes unconfirmed receipts")
    check(
        not set(model.run_ids) & set([*case["prefix_runs"], *case["runs"]]),
        "Learning/monitoring overlap",
    )
    rows = {row.run_id: row for row in monitoring.transitions}
    # Retrospective replay of ONLY recorded executed interventions in the owned
    # CPU fixture. Private service is read here by the independent auditor, never
    # by Planner/Engine, and this replay creates no new training observations.
    previous = initial.payload
    for run_id in [*case["prefix_runs"], *case["runs"]]:
        if run_id not in rows:
            continue
        row = rows[run_id]
        check(row.before.payload == previous, "Discontinuous executed World history")
        fixture.validate(row.before, row.action)
        amount = row.action.payload.get("amount", 0)
        if case["settings"].get("fixture"):
            check(amount <= previous["submission_limit"], "Executed fixture permit violation")
        expected, available, processed = transition(
            previous, amount, fixture._service[previous["tick"]]
        )
        check(
            expected == row.after.state.payload
            and row.after.metrics["processed"] == processed
            and row.after.metrics["available_work"] == available,
            "Actual outcome does not replay",
        )
        holding = protocol["holding_cost"] * expected["queue"]
        submission = protocol["submission_cost"] * amount
        probe = protocol["probe_cost"] if row.action.kind == "probe_service" else 0
        check(
            row.after.metrics["holding_loss"] == holding
            and row.after.metrics["submission_cost"] == submission
            and row.after.metrics["probe_cost"] == probe
            and row.after.metrics["reward"] == processed - holding - submission - probe,
            "Actual reward/cost does not replay",
        )
        previous = expected
    all_observations = [rows[r].after for r in [*case["prefix_runs"], *case["runs"]] if r in rows]
    total_holding = sum(o.metrics["holding_loss"] for o in all_observations)
    total_submission = sum(o.metrics["submission_cost"] for o in all_observations)
    total_probe = sum(o.metrics["probe_cost"] for o in all_observations)
    delivered = all_observations[-1].state.payload["delivered"] if all_observations else 0
    expected_metrics = {
        "holding_cost": total_holding,
        "submission_cost": total_submission,
        "probe_cost": total_probe,
        "delivered": delivered,
        "reward": delivered - total_holding - total_submission - total_probe,
        "goal_utility": min(delivered, protocol["target"])
        - total_holding
        - total_submission
        - total_probe,
        "goal_success": delivered >= protocol["target"],
        "unsafe": sum(o.unsafe for o in all_observations),
        "goal_tick": next(
            (
                o.state.payload["tick"]
                for o in all_observations
                if o.state.payload["delivered"] >= protocol["target"]
            ),
            None,
        ),
        "top_rejected": 0,
        "alternatives_exist": 0,
        "alternatives_executed": 0,
        "complete_qualification": 0,
        "unresolved_abstain": 0,
        "abstain": 0,
        "qualified_counts": [],
        "unresolved_counts": [],
        "actions": [],
        "gate_rejected": 0,
        "verifier_calls": 0,
        "local_prediction_calls": 0,
        "local_revalidation_calls": 0,
        "model_decisions": 0,
        "fallback_decisions": 0,
        "preparation_ticks": len(case["prefix_runs"]),
        "decisions": len(case["runs"]),
        "external_api_calls": 0,
        "api_cost_usd": 0,
        "compute_dollar_cost": None,
        "escalations": 0,
        "preparation_verifier_calls": sum(
            store.get_run(r)["result"]["calls"] for r in case["prefix_runs"]
        ),
    }
    errors = []
    model_execution_ticks, fallback_execution_ticks = [], []
    promotion_deactivated = False
    for index, run_id in enumerate(case["runs"]):
        events = store.read_events(run_id)
        run = store.get_run(run_id)
        policy = Policy.model_validate(run["config"]["policy"])
        expected_policy = Policy(
            **{
                **protocol["policy"],
                **({"width": 1, "max_nodes": 1} if case["mode"] == "single_learned" else {}),
            }
        )
        check(policy == expected_policy, "Policy differs from frozen protocol")
        prediction_events = [
            e for e in events if e["kind"] in {"cognitive_prediction", "cognitive_selection"}
        ]
        check(len(prediction_events) == 1, "Missing/duplicate prediction-value record")
        event = prediction_events[0]
        record = event["data"]
        expected_mode = {
            "multi_learned": "learned",
            "single_learned": "learned",
            "multi_prior": "prior",
            "existing": "existing",
            "fixed": "fixed",
            "core_utility_control": "learned",
        }[case["mode"]]
        check(record["mode"] == expected_mode, "Planner mode changed")
        if case["mode"] != "single_learned":
            check(
                record["selected"] is None
                and record["selected_fingerprint"] is None
                and record["selection_mode"] == "qualified_value/v1"
                and record["selection_rule"]
                == (
                    "core_utility_control"
                    if case["mode"] == "core_utility_control"
                    else "model_value"
                ),
                "Prediction ranking is being confused with final selection",
            )
        check(
            record["goals"]
            == canonical(
                [Goal(name="Deliver", metric="delivered", target=protocol["target"]).model_dump()]
            ),
            "Goal differs from frozen protocol",
        )
        expected_metrics["model_decisions"] += int(
            record["basis"] == "learned_model"
            and record.get("selection_rule") != "core_utility_control"
        )
        expected_metrics["fallback_decisions"] += int(record["basis"] == "fallback")
        expected_metrics["escalations"] += sum(e["kind"] == "escalation" for e in events)
        expected_metrics["api_cost_usd"] += run["result"]["cost_usd"]
        state = State.model_validate(record["state"])
        observed = [e for e in events if e["kind"] == "observed" and e["seq"] < event["seq"]]
        check(
            observed[-1]["data"]["state"] == record["state"],
            "Prediction is not the actual Runtime observation",
        )
        check(
            record["run_id"] == run_id and record["goals"] == run["config"]["cognitive_goals"],
            "Run/Goal binding changed",
        )
        check(
            record["monitor_runs"] == case["prefix_runs"] + case["runs"][:index],
            "Monitoring chronology changed",
        )
        check(
            record["evaluation_function"] == "capped-progress-holding-root-cost/v1"
            and record["horizon"] == 3,
            "Evaluation function changed",
        )
        check(
            datetime.fromisoformat(state.timestamp) <= datetime.fromisoformat(event["timestamp"]),
            "Prediction has future observation",
        )
        health = await guard.health(state, record["monitor_runs"])
        check(canonical(asdict(health)) == record["health"], "Health does not reproduce")
        if case["promotion"]:
            check(
                record["promotion"] == canonical(promotion.model_dump()),
                "Prediction lost explicit promotion provenance",
            )
            check(
                datetime.fromisoformat(committed[0]["timestamp"])
                <= datetime.fromisoformat(state.timestamp)
                and committed[0]["seq"] <= record["source_heads"][case["journal_run"]]["next_seq"],
                "Prediction precedes durable promotion commit",
            )
            promotion_deactivated |= health.status != "available"
        available = (
            expected_mode in {"learned", "prior"}
            and health.status == "available"
            and state.payload["tick"] + 3 <= state.payload["episode_ticks"]
            and not promotion_deactivated
        )
        expected_basis = (
            "fixed_prior"
            if available and expected_mode == "prior"
            else "learned_model"
            if available
            else "fixed_rule"
            if expected_mode == "fixed"
            else "existing_planner"
            if expected_mode == "existing"
            else "fallback"
        )
        check(record["basis"] == expected_basis, "Selection basis contradicts Health/scope")
        dependencies = [r for runs in case["training"].values() for r in runs]
        if case["promotion"]:
            report = recovery._load(promotion.evaluation_artifact, CandidateEvaluation)
            dependencies = await recovery._source_runs(report.candidate_artifact, report.cases) + [
                case["journal_run"]
            ]
        check(
            set(record["source_runs"]) == set(dependencies + record["monitor_runs"])
            and set(record["source_heads"]) == set(record["source_runs"]),
            "Incomplete source dependency fence",
        )
        for source, token in record["source_heads"].items():
            current = store.run_heads([source])[source]
            if source != case["journal_run"]:
                check(asdict(current) == token, "Known source changed since decision")
            else:
                check(
                    current.next_seq >= token["next_seq"]
                    and current.execution_digest == token["execution_digest"],
                    "Recovery journal binding changed",
                )
        cs = record["candidates"]
        check(
            len(cs) == 3 and [c["amount"] for c in cs] == [3, 1, 0],
            "Prediction candidate set changed",
        )
        for c in cs:
            check((c["value"] is not None) == available, "Incomplete or fabricated value set")
            action = Action.model_validate(c["action"])
            check(
                action.state_id == state.id and action.fingerprint == c["fingerprint"],
                "Prediction fingerprint changed",
            )
            if c["value"] is not None:
                check(health.status == "available", "Score used unavailable Health")
                saved = Prediction.model_validate(c["prediction"])
                probability = (
                    protocol["fixed_prior"]
                    if record["basis"] == "fixed_prior"
                    else model.cells[0].mean[1] / model.cells[0].mean[0]
                )
                branches = paths(state, c["amount"], probability)
                check(
                    saved.state_id == state.id
                    and saved.action_ids == [action.id]
                    and saved.horizon == 3
                    and saved.evidence == "inference",
                    "Learned prediction authority changed",
                )
                base_version = (
                    model.version
                    if record["basis"] != "fixed_prior"
                    else identity({"prior": protocol["fixed_prior"], "version": "1"})
                )
                expected_view = (
                    identity(
                        {
                            "base": base_version,
                            "health": health.version,
                            "code": hashlib.sha256(
                                (ROOT / "src/preact/engines/queue_temporal_guard.py").read_bytes()
                            ).hexdigest(),
                        }
                    )
                    if record["basis"] != "fixed_prior"
                    else base_version
                )
                check(
                    saved.engine_version == record["engine_view_version"] == expected_view,
                    "Engine view/model/Health version mismatch",
                )
                check(
                    saved.raw["dataset_hash"]
                    == (model.dataset_hash if record["basis"] != "fixed_prior" else None),
                    "Prediction dataset provenance mismatch",
                )
                check(
                    saved.raw["branches"] == branches and saved.raw["p_high"] == probability,
                    "Forecast distribution changed",
                )
                check(
                    abs(c["value"] - value(state, branches, c["amount"], protocol["target"]))
                    < 1e-9,
                    "Forecast value changed",
                )
                for metric in ("queue", "delivered", "pending_work"):
                    check(
                        all(
                            abs(
                                saved.vectors[metric][t]
                                - sum(b["weight"] * b[metric][t] for b in branches)
                            )
                            < 1e-9
                            for t in range(3)
                        ),
                        "Forecast trajectory changed",
                    )
        if record["basis"] in {"learned_model", "fixed_prior"}:
            eligible = (
                cs
                if case["mode"] != "single_learned"
                else [c for c in cs if bounded_safe(state.payload, c["amount"])]
            )
            order = [
                c["fingerprint"] for c in sorted(eligible, key=lambda c: (-c["value"], c["amount"]))
            ]
            check(order == record["ranked_fingerprints"], "Value ranking changed")
        elif record["basis"] == "fixed_rule":
            order = [
                c["fingerprint"] for c in sorted(cs, key=lambda c: (c["amount"] != 1, c["amount"]))
            ]
            check(order == record["ranked_fingerprints"], "Fixed rule changed")
        else:
            memory = EpisodicMemory(store)
            memory.run_ids.extend(record["monitor_runs"])
            planner = InformationQueuePlanner("no_probe")
            belief = planner.infer(state, await memory.retrieve(state.domain))
            proposed = planner.propose(belief, [Goal.model_validate(g) for g in record["goals"]], 3)
            order = [a.fingerprint for a in proposed]
            if case["mode"] != "single_learned":
                order = [
                    c["fingerprint"]
                    for c in sorted(
                        cs,
                        key=lambda c: (
                            order.index(c["fingerprint"])
                            if c["fingerprint"] in order
                            else len(order),
                            c["amount"],
                        ),
                    )
                ]
                check(order == record["ranked_fingerprints"], "Fallback ordering changed")
        trusts = next(e["data"]["engines"] for e in events if e["kind"] == "trust_snapshot")
        batch_events = [e for e in events if e["kind"] == "candidate_qualification"]
        expected_choice = None
        if case["mode"] != "single_learned":
            check(len(batch_events) == 1, "Missing/duplicate qualification batch")
            raw = batch_events[0]["data"]
            batch = raw["batch"]
            check(
                batch["run_id"] == run_id
                and batch["state"] == record["state"]
                and raw["batch_hash"] == identity(batch),
                "Qualification binding changed",
            )
            check(len(batch["candidates"]) == 3, "Proposal completeness changed")
            qualified = []
            unresolved = []
            classified = []
            for entry, c in zip(batch["candidates"], cs):
                action = Action.model_validate(entry["action"])
                check(
                    action.fingerprint == c["fingerprint"] and entry["action"] == c["action"],
                    "Runtime proposal differs from prediction",
                )
                predictions = await verified_predictions(
                    store, artifacts, events, entry["node_id"], state, action, task
                )
                check(
                    [Prediction.model_validate(p) for p in entry["predictions"]] == predictions,
                    "Qualification predictions differ from verifier records",
                )
                assessment = evaluate(predictions, task, trusts, state=state, action=action)
                result = gate(assessment, state.id, action.fingerprint, policy, True)
                classification = (
                    {
                        Decision.EXECUTE: "qualified",
                        Decision.VERIFY: "unresolved",
                        Decision.ABSTAIN: "rejected",
                    }[result.decision]
                    if entry["node_id"]
                    else "unexpanded"
                )
                check(
                    entry["status"] == classification,
                    f"Qualification contradicts Gate: saved={entry['status']} actual={classification} checks={assessment.checks} violations={assessment.violations}",
                )
                if entry["evaluation"] is not None:
                    check(
                        entry["evaluation"] == canonical(assessment.model_dump()),
                        "Saved Evaluation changed",
                    )
                if entry["gate"] is not None:
                    final = gate(assessment, state.id, action.fingerprint, policy, False)
                    check(
                        entry["gate"]["decision"] == final.decision
                        and entry["gate"]["evidence_hash"] == final.evidence_hash
                        and entry["gate"]["policy_hash"] == final.policy_hash,
                        "Gate Preview changed",
                    )
                classified.append(classification)
                if classification == "qualified":
                    qualified.append((entry, assessment))
                if classification in {"unexpanded", "unresolved"}:
                    unresolved.append(entry)
            complete = bool(classified) and not unresolved
            check(
                raw["complete"] == complete
                and raw["qualified_ids"] == [e["node_id"] for e, _ in qualified],
                "Qualification set/completeness changed",
            )
            expected_metrics["qualified_counts"].append(len(qualified))
            expected_metrics["unresolved_counts"].append(len(unresolved))
            expected_metrics["complete_qualification"] += int(complete)
            expected_metrics["unresolved_abstain"] += int(bool(unresolved))
            expected_metrics["gate_rejected"] += classified.count("rejected")
            first = record["first_choice_fingerprint"]
            rejected = any(
                Action.model_validate(e["action"]).fingerprint == first and s == "rejected"
                for e, s in zip(batch["candidates"], classified)
            )
            expected_metrics["top_rejected"] += int(rejected)
            expected_metrics["alternatives_exist"] += int(rejected and bool(qualified))
            if complete and qualified:
                chosen = (
                    max(qualified, key=lambda p: p[1].utility)[0]
                    if record["selection_rule"] == "core_utility_control"
                    else min(
                        qualified,
                        key=lambda p: order.index(
                            Action.model_validate(p[0]["action"]).fingerprint
                        ),
                    )[0]
                )
                expected_choice = Action.model_validate(chosen["action"])
            choices = [e for e in events if e["kind"] == "candidate_selection"]
            check(len(choices) == 1, "Missing/duplicate final selection")
            choice = choices[0]["data"]
            check(choice["batch_hash"] == raw["batch_hash"], "Final choice has different batch")
            check(
                choice.get("node_id") == (chosen["node_id"] if expected_choice else None),
                "Final choice is not highest qualified value",
            )
            if expected_choice:
                check(
                    choice["state_id"] == state.id
                    and choice["action_fingerprint"] == expected_choice.fingerprint
                    and choice["evidence_hash"] == chosen["gate"]["evidence_hash"]
                    and choice["policy_hash"] == chosen["gate"]["policy_hash"],
                    "Selected candidate evidence/State binding changed",
                )
        else:
            expected_choice = (
                Action.model_validate(record["selected"]) if record["selected"] else None
            )
            nodes = [
                e["data"]["node"]
                for e in events
                if e["kind"] == "node" and e["data"]["node"]["action"]
            ]
            check(len(nodes) <= 1, "Single-candidate baseline expanded alternatives")
            previews = [e for e in events if e["kind"] == "gate_preview"]
            expected_metrics["complete_qualification"] += int(bool(previews))
            rejected = bool(previews) and previews[0]["data"]["gate"]["decision"] != "execute"
            expected_metrics["top_rejected"] += int(rejected)
            expected_metrics["gate_rejected"] += int(rejected)
        outcomes = [e for e in events if e["kind"] == "outcome"]
        expected_metrics["abstain"] += int(not outcomes)
        if outcomes:
            check(
                len(outcomes) == 1 and expected_choice is not None,
                "Execution without a unique choice",
            )
            row = rows[run_id]
            check(
                row.action == expected_choice and row.before == state,
                "Receipt differs from final selected action/State",
            )
            auth = [e for e in events if e["kind"] == "authorization"]
            intent = [e for e in events if e["kind"] == "execution_intent"]
            dispatch = [
                e
                for e in events
                if e["kind"] in {"cognitive_multi_dispatch", "cognitive_dispatch_binding"}
            ]
            check(
                len(auth) == len(intent) == len(dispatch) == 1
                and event["seq"]
                < auth[0]["seq"]
                < intent[0]["seq"]
                < dispatch[0]["seq"]
                < outcomes[0]["seq"],
                "Authorization/intent/dispatch/outcome chronology changed",
            )
            fresh = State.model_validate(dispatch[0]["data"]["fresh_state"])
            check(
                fresh.model_dump(exclude={"timestamp"}) == state.model_dump(exclude={"timestamp"})
                and datetime.fromisoformat(intent[0]["timestamp"])
                <= datetime.fromisoformat(fresh.timestamp)
                <= datetime.fromisoformat(dispatch[0]["timestamp"]),
                "Fresh dispatch observation changed",
            )
            check(dispatch[0]["data"]["receipt"] == row.receipt, "Dispatch receipt changed")
            predictions = await verified_predictions(
                store, artifacts, events, outcomes[0]["data"]["node_id"], state, row.action, task
            )
            assessment = evaluate(predictions, task, trusts, state=state, action=row.action)
            verdict = gate(assessment, state.id, row.action.fingerprint, policy, False)
            check(
                verdict.decision == Decision.EXECUTE
                and auth[0]["data"]["evidence_hash"] == verdict.evidence_hash
                and auth[0]["data"]["policy_hash"] == verdict.policy_hash,
                "Final independent Gate would not authorize execution",
            )
            check(
                auth[0]["data"]["action_hash"] == row.action.fingerprint
                and auth[0]["data"]["state_id"] == state.id,
                "Authorization State/action changed",
            )
            check(
                datetime.fromisoformat(dispatch[0]["timestamp"]).timestamp()
                < auth[0]["data"]["expires_at"],
                "Authorization expired before dispatch",
            )
            expected_metrics["actions"].append(row.action.payload["amount"])
            if (
                record["basis"] == "learned_model"
                and record.get("selection_rule") != "core_utility_control"
            ):
                model_execution_ticks.append(state.payload["tick"])
            if (
                record["basis"] == "fallback"
                and record.get("selection_rule") != "core_utility_control"
            ):
                fallback_execution_ticks.append(state.payload["tick"])
            expected_metrics["alternatives_executed"] += int(
                rejected and case["mode"] != "single_learned"
            )
            if record["basis"] in {"learned_model", "fixed_prior"}:
                forecast = next(
                    c["prediction"] for c in cs if c["fingerprint"] == row.action.fingerprint
                )
                errors.extend(
                    abs(forecast["vectors"][m][0] - row.after.state.payload[m])
                    for m in ("queue", "delivered")
                )
        else:
            check(
                not any(
                    e["kind"]
                    in {
                        "execution_intent",
                        "cognitive_multi_dispatch",
                        "cognitive_dispatch_binding",
                    }
                    for e in events
                ),
                "ABSTAIN caused dispatch or intent",
            )
        usage = [e["data"] for e in events if e["kind"] == "agent_usage"]
        check(
            len(usage)
            == record["local_prediction_calls"] + record["local_revalidation_calls"]
            == run["result"]["agent_calls"],
            "Local reservation/usage accounting changed",
        )
        check(
            all(u["external_api_calls"] == 0 and u["cost_usd"] == 0 for u in usage),
            "Invented external API usage",
        )
        expected_metrics["local_prediction_calls"] += record["local_prediction_calls"]
        expected_metrics["local_revalidation_calls"] += record["local_revalidation_calls"]
        expected_metrics["verifier_calls"] += sum(
            e["data"]["prediction"]["sample_count"]
            for e in events
            if e["kind"] == "prediction" and not e["data"]["cached"]
        )
        check(
            run["result"]["calls"] + run["result"]["agent_calls"] <= policy.max_calls,
            "Call budget exceeded",
        )
    expected_metrics.update(
        one_tick_state_mae=fmean(errors) if errors else None,
        scored_state_values=len(errors),
        forecast_coverage=len(errors) / (2 * max(1, len(expected_metrics["actions"]))),
        unknown_rate=1 - len(errors) / (2 * max(1, len(expected_metrics["actions"]))),
    )
    for key, expected in expected_metrics.items():
        check(case["metrics"][key] == expected, "Metric does not reproduce: " + key)
    final = State.model_validate(case["final_observation"])
    actual = all_observations[-1].state if all_observations else initial
    check(
        final.model_dump(exclude={"timestamp"}) == actual.model_dump(exclude={"timestamp"})
        and datetime.fromisoformat(final.timestamp) >= datetime.fromisoformat(actual.timestamp),
        "Final observation changed",
    )
    if execution_stats is not None:
        execution_stats.update(
            environment=case["environment"],
            seed=case["seed"],
            mode=case["mode"],
            model_executions=len(model_execution_ticks),
            first_model_execution_tick=model_execution_ticks[0] if model_execution_ticks else None,
            fallback_executions=len(fallback_execution_ticks),
            submit_actions_after_goal=sum(
                r.action.payload.get("amount", 0) > 0
                and r.before.payload["delivered"] >= protocol["target"]
                for r in monitoring.transitions
            ),
            submitted_jobs=sum(r.action.payload.get("amount", 0) for r in monitoring.transitions),
        )
    return len(case["runs"]), len(expected_metrics["actions"])


async def audit_diagnostics(store, artifacts, protocol, manifest, summary):
    diagnostics = manifest["diagnostics"]
    seeds = protocol["pilot_seeds"] if manifest["pilot"] else protocol["diagnostic_seeds"]
    check(
        [(d["seed"], d["high"]) for d in diagnostics]
        == [(s, h) for s in seeds for h in (False, True)],
        "Diagnostic conditions changed",
    )
    episode_runs = {r for c in manifest["cases"] for r in c["prefix_runs"] + c["runs"]}
    branch_runs = []
    for d in diagnostics:
        model = DynamicsModel.load(artifacts, d["model_artifact"], QueueServiceAdapter())
        training = await TransitionDataset(store, d["training"]).snapshot()
        targets = [QueueServiceAdapter().targets(r) for r in training.transitions]
        effective = sum(int(t[0]) for t in targets)
        probability = sum(int(t[1]) for t in targets) / effective
        state = State.model_validate(d["state"])
        for row in training.transitions:
            outcomes = [e for e in store.read_events(row.run_id) if e["kind"] == "outcome"]
            check(
                len(outcomes) == 1
                and datetime.fromisoformat(outcomes[0]["timestamp"])
                <= datetime.fromisoformat(state.timestamp),
                "Diagnostic model includes future or unconfirmed learning",
            )
        check([b["amount"] for b in d["branches"]] == [3, 1, 0], "Diagnostic actions changed")
        for b in d["branches"]:
            runs = b["prefix_runs"] + b["runs"]
            branch_runs.extend(runs)
            snapshot = await TransitionDataset(store, {"diagnostic": runs}).snapshot()
            rows = snapshot.transitions
            check(
                not snapshot.unlabelled_receipts
                and len(rows) == len(protocol["diagnostic_prefix"]) + 3
                and not set(model.run_ids) & set(runs)
                and [r.action.payload.get("amount", 0) for r in rows[:-3]]
                == protocol["diagnostic_prefix"],
                "Diagnostic authority/independence/prefix changed",
            )
            check(
                rows[-3].before.model_dump(exclude={"timestamp"})
                == state.model_dump(exclude={"timestamp"})
                and datetime.fromisoformat(state.timestamp)
                <= datetime.fromisoformat(rows[-3].before.timestamp)
                and [r.action.payload["amount"] for r in rows[-3:]] == [b["amount"], 0, 0]
                and [r.after.state.payload for r in rows[-3:]] == b["trace"],
                "Diagnostic root/environment-only actual trace changed",
            )
        for mode, comparison in d["comparisons"].items():
            verify_distribution(
                comparison, state, probability if mode == "learned" else protocol["fixed_prior"]
            )
            if mode == "learned":
                check(
                    all(
                        p["raw"]["dataset_hash"] == model.dataset_hash
                        and p["raw"]["model_version"] == model.version
                        for p in comparison["predictions"]
                    ),
                    "Diagnostic model provenance changed",
                )
    check(
        len(branch_runs) == len(set(branch_runs)) and not set(branch_runs) & episode_runs,
        "Diagnostic/closed-loop executions overlap",
    )
    for high, mode in itertools.product((False, True), ("learned", "prior")):
        selected = [d for d in diagnostics if d["high"] == high]
        errors, differences, covered = [], [], []
        for d in selected:
            forecasts = d["comparisons"][mode]["predictions"]
            truth = [b["trace"] for b in d["branches"]]
            for a, forecast in enumerate(forecasts):
                errors.extend(
                    abs(forecast["vectors"]["delivered"][t] - truth[a][t]["delivered"])
                    for t in (1, 2)
                )
                lo, hi = forecast["metric_intervals"]["delivered"]
                covered.append(lo <= truth[a][-1]["delivered"] <= hi)
            for a, b in itertools.combinations(range(3), 2):
                differences.extend(
                    abs(
                        forecasts[a]["vectors"]["delivered"][t]
                        - forecasts[b]["vectors"]["delivered"][t]
                        - truth[a][t]["delivered"]
                        + truth[b][t]["delivered"]
                    )
                    for t in (1, 2)
                )
        score = summary["diagnostics"][f"{high}:{mode}"]
        check(
            score["comparisons"] == score["supported"] == len(selected)
            and score["coverage"] == 1
            and math.isclose(score["tick_2_3_state_mae"], fmean(errors))
            and math.isclose(score["tick_2_3_action_difference_mae"], fmean(differences))
            and score["support_range_contains_actual"] == fmean(covered),
            "Diagnostic MAE/coverage changed",
        )


def audit_protocol_cases(protocol, manifest, summary):
    check(manifest["pilot"] == summary["pilot"], "Pilot mode changed")
    seeds = protocol["pilot_seeds"] if manifest["pilot"] else protocol["evaluation_seeds"]
    recovery_seeds = protocol["pilot_seeds"] if manifest["pilot"] else protocol["recovery_seeds"]
    expected = [
        (name, seed, mode, settings)
        for name, settings in protocol["environments"].items()
        for seed in seeds
        for mode in protocol["modes"]
    ] + [
        (
            "recovered_" + direction,
            seed,
            mode,
            {"high_first": direction == "low_to_high", "shift_tick": 48, "noise": 0},
        )
        for direction in protocol["recovery_directions"]
        for seed in recovery_seeds
        for mode in protocol["modes"]
    ]
    check(len(manifest["cases"]) == len(expected), "Frozen evaluation cases missing")
    all_runs = []
    for case, (name, seed, mode, settings) in zip(manifest["cases"], expected):
        check(
            (case["environment"], case["seed"], case["mode"], case["settings"])
            == (name, seed, mode, settings)
            and case["id"] == f"{name}:{seed}:{mode}",
            "Frozen evaluation case changed",
        )
        all_runs.extend(case["prefix_runs"] + case["runs"])
    check(len(all_runs) == len(set(all_runs)), "Policy episodes share actual execution")
    groups = {}
    for c in manifest["cases"]:
        groups.setdefault(c["environment"] + ":" + c["mode"], []).append(c["metrics"])
    expected_groups = {
        key: {
            k: fmean(r[k] for r in rows)
            for k in rows[0]
            if isinstance(rows[0][k], (int, float)) and all(r[k] is not None for r in rows)
        }
        for key, rows in groups.items()
    }
    check(summary["groups"] == expected_groups, "Public aggregate changed")
    check(
        summary["semantic_hash"]
        == identity(
            [
                {
                    "environment": c["environment"],
                    "seed": c["seed"],
                    "mode": c["mode"],
                    "metrics": {k: v for k, v in c["metrics"].items() if "seconds" not in k},
                }
                for c in manifest["cases"]
            ]
        ),
        "Semantic result hash changed",
    )


async def audit(output, protocol_path):
    protocol = json.loads(protocol_path.read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    check(
        manifest["protocol"] == protocol
        and manifest["protocol_hash"] == identity(protocol) == summary["protocol_hash"],
        "Frozen protocol changed",
    )
    check(
        manifest["source_hash"] == selection_source_hash() == summary["source_hash"],
        "Implementation source changed",
    )
    store = Store("sqlite:///" + str(output / "ledger.db"))
    artifacts = Artifacts(str(output / "artifacts"))
    audit_protocol_cases(protocol, manifest, summary)
    decisions, executed = 0, 0
    execution_stats = []
    for case in manifest["cases"]:
        stats = {}
        d, e = await audit_case(store, artifacts, protocol, case, execution_stats=stats)
        execution_stats.append(stats)
        decisions += d
        executed += e
        print("audited", case["id"], flush=True)
    check(len(manifest["cases"]) == summary["episodes"], "Episode count changed")
    for case, published in zip(manifest["cases"], summary["cases"]):
        check(case["metrics"] == published["metrics"], "Public metric differs from audited case")
    await audit_diagnostics(store, artifacts, protocol, manifest, summary)
    report = {
        "status": "passed-independent-multi-selection-audit",
        "episodes": len(manifest["cases"]),
        "decisions": decisions,
        "executions": executed,
        "protocol_hash": identity(protocol),
        "execution_stats": execution_stats,
        "auditor_hash": AUDITOR_HASH,
    }
    check(
        AUDITOR_HASH
        == identity({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in AUDITOR_FILES}),
        "Auditor implementation changed during replay",
    )
    (output / "audit.json").write_text(json.dumps(report, indent=2))
    store.db.dispose()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--protocol", type=Path, default=ROOT / "benchmarks/queue-multi-selection-v2.json"
    )
    args = parser.parse_args()
    asyncio.run(audit(args.output, args.protocol))
