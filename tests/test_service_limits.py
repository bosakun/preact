import asyncio

import httpx
import pytest

from preact.core.models import Policy
from preact.service.app import create_app


@pytest.mark.parametrize(
    "policy",
    [
        {"max_seconds": 1e12},
        {"max_calls": 1000000},
        {"max_cost_usd": 100000},
        {"max_risk": 1},
        {"min_success": 0},
        {"uncertainty_threshold": 1},
        {"disagreement_threshold": 1},
        {"calibration": False},
    ],
)
async def test_public_requests_cannot_expand_resources_or_relax_safety(
    policy, tmp_path, monkeypatch
):
    def reject_setup(*args):
        raise AssertionError("Rejected requests must not contact engines")

    monkeypatch.setattr("preact.service.app.component_scope", reject_setup)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/runs", json={"domain": "software", "policy": policy})
        assert response.status_code == 422
        assert response.json()["detail"]["reason"] == "Policy exceeds server limits"
        assert (await client.get("/api/runs")).json() == []


async def test_server_policy_defaults_and_stricter_client_override_are_recorded(tmp_path):
    limits = Policy(max_seconds=30, max_cost_usd=0.5, max_risk=0.02)
    app = create_app("sqlite:///:memory:", str(tmp_path), "local", service_policy=limits)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/runs",
                json={
                    "domain": "software",
                    "policy": {"max_calls": 1},
                },
            )
            assert response.status_code == 202
            run_id = response.json()["id"]
            async with asyncio.timeout(2):
                while (record := (await client.get(f"/api/runs/{run_id}")).json())["status"] in {
                    "queued",
                    "running",
                }:
                    await asyncio.sleep(0.01)
            policy = record["config"]["request"]["policy"]
            assert policy["max_calls"] == 1
            assert policy["max_seconds"] == 30 and policy["max_risk"] == 0.02
            assert record["status"] == "abstained" and record["result"]["steps"] == 0
