"""Regression evidence for draining cancelled writes without blocking the loop."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from preact.core.io import durable_io
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld
from preact.service.app import create_app


async def started(event):
    async with asyncio.timeout(2):
        while not event.is_set():
            await asyncio.sleep(0.001)


async def test_repeated_cancellation_drains_write_before_returning():
    entered, release = threading.Event(), threading.Event()
    committed = []

    def write():
        entered.set()
        assert release.wait(2)
        committed.append("written")
        raise OperationalError("private statement", {}, Exception("private details"))

    task = asyncio.create_task(durable_io(write))
    try:
        await started(entered)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and not committed
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert committed == ["written"]


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
@pytest.mark.parametrize("phase", ["create", "intent"])
async def test_stalled_write_drains_without_execution_or_false_observation(tmp_path, stop, phase):
    entered, release = threading.Event(), threading.Event()
    executed = []

    class SlowStore(Store):
        def create_run(self, *args, **kwargs):
            if phase == "create":
                entered.set()
                assert release.wait(2)
            return super().create_run(*args, **kwargs)

        def intent(self, *args):
            if phase == "intent":
                entered.set()
                assert release.wait(2)
            return super().intent(*args)

    class WatchedWorld(SoftwareWorld):
        async def execute(self, action, receipt):
            executed.append(receipt)
            return await super().execute(action, receipt)

    store = SlowStore("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([]), Policy(max_seconds=0.1))
    operation = asyncio.create_task(runtime.run(WatchedWorld(), direct=True))
    try:
        await started(entered)
        if stop == "cancel":
            operation.cancel()
        # This heartbeat continues while a real SQLite write is pending off-loop.
        clock = time.monotonic()
        beats = 0
        while time.monotonic() - clock < 0.13:
            await asyncio.sleep(0.005)
            beats += 1
        assert beats >= 10
        assert not operation.done() and not executed
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError if stop == "cancel" else TimeoutError):
        await operation
    assert not executed
    assert store.pending_execution(runtime.run_id) == (phase == "intent")
    assert store.get_run(runtime.run_id)["status"] == (
        "interrupted" if phase == "intent" else "failed" if stop == "deadline" else "cancelled"
    )
    assert not store.error_rows()
    assert not any(e["kind"] == "outcome" for e in store.read_events(runtime.run_id))


async def test_cancelled_http_admission_does_not_orphan_queued_run(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    store = app.state.store
    create = store.create_run

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return create(*args, **kwargs)

    monkeypatch.setattr(store, "create_run", delayed)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            admission = asyncio.create_task(client.post("/api/runs", json={"domain": "software"}))
            try:
                await started(entered)
                admission.cancel()
                await asyncio.sleep(0.01)
                assert not admission.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await admission
    records = store.list_runs()
    assert len(records) == 1 and records[0]["status"] == "cancelled"
    assert not store.pending_execution(records[0]["id"])


async def test_async_admission_keeps_queue_limit_under_concurrent_requests(tmp_path, monkeypatch):
    @asynccontextmanager
    async def stalled_scope(*_):
        await asyncio.Event().wait()
        yield SoftwareWorld(), Registry([])

    monkeypatch.setattr("preact.service.app.component_scope", stalled_scope)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(client.post("/api/runs", json={"domain": "software"}) for _ in range(8))
            )
            assert sorted(r.status_code for r in responses) == [202] * 4 + [429] * 4
            assert len(app.state.store.list_runs()) == 4
            for response in responses:
                if response.status_code == 202:
                    await client.delete("/api/runs/" + response.json()["id"])
    assert all(run["status"] == "cancelled" for run in app.state.store.list_runs())


async def test_storage_outage_is_503_without_sql_details(tmp_path, monkeypatch):
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")

    def failed():
        raise OperationalError("private statement", {}, Exception("private connection"))

    monkeypatch.setattr(app.state.store, "list_runs", failed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/runs")
    assert response.status_code == 503
    assert response.json() == {"detail": "Durable storage is unavailable"}


async def test_finished_task_storage_failure_is_reconciled_after_reconnection(
    tmp_path, monkeypatch
):
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    store = app.state.store
    healthy_write = store.set_status

    def failed(*args):
        raise OperationalError("private statement", {}, Exception("private connection"))

    monkeypatch.setattr(store, "set_status", failed)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/runs", json={"domain": "software"})
            run_id = response.json()["id"]
            await asyncio.sleep(0.1)
            assert store.get_run(run_id)["status"] == "queued"
            monkeypatch.setattr(store, "set_status", healthy_write)
            assert (await client.get("/api/health")).status_code == 200
            record = (await client.get(f"/api/runs/{run_id}")).json()
            assert record["status"] == "failed"
            assert record["result"] == {"error": "OperationalError", "cost_known": False}
            assert not store.pending_execution(run_id) and not store.error_rows()
