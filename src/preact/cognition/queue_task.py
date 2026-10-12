"""Queue-specific task/information arbitration using public dynamics and verified memory."""

import copy
import json
import time
from dataclasses import asdict, dataclass

from preact.core.candidates import CandidateBatch
from preact.core.models import identity
from preact.core.registry import Registry
from preact.domains.cognitive_queue import transition
from preact.domains.information_queue import InformationQueueWorld
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions

from .queue_model import candidate_value
from .queue_multi import QueueMultiPlanner
from .scheduler import ScheduleDecision


@dataclass(frozen=True)
class TaskInformationModel:
    """Uncalibrated persistence/readiness approximation; never a safety bound."""

    persistence: float = 0.95
    monitoring_samples: int = 8
    training_samples: int = 16

    def __post_init__(self):
        if (
            not 0.5 <= self.persistence < 1
            or self.monitoring_samples != 8
            or self.training_samples != 16
        ):
            raise ValueError("Invalid task-information model")


def lifecycle_recommendation(stage: str, effective_training: int, remaining: int) -> str:
    """Cognitive recommendation only; Recovery still validates all eligibility."""
    if stage == "collect" and effective_training >= 16 and remaining > 11:
        return "train_evaluate"
    return "await_promotion" if stage == "await_promotion" else "continue"


def schedule_queue(state, belief, goal, task_amount, reference_values, health, context, model):
    """Value of correcting a frozen task preference, with ordinary information credited.

    Each new effective sample is credited a fraction of the readiness requirement.
    Persistence caps the useful future decisions; no future schedule is consulted.
    This is an explicit heuristic VOI approximation, not a calibrated optimal policy.
    """
    p = belief.inferred["service_high_probability"].value
    values_low, values_high = reference_values["low"], reference_values["high"]
    weighted = {n: (1 - p) * values_low[n] + p * values_high[n] for n in (3, 1, 0)}
    low_action = max(values_low, key=lambda n: (values_low[n], -n))
    high_action = max(values_high, key=lambda n: (values_high[n], -n))
    gap = max(
        0.0,
        (1 - p) * values_low[low_action] + p * values_high[high_action] - weighted[task_amount],
    )
    _, available, _ = transition(state.payload, task_amount, 1)
    ordinary_information = float(available >= 3)
    remaining = state.payload["episode_ticks"] - state.payload["tick"]
    stage = context.get("stage", "monitor")
    effective = context.get("effective_training", 0)
    missing_training = max(0, model.training_samples - effective) if stage == "collect" else 0
    missing_monitor = context.get("missing_monitor", model.monitoring_samples)
    readiness = model.monitoring_samples
    if health and health["status"] == "insufficient_data":
        # Counts are receipt-validated. A stale but otherwise sufficient history
        # needs a fresh informative sample, not eight invented new observations.
        readiness = max(1, model.monitoring_samples - health["effective_count"])
    required = (
        missing_training + missing_monitor if stage in {"collect", "await_promotion"} else readiness
    )
    required = max(1, required)
    feasible = remaining > required + 3
    future = min(max(0, remaining - 1), 1 / (1 - model.persistence))
    useful_sample_value = gap * future / required * (1 - ordinary_information)
    opportunity = max(0.0, weighted[task_amount] - weighted[0])
    cost = state.payload["probe_cost"]
    net = useful_sample_value - opportunity - cost
    change = low_action != high_action
    unfinished = goal.progress(state) < goal.target
    probe = unfinished and feasible and change and net > 1e-9 and ordinary_information == 0
    lifecycle = lifecycle_recommendation(stage, effective, remaining)
    if not unfinished:
        lifecycle = "continue"
    reasons = ["positive_information_value" if probe else "task_value_or_free_information"]
    if ordinary_information:
        reasons.append("ordinary_action_identifies_service")
    if not feasible:
        reasons.append("insufficient_remaining_observation_budget")
    if not change:
        reasons.append("reference_actions_do_not_differ")
    return ScheduleDecision(
        action="information" if probe else "task",
        lifecycle=lifecycle,
        reasons=reasons,
        estimates={
            "p_high": p,
            "uncertainty": 4 * p * (1 - p),
            "health": health,
            "normal_informative_probability": ordinary_information,
            "available_work": available,
            "remaining_ticks": remaining,
            "required_effective_samples": required,
            "recovery_feasible": feasible,
            "effective_future_decisions": future,
            "conditional_best_actions": [low_action, high_action],
            "current_task_amount": task_amount,
            "per_decision_regret": gap,
            "information_gain": useful_sample_value,
            "opportunity_cost": opportunity,
            "measurement_cost": cost,
            "net_voi": net,
        },
        assumptions={
            **asdict(model),
            "value_function": "capped-progress-holding-root-cost/v1",
            "calibrated": False,
        },
    )


