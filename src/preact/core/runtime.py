from __future__ import annotations

import asyncio
import heapq
import math
import time

from .calibration import Calibration, context_key
from .comparison import compare
from .decision import evaluate, gate
from .interfaces import World, cleanup_evidence
from .io import durable_io
from .models import (
    Action,
    Decision,
    Observation,
    Policy,
    PredictionRequest,
    State,
    TreeNode,
    identity,
    uid,
)
from .registry import Registry
from .store import Artifacts, Store
from .verification import admission_reasons, rank_verification


class Runtime:
    """The same decision loop drives every World; no domain dispatch inside Core."""

    def __init__(
        self, store: Store, artifacts: Artifacts, registry: Registry, policy: Policy | None = None
    ):
        self.store, self.artifacts, self.registry = store, artifacts, registry
        self.policy = Policy.model_validate((policy or Policy()).model_dump(warnings=False))
        self.cancelled = False
        self.calls = 0
        self.agent_calls = 0
        self.execution_calls = 0
        self.cost_known = True
        self.cost = 0.0
        self.nodes: list[TreeNode] = []
        self.calibration = Calibration(store)

    async def emit(self, kind: str, data: dict):
        return await self.store.call("append", self.run_id, kind, data)

    def check_task(self, world):
        if world.task != self.task:
            raise ValueError("Task constraints changed during the episode")

    async def observe(self, world):
        self.check_task(world)
        state = State.model_validate((await world.observe()).model_dump(warnings=False))
        self.check_task(world)
        if state.kind != "observed" or state.domain != self.task.domain:
            raise ValueError("Observer must return authoritative state in the task domain")
        return state

    def available(self):
        return (
            not self.cancelled
            and self.calls + self.agent_calls < self.policy.max_calls
            and self.cost < self.policy.max_cost_usd
            and time.monotonic() - self.started < self.policy.max_seconds
        )

    async def predict(self, node, engine, world, trust):
        if not self.available():
            return False
        cap = self.registry.declarations[id(engine)]
        reasons = admission_reasons(
            cap,
            {
                "calls": self.policy.max_calls - self.calls - self.agent_calls,
                "seconds": self.policy.max_seconds - (time.monotonic() - self.started),
                "cost_usd": self.policy.max_cost_usd - self.cost,
            },
        )
        if reasons:
            await self.emit(
                "verification_skipped",
                {
                    "node_id": node.id,
                    "engine_id": cap.engine_id,
                    "reasons": reasons,
                },
            )
            return False
        # Reserve every possible rollout even on failure; a timeout is not free work.
        reservation = min(
            engine.capabilities.max_samples,
            self.policy.max_calls - self.calls - self.agent_calls,
        )
        self.calls += reservation
        request = PredictionRequest(
            state=node.state,
            actions=[node.action],
            seed=self.task.seed,
            sample_budget=reservation,
            deadline_seconds=max(
                0.01, min(30, self.policy.max_seconds - (time.monotonic() - self.started))
            ),
        )
        await self.emit(
            "verification_requested",
            {"node_id": node.id, "engine": engine.capabilities.model_dump()},
        )
        try:
            prediction, cached = await self.registry.predict(engine, request)
            self.calls -= reservation - (0 if cached else prediction.sample_count)
            node.predictions.append(prediction)
            if not cached:
                self.cost += prediction.cost_usd
                self.cost_known &= prediction.raw.get("cost_known", True)
            digest = await durable_io(self.artifacts.json, prediction.model_dump())
            await self.emit(
                "prediction",
                {
                    "node_id": node.id,
                    "prediction": prediction.model_dump(),
                    "cached": cached,
                    "artifact": digest,
                },
            )
        except asyncio.CancelledError as error:
            cleanup = cleanup_evidence(error)
            if cleanup is not None:
                await self.emit(
                    "engine_cancelled",
                    {
                        "node_id": node.id,
                        "engine_id": engine.capabilities.engine_id,
                        "cleanup": cleanup,
                    },
                )
            raise
        except Exception as error:
            self.cost_known = False
            await self.emit(
                "engine_failed",
                {
                    "node_id": node.id,
                    "engine_id": engine.capabilities.engine_id,
                    "error": type(error).__name__,
                    "cleanup": cleanup_evidence(error),
                },
            )
        node.evaluation = evaluate(node.predictions, self.task, trust)
        await self.emit("node_updated", {"node": node.model_dump()})
        return True

    async def propose(self, world, state, width):
        remaining = self.policy.max_seconds - (time.monotonic() - self.started)
        if remaining <= 0 or not self.available():
            return []
        reservation = getattr(world, "proposal_calls", 0)
        if self.calls + self.agent_calls + reservation > self.policy.max_calls:
            await self.emit("proposal_skipped", {"reason": "Insufficient proposer call budget"})
            return []
        self.agent_calls += reservation
        completed = False
        try:
            isolated = state.model_copy(deep=True)
            self.check_task(world)
            actions = await asyncio.wait_for(world.propose(isolated, width), remaining)
            self.check_task(world)
            if isolated != state:
                raise ValueError("Proposer mutated its state input")
            validated = [Action.model_validate(a.model_dump(warnings=False)) for a in actions]
            completed = True
            return validated
        finally:
            usage = getattr(world, "usage", None)
            receipts = tuple(usage or [])
            # Legacy/failed proposers retain their conservative reservation. Only
            # adapters certifying complete receipts on successful return can refund
            # unneeded work; completed receipts always count even above reservation.
            charged = max(reservation, len(receipts))
            if completed and getattr(world, "proposal_usage_complete", False) is True:
                charged = len(receipts)
            self.agent_calls += charged - reservation
            # Account every completed receipt before any cancellable event write.
            # Otherwise cancellation can leave a partial sum incorrectly marked known.
            self.cost += sum(receipt.get("cost_usd", 0) for receipt in receipts)
            self.cost_known &= charged == len(receipts) and all(
                receipt.get("cost_known", False) is True for receipt in receipts
            )
            if usage:
                usage.clear()
            if charged < reservation:
                await self.emit(
                    "proposal_budget_refunded",
                    {
                        "reserved": reservation,
                        "charged": charged,
                        "reason": "Successful proposer certifies complete usage receipts",
                    },
                )
            for receipt in receipts:
                await self.emit("agent_usage", receipt)
            if completed and (
                (reservation > 0 and charged > reservation)
                or self.calls + self.agent_calls > self.policy.max_calls
            ):
                await self.emit(
                    "proposal_budget_exceeded",
                    {"reserved": reservation, "charged": charged},
                )
                raise ValueError("Proposer exceeded its declared call budget")

    async def search(self, world: World, state, trust):
        root = TreeNode(parent_id=None, state=state, status="observed", kind="state")
        self.nodes.append(root)
        await self.emit("node", {"node": root.model_dump()})
        engines = self.registry.eligible(state.domain)
        frontier, roots, count, serial = [], [], 0, 0
        seen = {}
        pending, visits = {}, {}

        async def expand(parent, successor):
            actions = await self.propose(world, successor, self.policy.width)
            await self.emit(
                "search_expanded",
                {
                    "parent_id": parent.id,
                    "state_id": successor.id,
                    "candidate_count": len(actions),
                    "reason": "Explore bounded proposals for a qualified successor"
                    if parent.action
                    else "Explore current observed state",
                },
            )
            pending[parent.id] = {
                "parent": parent,
                "state": successor,
                "actions": actions,
                "index": 0,
            }
            await widen(parent.id, 1)

        async def widen(parent_id, limit):
            nonlocal count, serial
            pool = pending[parent_id]
            parent, successor = pool["parent"], pool["state"]
            while (
                pool["index"] < min(limit, len(pool["actions"]))
                and count < self.policy.max_nodes
                and self.available()
            ):
                action = pool["actions"][pool["index"]]
                pool["index"] += 1
                await self.emit(
                    "search_widened",
                    {
                        "parent_id": parent.id,
                        "released": pool["index"],
                        "available": len(pool["actions"]),
                        "visits": visits.get(parent.id, 0),
                    },
                )
                try:
                    world.validate(successor, action)
                except ValueError:
                    await self.emit(
                        "action_rejected",
                        {"name": action.name, "reason": "Invalid action contract"},
                    )
                    continue
                node = TreeNode(
                    parent_id=parent.id,
                    state=successor,
                    action=action,
                    depth=parent.depth + 1,
                    cumulative_risk=parent.cumulative_risk,
                )
                self.nodes.append(node)
                if parent.id == root.id:
                    roots.append(node)
                await self.emit("node", {"node": node.model_dump()})
                count += 1
                if engines:
                    await self.predict(node, engines[0], world, trust)
                node.evaluation = evaluate(node.predictions, self.task, trust)
                probability = node.evaluation.success
                priority = (
                    0.5 if probability is None else probability
                ) + 0.25 * node.evaluation.uncertainty
                serial += 1
                heapq.heappush(frontier, (-priority, serial, node))

        await expand(root, state)
        while self.available():
            if not frontier:
                if count >= self.policy.max_nodes:
                    break
                for parent_id, pool in list(pending.items()):
                    await widen(parent_id, pool["index"] + 1)
                if not frontier:
                    break
            _, _, node = heapq.heappop(frontier)
            used = {p.engine_id for p in node.predictions}
            while self.available():
                candidates = [e for e in engines if e.capabilities.engine_id not in used]
                if not candidates:
                    break
                assessment = gate(
                    node.evaluation, node.state.id, node.action.fingerprint, self.policy, True
                )
                if assessment.decision == Decision.ABSTAIN:
                    break  # A supported hard veto cannot be repaired with extra votes.
                reason = "mandatory evidence"
                if node.evaluation.disagreement >= self.policy.disagreement_threshold:
                    reason = "engine disagreement"
                elif node.evaluation.uncertainty >= self.policy.uncertainty_threshold:
                    reason = "prediction uncertainty"
                if assessment.decision == Decision.EXECUTE and self.policy.adaptive:
                    break
                plans = rank_verification(
                    candidates,
                    self.registry.declarations,
                    node.evaluation,
                    self.task,
                    trust,
                    {
                        "calls": self.policy.max_calls - self.calls - self.agent_calls,
                        "seconds": self.policy.max_seconds - (time.monotonic() - self.started),
                        "cost_usd": self.policy.max_cost_usd - self.cost,
                    },
                )
                await self.emit(
                    "verification_allocation",
                    {
                        "node_id": node.id,
                        "plans": [plan for _, plan in plans],
                        "adaptive": self.policy.adaptive,
                    },
                )
                admitted = {id(engine) for engine, plan in plans if plan["admissible"]}
                ordered = [engine for engine, _ in plans] if self.policy.adaptive else candidates
                engine = next((e for e in ordered if id(e) in admitted), None)
                if engine is None:
                    break
                used.add(engine.capabilities.engine_id)
                await self.emit(
                    "escalation",
                    {
                        "node_id": node.id,
                        "reason": reason,
                        "engine_id": engine.capabilities.engine_id,
                    },
                )
                await self.predict(node, engine, world, trust)
                if node.evaluation.violations or any(
                    v is False for v in node.evaluation.checks.values()
                ):
                    break
            result = gate(
                node.evaluation, node.state.id, node.action.fingerprint, self.policy, False
            )
            node.status = "verified" if result.decision == Decision.EXECUTE else "rejected"
            node.value = node.evaluation.utility
            node.cumulative_risk = min(1.0, node.cumulative_risk + node.evaluation.risk_upper)
            await self.emit("gate_preview", {"node_id": node.id, "gate": result.model_dump()})
            await self.emit("node_updated", {"node": node.model_dump()})
            successors = [p.successor for p in reversed(node.predictions) if p.successor]
            stochastic = [p.outcomes for p in reversed(node.predictions) if p.outcomes]
            if (
                result.decision == Decision.EXECUTE
                and self.policy.search
                and (successors or stochastic)
                and node.depth < self.policy.max_depth
                and (not successors or not world.complete(successors[0]))
                and node.cumulative_risk <= self.policy.max_risk
            ):
                if stochastic:
                    for outcome in stochastic[0]:
                        chance = TreeNode(
                            parent_id=node.id,
                            state=outcome.state,
                            kind="outcome",
                            label=outcome.label,
                            outcome_probability=outcome.probability,
                            depth=node.depth,
                            status="imagined_outcome",
                            cumulative_risk=node.cumulative_risk,
                            value=1.75 if world.complete(outcome.state) else 0,
                        )
                        self.nodes.append(chance)
                        await self.emit("node", {"node": chance.model_dump()})
                        if not world.complete(outcome.state):
                            await expand(chance, outcome.state)
                else:
                    successor = successors[0]
                    if successor.id in seen:
                        node.reused_from = seen[successor.id]
                        await self.emit(
                            "search_reused",
                            {
                                "node_id": node.id,
                                "reused_from": node.reused_from,
                                "reason": "Equivalent successor already expanded; avoid duplicate descendant work",
                            },
                        )
                    else:
                        seen[successor.id] = node.id
                        await expand(node, successor)
            else:
                reason = (
                    "Decision Gate rejected this branch"
                    if result.decision != Decision.EXECUTE
                    else "Search depth limit"
                    if node.depth >= self.policy.max_depth
                    else "Cumulative predicted risk limit"
                    if node.cumulative_risk > self.policy.max_risk
                    else "Flat search condition"
                    if not self.policy.search
                    else "Predicted goal is complete"
                    if successors and world.complete(successors[0])
                    else "No successor evidence available"
                )
                await self.emit(
                    "search_pruned",
                    {
                        "node_id": node.id,
                        "scope": "descendant expansion",
                        "reason": reason,
                        "first_action_qualified": result.decision == Decision.EXECUTE,
                    },
                )
            ancestor = node.parent_id
            by_id = {n.id: n for n in self.nodes}
            while ancestor:
                visits[ancestor] = visits.get(ancestor, 0) + 1
                ancestor = by_id[ancestor].parent_id
            for parent_id in list(pending):
                # Progressive widening explores additional candidates as a state's
                # subtree receives work. Mandatory failures also free room for siblings.
                await widen(parent_id, math.ceil(math.sqrt(visits.get(parent_id, 0) + 1)))

        if not self.available() or count >= self.policy.max_nodes:
            await self.emit(
                "search_budget_exhausted",
                {
                    "nodes": count,
                    "calls": self.calls,
                    "agent_calls": self.agent_calls,
                    "cost_usd": self.cost,
                    "cost_known": self.cost_known,
                    "elapsed_seconds": time.monotonic() - self.started,
                    "reason": "A declared search resource bound stopped expansion",
                },
            )
        # Unknown or inferred outcome weights use worst-case continuation, not invented odds.
        current_nodes = [n for n in self.nodes if n is root or n.id in {r.id for r in roots}]
        current_ids = {root.id}
        for node in self.nodes:
            if node.parent_id in current_ids:
                current_ids.add(node.id)
                current_nodes.append(node)
        for node in sorted(
            {n.id: n for n in current_nodes}.values(),
            key=lambda n: (-n.depth, 0 if n.kind == "outcome" else 1),
        ):
            children = [
                c
                for c in current_nodes
                if c.parent_id == node.id and c.status in {"verified", "imagined_outcome"}
            ]
            if children:
                before = node.value
                if all(c.kind == "outcome" for c in children):
                    known = all(
                        c.outcome_probability.value is not None
                        and c.outcome_probability.measured
                        and c.outcome_probability.uncertainty <= self.policy.uncertainty_threshold
                        for c in children
                    )
                    continuation = (
                        sum(c.outcome_probability.value * c.value for c in children)
                        if known
                        else min(c.value for c in children)
                    )
                else:
                    continuation = max(c.value for c in children)
                node.value = max(node.value, 0.95 * continuation)
                await self.emit(
                    "search_backed_up",
                    {
                        "node_id": node.id,
                        "before": before,
                        "value": node.value,
                        "continuation": continuation,
                        "discount": 0.95,
                        "rule": "Retain optimistic immediate value and bounded continuation",
                        "outcome_aggregation": "expected"
                        if all(c.kind == "outcome" for c in children) and known
                        else "worst_case"
                        if all(c.kind == "outcome" for c in children)
                        else "best_qualified_child",
                    },
                )
                await self.emit("node_updated", {"node": node.model_dump()})
        qualified = [n for n in roots if n.status == "verified"]
        selected = max(qualified, key=lambda n: n.value) if qualified else None
        await self.emit(
            "search_selected",
            {
                "node_id": selected.id if selected else None,
                "reason": "Highest backed-up value among qualified first actions"
                if selected
                else "No root action passed the evidence gate",
                "candidates": [
                    {"node_id": n.id, "name": n.action.name, "status": n.status, "value": n.value}
                    for n in roots
                ],
                "scope": "Only the first action is authorized; observe and replan after execution",
            },
        )
        return selected

    async def run(
        self,
        world: World,
        run_id: str | None = None,
        direct: bool = False,
        forecast_selected: bool = False,
    ):
        deadline = asyncio.timeout(self.policy.max_seconds)
        try:
            async with deadline:
                return await self._run(world, run_id, direct, forecast_selected)
        except TimeoutError:
            if deadline.expired():
                pending = await self.store.call("pending_execution", self.run_id)
                status = "interrupted" if pending else "failed"
                await self.store.call(
                    "set_status",
                    self.run_id,
                    status,
                    {
                        "error": "EpisodeDeadlineExceeded",
                        "reason": "Reconcile pending execution"
                        if pending
                        else "Episode budget exhausted",
                        "cost_known": False,
                        "calls": self.calls,
                        "agent_calls": self.agent_calls,
                    },
                )
                await self.emit("failed", {"error": "EpisodeDeadlineExceeded", "status": status})
            raise

    async def _run(
        self,
        world: World,
        run_id: str | None = None,
        direct: bool = False,
        forecast_selected: bool = False,
    ):
        self.task = world.task.model_copy(deep=True)
        # Retain identity even if cancellation arrives during the initial commit.
        self.run_id = run_id or uid()
        self.started = time.monotonic()
        unsafe = False
        executed = 0
        observation = None
        try:
            if run_id is None:
                await self.store.call(
                    "create_run",
                    {"task": world.task.model_dump(), "policy": self.policy.model_dump()},
                    run_id=self.run_id,
                )
            await self.store.call("set_status", self.run_id, "running")
            for step in range(self.task.max_steps):
                if self.cancelled:
                    break
                state = await self.observe(world)
                await self.emit("observed", {"state": state.model_dump(), "step": step})
                if world.complete(state):
                    break
                context = context_key(
                    state.domain,
                    self.task.id,
                    1,
                    self.task.success_metric,
                    self.task.risk_metric,
                    state.provenance,
                )
                trust = (
                    await self.store.call(self.calibration.snapshot, context)
                    if self.policy.calibration
                    else {}
                )
                await self.emit("trust_snapshot", {"context": context, "engines": trust})
                if direct:
                    candidates = await self.propose(world, state, 1)
                    if not candidates:
                        await self.emit(
                            "decision",
                            {"decision": "abstain", "reasons": ["No further candidate actions"]},
                        )
                        break
                    action = candidates[0]
                    world.validate(state, action)
                    node = TreeNode(parent_id=None, state=state, action=action, status="selected")
                    self.nodes.append(node)
                    await self.emit("node", {"node": node.model_dump()})
                    if forecast_selected:
                        engines = self.registry.eligible(state.domain)
                        if engines:
                            # Chosen-action logging never searches alternatives or
                            # changes the baseline decision. Its cost is recorded.
                            await self.emit("selected_action_forecast", {"node_id": node.id})
                            await self.predict(node, engines[0], world, trust)
                else:
                    node = await self.search(world, state, trust)
                    if node is None:
                        await self.emit(
                            "decision",
                            {
                                "decision": "abstain",
                                "reasons": [
                                    "No candidate satisfied the evidence gate within the budget"
                                ],
                            },
                        )
                        break
                    fresh = await self.observe(world)
                    if fresh.id != state.id:
                        await self.emit(
                            "decision",
                            {"decision": "abstain", "reasons": ["State changed during search"]},
                        )
                        break
                    decision = gate(
                        node.evaluation, fresh.id, node.action.fingerprint, self.policy, False
                    )
                    if decision.decision != Decision.EXECUTE:
                        break
                    if (
                        time.time() >= decision.expires_at
                        or self.cost > self.policy.max_cost_usd
                        or time.monotonic() - self.started >= self.policy.max_seconds
                    ):
                        await self.emit(
                            "decision",
                            {"decision": "abstain", "reasons": ["Authorization or budget expired"]},
                        )
                        break
                    await self.emit("authorization", decision.model_dump())
                if self.cancelled:
                    break
                node.status = "selected"
                await self.emit(
                    "decision",
                    {
                        "decision": "execute",
                        "node_id": node.id,
                        "action": node.action.model_dump(),
                        "direct": direct,
                    },
                )
                # Intent must be durable before any side effect.
                receipt = await self.store.call(
                    "intent", self.run_id, state.id, node.action.fingerprint
                )
                await self.emit("execution_intent", {"node_id": node.id, "receipt": receipt})
                # A durable write can outlast authorization. Recheck at dispatch,
                # and distinguish known non-execution from an uncertain receipt.
                expired = (
                    self.cancelled
                    or self.cost > self.policy.max_cost_usd
                    or time.monotonic() - self.started >= self.policy.max_seconds
                    or (
                        not direct
                        and (
                            time.time() >= decision.expires_at
                            or decision.action_hash != node.action.fingerprint
                            or decision.policy_hash
                            != identity(
                                {
                                    "policy": self.policy.model_dump(),
                                    "stakes": node.evaluation.stakes,
                                }
                            )
                        )
                    )
                )
                if expired:
                    reason = "Authorization or budget expired before dispatch"
                    await self.store.call("abort_execution", receipt, reason)
                    node.status = "rejected"
                    await self.emit("node_updated", {"node": node.model_dump()})
                    if not direct:
                        await self.emit(
                            "gate_preview",
                            {
                                "node_id": node.id,
                                "gate": {
                                    **decision.model_dump(),
                                    "decision": Decision.ABSTAIN,
                                    "reasons": [reason],
                                },
                            },
                        )
                    await self.emit(
                        "execution_aborted",
                        {"node_id": node.id, "receipt": receipt, "reason": reason},
                    )
                    await self.emit("decision", {"decision": "abstain", "reasons": [reason]})
                    break
                observation = Observation.model_validate(
                    (await world.execute(node.action, receipt)).model_dump(warnings=False)
                )
                if (
                    observation.receipt != receipt
                    or observation.state.kind != "observed"
                    or observation.state.domain != state.domain
                ):
                    raise ValueError("Executor returned an invalid observation/receipt")
                await self.store.call("complete_execution", receipt, observation.model_dump())
                self.execution_calls += observation.execution_calls
                self.cost_known &= observation.cost_usd is not None
                self.cost += observation.cost_usd or 0
                executed += 1
                unsafe |= observation.unsafe
                node.actual = observation.model_dump()
                node.status = "executed"
                await self.emit(
                    "outcome", {"node_id": node.id, "observation": observation.model_dump()}
                )
                for prediction in node.predictions:
                    comparison = compare(prediction, observation)
                    if comparison is not None:
                        await self.emit("comparison", {"node_id": node.id, **comparison})
                    if (
                        prediction.horizon != 1
                        or prediction.success.value is None
                        or prediction.success_metric != observation.success_metric
                        or prediction.risk_metric != observation.risk_metric
                    ):
                        continue
                    # success means action postconditions; goal completion is a separate metric.
                    label = observation.checks.get("action_success", observation.success)
                    if await self.store.call(
                        "record_error",
                        prediction.id,
                        f"{prediction.engine_id}@{prediction.engine_version}",
                        context,
                        prediction.success.value,
                        label,
                        prediction.risk.value,
                        observation.unsafe,
                    ):
                        error = (prediction.success.value - int(label)) ** 2
                        await self.emit(
                            "prediction_error",
                            {
                                "node_id": node.id,
                                "prediction_id": prediction.id,
                                "engine_id": prediction.engine_id,
                                "predicted": prediction.success.value,
                                "observed": int(label),
                                "brier": error,
                                "truth_source": observation.state.provenance,
                            },
                        )
                await self.emit(
                    "trust_updated",
                    {
                        "context": context,
                        "engines": await self.store.call(self.calibration.snapshot, context),
                    },
                )
                if observation.success or observation.unsafe:
                    break
            final = await self.observe(world)
            success = world.complete(final) and not unsafe
            if success:
                await self.emit(
                    "decision",
                    {
                        "decision": Decision.COMPLETE,
                        "state_id": final.id,
                        "reasons": ["Authoritative goal confirmation"],
                    },
                )
            status = (
                "cancelled"
                if self.cancelled
                else "complete"
                if success
                else "failed_task"
                if unsafe or executed >= self.task.max_steps
                else "abstained"
            )
            result = {
                "success": success,
                "unsafe": unsafe,
                "steps": executed,
                "calls": self.calls,
                "agent_calls": self.agent_calls,
                "execution_calls": self.execution_calls,
                "cost_usd": self.cost,
                "cost_known": self.cost_known,
                "latency_ms": (time.monotonic() - self.started) * 1000,
                "status": status,
                "final_state": final.model_dump(),
            }
            await self.store.call("set_status", self.run_id, status, result)
            await self.emit("finished", result)
            return result
        except asyncio.CancelledError:
            status = (
                "interrupted"
                if await self.store.call("pending_execution", self.run_id)
                else "cancelled"
            )
            await self.store.call(
                "set_status",
                self.run_id,
                status,
                {"reason": "Task cancelled; reconcile any pending intent"},
            )
            raise
        except Exception as error:
            status = (
                "interrupted"
                if await self.store.call("pending_execution", self.run_id)
                else "failed"
            )
            await self.store.call(
                "set_status", self.run_id, status, {"error": type(error).__name__}
            )
            await self.emit("failed", {"error": type(error).__name__, "status": status})
            raise
