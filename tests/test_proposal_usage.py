"""Proposal budget/receipt boundaries; injected models, no sponsor inference claim."""

import asyncio
import time

import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalHeuristic
from preact.engines.reasoning import ModelProposer


class NoChoiceWorld(SoftwareWorld):
    def __init__(self, count):
        super().__init__()
        self.count = count

    async def propose(self, state, width):
        return (await super().propose(state, width))[: self.count]


class Ranking:
    def __init__(self):
        self.calls = 0

    async def json_call(self, prompt, schema, seed, deadline):
        self.calls += 1
        return {"ranking": [1, 0]}, {"cost_usd": 0.2, "cost_known": True}


def prepared_runtime(world, tmp_path, max_calls=4):
    store = Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]), Policy(max_calls=max_calls))
    runtime.task = world.task.model_copy(deep=True)
    runtime.started = time.monotonic()
    runtime.run_id = store.create_run({"task": runtime.task.model_dump()})
    return runtime


@pytest.mark.parametrize("count", [0, 1])
async def test_no_choice_avoids_model_io_preserves_actions_and_refunds_budget(tmp_path, count):
    world, engine = NoChoiceWorld(count), Ranking()
    state = await world.observe()
    expected = await world.propose(state, 1)
    proposer = ModelProposer(world, engine)
    runtime = prepared_runtime(proposer, tmp_path, max_calls=1)
    try:
        actions = await runtime.propose(proposer, state, 1)
        assert engine.calls == 0
        assert [a.fingerprint for a in actions] == [a.fingerprint for a in expected]
        if actions:
            assert actions[0] is not expected[0]
        assert runtime.agent_calls == 0 and runtime.cost == 0 and runtime.cost_known
        assert runtime.available()
        refunds = [
            e
            for e in runtime.store.read_events(runtime.run_id)
            if e["kind"] == "proposal_budget_refunded"
        ]
        assert len(refunds) == 1
        assert refunds[0]["data"]["reserved"] == 1
        assert refunds[0]["data"]["charged"] == 0
    finally:
        runtime.store.db.dispose()


async def test_multiple_choices_still_rank_and_charge_actual_cost(tmp_path):
    world, engine = SoftwareWorld(), Ranking()
    proposer = ModelProposer(world, engine)
    runtime = prepared_runtime(proposer, tmp_path, max_calls=1)
    try:
        actions = await runtime.propose(proposer, await world.observe(), 2)
        assert engine.calls == runtime.agent_calls == 1
        assert actions[0].name == "Prepare a reusable validator"
        assert runtime.cost == 0.2 and runtime.cost_known
        assert not runtime.available()
        assert not any(
            e["kind"] == "proposal_budget_refunded"
            for e in runtime.store.read_events(runtime.run_id)
        )
    finally:
        runtime.store.db.dispose()


@pytest.mark.parametrize("certified", [True, False, "true"])
async def test_partial_receipts_only_refund_certified_successful_work(tmp_path, certified):
    class Metered(SoftwareWorld):
        proposal_calls = 2
        proposal_usage_complete = certified

        async def propose(self, state, width):
            self.usage = [{"cost_usd": 0.3, "cost_known": True}]
            return await super().propose(state, width)

    world = Metered()
    runtime = prepared_runtime(world, tmp_path)
    try:
        await runtime.propose(world, await world.observe(), 2)
        assert runtime.agent_calls == (1 if certified is True else 2)
        assert runtime.cost == 0.3
        assert runtime.cost_known is (certified is True)
        assert world.usage == []
    finally:
        runtime.store.db.dispose()


@pytest.mark.parametrize("fault", ["failure", "cancel", "mutation"])
async def test_failed_proposal_never_refunds_a_reservation(tmp_path, fault):
    class Failed(SoftwareWorld):
        proposal_calls = 2
        proposal_usage_complete = True

        async def propose(self, state, width):
            self.usage = [{"cost_usd": 0.1, "cost_known": True}]
            if fault == "cancel":
                raise asyncio.CancelledError
            if fault == "failure":
                raise EngineFailure("Injected proposal failure")
            state.payload["unexpected_mutation"] = True
            return await super().propose(state, width)

    world = Failed()
    runtime = prepared_runtime(world, tmp_path)
    try:
        error = {
            "failure": EngineFailure,
            "cancel": asyncio.CancelledError,
            "mutation": ValueError,
        }[fault]
        with pytest.raises(error):
            await runtime.propose(world, await world.observe(), 2)
        assert runtime.agent_calls == 2 and runtime.cost == 0.1 and not runtime.cost_known
        assert not any(
            e["kind"] == "proposal_budget_refunded"
            for e in runtime.store.read_events(runtime.run_id)
        )
    finally:
        runtime.store.db.dispose()


async def test_completed_receipts_cannot_undercharge_an_exceeded_reservation(tmp_path):
    class ExtraCalls(SoftwareWorld):
        proposal_calls = 1

        async def propose(self, state, width):
            self.usage = [{"cost_usd": 0.2, "cost_known": True}] * 2
            return await super().propose(state, width)

    world = ExtraCalls()
    runtime = prepared_runtime(world, tmp_path, max_calls=1)
    try:
        with pytest.raises(ValueError, match="declared call budget"):
            await runtime.propose(world, await world.observe(), 1)
        assert runtime.agent_calls == 2 and runtime.cost == 0.4 and runtime.cost_known
        assert not runtime.available()
    finally:
        runtime.store.db.dispose()


@pytest.mark.parametrize("event_kind", ["proposal_budget_refunded", "agent_usage"])
async def test_cancellation_during_receipt_events_retains_complete_accounting(tmp_path, event_kind):
    class Metered(SoftwareWorld):
        proposal_calls = 3

        async def propose(self, state, width):
            self.usage = [{"cost_usd": 0.2, "cost_known": True}] * 2
            return await super().propose(state, width)

    world = Metered()
    runtime = prepared_runtime(world, tmp_path)
    entered = asyncio.Event()
    emit = runtime.emit

    async def intercepted(kind, data):
        await emit(kind, data)
        if kind == event_kind:
            entered.set()
            await asyncio.Event().wait()

    runtime.emit = intercepted
    task = asyncio.create_task(runtime.propose(world, await world.observe(), 1))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.agent_calls == 2
        assert runtime.cost == 0.4 and runtime.cost_known
        assert world.usage == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime.store.db.dispose()


async def test_single_candidate_does_not_bypass_measurement_gate(tmp_path):
    world, engine = NoChoiceWorld(1), Ranking()
    proposer = ModelProposer(world, engine)
    store = Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world)]))
    try:
        result = await runtime.run(proposer)
        assert result["status"] == "abstained" and result["steps"] == 0
        assert engine.calls == runtime.agent_calls == 0
        assert not any(e["kind"] == "execution_intent" for e in store.read_events(runtime.run_id))
    finally:
        store.db.dispose()
