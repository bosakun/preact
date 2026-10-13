"""Rebuild teachers, forecasts, qualifications and execution from retained authority."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from statistics import fmean

from preact.core.candidates import CandidateBatch
from preact.core.decision import evaluate, gate
from preact.core.models import Action, Policy, Prediction, State, Task, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.datasets.programs import LOGIC
from preact.domains.program import ProgramWorld
from preact.domains.program_learning import ProgramOutcomeAdapter
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_continuous import ContinuousQueueAdaptation
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import DynamicsModel, TransitionDataset
from preact.learning.transitions import require_disjoint
from scripts.audit_deliberation import queue_scores, semantic


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def metrics_from_actual(training, adapter, features):
    values = [
        adapter.targets(r)
        for r in training.transitions
        if adapter.features(r.before, r.action) == features
    ]
    require(len(values) >= 4, "Insufficient actual training outcomes")
    return {
        "goal_complete": fmean(v[0] for v in values),
        "test_pass_fraction": fmean(v[1] for v in values),
    }


async def program_sources(store, artifacts, checkpoint):
    candidate = checkpoint.get("candidate")
    if not candidate:
        return None
    record = json.loads(artifacts.read(candidate))
    adapter = ProgramOutcomeAdapter(ProgramWorld(LOGIC[0]))
    model = DynamicsModel.load(artifacts, record["model"], adapter)
    training = await TransitionDataset(store, record["training"]).snapshot()
    require(
        model.dataset_hash == identity([r.model_dump() for r in training.transitions]),
        "Model dataset source differs",
    )
    for cell in model.cells:
        metrics = metrics_from_actual(training, adapter, cell.features)
        require(
            list(metrics.values()) == list(cell.mean), "Model cell not derived from actual outcomes"
        )
    evaluation_digest = checkpoint.get("evaluation")
    if evaluation_digest:
        report = json.loads(artifacts.read(evaluation_digest))
        held = await TransitionDataset(store, report["episodes"]).snapshot()
        require_disjoint(training, held)
        errors = []
        first_prediction = min(f["state"]["timestamp"] for f in report["forecasts"])
        for source, cutoff in ((training, first_prediction), (held, report["cutoff"])):
            for row in source.transitions:
                event = next(
                    e
                    for e in store.read_events(row.run_id)
                    if e["seq"] == int(row.outcome_reference.rsplit(":", 1)[1])
                )
                require(
                    row.after.state.timestamp <= cutoff and event["timestamp"] <= cutoff,
                    "Future/unconfirmed receipt crossed model evaluation cutoff",
                )
        for f in report["forecasts"]:
            rows = [r for r in held.transitions if r.episode_id == f["episode"]]
            require(len(rows) == 1, "Held-out branch did not execute exactly once")
            row = rows[0]
            state, action = State.model_validate(f["state"]), Action.model_validate(f["action"])
            pred = Prediction.model_validate(f["prediction"])
            require(
                row.before.model_dump(exclude={"timestamp"})
                == state.model_dump(exclude={"timestamp"})
                and row.before.timestamp >= state.timestamp
                and row.action.fingerprint == action.fingerprint,
                "Held-out State/Action/time binding changed",
            )
            metrics = metrics_from_actual(training, adapter, adapter.features(state, action))
            require(
                pred.engine_version == model.version
                and pred.metrics == metrics
                and pred.successor is None
                and not pred.mandatory_checks
                and pred.evidence == "inference",
                "Forged evaluation forecast",
            )
            errors.append(abs(metrics["goal_complete"] - adapter.targets(row)[0]))
        scores = {"goal_mae": fmean(errors), "prior_mae": 0.5, "coverage": 1.0, "unknown_rate": 0.0}
        require(
            scores == report["scores"] and report["dataset_hash"] == held.dataset_hash,
            "Forged evaluation scores",
        )
        require(
            report["passed"] == (scores["goal_mae"] <= 0.25 and scores["goal_mae"] <= 0.5),
            "Forged evaluation acceptance",
        )
    if checkpoint.get("promotion"):
        promotion = json.loads(artifacts.read(checkpoint["promotion"]))
        require(
            promotion["candidate"] == candidate
            and promotion["evaluation"] == evaluation_digest
            and report["passed"],
            "Promotion not bound to accepted independent evaluation",
        )
    return model, training, adapter


async def audit_case(folder):
    metadata = json.loads((folder / "case.json").read_text())
    store, artifacts = (
        Store("sqlite:///" + str(folder / "ledger.db")),
        Artifacts(str(folder / "artifacts")),
    )
    snapshot = await TransitionDataset(store, metadata["episodes"]).snapshot()
    require(not snapshot.unlabelled_receipts, "Incomplete execution is not experience")
    rows = {r.run_id: r for r in snapshot.transitions}
    learning_events = store.read_events(metadata["journal"])
    checkpoints = {
        identity(e["data"]): e["data"]
        for e in learning_events
        if e["kind"] == "learning_checkpoint"
    }
    model_sources = {}
    if metadata["domain"] == "program":
        for digest, checkpoint in checkpoints.items():
            model_sources[digest] = await program_sources(store, artifacts, checkpoint["backend"])
        await program_sources(store, artifacts, metadata["backend_state"])
    qualified_total, unsafe, selected, total = 0, 0, 0, 0
    errors = []
    for episode, runs in metadata["episodes"].items():
        for run in runs:
            events = store.read_events(run)
            config = store.get_run(run)["config"]
            task, policy = (
                Task.model_validate(config["task"]).model_copy(
                    update={"stakes": config["task"]["stakes"]}
                ),
                Policy.model_validate(config["policy"]),
            )
            row = rows.get(run)
            traces = [e["data"] for e in events if e["kind"] == "deliberation"]
            binding = [e["data"] for e in events if e["kind"] == "learning_binding"]
            if binding:
                require(
                    len(binding) == 1 and binding[0]["journal"] == metadata["journal"],
                    "Learning/run binding mismatch",
                )
                digest = binding[0]["checkpoint_hash"]
                require(digest in checkpoints, "No durable checkpoint for this decision")
                require(
                    binding[0]["provenance"] == checkpoints[digest]["backend"],
                    "Wrong round backend version",
                )
            if traces:
                require(len(traces) == 1, "Duplicate deliberation")
                trace = traces[0]
                require(trace["goals"] == config["cognitive_goals"], "Deliberation Goal changed")
                state = State.model_validate(trace["state"])
                require(
                    trace["belief"]["observed"] == state.model_dump() and state.kind == "observed",
                    "Hypothesis used as actual State",
                )
                proposals = trace["simulation"]["candidates"]
                recalculated = []
                for index, future in enumerate(proposals):
                    action = Action.model_validate(future["action"])
                    require(
                        action.state_id == state.id and future["fingerprint"] == action.fingerprint,
                        "Candidate binding mismatch",
                    )
                    if metadata["domain"] == "program":
                        if future["basis"] == "learned_observed_goal":
                            model, training, adapter = model_sources[digest]
                            require(
                                binding[0]["provenance"]["promotion"]
                                and binding[0]["provenance"]["status"] == "available",
                                "Unpromoted model used",
                            )
                            pred = Prediction.model_validate(future["records"][0]["prediction"])
                            metrics = metrics_from_actual(
                                training, adapter, adapter.features(state, action)
                            )
                            require(
                                pred.metrics == metrics
                                and pred.engine_version == model.version
                                and pred.state_id == state.id
                                and pred.action_ids == [action.id]
                                and not pred.mandatory_checks
                                and pred.successor is None,
                                "Prediction is not the approved observed-outcome model",
                            )
                            value = metrics["goal_complete"]
                        elif future["basis"] == "fixed_goal_prior":
                            value = 0.5
                        elif future["basis"] == "bounded_receipt_collection":
                            value = -binding[0]["provenance"]["candidate_counts"][
                                identity(action.payload["files"]["program.py"])
                            ]
                        elif future["basis"] == "native_unknown_input_fallback":
                            model, training, adapter = model_sources[digest]
                            require(
                                any(
                                    model.lookup(
                                        adapter.features(state, Action.model_validate(f["action"]))
                                    )
                                    is None
                                    for f in trace["simulation"]["candidates"]
                                )
                                and all(
                                    f["basis"] == "native_unknown_input_fallback"
                                    for f in trace["simulation"]["candidates"]
                                ),
                                "Unknown-input fallback must cover the entire comparison",
                            )
                            pred = Prediction.model_validate(future["records"][0]["prediction"])
                            features = adapter.features(state, action)
                            supported = model.lookup(features) is not None
                            require(
                                pred.engine_version == model.version
                                and pred.state_id == state.id
                                and pred.action_ids == [action.id]
                                and not pred.mandatory_checks
                                and pred.successor is None
                                and pred.raw["status"] == ("estimated" if supported else "unknown")
                                and future["learned_status"] == pred.raw["status"]
                                and (
                                    pred.metrics == metrics_from_actual(training, adapter, features)
                                    if supported
                                    else not pred.metrics
                                    and pred.raw["reason"] == "unsupported_cell"
                                ),
                                "Fallback changed the original prediction binding",
                            )
                            value = -index
                        else:
                            require(
                                future["basis"] == "native_proposal_fallback", "Undeclared fallback"
                            )
                            value = -index
                        recalculated.append(
                            {"value": value, "rule": future["basis"], "calibrated": False}
                        )
                    elif action.kind == "probe_service":
                        require(
                            not future["records"]
                            and future["basis"] == "readiness_not_learned_value",
                            "Probe measurement predicted before execution",
                        )
                        recalculated.append(
                            {
                                "value": -state.payload["probe_cost"],
                                "rule": "bounded_health_readiness_collection",
                                "calibrated": False,
                            }
                        )
                    else:
                        checkpoint = binding[0]["provenance"]
                        epoch_data = checkpoint["restore"]["epoch"]
                        model = DynamicsModel.load(
                            artifacts, epoch_data["model_artifact"], QueueServiceAdapter()
                        )
                        guard = QueueTemporalGuard(
                            store,
                            Task.model_validate(epoch_data["task"]),
                            model,
                            epoch_data["training"],
                            episode_id=epoch_data["world_episode_id"],
                            initial=State.model_validate(epoch_data["anchor"]),
                            config=__import__(
                                "preact.learning.drift", fromlist=["DriftConfig"]
                            ).DriftConfig(**epoch_data["drift_config"]),
                            monitoring_epoch=epoch_data["epoch_id"],
                            allow_same_episode=True,
                        )
                        comparison = await guard.compare(
                            state, [action], checkpoint["monitor_runs"]
                        )
                        if future.get("basis") != "fixed_prior_fallback":
                            require(
                                comparison["status"] == "estimated"
                                and comparison["health"]["status"] == "available",
                                "Invalidated model scored",
                            )
                            require(
                                semantic(comparison["predictions"][0].model_dump())
                                == semantic(future["records"][0]["prediction"]),
                                "Queue learned forecast differs from receipt/Health replay",
                            )
                        else:
                            engine = QueueTemporalEngine(
                                Task.model_validate(epoch_data["task"]), prior=0.5
                            )
                            prior = await compare_actions(
                                Registry([engine]),
                                engine,
                                state,
                                [action],
                                horizon=min(
                                    3, state.payload["episode_ticks"] - state.payload["tick"]
                                ),
                            )
                            require(
                                semantic(prior["predictions"][0].model_dump())
                                == semantic(future["records"][0]["prediction"]),
                                "Fixed-prior fallback forecast changed",
                            )
                        require(
                            future["paths"]
                            == future["records"][0]["prediction"]["raw"]["branches"],
                            "Queue paths are not the bound prediction distribution",
                        )
                        recalculated.append(queue_scores(future, state, trace["goals"][0], 3))
                require(trace["scores"] == recalculated, "Saved value differs from recomputation")
                rank = sorted(range(len(proposals)), key=lambda i: (-recalculated[i]["value"], i))
                order = [proposals[i]["fingerprint"] for i in rank]
                require(trace["ranked_fingerprints"] == order, "Ranking changed")
                batches = [e["data"] for e in events if e["kind"] == "candidate_qualification"]
                require(len(batches) == 1, "No qualification evidence")
                batch = CandidateBatch.model_validate(batches[0]["batch"])
                require(
                    batch.state == state
                    and batch.run_id == run
                    and batches[0]["batch_hash"] == identity(batches[0]["batch"]),
                    "Runtime qualification binding changed",
                )
                qualified = []
                for candidate in batch.candidates:
                    if not candidate.verification_performed:
                        require(
                            candidate.status in {"unresolved", "unexpanded", "invalid_contract"},
                            "Unverified execution candidate",
                        )
                        continue
                    ev = evaluate(
                        candidate.predictions, task, {}, state=state, action=candidate.action
                    )
                    preview = gate(ev, state.id, candidate.action.fingerprint, policy, True)
                    status = {
                        "execute": "qualified",
                        "abstain": "rejected",
                        "verify_more": "unresolved",
                    }[str(preview.decision)]
                    require(
                        ev == candidate.evaluation and status == candidate.status,
                        "Qualification flag forged",
                    )
                    if status == "qualified":
                        qualified.append(candidate)
                qualified_total += len(qualified)
                if row:
                    require(
                        batch.complete and qualified, "Execution without fully resolved candidates"
                    )
                    choice = min(qualified, key=lambda c: order.index(c.action.fingerprint))
                    selections = [e["data"] for e in events if e["kind"] == "candidate_selection"]
                    require(
                        len(selections) == 1
                        and selections[0]["node_id"] == choice.node_id
                        and row.action == choice.action
                        and row.before == state,
                        "Ranking did not determine the executed action",
                    )
                    dispatch = [
                        e["data"] for e in events if e["kind"] == "cognitive_multi_dispatch"
                    ]
                    require(
                        len(dispatch) == 1
                        and dispatch[0]["prediction_hash"] == identity(trace)
                        and dispatch[0]["receipt"] == row.receipt,
                        "No prediction-to-intent dispatch binding",
                    )
                    fresh = State.model_validate(dispatch[0]["fresh_state"])
                    require(
                        fresh.model_dump(exclude={"timestamp"})
                        == state.model_dump(exclude={"timestamp"})
                        and fresh.timestamp >= state.timestamp,
                        "Fabricated dispatch observation",
                    )
                    selected += 1
                    if (
                        metadata["domain"] == "program"
                        and future.get("basis") == "learned_observed_goal"
                    ):
                        selected_future = next(
                            f for f in proposals if f["fingerprint"] == row.action.fingerprint
                        )
                        errors.append(
                            abs(
                                selected_future["value"]
                                - float(row.after.state.payload["goal_complete"])
                            )
                        )
            if row:
                auth = [e for e in events if e["kind"] == "authorization"]
                intents = [e for e in events if e["kind"] == "execution_intent"]
                outcomes = [e for e in events if e["kind"] == "outcome"]
                require(
                    len(auth) == len(intents) == len(outcomes) == 1,
                    "Multiple/missing execution bindings",
                )
                a = auth[0]["data"]
                require(
                    a["state_id"] == row.before.id
                    and a["action_hash"] == row.action.fingerprint
                    and a["decision"] == "execute",
                    "Authorization binding mismatch",
                )
                require(
                    auth[0]["seq"] < intents[0]["seq"] < outcomes[0]["seq"]
                    and intents[0]["data"]["receipt"] == row.receipt,
                    "Intent/outcome ordering mismatch",
                )
                require(
                    datetime.fromisoformat(intents[0]["timestamp"]).timestamp() < a["expires_at"],
                    "Expired authorization",
                )
                nodes = [
                    e["data"]["node"]
                    for e in events
                    if e["kind"] == "node_updated"
                    and e["data"]["node"]["id"] == outcomes[0]["data"]["node_id"]
                ]
                predictions = [Prediction.model_validate(p) for p in nodes[-1]["predictions"]]
                final = gate(
                    evaluate(predictions, task, {}, state=row.before, action=row.action),
                    row.before.id,
                    row.action.fingerprint,
                    policy,
                    False,
                )
                require(
                    final.decision == "execute"
                    and final.evidence_hash == a["evidence_hash"]
                    and final.policy_hash == a["policy_hash"],
                    "Final Gate differs from authorization",
                )
                require(
                    store.execution_record(row.receipt)["receipt"] == row.after.model_dump(),
                    "Forged actual outcome",
                )
                unsafe += row.after.unsafe
                total += 1
    if metadata["domain"] == "queue" and metadata["backend_state"]["epoch"]["promotion_artifact"]:
        restored = ContinuousQueueAdaptation(
            store, artifacts, metadata["backend_state"]["lifecycle_journal"]
        )
        last_run = metadata["episodes"]["source"][-1]
        fresh = [
            e["data"]["belief"]["observed"]
            for e in store.read_events(last_run)
            if e["kind"] == "cognitive_update"
        ]
        require(len(fresh) == 1, "Missing actual post-receipt fresh observation")
        latest = State.model_validate(fresh[0])
        require(
            latest.model_dump(exclude={"timestamp"})
            == snapshot.transitions[-1].after.state.model_dump(exclude={"timestamp"}),
            "Final fresh observation disagrees with the completed receipt",
        )
        epoch = metadata["backend_state"]["epoch"]
        manifest = [
            r.run_id
            for r in snapshot.transitions
            if r.before.payload["tick"] >= epoch["anchor"]["payload"]["tick"]
        ]
        await restored.restore(latest, manifest)
    return {
        "case": folder.name,
        "status": "passed",
        "task_receipts": total,
        "value_selected_executions": selected,
        "qualified_candidates": qualified_total,
        "unsafe": unsafe,
        "executed_h1_mae": fmean(errors) if errors else None,
    }


async def audit(bundle, output):
    results = []
    for folder in sorted(bundle.iterdir()):
        if (folder / "case.json").exists():
            results.append(await audit_case(folder))
    report = {
        "status": "passed",
        "cases": results,
        "limits": [
            "Append-only Store, single owner",
            "Known-candidate deterministic fixtures do not establish generalization",
            "Core checks recomputed from retained evidence; no real side effect replayed",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(audit(args.bundle, args.output)), indent=2))


if __name__ == "__main__":
    main()