class QueueTaskPlanner(QueueMultiPlanner):
    """Task forecasts and scheduling stay separate from Runtime qualification."""

    def __init__(
        self,
        *args,
        context=None,
        information_model=TaskInformationModel(),
        strategy="task_aware",
        episode_memory_runs=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if strategy not in {"task_aware", "probe_centric", "no_recovery"}:
            raise ValueError("Unknown task scheduling strategy")
        self.strategy = strategy
        self.context = copy.deepcopy(context or {"stage": "monitor"})
        self.information_model = information_model
        self.schedule = None
        self.schedule_hash = ""
        self._memory_runs = ()
        self._episode_memory_runs = (
            tuple(episode_memory_runs) if episode_memory_runs is not None else None
        )

    def begin_round(self, run_id, policy, previous):
        super().begin_round(run_id, policy, previous)
        # CognitiveAgent re-observes after committing this round to Memory and
        # clearing the trace context. Retain only Runtime-assigned own run IDs;
        # retrieve still validates their actual complete receipts.
        declared = self._episode_memory_runs if self._episode_memory_runs is not None else self.runs
        self._episode_memory_runs = tuple(dict.fromkeys((*declared, run_id)))

    def _recovery_binding(self):
        recovery = self.source.recovery
        if (
            recovery is not None
            and recovery._active is None
            and self.source._statistical_deactivation is None
        ):
            raise ValueError("Task scheduler lost its validated promoted Recovery")

    @property
    def proposal_calls(self):
        return super().proposal_calls + 6

    def infer(self, state, experience):
        allowed = set(
            self._episode_memory_runs if self._episode_memory_runs is not None else self.runs
        )
        if any(r.run_id not in allowed for r in experience):
            raise ValueError("Scheduler memory is outside the declared World episode manifest")
        self._memory_runs = tuple(dict.fromkeys(r.run_id for r in experience))
        return super().infer(state, experience)

    def end_round(self):
        super().end_round()
        self.schedule = None
        self.schedule_hash = ""

    async def propose(self, belief, goals, width):
        self.schedule = None
        self.schedule_hash = ""
        try:
            self._recovery_binding()
            task_actions = await super().propose(belief, goals, width)
            if not task_actions:
                return []
            record = self.pending
            state = belief.observed
            heads = await self.source.store.call("run_heads", self._memory_runs)
            reference_cpu, reference_wall = time.process_time(), time.perf_counter()
            forecasts, reference_values = {}, {}
            actions = [InformationQueueWorld.action(state, n) for n in (3, 1, 0)]
            # Explicit local reference models, not the active learned model and
            # not hidden current/future service. Account every Registry call.
            for label, prior in (("low", 0.0), ("high", 1.0)):
                engine = QueueTemporalEngine(self.source.guard._task, prior=prior)
                comparison = await compare_actions(
                    Registry([engine]),
                    engine,
                    state,
                    actions,
                    horizon=min(3, state.payload["episode_ticks"] - state.payload["tick"]),
                )
                forecasts[label] = [p.model_dump() for p in comparison["predictions"]]
                if len(forecasts[label]) != 3 or any(
                    p["raw"]["status"] != "estimated" for p in forecasts[label]
                ):
                    raise ValueError("Scheduler reference inference unavailable")
                reference_values[label] = {}
                for action, prediction in zip(actions, forecasts[label], strict=True):
                    amount = action.payload["amount"]
                    reference_values[label][amount] = candidate_value(
                        prediction, state, goals[0], amount
                    )
                    self.usage.append(
                        {
                            "operation_kind": "local_information_reference",
                            "engine_id": prediction["engine_id"],
                            "engine_version": prediction["engine_version"],
                            "cost_usd": 0.0,
                            "cost_known": True,
                            "external_api_calls": 0,
                        }
                    )
            first = next(
                c
                for c in record["candidates"]
                if c["fingerprint"] == record["first_choice_fingerprint"]
            )
            decision = schedule_queue(
                state,
                belief,
                goals[0],
                first["amount"],
                reference_values,
                record["health"],
                self.context,
                self.information_model,
            )
            if self.strategy == "probe_centric":
                task = (
                    record["basis"] == "learned_model"
                    and self.context.get("tasks_in_epoch", 0) == 0
                )
                decision = decision.model_copy(
                    update={
                        "action": "task" if task else "information",
                        "reasons": ["declared_probe_centric_control"],
                    }
                )
            elif self.strategy == "no_recovery":
                decision = decision.model_copy(
                    update={"action": "task", "reasons": ["declared_no_recovery_control"]}
                )
            proposals = (
                [InformationQueueWorld.probe(state)]
                if decision.action == "information"
                else task_actions
            )
            if await self.source.store.call("run_heads", self._memory_runs) != heads:
                raise ValueError("Memory authority changed during scheduling")
            schedule = {
                "schema_version": "1",
                "strategy": self.strategy,
                "run_id": self.run_id,
                "state": state.model_dump(),
                "goal": goals[0].model_dump(),
                "belief": belief.model_dump(),
                "prediction_hash": self._seal,
                "decision": decision.model_dump(),
                "context": copy.deepcopy(self.context),
                "reference_forecasts": forecasts,
                "reference_horizon": min(3, state.payload["episode_ticks"] - state.payload["tick"]),
                "proposed_fingerprints": [a.fingerprint for a in proposals],
                "memory_runs": list(self._memory_runs),
                "memory_heads": {r: asdict(h) for r, h in heads.items()},
                "local_reference_calls": 6,
                "reference_cpu_seconds": time.process_time() - reference_cpu,
                "reference_wall_seconds": time.perf_counter() - reference_wall,
            }
            schedule = json.loads(json.dumps(schedule, allow_nan=False))
            await self.source.store.call("append", self.run_id, "cognitive_schedule", schedule)
            self.schedule = copy.deepcopy(schedule)
            self.schedule_hash = identity(schedule)
            return proposals
        except BaseException:
            self.pending = self.schedule = None
            self._seal = self.schedule_hash = ""
            raise

    def select(self, batch: CandidateBatch) -> str:
        self._recovery_binding()
        schedule = self.schedule
        if (
            schedule is None
            or identity(schedule) != self.schedule_hash
            or batch.run_id != self.run_id
        ):
            raise ValueError("Missing or changed schedule")
        if (
            self.pending is None
            or self.pending["mode"] != self.mode
            or self.pending["selection_rule"] != self.selection_rule
        ):
            raise ValueError("Task selection configuration changed")
        if (
            batch.state.model_dump() != schedule["state"]
            or not batch.complete
            or {c.action.fingerprint for c in batch.candidates}
            != set(schedule["proposed_fingerprints"])
        ):
            raise ValueError("Qualification does not match scheduler proposals")
        if schedule["decision"]["action"] == "task":
            return super().select(batch)
        qualified = [c for c in batch.candidates if c.status == "qualified"]
        if len(qualified) != 1 or qualified[0].action.kind != "probe_service":
            raise ValueError("Probe requires independent qualification")
        return qualified[0].node_id

    async def check(self, state, action=None):
        self._recovery_binding()
        await super().check(state, action)
        if self.schedule is None:
            return
        if (
            identity(self.schedule) != self.schedule_hash
            or self.schedule["context"] != self.context
            or self.schedule["strategy"] != self.strategy
            or self.schedule["decision"]["assumptions"]
            != {
                **asdict(self.information_model),
                "value_function": "capped-progress-holding-root-cost/v1",
                "calibrated": False,
            }
        ):
            raise ValueError("Scheduler basis changed before dispatch")
        heads = await self.source.store.call("run_heads", self.schedule["memory_runs"])
        if {r: asdict(h) for r, h in heads.items()} != self.schedule["memory_heads"]:
            raise ValueError("Scheduler memory authority changed before dispatch")
        events = await self.source.store.call("read_events", self.run_id)
        schedules = [e["data"] for e in events if e["kind"] == "cognitive_schedule"]
        if schedules != [self.schedule]:
            raise ValueError("Durable schedule record changed")
