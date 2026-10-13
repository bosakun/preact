"""Independent receipt/lineage/value audit; no stored success flag is trusted."""

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from preact.cognition.memory import EpisodicMemory
from preact.cognition.queue_model import MonitoredQueueSource
from preact.core.candidates import CandidateBatch
from preact.core.decision import evaluate, gate
from preact.core.models import Action, Policy, Prediction, PredictionRequest, State, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.domains.software import SoftwareWorld
from preact.domains.software_deliberation import WorkflowSoftwareWorld
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.local import LocalHeuristic, LocalVerifier
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.engines.software_sequence import SoftwareSequenceHeuristic
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TransitionDataset

AUDITOR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def semantic(value):
    """Normalize nondeterministic prediction IDs/timing, never authoritative State."""
    if isinstance(value, (list, tuple)):
        return [semantic(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: semantic(v) for k, v in value.items()}
    if "engine_id" in value and "action_ids" in value:
        result.pop("id", None)
        result.pop("latency_ms", None)
    if value.get("kind") == "hypothetical" and "domain" in value:
        result.pop("timestamp", None)
    return result


def native_world(metadata):
    if metadata["domain"] == "software":
        return (
            SoftwareWorld(metadata["seed"])
            if metadata["condition"] == "native_checkout"
            else WorkflowSoftwareWorld(metadata["seed"])
        )
    spec = metadata["queue_spec"]
    return InformationQueueWorld(
        seed=metadata["seed"],
        ticks=32,
        target=int(metadata["goals"][0]["target"]),
        noise=spec.get("noise", 0),
    )


def software_scores(future, goal):
    options = []
    for path in future["paths"]:
        progress = max(float(s["payload"].get(goal["metric"], 0)) for s in path["states"])
        uncertain = max(r["prediction"]["success"]["uncertainty"] for r in future["records"])
        options.append(
            {
                "value": min(goal["target"], progress)
                - 0.05 * len(path["actions"])
                - 0.1 * uncertain,
                "progress": progress,
                "uncertainty": uncertain,
                "actions": len(path["actions"]),
                "prediction_ids": path["prediction_ids"],
            }
        )
    best = max(options, key=lambda x: x["value"])
    return {
        **best,
        "alternatives": options,
        "rule": "best_attained_goal_minus_cost/v1",
        "calibrated": False,
    }


def queue_scores(future, state, goal, projection, spread_cost=0):
    remaining = max(0, goal["target"] - state.payload["delivered"])
    i = projection - 1
    values = [
        min(remaining, b["delivered"][i] - state.payload["delivered"])
        - state.payload["holding_cost"] * sum(b["queue"][:projection])
        - state.payload["submission_cost"] * future["action"]["payload"]["amount"]
        for b in future["paths"]
    ]
    expected = sum(b["weight"] * v for b, v in zip(future["paths"], values, strict=True))
    spread = max(values) - min(values)
    return {
        "value": expected - spread_cost * spread,
        "expected": expected,
        "support_range": [min(values), max(values)],
        "spread": spread,
        "projection": projection,
        "forecast_horizon": 3,
        "calibrated": False,
        "rule": "expected_capped_progress_holding_submission/v1",
    }


async def replay_software(world, trace):
    predictor = LocalHeuristic(world)
    usage = []
    sequence = SoftwareSequenceHeuristic(world, predictor, usage)
    registry = Registry([predictor, sequence])
    horizon = trace["simulation"]["horizon"]
    require(horizon in (1, 2, 3), "Unsupported software horizon")
    require(
        trace["simulation"]["continuation"]
        == ("explicit_action_sequence" if horizon > 1 else "environment_only"),
        "Software continuation relabelled",
    )
    state = State.model_validate(trace["state"])
    native = await world.propose(state, 3)
    require(
        [a.fingerprint for a in native]
        == [f["fingerprint"] for f in trace["simulation"]["candidates"]],
        "Native candidate set changed",
    )
    expected_scores = []
    for future in trace["simulation"]["candidates"]:
        root = Action.model_validate(future["action"])
        require(
            root.state_id == state.id and root.fingerprint == future["fingerprint"], "Root binding"
        )
        endings = []
        intermediate = {}
        for entry in future["records"]:
            request = PredictionRequest.model_validate(entry["request"])
            saved = Prediction.model_validate(entry["prediction"])
            require(
                saved.evidence == "inference" and not saved.mandatory_checks,
                "Future safety promotion",
            )
            engine = predictor if saved.engine_id == predictor.capabilities.engine_id else sequence
            replayed, _ = await registry.predict(engine, request)
            require(
                semantic(replayed.model_dump()) == semantic(saved.model_dump()),
                "Software forecast changed",
            )
            # After independent semantic replay, preserve original source IDs for
            # Registry validation of the historical conditioning edges.
            registry.accepted.pop(replayed.id, None)
            registry.accepted_requests.pop(replayed.id, None)
            registry.accepted[saved.id] = saved.model_copy(deep=True)
            registry.accepted_requests[saved.id] = request.model_copy(deep=True)
            if saved.horizon == 1:
                intermediate[saved.id] = saved
                if horizon == 1:
                    require(
                        request.state == state and request.actions == [root],
                        "Immediate root context",
                    )
                    endings.append(
                        {
                            "actions": [root.model_dump()],
                            "states": [saved.successor.model_dump()],
                            "prediction_ids": [saved.id],
                            "weight": 1.0,
                        }
                    )
            else:
                require(
                    request.state == state and request.actions[0] == root, "Sequence root context"
                )
                for action in request.actions[1:]:
                    transition = next(
                        t
                        for t in request.conditioning.transitions
                        if t.successor.id == action.state_id
                    )
                    proposals = await world.propose(transition.successor, 2)
                    require(
                        action.fingerprint in [a.fingerprint for a in proposals],
                        "Native continuation changed",
                    )
                endings.append(
                    {
                        "actions": [a.model_dump() for a in request.actions],
                        "states": [
                            t.successor.model_dump() for t in request.conditioning.transitions
                        ]
                        + [saved.successor.model_dump()],
                        "prediction_ids": [
                            t.prediction_id for t in request.conditioning.transitions
                        ]
                        + [saved.id],
                        "weight": None,
                    }
                )
        require(endings == future["paths"], "Forged/omitted software branch")

        # Independently enumerate the bounded native leaf set. Replaying only saved
        # records would otherwise overlook deliberately omitted valid alternatives.
        async def native_leaves(current, actions):
            if len(actions) == horizon:
                return [tuple(a.fingerprint for a in actions)]
            leaves = []
            for action in await world.propose(current, 2):
                successor = world.materialize(current, action)
                leaves.extend(await native_leaves(successor, [*actions, action]))
            return leaves

        expected_leaves = await native_leaves(world.materialize(state, root), [root])
        require(
            expected_leaves
            == [tuple(Action.model_validate(a).fingerprint for a in p["actions"]) for p in endings],
            "Omitted native alternative",
        )
        require(future["status"] == "estimated" and endings, "Unknown software forecast")
        expected_scores.append(software_scores(future, trace["goals"][0]))
    return expected_scores


async def audit_case(folder):
    meta = json.loads((folder / "case.json").read_text())
    store = Store("sqlite:///" + str(folder / "ledger.db"))
    artifacts = Artifacts(str(folder / "artifacts"))
    world = native_world(meta)
    require(meta["task"] == world.task.model_dump(), "Task declaration changed")
    executed_runs = []
    outcome_rows = []
    allruns = [*meta["prefix"], *meta["runs"]]
    authority_runs = [*allruns, *(r for runs in meta["training"].values() for r in runs)]
    initial_heads = await store.call("run_heads", authority_runs)
    verifiers = (
        [LocalHeuristic(world), LocalVerifier(world)]
        if meta["domain"] == "software"
        else [
            InformationBoundVerifier(world.task),
            InformationBoundVerifier(world.task, future=True),
        ]
    )
    verification_registry = Registry(verifiers)
    by_engine = {v.capabilities.engine_id: v for v in verifiers}
    for run in allruns:
        if any(e["kind"] == "outcome" for e in store.read_events(run)):
            executed_runs.append(run)
    snapshot = await TransitionDataset(store, {"decision": executed_runs}).snapshot()
    require(
        len(snapshot.transitions) == len(executed_runs) and not snapshot.unlabelled_receipts,
        "Unconfirmed or missing Actual transition",
    )
    rows = {r.run_id: r for r in snapshot.transitions}
    source = None
    if meta["domain"] == "queue":
        training = await TransitionDataset(store, meta["training"]).snapshot()
        require(
            not training.unlabelled_receipts and len(training.transitions) == 16,
            "Training authority",
        )
        require(
            set(r.run_id for r in training.transitions).isdisjoint(executed_runs),
            "Training/evaluation leak",
        )
        model = await TabularDynamicsTrainer().fit(
            TransitionDataset(store, meta["training"]), QueueServiceAdapter()
        )
        saved = DynamicsModel.load(artifacts, meta["model_artifact"], QueueServiceAdapter())
        require(model == saved, "Model artifact/dataset disagreement")
        anchor = State.model_validate(meta["anchor"])
        require(
            anchor.kind == "observed"
            and all(
                datetime.fromisoformat(r.after.state.timestamp)
                < datetime.fromisoformat(anchor.timestamp)
                for r in training.transitions
            ),
            "Future training data or invented anchor",
        )
        guard = QueueTemporalGuard(
            store, world.task, saved, meta["training"], episode_id="decision", initial=anchor
        )
        source = MonitoredQueueSource(store, guard, meta["training"])
    memory = EpisodicMemory(store)
    memory.run_ids = list(meta["prefix"])
    scored = 0
    errors = []
    choices = []
    gate_rejections = 0
    unresolved = 0
    unknown = 0
    preview_vetoes = 0
    for index, run in enumerate(meta["runs"]):
        events = store.read_events(run)
        runtime_task = world.task.model_copy(deep=True, update={"max_steps": 1})
        config = (
            store.get_run(run)["config"] if "config" in store.get_run(run) else store.get_run(run)
        )
        # Current Store exposes serialized task/policy in the run config payload.
        if isinstance(config, str):
            config = json.loads(config)
        policy = Policy.model_validate(meta["policy"])
        require(
            config["task"] == runtime_task.model_dump()
            and config["policy"] == meta["policy"]
            and config["cognitive_goals"] == meta["goals"],
            "Runtime configuration changed",
        )
        nodes = {
            e["data"]["node"]["id"]: e["data"]["node"]
            for e in events
            if e["kind"] == "node_updated"
        }
        for event in (e for e in events if e["kind"] == "gate_preview"):
            node = nodes[event["data"]["node_id"]]
            node_state, node_action = (
                State.model_validate(node["state"]),
                Action.model_validate(node["action"]),
            )
            forecasts = [Prediction.model_validate(p) for p in node["predictions"]]
            for saved in forecasts:
                require(saved.engine_id in by_engine, "Undeclared preview verifier")
                replayed, _ = await verification_registry.predict(
                    by_engine[saved.engine_id],
                    PredictionRequest(
                        state=node_state,
                        actions=[node_action],
                        horizon=saved.horizon,
                        claims=saved.requested_claims or [],
                        seed=runtime_task.seed,
                    ),
                )
                require(
                    semantic(replayed.model_dump()) == semantic(saved.model_dump()),
                    "Preview source disagrees with trusted fixture",
                )
            preview = gate(
                evaluate(forecasts, runtime_task, {}, state=node_state, action=node_action),
                node_state.id,
                node_action.fingerprint,
                policy,
                True,
            )
            saved_preview = event["data"]["gate"]
            require(
                str(preview.decision) == saved_preview["decision"]
                and preview.evidence_hash == saved_preview["evidence_hash"]
                and preview.policy_hash == saved_preview["policy_hash"],
                "Preview Gate changed",
            )
            preview_vetoes += preview.decision == "abstain"
        traces = [e["data"] for e in events if e["kind"] == "deliberation"]
        row = rows.get(run)
        if traces:
            require(len(traces) == 1, "Duplicate deliberation")
            trace = traces[0]
            state = State.model_validate(trace["state"])
            require(
                state.kind == "observed" and trace["goals"] == meta["goals"],
                "Deliberation Goal or authority changed",
            )
            require(
                trace["run_id"] == run and trace["belief"]["observed"] == trace["state"],
                "Belief/State binding",
            )
            records = await memory.retrieve(state.domain)
            require(
                [e.reference for e in records] == trace["memory_references"],
                "Memory authority changed",
            )
            expected_monitor = [*meta["prefix"], *meta["runs"][:index]]
            require(
                trace["monitor_runs"] == expected_monitor, "Monitoring manifest or time reversal"
            )
            if source:
                heads = await source.heads(expected_monitor)
                require(
                    {r: asdict(h) if h else None for r, h in heads.items()}
                    == trace["source_heads"],
                    "Source updates",
                )
                actions = [
                    Action.model_validate(f["action"]) for f in trace["simulation"]["candidates"]
                ]
                require(
                    [a.fingerprint for a in actions]
                    == [InformationQueueWorld.action(state, n).fingerprint for n in (3, 1, 0)],
                    "Queue candidates changed",
                )
                require(
                    trace["simulation"]["horizon"] == 3
                    and trace["simulation"]["continuation"]
                    == (
                        "explicit_action_sequence"
                        if meta["condition"] == "unsupported"
                        else "environment_only"
                    ),
                    "Queue continuation relabelled",
                )
                if meta["condition"] == "unsupported":
                    require(
                        all(
                            f["status"] == "unknown"
                            and f["reason"] == "unsupported_action_sequence"
                            and not f["records"]
                            for f in trace["simulation"]["candidates"]
                        ),
                        "Unsupported silently converted",
                    )
                    scores = [None] * 3
                else:
                    comparison = await source.compare(
                        state,
                        actions,
                        expected_monitor,
                        0.5 if meta["strategy"] == "fixed_prior" else None,
                    )
                    require(
                        semantic(comparison["health"]) == semantic(trace["simulation"]["health"]),
                        "Health changed",
                    )
                    predictions = comparison.get("predictions", [])
                    scores = []
                    for i, f in enumerate(trace["simulation"]["candidates"]):
                        available = (
                            comparison.get("status") == "estimated"
                            and comparison["health"]["status"] == "available"
                        )
                        if available:
                            p = predictions[i]
                            require(
                                semantic(p.model_dump()) == semantic(f["records"][0]["prediction"]),
                                "Queue forecast/model/conditions changed",
                            )
                            require(
                                f["paths"] == p.raw["branches"] and p.successor is None,
                                "Mean State or fake service branch",
                            )
                            require(f["status"] == "estimated", "Status hides known forecast")
                            scores.append(
                                queue_scores(
                                    f,
                                    state,
                                    trace["goals"][0],
                                    1 if meta["strategy"] == "short" else 3,
                                    1.0 if meta["strategy"] == "conservative" else 0,
                                )
                            )
                        else:
                            require(
                                f["status"] == "unknown" and not f["paths"], "Invalid model used"
                            )
                            scores.append(None)
            else:
                heads = await store.call("run_heads", trace["memory_runs"])
                require(
                    {r: asdict(h) if h else None for r, h in heads.items()}
                    == trace["source_heads"],
                    "Software source update",
                )
                scores = await replay_software(world, trace)
            require(scores == trace["scores"], "Goal score modified")
            rank = (
                sorted(range(len(scores)), key=lambda i: (-scores[i]["value"], i))
                if all(s is not None for s in scores)
                else []
            )
            ordered = [trace["simulation"]["candidates"][i]["fingerprint"] for i in rank]
            require(ordered == trace["ranked_fingerprints"], "Ranking modified")
            unknown += not bool(rank)
            qualifications = [e["data"] for e in events if e["kind"] == "candidate_qualification"]
            require(len(qualifications) == 1, "Missing Runtime assessment")
            batch = CandidateBatch.model_validate(qualifications[0]["batch"])
            require(batch.state == state and batch.run_id == run, "Qualification State/run binding")
            require(
                qualifications[0]["batch_hash"] == identity(qualifications[0]["batch"]),
                "Batch hash changed",
            )
            recomputed = []
            for c in batch.candidates:
                require(c.action.state_id == state.id, "Candidate binding changed")
                if not c.verification_performed:
                    require(
                        c.status in ("unexpanded", "unresolved", "invalid_contract"),
                        "Unverified qualified",
                    )
                    continue
                for saved in c.predictions:
                    require(saved.engine_id in by_engine, "Undeclared verifier")
                    replayed, _ = await verification_registry.predict(
                        by_engine[saved.engine_id],
                        PredictionRequest(
                            state=state,
                            actions=[c.action],
                            horizon=saved.horizon,
                            claims=saved.requested_claims or [],
                            seed=runtime_task.seed,
                        ),
                    )
                    require(
                        semantic(replayed.model_dump()) == semantic(saved.model_dump()),
                        "Verification source disagrees with trusted fixture",
                    )
                e = evaluate(c.predictions, runtime_task, {}, state=state, action=c.action)
                preview = gate(e, state.id, c.action.fingerprint, policy, True)
                status = {
                    "execute": "qualified",
                    "abstain": "rejected",
                    "verify_more": "unresolved",
                }[str(preview.decision)]
                require(status == c.status and e == c.evaluation, "Forged qualification/evaluation")
                decision = gate(e, state.id, c.action.fingerprint, policy, False)
                require(
                    decision.evidence_hash == c.gate.evidence_hash
                    and decision.policy_hash == c.gate.policy_hash,
                    "Gate evidence binding",
                )
                if status == "qualified":
                    recomputed.append(c)
                gate_rejections += status == "rejected"
                unresolved += status == "unresolved"
            selected = [e["data"] for e in events if e["kind"] == "candidate_selection"]
            require(len(selected) == 1, "Selection evidence missing")
            if row:
                require(
                    batch.complete and recomputed and ordered,
                    "Execution without qualified forecast",
                )
                chosen = min(recomputed, key=lambda c: ordered.index(c.action.fingerprint))
                require(
                    selected[0]["node_id"] == chosen.node_id and row.action == chosen.action,
                    "Value not used for actual choice",
                )
                require(row.before == state, "Executed input not authoritative predicted State")
                f = next(
                    c
                    for c in trace["simulation"]["candidates"]
                    if c["fingerprint"] == row.action.fingerprint
                )
                if source:
                    p = f["records"][0]["prediction"]
                    after = row.after.state.payload
                    error = (
                        sum(abs(p["vectors"][k][0] - after[k]) for k in ("queue", "delivered")) / 2
                    )
                else:
                    p = next(
                        r["prediction"] for r in f["records"] if r["prediction"]["horizon"] == 1
                    )
                    error = abs(
                        p["successor"]["payload"][trace["goals"][0]["metric"]]
                        - row.after.state.payload[trace["goals"][0]["metric"]]
                    )
                errors.append(error)
                scored += 1
            else:
                require(selected[0]["node_id"] is None, "Selected nonexistent Actual")
        if row:
            choices.append(row.action.model_dump())
            outcome_rows.append(row)
            authorizations = [e for e in events if e["kind"] == "authorization"]
            intents = [e for e in events if e["kind"] == "execution_intent"]
            outcomes = [e for e in events if e["kind"] == "outcome"]
            require(
                len(authorizations) == len(intents) == len(outcomes) == 1,
                "Multiple/absent execution binding",
            )
            auth = authorizations[0]["data"]
            intent = intents[0]["data"]
            require(
                auth["decision"] == "execute"
                and auth["state_id"] == row.before.id
                and auth["action_hash"] == row.action.fingerprint,
                "Wrong authorization",
            )
            require(intent["receipt"] == row.after.receipt, "Wrong intent receipt")
            require(
                authorizations[0]["seq"] < intents[0]["seq"] < outcomes[0]["seq"],
                "Execution event order",
            )
            require(
                datetime.fromisoformat(intents[0]["timestamp"]).timestamp() < auth["expires_at"],
                "Expired authorization",
            )
            execution = store.execution_record(row.after.receipt)
            require(
                execution["status"] == "complete"
                and execution["receipt"] == row.after.model_dump()
                and execution["action_hash"] == row.action.fingerprint,
                "Forged execution record",
            )
            node = nodes[outcomes[0]["data"]["node_id"]]
            final = gate(
                evaluate(
                    [Prediction.model_validate(p) for p in node["predictions"]],
                    runtime_task,
                    {},
                    state=row.before,
                    action=row.action,
                ),
                row.before.id,
                row.action.fingerprint,
                policy,
                False,
            )
            require(
                final.decision == "execute"
                and final.evidence_hash == auth["evidence_hash"]
                and final.policy_hash == auth["policy_hash"],
                "Final Gate/Authorization disagreement",
            )
            dispatch = [e["data"] for e in events if e["kind"] == "cognitive_multi_dispatch"]
            if traces:
                require(
                    len(dispatch) == 1 and dispatch[0]["prediction_hash"] == identity(traces[0]),
                    "Dispatch forecast seal",
                )
                fresh = State.model_validate(dispatch[0]["fresh_state"])
                require(
                    fresh.model_dump(exclude={"timestamp"})
                    == row.before.model_dump(exclude={"timestamp"})
                    and datetime.fromisoformat(fresh.timestamp)
                    >= datetime.fromisoformat(intents[0]["timestamp"])
                    and datetime.fromisoformat(fresh.timestamp).timestamp() < auth["expires_at"],
                    "Forged/stale dispatch observation",
                )
            await memory.remember(run)
        else:
            require(not any(e["kind"] == "execution_intent" for e in events), "Unknown side effect")
    actual = meta["summary"]
    require(
        actual["actions"] == choices and actual["actual_steps"] == len(choices),
        "Actual sequence summary",
    )
    require(actual["unsafe"] == sum(r.after.unsafe for r in outcome_rows), "Unsafe summary")
    require(actual["coverage_decision_rounds"] == scored / len(meta["runs"]), "Coverage summary")
    require(
        actual["mae_first_executed_step"] == (sum(errors) / len(errors) if errors else None),
        "Prediction error summary",
    )
    end = outcome_rows[-1].after.state if outcome_rows else State.model_validate(meta["initial"])
    goal = meta["goals"][0]
    progress = end.payload.get(goal["metric"], 0)
    require(
        actual["goal_progress"] == progress
        and actual["goal_achieved"] == (progress >= goal["target"]),
        "Goal summary",
    )
    reward = (
        sum(r.after.metrics["reward"] for r in snapshot.transitions)
        if source
        else progress - 0.05 * len(choices)
    )
    calls = [
        e["data"]["prediction"]
        for run in meta["runs"]
        for e in store.read_events(run)
        if e["kind"] == "prediction" and not e["data"]["cached"]
    ]
    require(
        actual["verifier_calls"] == sum(p["evidence"] != "inference" for p in calls)
        and actual["runtime_inference_calls"] == sum(p["evidence"] == "inference" for p in calls)
        and actual["runtime_engine_calls"] == len(calls),
        "Runtime calls summary",
    )
    require(abs(reward - actual["reward"]) < 1e-8, "Reward summary")
    require(
        abs(actual["goal_capped_utility"] - (reward - max(0, progress - goal["target"]))) < 1e-8,
        "Utility summary",
    )
    require(
        abs(
            actual["holding_cost"]
            - sum(r.after.metrics.get("holding_loss", 0) for r in outcome_rows)
        )
        < 1e-8,
        "Holding summary",
    )
    require(
        initial_heads == await store.call("run_heads", authority_runs), "Store updated during audit"
    )
    return {
        "domain": meta["domain"],
        "condition": meta["condition"],
        "strategy": meta["strategy"],
        "seed": meta["seed"],
        "executed": len(choices),
        "gate_preview_vetoes_root_and_hypothetical": preview_vetoes,
        "qualification_rejections_root_only": gate_rejections,
        "receipts": len(snapshot.transitions),
        "status": "passed",
    }


async def audit(bundle, protocol_path, report_path, results_path):
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    results = json.loads(results_path.read_text())
    reports = []
    root = Path(__file__).resolve().parents[1]
    sources = [*sorted((root / "src/preact").rglob("*.py")), root / "scripts/bench_deliberation.py"]
    current_source = identity(
        {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    )
    require(
        current_source == results["source_hash"],
        "Evaluation source changed; retain historical audit and rerun at new paths",
    )
    require(
        protocol["fixed_scoring"]
        == {
            "software_action_cost": 0.05,
            "software_uncertainty_cost": 0.1,
            "queue_spread_cost": 0.0,
            "fixed_prior_high_probability": 0.5,
        },
        "Unsupported scoring protocol",
    )
    require(
        results["protocol_hash"] == hashlib.sha256(protocol_bytes).hexdigest(), "Protocol modified"
    )
    for seed in protocol["seeds"]:
        for condition in protocol.get("software_conditions", ["workflow"]):
            for strategy in protocol["software_strategies"]:
                reports.append(await audit_case(bundle / f"software-{condition}-{strategy}-{seed}"))
        for condition, spec in protocol["queue_conditions"].items():
            for strategy in spec.get("strategies", protocol["queue_strategies"]):
                reports.append(await audit_case(bundle / f"queue-{condition}-{strategy}-{seed}"))
    require(len(reports) == len(results["cases"]), "Result case set omitted")
    for checked, public in zip(reports, results["cases"], strict=True):
        key = f"{checked['domain']}-{checked['condition']}-{checked['strategy']}-{checked['seed']}"
        metadata = json.loads((bundle / key / "case.json").read_text())
        for field in (
            "domain",
            "condition",
            "strategy",
            "seed",
            "summary",
            "cpu_seconds",
            "wall_seconds",
            "max_rss_platform_units",
        ):
            expected = metadata[field]
            if field == "summary":
                expected = {
                    **expected,
                    "actions": [
                        {
                            "name": a["name"],
                            "kind": a["kind"],
                            "fingerprint": Action.model_validate(a).fingerprint,
                            "amount": a["payload"].get("amount"),
                        }
                        for a in expected["actions"]
                    ],
                }
            require(public[field] == expected, f"Published {field} changed")
        require(metadata["source_hash"] == results["source_hash"], "Source provenance changed")
    require(
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == AUDITOR_HASH,
        "Auditor changed during audit",
    )
    report = {
        "status": "passed",
        "auditor_hash": AUDITOR_HASH,
        "protocol_hash": results["protocol_hash"],
        "source_hash": results["source_hash"],
        "cases": reports,
        "receipt_count": sum(r["receipts"] for r in reports),
    }
    report_path.write_text(json.dumps(report, indent=2))
    print(
        json.dumps({"status": "passed", "cases": len(reports), "receipts": report["receipt_count"]})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(audit(args.bundle, args.protocol, args.output, args.results))
