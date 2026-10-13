"""Bounded, non-authoritative deliberation over exchangeable domain forecasts."""

import copy
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Protocol

from preact.core.candidates import CandidateBatch
from preact.core.models import Action, State, identity

from .world import WorldPlanner


class PredictionAdapter(Protocol):
    """Forecasts and authority fences; no action execution or safety certification."""

    store: object
    max_calls: int
    usage: list[dict]

    def signature(self) -> str: ...
    async def heads(self, runs: list[str]) -> dict: ...
    async def candidates(self, state: State, width: int) -> list[Action]: ...
    async def forecast(self, state: State, actions: list[Action], runs: list[str]) -> dict: ...
    async def check_basis(self, record: dict) -> None: ...


class GoalAdapter(Protocol):
    """Soft value only; unknown forecasts have no invented score."""

    def signature(self) -> str: ...
    def evaluate(self, future: dict, state: State, goals: list) -> dict: ...


def head_records(heads):
    return {r: asdict(h) if h is not None else None for r, h in heads.items()}


class DeliberationPlanner:
    proposal_usage_complete = True

    def __init__(self, world, source: PredictionAdapter, value: GoalAdapter, *, prefix_runs=()):
        self.context = WorldPlanner(world)
        self.source, self.value = source, value
        self.prefix_runs = tuple(prefix_runs)
        self._world_bound = False
        self.pending = None
        self._seal = ""
        self.run_id = None
        self.runs = []
        self.usage = []
        self.records = []
        self._memory_runs = []
        self._references = []
        self._policy = None
        self._configuration = (source.signature(), value.signature())

    @property
    def proposal_calls(self):
        return self.source.max_calls

    def infer(self, state, experience):
        self._memory_runs = list(dict.fromkeys(e.run_id for e in experience))
        self._references = [e.reference for e in experience]
        return self.context.infer(state, experience)

    def begin_round(self, run_id, policy, previous):
        self.end_round()
        if self._configuration != (self.source.signature(), self.value.signature()):
            raise ValueError("Create a new planner for different adapters/settings")
        if policy.search or not 1 <= policy.width <= 3 or policy.max_nodes < policy.width:
            raise ValueError("Deliberation requires bounded flat, completely assessed roots")
        self.run_id, self._policy = run_id, policy.model_copy(deep=True)
        self.runs = list(dict.fromkeys((*self.prefix_runs, *previous)))

    def end_round(self):
        self.pending, self._seal, self.run_id, self._policy = None, "", None, None

    async def propose(self, belief, goals, width):
        self.pending, self._seal = None, ""
        self.usage.clear()
        self.source.usage.clear()
        cpu, wall = time.process_time(), time.perf_counter()
        try:
            if not self._world_bound or self.run_id is None or not goals:
                raise ValueError("Bind the existing selection World and a cognitive round first")
            state = State.model_validate(belief.observed.model_dump())
            runs = list(dict.fromkeys((*self.runs, *self._memory_runs)))
            signature, objective = self.source.signature(), self.value.signature()
            heads = await self.source.heads(runs)
            actions = await self.source.candidates(state.model_copy(deep=True), width)
            if (
                not actions
                or len(actions) > width
                or len({a.fingerprint for a in actions}) != len(actions)
                or any(a.state_id != state.id for a in actions)
            ):
                raise ValueError("Invalid or duplicate native proposal set")
            simulation = await self.source.forecast(state.model_copy(deep=True), actions, self.runs)
            futures = simulation["candidates"]
            if [c["fingerprint"] for c in futures] != [a.fingerprint for a in actions]:
                raise ValueError("Forecast candidate binding changed")
            scores = [
                self.value.evaluate(copy.deepcopy(f), state.model_copy(deep=True), goals)
                if f["status"] == "estimated"
                else None
                for f in futures
            ]
            usable = all(s is not None for s in scores)
            ranked = (
                sorted(range(len(actions)), key=lambda i: (-scores[i]["value"], i))
                if usable
                else []
            )
            if (
                self.source.signature() != signature
                or self.value.signature() != objective
                or await self.source.heads(runs) != heads
            ):
                raise ValueError("Deliberation dependencies changed during prediction")
            record = {
                "schema_version": "1",
                "run_id": self.run_id,
                "state": state.model_dump(),
                "belief": belief.model_dump(),
                "goals": [g.model_dump() for g in goals],
                "memory_references": list(self._references),
                "memory_runs": list(self._memory_runs),
                "monitor_runs": list(self.runs),
                "source_heads": head_records(heads),
                "adapter_signature": signature,
                "objective_signature": objective,
                "simulation": simulation,
                "scores": scores,
                "ranked_fingerprints": [actions[i].fingerprint for i in ranked],
                "status": "estimated" if usable else "unknown",
                "stop_reason": None if usable else "unknown_or_unsupported_future",
                "cpu_seconds": time.process_time() - cpu,
                "wall_seconds": time.perf_counter() - wall,
                "local_calls": len(self.source.usage),
                "external_api_calls": 0,
                "compute_dollar_cost": None,
            }
            record = json.loads(json.dumps(record, allow_nan=False))
            await self.source.store.call("append", self.run_id, "deliberation", record)
            self.records.append(copy.deepcopy(record))
            self.pending, self._seal = copy.deepcopy(record), identity(record)
            return actions if usable else []
        except BaseException:
            self.pending, self._seal = None, ""
            raise
        finally:
            self.usage.extend(copy.deepcopy(self.source.usage))
            self.source.usage.clear()

    def select(self, batch: CandidateBatch) -> str:
        record = self.pending
        if (
            record is None
            or identity(record) != self._seal
            or self.source.signature() != record["adapter_signature"]
            or self.value.signature() != record["objective_signature"]
            or batch.run_id != self.run_id
            or batch.state.model_dump() != record["state"]
            or not batch.complete
            or {c.action.fingerprint for c in batch.candidates}
            != {c["fingerprint"] for c in record["simulation"]["candidates"]}
        ):
            raise ValueError("Qualified candidates do not match frozen deliberation")
        qualified = [c for c in batch.candidates if c.status == "qualified"]
        order = record["ranked_fingerprints"]
        if not qualified or any(c.action.fingerprint not in order for c in qualified):
            raise ValueError("No qualified predicted candidate")
        return min(qualified, key=lambda c: order.index(c.action.fingerprint)).node_id

    async def check(self, state, action=None):
        if self.pending is None:
            return
        record = self.pending
        if (
            identity(record) != self._seal
            or self.source.signature() != record["adapter_signature"]
            or self.value.signature() != record["objective_signature"]
        ):
            raise ValueError("Deliberation basis changed")
        before = State.model_validate(record["state"])
        now, then = (
            datetime.fromisoformat(state.timestamp),
            datetime.fromisoformat(before.timestamp),
        )
        if (
            now.tzinfo is None
            or now < then
            or now > datetime.now(timezone.utc)
            or state.model_dump(exclude={"timestamp"}) != before.model_dump(exclude={"timestamp"})
        ):
            raise ValueError("Fresh observation differs from deliberation")
        runs = list(dict.fromkeys((*record["monitor_runs"], *record["memory_runs"])))
        if head_records(await self.source.heads(runs)) != record["source_heads"]:
            raise ValueError("Receipt/Store authority changed")
        await self.source.check_basis(record)
        events = await self.source.store.call("read_events", self.run_id)
        if [e["data"] for e in events if e["kind"] == "deliberation"] != [record]:
            raise ValueError("Durable deliberation changed")
        if action is not None:
            # Existing method contains no Queue-specific value or safety logic.
            from .queue_multi import QueueMultiPlanner

            QueueMultiPlanner.check_final_selection(self, events, action)
