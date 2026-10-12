"""Reconstruct task schedules, independent qualification and actual receipt outcomes."""

import argparse
import asyncio
import hashlib
import json
import math
import random
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from preact.cognition.information import InformationQueuePlanner
from preact.cognition.memory import EpisodicMemory
from preact.core.decision import evaluate, gate
from preact.core.models import Action, Policy, State, identity
from preact.core.store import Artifacts, Store
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.queue_temporal import QueueTemporalEngine
from preact.engines.queue_temporal_guard import QueueTemporalGuard, _HealthView
from preact.engines.queue_temporal_recovery import QueueModelRecovery
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.continuous import MonitoringEpoch
from preact.learning.drift import DriftConfig
from preact.learning.recovery import CandidateEvaluation, ModelPromotion
from scripts.audit_queue_multi_selection import paths, value, verified_predictions

AUDITOR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def one_event(events, kind):
    matches = [e for e in events if e["kind"] == kind]
    require(len(matches) == 1, "Missing or duplicate event: " + kind)
    return matches[0]


def canonical(value):
    return json.loads(json.dumps(value, allow_nan=False))


def reference_paths(state, amount, probability, horizon):
    rows, seen = [], set()
    for row in paths(state, amount, probability):
        service = tuple(row["service"][:horizon])
        if service in seen:
            continue
        seen.add(service)
        rows.append(
            {
                "service": list(service),
                "weight": math.prod(probability if s == 3 else 1 - probability for s in service),
                **{key: row[key][:horizon] for key in ("queue", "delivered", "pending_work")},
            }
        )
    return rows


def independently_schedule(schedule, candidate_values, task_amount, protocol):
    """Deliberately recompute the formula without calling the production scheduler."""
    state = State.model_validate(schedule["state"])
    p = schedule["belief"]["inferred"]["service_high_probability"]["value"]
    lo, hi = candidate_values["low"], candidate_values["high"]
    best_lo, best_hi = (max(v, key=lambda n: (v[n], -n)) for v in (lo, hi))
    scores = {n: (1 - p) * lo[n] + p * hi[n] for n in (3, 1, 0)}
    regret = max(0, (1 - p) * lo[best_lo] + p * hi[best_hi] - scores[task_amount])
    payload = state.payload
    available = payload["queue"] + sum(
        j["amount"] for j in payload["pending"] if j["due"] <= payload["tick"] + 1
    )
    normal_information = float(available >= 3)
    context, model = schedule["context"], protocol["information_model"]
    remaining = payload["episode_ticks"] - payload["tick"]
    missing = max(0, 16 - context["effective_training"]) if context["stage"] == "collect" else 0
    health = schedule["decision"]["estimates"]["health"]
    readiness = (
        max(1, 8 - health["effective_count"]) if health["status"] == "insufficient_data" else 8
    )
    if protocol["protocol_id"] in {"task-aware-pilot-v1", "task-aware-pilot-v2"}:
        # Preserve the exploratory pilot's declared constant readiness budget.
        readiness = 8
    required = max(
        1,
        missing + context["missing_monitor"]
        if context["stage"] in {"collect", "await_promotion"}
        else readiness,
    )
    future = min(max(0, remaining - 1), 1 / (1 - model["persistence"]))
    gain = regret * future / required * (1 - normal_information)
    opportunity = max(0, scores[task_amount] - scores[0])
    net = gain - opportunity - payload["probe_cost"]
    feasible = remaining > required + 3
    probe = (
        payload["delivered"] < schedule["goal"]["target"]
        and feasible
        and best_lo != best_hi
        and net > 1e-9
        and normal_information == 0
    )
    expected = {
        "p_high": p,
        "uncertainty": 4 * p * (1 - p),
        "normal_informative_probability": normal_information,
        "available_work": available,
        "remaining_ticks": remaining,
        "required_effective_samples": required,
        "recovery_feasible": feasible,
        "effective_future_decisions": future,
        "conditional_best_actions": [best_lo, best_hi],
        "current_task_amount": task_amount,
        "per_decision_regret": regret,
        "information_gain": gain,
        "opportunity_cost": opportunity,
        "measurement_cost": payload["probe_cost"],
        "net_voi": net,
    }
    for key, expected_value in expected.items():
        actual = schedule["decision"]["estimates"][key]
        require(
            math.isclose(actual, expected_value, abs_tol=1e-9)
            if isinstance(expected_value, (int, float))
            else actual == expected_value,
            "Information estimate changed: " + key,
        )
    lifecycle = (
        "train_evaluate"
        if context["stage"] == "collect" and context["effective_training"] >= 16 and remaining > 11
        else ("await_promotion" if context["stage"] == "await_promotion" else "continue")
    )
    if payload["delivered"] >= schedule["goal"]["target"]:
        lifecycle = "continue"
    require(
        schedule["decision"]["lifecycle"] == lifecycle, "Scheduler lifecycle recommendation changed"
    )
    return "information" if probe else "task"


