import asyncio

import httpx
from sqlalchemy.exc import OperationalError

from preact.service.app import create_app


async def test_health_requires_available_durable_storage(tmp_path, monkeypatch):
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/health")).status_code == 200

        def unavailable():
            raise OperationalError("SELECT 1", {}, RuntimeError("offline"))

        monkeypatch.setattr(app.state.store, "healthy", unavailable)
        response = await client.get("/api/health")
        assert response.status_code == 503
        assert response.json() == {"detail": "Durable storage is unavailable"}


async def test_service_executes_replays_and_rejects_invalid_domain(tmp_path):
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/runs", json={"domain": "software"})
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(100):
            record = (await client.get(f"/api/runs/{run_id}")).json()
            if record["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.02)
        assert record["result"]["success"]
        events = (await client.get(f"/api/runs/{run_id}/events")).json()
        resumed = (await client.get(f"/api/runs/{run_id}/events?after=10")).json()
        assert resumed == events[10:]
        stream = await client.get(f"/api/runs/{run_id}/stream?after={events[-2]['seq']}")
        assert f"id: {events[-1]['seq']}" in stream.text
        resumed_stream = await client.get(
            f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": str(events[-2]["seq"])}
        )
        assert resumed_stream.text == stream.text
        assert (
            await client.get(f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": "invalid"})
        ).status_code == 422
        assert (await client.post("/api/runs", json={"domain": "made-up"})).status_code == 422
        assert (await client.get("/api/runs/unknown")).status_code == 404
        assert (await client.get("/api/ledger")).json()


async def test_task_routing_rejects_physical_release_and_cloud_without_keys_fails_honestly(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("NEBIUS_MODEL", raising=False)
    app = create_app("sqlite:///:memory:", str(tmp_path), "cloud")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/runs", json={"domain": "software", "task": "release"})
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(100):
            record = (await client.get(f"/api/runs/{run_id}")).json()
            if record["status"] == "failed":
                break
            await asyncio.sleep(0.02)
        assert record["status"] == "failed" and record["result"]["error"] == "EngineFailure"
        assert record["result"]["cost_known"] is False
        assert (
            await client.post("/api/runs", json={"domain": "physical", "task": "release"})
        ).status_code == 422
        events = (await client.get(f"/api/runs/{run_id}/events")).json()
        assert not any(e["kind"] in {"prediction", "execution_intent", "outcome"} for e in events)
