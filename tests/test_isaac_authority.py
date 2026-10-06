"""Authority HTTP/SQL boundaries with an injected SDK; not NVIDIA validation."""

import asyncio
from contextlib import AsyncExitStack

import httpx
import pytest
from sqlalchemy import select, update

from preact.core.models import Action, State
from preact.core.store import Store
from preact.domains.physical import PhysicalWorld
from workers.isaac_authority import authority_app

TOKEN = "boundary-fixture-token"


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://authority",
        headers={"Authorization": "Bearer " + TOKEN},
    )


def result(bundle):
    state = State.model_validate(bundle["state"])
    if bundle.get("actions"):
        action = Action.model_validate(bundle["actions"][0])
        future = PhysicalWorld().materialize(state, action)
        state = State.create("physical", future.payload, "injected-sdk-boundary")
    return {"state": state.model_dump(), "checks": {"clearance": True}, "metrics": {}}


async def episode_and_action(client):
    response = await client.post("/episodes", json={"seed": 0})
    assert response.status_code == 200, response.text
    episode = response.json()["id"]
    state = State.model_validate((await client.get("/episodes/" + episode)).json())
    action = (await PhysicalWorld().propose(state, 3))[1]
    return episode, state, action


async def test_two_authorities_cannot_dispatch_from_one_reserved_state(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def step(bundle, *, authority):
        assert authority
        if bundle.get("actions"):
            calls.append(bundle)
            started.set()
            await release.wait()
        return result(bundle)

    url = "sqlite:///" + str(tmp_path / "shared.db")
    stores = [Store(url), Store(url)]
    async with AsyncExitStack() as stack:
        one, two = [
            await stack.enter_async_context(client(authority_app(store, TOKEN, step)))
            for store in stores
        ]
        episode, state, action = await episode_and_action(one)
        path = "/episodes/" + episode + "/actions"
        payload = {"action": action.model_dump(), "receipt": "1" * 64}
        first = asyncio.create_task(one.post(path, json=payload))
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert (await two.get("/episodes/" + episode)).status_code == 409
            second = await two.post(path, json={**payload, "receipt": "2" * 64})
            assert second.status_code == 409 and len(calls) == 1
            assert stores[1].pending_execution(episode)
        finally:
            release.set()
        response = await first
        assert response.status_code == 200
        observed = response.json()["state"]
        assert observed["id"] != state.id
        assert (await two.get("/episodes/" + episode)).json() == observed
        assert (await two.post(path, json=payload)).json() == response.json()
        assert len(calls) == 1 and not stores[0].pending_execution(episode)
        stale = await two.post(path, json={**payload, "receipt": "3" * 64})
        assert stale.status_code == 409 and len(calls) == 1
        altered = action.model_copy(update={"duration": action.duration + 1})
        mismatch = await two.post(path, json={**payload, "action": altered.model_dump()})
        assert mismatch.status_code == 409
        other, _, _ = await episode_and_action(two)
        assert (await two.post("/episodes/" + other + "/actions", json=payload)).status_code == 409


@pytest.mark.parametrize("failure", ["cancelled", "sdk_error", "hypothetical"])
async def test_failed_sdk_keeps_episode_unresolved_across_worker_restart(tmp_path, failure):
    started = asyncio.Event()
    calls = []

    async def step(bundle, *, authority):
        if bundle.get("actions"):
            calls.append(bundle)
            started.set()
            if failure == "cancelled":
                await asyncio.Event().wait()
            if failure == "sdk_error":
                raise RuntimeError("SDK fixture failure")
            value = result(bundle)
            value["state"]["kind"] = "hypothetical"
            return value
        return result(bundle)

    url = "sqlite:///" + str(tmp_path / "durable.db")
    store = Store(url)
    async with client(authority_app(store, TOKEN, step)) as one:
        episode, state, action = await episode_and_action(one)
        payload = {"action": action.model_dump(), "receipt": "4" * 64}
        operation = asyncio.create_task(one.post("/episodes/" + episode + "/actions", json=payload))
        await asyncio.wait_for(started.wait(), 2)
        if failure == "cancelled":
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            assert (await operation).status_code == (502 if failure == "hypothetical" else 500)
        assert store.get_run(episode)["status"] == "executing"
        assert store.get_run(episode)["result"]["id"] == state.id
        assert store.pending_execution(episode) and not store.error_rows()
    restarted = Store(url)
    app = authority_app(restarted, TOKEN, step)
    async with app.router.lifespan_context(app):
        async with client(app) as two:
            assert (await two.get("/episodes/" + episode)).status_code == 409
            for receipt in [payload["receipt"], "5" * 64]:
                response = await two.post(
                    "/episodes/" + episode + "/actions", json={**payload, "receipt": receipt}
                )
                assert response.status_code == 409
    assert len(calls) == 1 and restarted.pending_execution(episode)


async def test_receipt_and_observation_publish_atomically_on_conflict(tmp_path):
    store = Store("sqlite:///" + str(tmp_path / "conflict.db"))
    started, release = asyncio.Event(), asyncio.Event()

    async def step(bundle, *, authority):
        if bundle.get("actions"):
            started.set()
            await release.wait()
        return result(bundle)

    async with client(authority_app(store, TOKEN, step)) as one:
        episode, state, action = await episode_and_action(one)
        operation = asyncio.create_task(
            one.post(
                "/episodes/" + episode + "/actions",
                json={"action": action.model_dump(), "receipt": "6" * 64},
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 2)
            # An independent administrative reconciliation conflicts with publication.
            with store.db.begin() as conn:
                conn.execute(
                    update(store.runs)
                    .where(store.runs.c.id == episode)
                    .values(status="interrupted")
                )
        finally:
            release.set()
        assert (await operation).status_code == 409
        assert store.get_run(episode)["result"]["id"] == state.id
        with store.db.connect() as conn:
            receipt = (
                conn.execute(select(store.executions).where(store.executions.c.id == "6" * 64))
                .mappings()
                .one()
            )
        assert receipt["status"] == "pending" and receipt["receipt"] == {}