async def audit_case(bundle, protocol):
    record = json.loads((bundle / "task-result.json").read_text())
    require(
        record["condition"] in protocol["conditions"] and record["seed"] in protocol["seeds"],
        "Undeclared source case",
    )
    spec = protocol["conditions"][record["condition"]]
    require(
        record["strategy"] in spec.get("strategies", protocol["strategies"]), "Undeclared strategy"
    )
    fixture = InformationQueueWorld(
        seed=record["seed"],
        ticks=protocol["episode_ticks"],
        target=protocol["goal"],
        capacity=protocol["capacity"],
        noise=0,
        submission_cost=spec.get("submission_cost", protocol["submission_cost"]),
        probe_cost=spec.get("probe_cost", protocol["probe_cost"]),
    )
    require(
        State.model_validate(record["initial"]).payload == fixture.payload,
        "Paired initial public conditions changed",
    )
    rng = random.Random(record["seed"])
    declared_services = []
    for tick in range(protocol["episode_ticks"]):
        regime = next(r for r in spec["regimes"] if r["start"] <= tick < r["end"])
        high = regime["service"] == 3
        if rng.random() < spec.get("noise", 0):
            high = not high
        declared_services.append(3 if high else 1)
    store, artifacts = (
        Store("sqlite:///" + str(bundle / "ledger.db")),
        Artifacts(str(bundle / "artifacts")),
    )
    snapshot = await TransitionDataset(store, {record["episode"]: record["runs"]}).snapshot()
    snapshot.verify_identity()
    require(
        snapshot.dataset_hash == record["dataset_hash"] and not snapshot.unlabelled_receipts,
        "Incomplete or changed transition dataset",
    )
    rows = snapshot.transitions
    require(len(rows) == record["summary"]["executed_ticks"], "Execution count changed")
    previous = State.model_validate(record["initial"])
    for row in rows:
        require(
            previous.model_dump(exclude={"timestamp"})
            == row.before.model_dump(exclude={"timestamp"})
            and datetime.fromisoformat(previous.timestamp)
            <= datetime.fromisoformat(row.before.timestamp),
            "World episode reset/discontinuity",
        )
        require(
            row.after.state.payload["tick"] == row.before.payload["tick"] + 1,
            "Actual tick alignment changed",
        )
        previous = row.after.state
        execution = await store.call("execution_record", row.receipt)
        require(
            execution["status"] == "complete" and execution["receipt"] == row.after.model_dump(),
            "Receipt authority changed",
        )
    require(
        previous.model_dump(exclude={"timestamp"})
        == State.model_validate(record["final"]).model_dump(exclude={"timestamp"}),
        "Final observation changed",
    )

    if rows:
        final_time = datetime.fromisoformat(record["final"]["timestamp"])
        last_events = await store.call("read_events", rows[-1].run_id)
        last_outcome = one_event(last_events, "outcome")
        require(
            datetime.fromisoformat(last_outcome["timestamp"]) <= final_time,
            "Final Observation precedes the completed outcome",
        )
    for rid in set(record["runs"]) - {r.run_id for r in rows}:
        events = await store.call("read_events", rid)
        require(
            not any(e["kind"] in {"outcome", "execution_intent"} for e in events),
            "Unaccounted or incomplete side effect in an abstained run",
        )
    guards, models, promotions, recovery_scopes = {}, {}, {}, {}
    recovery_audits = []
    if record["strategy"] != "fixed_task":
        events = await store.call("read_events", record["journal"])
        invalidations = [e for e in events if e["kind"] == "continuous_epoch_invalidated"]
        require(
            len(invalidations) == len(record["recoveries"]), "Recovery/invalidation history omitted"
        )
        for recovery_record in record["recoveries"]:
            manager = QueueModelRecovery(store, artifacts, recovery_record["journal"])
            origin = await manager._origin(recovery_record["origin"])
            require(
                origin.cutoff.model_dump() == recovery_record["cutoff"]
                and origin.episode_id == record["episode"],
                "First invalidation cutoff changed",
            )
            recovery_audits.append(
                {
                    "invalidation_tick": origin.cutoff.payload["tick"],
                    "training_effective": None,
                    "evaluation_effective": None,
                    "evaluation_passed": None,
                }
            )
            recovery_scopes[origin.monitoring_epoch] = (origin, recovery_record)
            if "candidate" in recovery_record:
                candidate, _, _, training = await manager._candidate(recovery_record["candidate"])
                teachers = [QueueServiceAdapter().targets(r) for r in training.transitions]
                require(
                    sum(int(t[3]) for t in teachers) == recovery_record["training_ordinary"]
                    and sum(int(t[2]) for t in teachers) == recovery_record["training_probe"],
                    "Training teacher source/censoring counts changed",
                )
                report = manager._load(recovery_record["evaluation"], CandidateEvaluation)
                recovery_audits[-1].update(
                    training_effective=candidate.effective_count,
                    evaluation_effective=report.effective_count,
                    evaluation_passed=report.passed,
                    model_version=candidate.model_version,
                    dataset_hash=candidate.dataset_hash,
                )
                replay, _ = await manager._evaluate(
                    recovery_record["candidate"], report.cases, report.cutoff
                )
                require(
                    replay == report
                    and report.passed == recovery_record["evaluation_passed"]
                    and report.reasons == recovery_record["evaluation_reasons"]
                    and report.scores == recovery_record["scores"],
                    "Candidate held-out evaluation changed",
                )
        epochs = [
            MonitoringEpoch.model_validate(json.loads(artifacts.read(e["data"]["artifact"])))
            for e in events
            if e["kind"] in {"continuous_epoch_started", "continuous_epoch_activated"}
        ]
        require(len(epochs) == len(record["epochs"]), "Epoch manifest changed")
        for i, epoch in enumerate(epochs):
            require(
                epoch.generation == i
                and epoch.world_episode_id == record["episode"]
                and epoch.parent_epoch == (epochs[i - 1].epoch_id if i else None),
                "Epoch lineage changed",
            )
            if i == 0:
                require(
                    epoch.anchor.model_dump() == record["initial"],
                    "Initial epoch lost its fresh observation binding",
                )
            model = DynamicsModel.load(artifacts, epoch.model_artifact, QueueServiceAdapter())
            refit = await TabularDynamicsTrainer().fit(
                TransitionDataset(store, epoch.training),
                QueueServiceAdapter(),
                TrainingConfig(min_samples=model.min_samples, seed=model.seed),
                limit=len(model.receipts),
            )
            require(
                model == refit
                and model.version == epoch.model_version
                and model.dataset_hash == epoch.dataset_hash,
                "Receipt-derived model provenance changed",
            )
            guards[epoch.epoch_id] = QueueTemporalGuard(
                store,
                epoch.task,
                model,
                epoch.training,
                episode_id=epoch.world_episode_id,
                initial=epoch.anchor,
                config=DriftConfig(**epoch.drift_config),
                monitoring_epoch=epoch.epoch_id,
                allow_same_episode=True,
            )
            models[epoch.epoch_id] = model
            if epoch.promotion_artifact:
                recovery = QueueModelRecovery(store, artifacts, epoch.recovery_journal)
                promotion = recovery._load(epoch.promotion_artifact, ModelPromotion)
                report = recovery._load(promotion.evaluation_artifact, CandidateEvaluation)
                _, _ = await recovery._promotion_guard(promotion, promotion.cutoff, promotion.runs)
                committed = [
                    e
                    for e in await recovery._journal()
                    if e["kind"] == "model_promotion_committed"
                    and e["data"]["artifact"] == epoch.promotion_artifact
                ]
                explicit = [
                    e
                    for e in await recovery._journal()
                    if e["kind"] == "harness_promotion_requested"
                ]
                require(
                    report.passed
                    and len(committed) == len(explicit) == 1
                    and explicit[0]["data"]["evaluation"] == promotion.evaluation_artifact
                    and explicit[0]["data"]["state"] == canonical(promotion.cutoff.model_dump())
                    and explicit[0]["seq"] < committed[0]["seq"],
                    "Promotion has no explicit validated operation",
                )
                promotions[epoch.epoch_id] = promotion.model_dump()

    totals = {
        "task_actions": 0,
        "probe_actions": 0,
        "ordinary_informative": 0,
        "probe_informative": 0,
        "fallback_task_actions": 0,
        "learned_task_actions": 0,
        "reward": 0.0,
        "holding_loss": 0.0,
        "probe_cost": 0.0,
        "unsafe": 0,
    }
    model_actions, goal_actions, amounts, actual_errors = {}, {}, [], []
    work_advancing, reference_disagreements = 0, 0
    for index, row in enumerate(rows):
        target = QueueServiceAdapter().targets(row)
        totals["ordinary_informative"] += int(target[3])
        totals["probe_informative"] += int(target[2])
        probe = row.action.kind == "probe_service"
        totals["probe_actions" if probe else "task_actions"] += 1
        amount = 0 if probe else row.action.payload["amount"]
        payload = row.after.state.payload
        processed = payload["delivered"] - row.before.payload["delivered"]
        holding = payload["holding_cost"] * payload["queue"]
        probe_cost = payload["probe_cost"] if probe else 0
        service = declared_services[row.before.payload["tick"]]
        possible_services = [row.after.metrics["measured_service"]] if probe else [1, 3]
        # Fixture-only conformance check after execution; censored service is
        # still not labelled as a teacher or exposed to the scheduler.
        require(
            transition(row.before.payload, amount, service)[0] == payload
            and (not probe or row.after.metrics["measured_service"] == service),
            "Actual outcome differs from the declared exogenous fixture",
        )
        require(
            any(
                transition(row.before.payload, amount, service)[0] == payload
                for service in possible_services
            ),
            "Observed conservation/dynamics changed",
        )
        actual_unsafe = payload["queue"] > payload["capacity"]
        require(
            row.after.unsafe == actual_unsafe
            and row.after.checks["capacity"] == (not actual_unsafe)
            and row.after.metrics["processed"] == processed,
            "Observed safety/throughput changed",
        )
        require(
            (
                row.after.metrics["measurement_tick"] == row.before.payload["tick"]
                and possible_services[0] in {1, 3}
            )
            if probe
            else not ({"measured_service", "measurement_tick"} & row.after.metrics.keys()),
            "Measurement leaked or lost its executed-tick reference",
        )
        reward = processed - holding - payload["submission_cost"] * amount - probe_cost
        require(
            math.isclose(reward, row.after.metrics["reward"], abs_tol=1e-9),
            "Actual reward/cost changed",
        )
        totals["reward"] += reward
        totals["holding_loss"] += holding
        totals["probe_cost"] += probe_cost
        totals["unsafe"] += row.after.unsafe
        amounts.append("probe" if probe else amount)
        work_advancing += processed > 0 or (not probe and amount > 0)
        events = await store.call("read_events", row.run_id)
        run = await store.call("get_run", row.run_id)
        # Preserve the owned Task's numeric representation in its claim hash.
        task = fixture.task
        require(
            identity(task.model_dump()) == identity(run["config"]["task"]),
            "Runtime Task representation changed",
        )
        policy = Policy.model_validate(run["config"]["policy"])
        expected_policy = (
            protocol["collection_policy"]
            if (
                record["strategy"] == "fixed_task"
                or (record["strategy"] == "probe_centric" and probe)
            )
            else protocol["policy"]
        )
        require(policy == Policy(**expected_policy), "Paired verification budget changed")
        trusts = next(e["data"]["engines"] for e in events if e["kind"] == "trust_snapshot")
        outcome = one_event(events, "outcome")
        predictions = await verified_predictions(
            store, artifacts, events, outcome["data"]["node_id"], row.before, row.action, task
        )
        assessment = evaluate(predictions, task, trusts, state=row.before, action=row.action)
        verdict = gate(assessment, row.before.id, row.action.fingerprint, policy, False)
        authorization = one_event(events, "authorization")
        intent = one_event(events, "execution_intent")
        require(
            verdict.decision == "execute"
            and authorization["data"]["evidence_hash"] == verdict.evidence_hash
            and authorization["data"]["policy_hash"] == verdict.policy_hash,
            "Final Gate proof changed",
        )
        require(
            authorization["seq"] < intent["seq"] < outcome["seq"]
            and intent["data"]["receipt"] == row.receipt
            and authorization["data"]["action_hash"] == row.action.fingerprint
            and authorization["data"]["state_id"] == row.before.id,
            "Authorization/Intent/Receipt binding changed",
        )
        if record["strategy"] == "fixed_task" or (record["strategy"] == "probe_centric" and probe):
            require(
                (amount == 1 and not probe) if record["strategy"] == "fixed_task" else probe,
                "Declared control action changed",
            )
            continue
        forecast = one_event(events, "cognitive_prediction")["data"]
        schedule = one_event(events, "cognitive_schedule")["data"]
        require(
            schedule["prediction_hash"] == identity(forecast)
            and schedule["state"] == row.before.model_dump()
            and forecast["state"] == schedule["state"],
            "Scheduler/prediction State binding changed",
        )
        require(
            schedule["context"]["monitoring_epoch"] == forecast["health"]["scope"],
            "Scheduler model epoch changed",
        )
        guard = guards[schedule["context"]["monitoring_epoch"]]
        health = await guard.health(row.before, forecast["monitor_runs"])
        require(canonical(asdict(health)) == forecast["health"], "Model Health does not replay")
        model = models[guard._monitoring_epoch]
        if guard._monitoring_epoch in promotions:
            require(
                forecast["promotion"] == canonical(promotions[guard._monitoring_epoch]),
                "Promoted prediction lost explicit Recovery binding",
            )
        memory = EpisodicMemory(store)
        memory.run_ids = record["runs"][max(0, index - 12) : index]
        experience = await memory.retrieve(row.before.domain)
        belief = InformationQueuePlanner("no_probe").infer(row.before, experience)
        require(
            belief.model_dump() == schedule["belief"]
            and [r.run_id for r in experience] == schedule["memory_runs"],
            "Scheduler Memory/Belief changed",
        )
        for rid, token in schedule["memory_heads"].items():
            require(
                asdict(store.run_heads([rid])[rid]) == token, "Scheduler authority source changed"
            )
        context = schedule["context"]
        activated_tick = (
            promotions[guard._monitoring_epoch]["cutoff"]["payload"]["tick"]
            if (guard._monitoring_epoch in promotions)
            else guard._initial.payload["tick"]
        )
        require(
            context["tasks_in_epoch"]
            == sum(
                r.action.kind == "submit"
                for r in rows[:index]
                if r.run_id in forecast["monitor_runs"]
                and r.before.payload["tick"] >= activated_tick
            ),
            "Scheduler epoch task counter changed",
        )
        if context["stage"] == "monitor":
            require(
                context["effective_training"] == 0 and context["missing_monitor"] == 8,
                "Scheduler monitor readiness changed",
            )
        else:
            origin, recovery_record = recovery_scopes[guard._monitoring_epoch]
            if context["stage"] == "collect":
                expected_effective = sum(
                    int(QueueServiceAdapter().targets(r)[0])
                    for r in rows[:index]
                    if r.before.payload["tick"] >= origin.cutoff.payload["tick"]
                )
                require(
                    context["effective_training"] == expected_effective
                    and context["missing_monitor"] == 8,
                    "Scheduler post-cutoff training counter changed",
                )
            elif context["stage"] in {"await_promotion", "rejected"}:
                manager = QueueModelRecovery(store, artifacts, recovery_record["journal"])
                from preact.learning.recovery import CandidateModel

                candidate = manager._load(recovery_record["candidate"], CandidateModel)
                require(
                    context["effective_training"] == candidate.effective_count,
                    "Scheduler candidate count changed",
                )
                if context["stage"] == "await_promotion":
                    candidate_model = DynamicsModel.load(
                        artifacts, candidate.model_artifact, QueueServiceAdapter()
                    )
                    anchor = State.model_validate(recovery_record["monitor_anchor"])
                    monitor_runs = [
                        r.run_id
                        for r in rows[:index]
                        if r.before.payload["tick"] >= anchor.payload["tick"]
                    ]
                    pending_guard = QueueTemporalGuard(
                        store,
                        guard._task,
                        candidate_model,
                        candidate.training,
                        episode_id=record["episode"],
                        initial=anchor,
                        monitoring_epoch=recovery_record["monitor_health"]["scope"],
                        config=DriftConfig(**protocol["detection"]),
                        allow_same_episode=True,
                    )
                    ready = await pending_guard.health(row.before, monitor_runs)
                    require(
                        context["missing_monitor"] == max(0, 8 - ready.effective_count),
                        "Scheduler candidate monitor count changed",
                    )
            else:
                raise ValueError("Unknown scheduler lifecycle stage")
        goal = schedule["goal"]["target"]
        require(goal == protocol["goal"], "Scheduler Goal changed")
        require(
            schedule["strategy"] == record["strategy"]
            and schedule["run_id"] == forecast["run_id"] == row.run_id
            and forecast["goals"] == [schedule["goal"]]
            and forecast["evaluation_function"] == "capped-progress-holding-root-cost/v1"
            and schedule["decision"]["estimates"]["health"] == forecast["health"]
            and schedule["decision"]["assumptions"]
            == {
                **protocol["information_model"],
                "value_function": "capped-progress-holding-root-cost/v1",
                "calibrated": False,
            },
            "Scheduler configuration/binding changed",
        )
        expected_sources = set(forecast["monitor_runs"]) | set(model.run_ids)
        if guard._monitoring_epoch in promotions:
            epoch = next(e for e in epochs if e.epoch_id == guard._monitoring_epoch)
            manager = QueueModelRecovery(store, artifacts, epoch.recovery_journal)
            promo = manager._load(epoch.promotion_artifact, ModelPromotion)
            candidate, origin, _, _ = await manager._candidate(promo.candidate_artifact)
            report = manager._load(promo.evaluation_artifact, CandidateEvaluation)
            expected_sources |= {
                epoch.recovery_journal,
                *candidate.source_runs,
                *(r for runs in origin.training.values() for r in runs),
                *(r for case in report.cases for b in case.branches for r in b.runs),
            }
        require(
            set(forecast["source_runs"]) == expected_sources == set(forecast["source_heads"]),
            "Prediction dependency manifest changed",
        )
        for rid, token in forecast["source_heads"].items():
            require(
                asdict(store.run_heads([rid])[rid]) == token, "Prediction source authority changed"
            )
        values = {}
        for label, probability in (("low", 0), ("high", 1)):
            values[label] = {}
            for amount0, predicted in zip(
                (3, 1, 0), schedule["reference_forecasts"][label], strict=True
            ):
                horizon = min(3, row.before.payload["episode_ticks"] - row.before.payload["tick"])
                require(
                    schedule.get("reference_horizon", 3) == horizon, "Information horizon changed"
                )
                branch = reference_paths(row.before, amount0, probability, horizon)
                require(
                    predicted["raw"]["branches"] == branch
                    and predicted["state_id"] == row.before.id
                    and predicted["evidence"] == "inference",
                    "Information reference prediction changed",
                )
                values[label][amount0] = value(row.before, branch, amount0, goal)
        cs = forecast["candidates"]
        require([c["amount"] for c in cs] == [3, 1, 0], "Task candidate set changed")
        for c in cs:
            a = Action.model_validate(c["action"])
            require(
                a.fingerprint
                == c["fingerprint"]
                == InformationQueueWorld.action(row.before, c["amount"]).fingerprint,
                "Candidate Action contract/binding changed",
            )
        learned = forecast["basis"] == "learned_model"
        if learned:
            require(health.status == "available", "Unavailable model used for task value")
            p = round(model.cells[0].count * model.cells[0].mean[1]) / round(
                model.cells[0].count * model.cells[0].mean[0]
            )
            view = _HealthView(QueueTemporalEngine(guard._task, model), health)
            require(
                forecast["engine_view_version"] == view.capabilities.version,
                "Prediction Engine view changed",
            )
            for c in cs:
                predicted = c["prediction"]
                a = Action.model_validate(c["action"])
                require(
                    predicted["state_id"] == row.before.id
                    and predicted["action_ids"] == [a.id]
                    and predicted["horizon"] == 3
                    and predicted["engine_id"] == view.capabilities.engine_id
                    and predicted["engine_version"] == view.capabilities.version
                    and predicted["raw"]["training_dataset_hash"] == model.dataset_hash
                    and predicted["raw"]["model_health"] == canonical(asdict(health))
                    and predicted["raw"]["health_version"] == health.version
                    and not predicted["mandatory_checks"],
                    "Forecast authority/provenance changed",
                )
                for claim in predicted["requested_claims"]:
                    require(
                        claim["state_id"] == row.before.id
                        and claim["action_ids"] == [a.id]
                        and claim["action_fingerprints"] == [a.fingerprint]
                        and claim["horizon"] == 3
                        and claim["continuation"] == "environment_only",
                        "Temporal claim binding changed",
                    )
                branches = paths(row.before, c["amount"], p)
                require(
                    c["prediction"]["raw"]["branches"] == branches
                    and c["prediction"]["raw"]["base_model_version"] == model.version
                    and c["prediction"]["evidence"] == "inference"
                    and math.isclose(
                        c["value"], value(row.before, branches, c["amount"], goal), abs_tol=1e-9
                    ),
                    "Learned value/model binding changed",
                )
            ranking = [
                c["fingerprint"] for c in sorted(cs, key=lambda c: (-c["value"], c["amount"]))
            ]
        else:
            require(all(c["value"] is None for c in cs), "Fallback has fabricated learned scores")
            from preact.cognition.models import Goal

            ranking = [
                a.fingerprint
                for a in InformationQueuePlanner("no_probe").propose(
                    belief, [Goal.model_validate(schedule["goal"])], 3
                )
            ]
        require(ranking == forecast["ranked_fingerprints"], "Task ranking changed")
        first = next(c for c in cs if c["fingerprint"] == ranking[0])
        expected_kind = independently_schedule(schedule, values, first["amount"], protocol)
        if record["strategy"] == "probe_centric":
            expected_kind = (
                "task" if learned and schedule["context"]["tasks_in_epoch"] == 0 else "information"
            )
        elif record["strategy"] == "no_recovery":
            expected_kind = "task"
        require(
            schedule["decision"]["action"] == expected_kind,
            "Scheduler action does not follow its value rule",
        )
        require(probe == (expected_kind == "information"), "Executed action differs from scheduler")
        usage = [e for e in events if e["kind"] == "agent_usage"]
        require(
            len(usage) == forecast["local_total_prediction_calls"] + 6
            and sum(e["data"]["operation_kind"] == "local_information_reference" for e in usage)
            == 6,
            "Local prediction usage not accounted",
        )
        batch_event = one_event(events, "candidate_qualification")["data"]
        batch = batch_event["batch"]
        require(
            identity(batch) == batch_event["batch_hash"]
            and batch["run_id"] == row.run_id
            and batch["state"] == row.before.model_dump(),
            "Candidate batch binding changed",
        )
        require(
            {Action.model_validate(e["action"]).fingerprint for e in batch["candidates"]}
            == set(schedule["proposed_fingerprints"]),
            "Scheduler proposal set changed",
        )
        qualified = []
        for entry in batch["candidates"]:
            action = Action.model_validate(entry["action"])
            ps = await verified_predictions(
                store, artifacts, events, entry["node_id"], row.before, action, task
            )
            ev = evaluate(ps, task, trusts, state=row.before, action=action)
            require(
                entry["predictions"] == canonical([p.model_dump() for p in ps]),
                "Candidate prediction records changed",
            )
            check = gate(ev, row.before.id, action.fingerprint, policy, True)
            classification = {
                "execute": "qualified",
                "verify": "unresolved",
                "abstain": "rejected",
            }[check.decision]
            require(
                entry["status"] == classification
                and entry["evaluation"] == canonical(ev.model_dump()),
                "Qualification/Gate changed",
            )
            require(classification != "unresolved", "Unresolved candidate was selected")
            if classification == "qualified":
                preview = gate(ev, row.before.id, action.fingerprint, policy, False)
                require(
                    entry["gate"]["evidence_hash"] == preview.evidence_hash
                    and entry["gate"]["policy_hash"] == preview.policy_hash
                    and entry["gate"]["decision"] == preview.decision,
                    "Candidate Gate Preview changed",
                )
                qualified.append(entry)
        require(
            qualified
            and batch_event["complete"]
            and batch_event["qualified_ids"] == [c["node_id"] for c in qualified],
            "No complete qualified set",
        )
        chosen = (
            qualified[0]
            if probe
            else min(
                qualified,
                key=lambda e: ranking.index(Action.model_validate(e["action"]).fingerprint),
            )
        )
        choice = one_event(events, "candidate_selection")["data"]
        require(
            choice["node_id"] == chosen["node_id"] == outcome["data"]["node_id"]
            and choice["state_id"] == row.before.id
            and choice["action_fingerprint"] == row.action.fingerprint
            and choice["evidence_hash"] == chosen["gate"]["evidence_hash"]
            and choice["policy_hash"] == chosen["gate"]["policy_hash"]
            and Action.model_validate(chosen["action"]) == row.action
            and choice["batch_hash"] == batch_event["batch_hash"],
            "Value/qualification selection did not reach actual receipt",
        )
        dispatch = one_event(events, "cognitive_multi_dispatch")
        fresh = State.model_validate(dispatch["data"]["fresh_state"])
        require(
            fresh.model_dump(exclude={"timestamp"}) == row.before.model_dump(exclude={"timestamp"})
            and intent["seq"] < dispatch["seq"] < outcome["seq"]
            and dispatch["data"]["receipt"] == row.receipt
            and dispatch["data"]["prediction_hash"] == identity(forecast)
            and dispatch["data"]["action_fingerprint"] == row.action.fingerprint
            and datetime.fromisoformat(dispatch["timestamp"]).timestamp()
            < authorization["data"]["expires_at"]
            and datetime.fromisoformat(intent["timestamp"])
            <= datetime.fromisoformat(fresh.timestamp)
            <= datetime.fromisoformat(dispatch["timestamp"]),
            "Fresh dispatch/receipt chronology changed",
        )
        if not probe:
            totals["learned_task_actions" if learned else "fallback_task_actions"] += 1
            if learned:
                model_actions[model.version] = model_actions.get(model.version, 0) + 1
                if row.before.payload["delivered"] < protocol["goal"]:
                    goal_actions[model.version] = goal_actions.get(model.version, 0) + 1
                low_amount = max(values["low"], key=lambda n: (values["low"][n], -n))
                reference_disagreements += amount != low_amount
                chosen_forecast = next(c for c in cs if c["fingerprint"] == row.action.fingerprint)
                actual_errors.extend(
                    abs(
                        sum(
                            b["weight"] * b[key][0]
                            for b in chosen_forecast["prediction"]["raw"]["branches"]
                        )
                        - payload[key]
                    )
                    for key in ("queue", "delivered")
                )
    for key, actual in totals.items():
        require(
            math.isclose(actual, record["summary"][key], abs_tol=1e-8),
            "Summary not derived from receipt: " + key,
        )
    require(
        model_actions == record["summary"]["model_task_actions"]
        and amounts == record["summary"]["action_sequence"],
        "Executed action history changed",
    )
    if "model_goal_active_actions" in record["summary"]:
        require(
            record["summary"]["model_goal_active_actions"] == goal_actions
            and record["summary"]["work_advancing_actions"] == work_advancing
            and record["summary"]["learned_vs_low_reference_action_differences"]
            == reference_disagreements,
            "Task contribution counters changed",
        )
    expected_mae = sum(actual_errors) / len(actual_errors) if actual_errors else None
    require(
        record["summary"]["one_step_state_mae"] == expected_mae
        and record["summary"]["one_step_prediction_coverage"]
        == (len(actual_errors) / (2 * len(rows)) if rows else 0),
        "Executed first-tick scoring/coverage changed",
    )
    require(
        record["summary"]["goal_success"]
        == (
            previous.payload["tick"] >= protocol["episode_ticks"]
            and previous.payload["delivered"] >= protocol["goal"]
        ),
        "Task completion semantics changed",
    )
    require(
        record["summary"]["delivered"] == previous.payload["delivered"], "Delivered summary changed"
    )
    actual_utility = min(protocol["goal"], previous.payload["delivered"]) - sum(
        r.after.state.payload["holding_cost"] * r.after.state.payload["queue"]
        + r.before.payload["submission_cost"]
        * (r.action.payload["amount"] if r.action.kind == "submit" else 0)
        + (r.before.payload["probe_cost"] if r.action.kind == "probe_service" else 0)
        for r in rows
    )
    require(
        math.isclose(actual_utility, record["summary"]["goal_capped_utility"], abs_tol=1e-8),
        "Goal-capped utility changed",
    )
    store.db.dispose()
    return {
        "condition": record["condition"],
        "strategy": record["strategy"],
        "receipt_count": len(rows),
        "task_actions": totals["task_actions"],
        "probe_actions": totals["probe_actions"],
        "unsafe": totals["unsafe"],
        "recoveries": recovery_audits,
        "lifecycle_compute": record["stages"],
        "status": "passed",
    }


