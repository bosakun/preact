"""The episode budget must also bound observation and execution infrastructure."""

import asyncio
import time
from contextlib import asynccontextmanager

import httpx
import pytest

from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.service.app import create_app


@pytest.mark.parametrize("phase", ["observe", "execute"])
async def test_episode_deadline_bounds_authority_and_retains_uncertain_intent(tmp_path, phase):
    entered = asyncio.Event()

    class Stalled(SoftwareWorld):
        async def observe(self):
            if phase == "observe":
                entered.set()
                await asyncio.Event().wait()
            return await super().observe()

        async def execute(self, action, receipt):
            entered.set()
            await asyncio.Event().wait()

    store = Store("sqlite:///:memory:")
    # The execution case must first reach durable intent; 30ms sometimes expires
    # during legitimate cold-start setup and correctly produces no pending receipt.
    budget = 0.25 if phase == "execute" else 0.03
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]), Policy(max_seconds=budget))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runtime.run(Stalled(), direct=True), 1)
    assert time.monotonic() - started < 0.75
    assert entered.is_set(), "The intended stalled authority phase must actually be reached"
    assert store.get_run(runtime.run_id)["status"] == (
        "interrupted" if phase == "execute" else "failed"
    )
    assert store.pending_execution(runtime.run_id) == (phase == "execute")
    assert not store.error_rows()
    assert not any(e["kind"] == "outcome" for e in store.read_events(runtime.run_id))


async def test_api_deadline_bounds_component_setup_and_releases_queue_slot(tmp_path, monkeypatch):
    closed = asyncio.Event()

    @asynccontextmanager
    async def stalled_scope(*_):
        try:
            await asyncio.Event().wait()
            yield SoftwareWorld(), Registry([])
        finally:
            closed.set()

    monkeypatch.setattr("preact.service.app.component_scope", stalled_scope)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/runs", json={"domain": "software", "policy": {"max_seconds": 0.03}}
            )
            run_id = response.json()["id"]
            await asyncio.sleep(0.1)
            record = (await client.get(f"/api/runs/{run_id}")).json()
            assert record["status"] == "failed"
            assert closed.is_set()
            assert not app.state.store.pending_execution(run_id)


async def test_api_execution_timeout_preserves_pending_intent(tmp_path, monkeypatch):
    closed = asyncio.Event()
    entered = asyncio.Event()

    class StalledExecution(SoftwareWorld):
        async def execute(self, action, receipt):
            entered.set()
            await asyncio.Event().wait()

    @asynccontextmanager
    async def scope(*_):
        try:
            yield StalledExecution(), Registry([])
        finally:
            closed.set()

    monkeypatch.setattr("preact.service.app.component_scope", scope)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/runs",
                json={"domain": "software", "mode": "direct", "policy": {"max_seconds": 0.25}},
            )
            run_id = response.json()["id"]
            await asyncio.wait_for(entered.wait(), 0.75)
            async with asyncio.timeout(1):
                while True:
                    record = (await client.get(f"/api/runs/{run_id}")).json()
                    if record["status"] == "interrupted" and closed.is_set():
                        break
                    await asyncio.sleep(0.01)
            assert record["status"] == "interrupted"
            assert record["result"]["cost_known"] is False
            assert app.state.store.pending_execution(run_id)
            assert closed.is_set()
            assert not app.state.store.error_rows()


async def test_setup_deadline_before_dispatch_has_no_uncertain_execution(tmp_path):
    entered = asyncio.Event()

    class SlowSetup(SoftwareWorld):
        async def observe(self):
            await asyncio.sleep(0.06)
            return await super().observe()

        async def execute(self, action, receipt):
            entered.set()
            raise AssertionError("Setup exhausted the episode budget before dispatch")

    store = Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]), Policy(max_seconds=0.03))
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runtime.run(SlowSetup(), direct=True), 1)
    assert not entered.is_set() and not store.pending_execution(runtime.run_id)
    assert store.get_run(runtime.run_id)["status"] == "failed"
    assert not any(
        e["kind"] in ("execution_intent", "outcome") for e in store.read_events(runtime.run_id)
    )


async def test_cancelled_pending_execution_immediately_records_unknown_cost(tmp_path):
    entered = asyncio.Event()

    class StalledExecution(SoftwareWorld):
        async def execute(self, action, receipt):
            entered.set()
            await asyncio.Event().wait()

    store = Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]))
    operation = asyncio.create_task(runtime.run(StalledExecution(), direct=True))
    await asyncio.wait_for(entered.wait(), 1)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    record = store.get_run(runtime.run_id)
    assert record["status"] == "interrupted" and record["result"]["cost_known"] is False
    assert store.pending_execution(runtime.run_id)
    assert not store.error_rows()
    assert not any(e["kind"] == "outcome" for e in store.read_events(runtime.run_id))
