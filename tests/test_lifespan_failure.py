"""Normal and exceptional API shutdown must drain work and stop new admission."""

import asyncio
import threading
from contextlib import asynccontextmanager

import httpx
import pytest

from preact.core.registry import Registry
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.service.app import create_app


@pytest.mark.parametrize("phase", ["predict", "execute"])
@pytest.mark.parametrize("exceptional", [True, False])
async def test_shutdown_closes_active_scope_and_preserves_authority_uncertainty(
    tmp_path, monkeypatch, phase, exceptional
):
    started, closed = asyncio.Event(), asyncio.Event()

    class SlowWorld(SoftwareWorld):
        async def execute(self, action, receipt):
            started.set()
            await asyncio.Event().wait()

    class SlowVerifier(LocalVerifier):
        async def predict(self, request):
            started.set()
            await asyncio.Event().wait()

    @asynccontextmanager
    async def scope(*args):
        world = SlowWorld() if phase == "execute" else SoftwareWorld()
        try:
            yield world, Registry([] if phase == "execute" else [SlowVerifier(world)])
        finally:
            closed.set()

    monkeypatch.setattr("preact.service.app.component_scope", scope)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        run_id = None

        async def exercise():
            nonlocal run_id
            async with app.router.lifespan_context(app):
                response = await client.post(
                    "/api/runs",
                    json={
                        "domain": "software",
                        "mode": "direct" if phase == "execute" else "preact",
                    },
                )
                assert response.status_code == 202
                run_id = response.json()["id"]
                await asyncio.wait_for(started.wait(), 2)
                if exceptional:
                    raise RuntimeError("fixture lifespan failure")

        if exceptional:
            with pytest.raises(RuntimeError, match="fixture lifespan failure"):
                await exercise()
        else:
            await exercise()
        assert closed.is_set()
        record = app.state.store.get_run(run_id)
        assert record["status"] == ("interrupted" if phase == "execute" else "cancelled")
        assert app.state.store.pending_execution(run_id) == (phase == "execute")
        assert not app.state.store.error_rows()
        assert not any(e["kind"] == "outcome" for e in app.state.store.read_events(run_id))
        assert (await client.get("/api/health")).status_code == 503
        assert (await client.post("/api/runs", json={"domain": "software"})).status_code == 503
        assert len(app.state.store.list_runs()) == 1


async def test_shutdown_drains_admission_write_without_starting_late_work(tmp_path, monkeypatch):
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    original = app.state.store.create_run

    def delayed(*args, **kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(2)
        return original(*args, **kwargs)

    def forbidden_scope(*args):
        raise AssertionError("Shutdown admission must never allocate engines")

    monkeypatch.setattr(app.state.store, "create_run", delayed)
    monkeypatch.setattr("preact.service.app.component_scope", forbidden_scope)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        manager = app.router.lifespan_context(app)
        await manager.__aenter__()
        admission = asyncio.create_task(client.post("/api/runs", json={"domain": "software"}))
        shutdown = None
        try:
            await asyncio.wait_for(started.wait(), 1)
            shutdown = asyncio.create_task(manager.__aexit__(None, None, None))
            await asyncio.sleep(0.01)
            assert not shutdown.done() and (await client.get("/api/health")).status_code == 503
        finally:
            release.set()
        assert (await admission).status_code == 503
        await asyncio.wait_for(shutdown, 1)
        rows = app.state.store.list_runs()
        assert len(rows) == 1 and rows[0]["status"] == "cancelled"
        assert not app.state.store.pending_execution(rows[0]["id"])
        assert not app.state.store.read_events(rows[0]["id"]) and not app.state.store.error_rows()
