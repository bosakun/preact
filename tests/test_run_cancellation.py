import asyncio
from contextlib import asynccontextmanager

import httpx

from preact.core.registry import Registry
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.service.app import create_app


async def wait_status(client, run_id, expected):
    async with asyncio.timeout(2):
        while True:
            record = (await client.get(f"/api/runs/{run_id}")).json()
            if record["status"] == expected:
                return record
            await asyncio.sleep(0.01)


async def test_cancelling_prediction_interrupts_engine_and_closes_scope(tmp_path, monkeypatch):
    started, cancelled, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Slow(LocalVerifier):
        async def predict(self, request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    @asynccontextmanager
    async def scope(*_):
        world = SoftwareWorld()
        try:
            yield world, Registry([Slow(world)])
        finally:
            closed.set()

    monkeypatch.setattr("preact.service.app.component_scope", scope)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        run_id = (await client.post("/api/runs", json={"domain": "software"})).json()["id"]
        await asyncio.wait_for(started.wait(), 2)
        await client.delete(f"/api/runs/{run_id}")
        await asyncio.wait_for(cancelled.wait(), 2)
        await asyncio.wait_for(closed.wait(), 2)
        await wait_status(client, run_id, "cancelled")
        assert not app.state.store.pending_execution(run_id)


async def test_queued_cancellation_terminates_and_inflight_execution_retains_uncertainty(
    tmp_path, monkeypatch
):
    executing = asyncio.Queue()

    class SlowWorld(SoftwareWorld):
        async def execute(self, action, receipt):
            await executing.put(receipt)
            await asyncio.Event().wait()

    @asynccontextmanager
    async def scope(*_):
        yield SlowWorld(), Registry([])

    monkeypatch.setattr("preact.service.app.component_scope", scope)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ids = []
            for _ in range(2):
                ids.append(
                    (
                        await client.post(
                            "/api/runs", json={"domain": "software", "mode": "direct"}
                        )
                    ).json()["id"]
                )
                await asyncio.wait_for(executing.get(), 2)
            queued = (
                await client.post("/api/runs", json={"domain": "software", "mode": "direct"})
            ).json()["id"]
            await client.delete(f"/api/runs/{queued}")
            await wait_status(client, queued, "cancelled")
            assert not app.state.store.pending_execution(queued)
            for run_id in ids:
                await client.delete(f"/api/runs/{run_id}")
                await wait_status(client, run_id, "interrupted")
                assert app.state.store.pending_execution(run_id)
                events = (await client.get(f"/api/runs/{run_id}/events")).json()
                assert any(e["kind"] == "execution_intent" for e in events)
                assert not any(e["kind"] == "outcome" for e in events)
