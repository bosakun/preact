"""Real PostgreSQL authority transactions with an injected SDK, never NVIDIA proof."""

import argparse
import asyncio
import hashlib
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from preact.core.models import Action, State
from preact.core.store import Store
from preact.domains.physical import PhysicalWorld
from workers.isaac_authority import authority_app


async def proof():
    url = make_url(os.environ["PREACT_DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise ValueError("Requires actual PostgreSQL")
    url = url.set(database="preact_verification").render_as_string(hide_password=False)
    stores = [Store(url), Store(url)]
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def injected_sdk(bundle, *, authority):
        assert authority
        state = State.model_validate(bundle["state"])
        if bundle.get("actions"):
            calls.append(bundle)
            started.set()
            await release.wait()
            action = Action.model_validate(bundle["actions"][0])
            successor = PhysicalWorld().materialize(state, action)
            state = State.create("physical", successor.payload, "injected-sdk-boundary")
        return {"state": state.model_dump(), "checks": {"clearance": True}, "metrics": {}}

    def client(store):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=authority_app(store, "boundary-fixture", injected_sdk)
            ),
            base_url="http://boundary",
            headers={"Authorization": "Bearer boundary-fixture"},
        )

    async with AsyncExitStack() as stack:
        one, two = [await stack.enter_async_context(client(store)) for store in stores]
        response = await one.post("/episodes", json={"seed": 0})
        episode = response.json()["id"]
        path = "/episodes/" + episode
        state = State.model_validate((await one.get(path)).json())
        action = (await PhysicalWorld().propose(state, 3))[1]
        payload = {
            "action": action.model_dump(),
            "receipt": hashlib.sha256(episode.encode()).hexdigest(),
        }
        first = asyncio.create_task(one.post(path + "/actions", json=payload))
        try:
            await asyncio.wait_for(started.wait(), 3)
            second = await two.post(path + "/actions", json={**payload, "receipt": "a" * 64})
            assert second.status_code == 409 and len(calls) == 1
            assert (await two.get(path)).status_code == 409
        finally:
            release.set()
        completed = await first
        assert completed.status_code == 200
        duplicate = await two.post(path + "/actions", json=payload)
        assert duplicate.json() == completed.json() and len(calls) == 1
        assert (await two.get(path)).json() == completed.json()["state"]
        stale = await two.post(path + "/actions", json={**payload, "receipt": "b" * 64})
        assert stale.status_code == 409

        # Cancel in-flight SDK work; reconnect through a new independent Store.
        started.clear()
        release.clear()
        episode2 = (await one.post("/episodes", json={"seed": 1})).json()["id"]
        path2 = "/episodes/" + episode2
        state2 = State.model_validate((await one.get(path2)).json())
        action2 = (await PhysicalWorld().propose(state2, 3))[1]
        payload2 = {
            "action": action2.model_dump(),
            "receipt": hashlib.sha256(episode2.encode()).hexdigest(),
        }
        cancelled = asyncio.create_task(one.post(path2 + "/actions", json=payload2))
        await asyncio.wait_for(started.wait(), 3)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        fresh = Store(url)
        async with client(fresh) as restarted:
            assert (await restarted.get(path2)).status_code == 409
            assert (await restarted.post(path2 + "/actions", json=payload2)).status_code == 409
            assert (
                await restarted.post(path2 + "/actions", json={**payload2, "receipt": "c" * 64})
            ).status_code == 409
        assert fresh.pending_execution(episode2)
        assert fresh.get_run(episode2)["result"]["id"] == state2.id

        # Atomic receipt/state publication must roll back on a conflicting episode.
        started.clear()
        episode3 = (await one.post("/episodes", json={"seed": 2})).json()["id"]
        path3 = "/episodes/" + episode3
        state3 = State.model_validate((await one.get(path3)).json())
        action3 = (await PhysicalWorld().propose(state3, 3))[1]
        payload3 = {
            "action": action3.model_dump(),
            "receipt": hashlib.sha256(episode3.encode()).hexdigest(),
        }
        conflicting = asyncio.create_task(one.post(path3 + "/actions", json=payload3))
        try:
            await asyncio.wait_for(started.wait(), 3)
            with stores[1].db.begin() as conn:
                conn.execute(
                    update(stores[1].runs)
                    .where(stores[1].runs.c.id == episode3)
                    .values(status="interrupted")
                )
        finally:
            release.set()
        assert (await conflicting).status_code == 409
        with stores[1].db.connect() as conn:
            receipt = (
                conn.execute(
                    select(stores[1].executions).where(
                        stores[1].executions.c.id == payload3["receipt"]
                    )
                )
                .mappings()
                .one()
            )
        assert receipt["status"] == "pending" and receipt["receipt"] == {}
        assert stores[1].get_run(episode3)["result"]["id"] == state3.id
    return {
        "scope": "Actual local PostgreSQL authority transactions; injected SDK, not NVIDIA/GPU validation",
        "worker_sha256": hashlib.sha256(
            Path("workers/isaac_authority.py").read_bytes()
        ).hexdigest(),
        "independent_database_clients": 3,
        "concurrent_action_responses": [completed.status_code, second.status_code],
        "dispatches_in_concurrent_scenario": 1,
        "duplicate_receipt_returned_same_observation": True,
        "stale_action_rejected": True,
        "cancelled_episode_requires_reconciliation_after_reconnect": True,
        "receipt_and_state_conflict_rolled_back": True,
        "episode_ids": [episode, episode2, episode3],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".preact/isaac-authority-proof.json")
    args = parser.parse_args()
    result = asyncio.run(proof())
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