async def audit(output, protocol_path, report_path, results_path=None):
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    public = json.loads(results_path.read_text()) if results_path else None
    if public:
        root = Path(__file__).resolve().parents[1]
        source_hash = identity(
            {
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (
                    root / "src/preact/cognition/queue_task.py",
                    root / "src/preact/cognition/scheduler.py",
                    root / "scripts/benchmark_queue_task.py",
                )
            }
        )
        require(
            public["protocol_id"] == protocol["protocol_id"]
            and public["protocol_hash"] == hashlib.sha256(protocol_bytes).hexdigest()
            and public["source_hash"] == source_hash,
            "Published protocol/source identity changed",
        )
    audits, records = [], []
    for label in protocol["conditions"]:
        for seed in protocol["seeds"]:
            for strategy in protocol["conditions"][label].get("strategies", protocol["strategies"]):
                bundle = output / f"{label}-{seed}-{strategy}"
                audits.append(await audit_case(bundle, protocol))
                record = json.loads((bundle / "task-result.json").read_text())
                require(
                    record["condition"] == label
                    and record["strategy"] == strategy
                    and record["seed"] == seed,
                    "Case directory/manifest binding changed",
                )
                records.append(record)
                if public:
                    matches = [
                        r
                        for r in public["results"]
                        if r["condition"] == label
                        and r["seed"] == seed
                        and r["strategy"] == strategy
                    ]
                    require(len(matches) == 1, "Published case omitted/duplicated")
                    for key, val in record["summary"].items():
                        require(matches[0][key] == val, "Published summary changed: " + key)
                    recovered = matches[0]["recoveries"]
                    require(
                        len(recovered) == len(record["recoveries"]), "Published recovery omitted"
                    )
                    for raw, published in zip(record["recoveries"], recovered, strict=True):
                        for key in (
                            "training_probe",
                            "training_ordinary",
                            "evaluation_passed",
                            "evaluation_reasons",
                            "scores",
                        ):
                            require(
                                published.get(key) == raw.get(key),
                                "Published recovery changed: " + key,
                            )
                        require(
                            published["invalidation_tick"] == raw["cutoff"]["payload"]["tick"]
                            and published["promotion_tick"]
                            == (
                                raw["monitor_state"]["payload"]["tick"]
                                if "promotion" in raw
                                else None
                            ),
                            "Published lifecycle timing changed",
                        )
    if public:
        require(len(public["results"]) == len(records), "Unexpected published cases")
    acceptance = None
    if "acceptance" in protocol:
        policy = protocol["acceptance"]
        primary = next(
            r
            for r in records
            if r["condition"] == policy["primary_condition"] and r["strategy"] == "task_aware"
        )
        summary = primary["summary"]
        checks = {
            "task_actions": summary["task_actions"] >= policy["minimum_task_actions"],
            "probe_rate": summary["probe_rate"] <= policy["maximum_probe_rate"],
            "promotions": summary["promotions"] >= policy["minimum_promotions"],
            "ordinary_training": sum(r["training_ordinary"] for r in primary["recoveries"])
            >= policy["minimum_ordinary_training_samples"],
            "goal_active_model_actions": len(summary["model_goal_active_actions"])
            == len(primary["epochs"])
            and all(
                n >= policy["minimum_goal_active_actions_per_activated_model"]
                for n in summary["model_goal_active_actions"].values()
            ),
            "unsafe": all(r["summary"]["unsafe"] == 0 for r in records),
        }
        acceptance = {"checks": checks, "passed": all(checks.values())}
    require(
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == AUDITOR_HASH,
        "Auditor source changed during execution",
    )
    result = {
        "protocol_id": protocol["protocol_id"],
        "protocol_hash": hashlib.sha256(protocol_bytes).hexdigest(),
        "auditor_hash": AUDITOR_HASH,
        "audits": audits,
        "acceptance": acceptance,
        "status": "passed",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(audit(args.bundle, args.protocol, args.output, args.results))))


if __name__ == "__main__":
    main()
