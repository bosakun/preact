"""Actual local worker/SQL lifecycle; engine cleanup evidence is injected, not GPU validation."""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import update

from preact.core.interfaces import EngineFailure
from preact.core.models import PredictionRequest
from preact.core.store import Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.engines.remote import RemoteEngine
from preact.service.worker import worker_app


async def enqueue(client, world):
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    request = PredictionRequest(state=state, actions=[action])
    return (await client.post("/predictions", json=request.model_dump())).json()["id"]


async def wait_status(client, job, expected):
    async with asyncio.timeout(2):
        while True:
            record = (await client.get("/jobs/" + job)).json()
            if record["status"] == expected:
                return record
            await asyncio.sleep(0.005)


def connection(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://worker",
        headers={"Authorization": "Bearer fixture-token"},
    )


@pytest.mark.parametrize("confirmed", [True, False])
async def test_cancel_remains_in_progress_until_drained_and_retains_actual_evidence(confirmed):
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")

    class Slow(LocalVerifier):
        async def predict(self, request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as error:
                cleaning.set()
                await release.wait()
                error.preact_cleanup = {
                    "operation_kind": "local_process",
                    "request_accepted": confirmed,
                    "terminal_state_confirmed": confirmed,
                }
                if not confirmed:
                    error.preact_cleanup["error"] = "PermissionError"
                raise

    app = worker_app(Slow(world), store, "fixture-token")
    async with app.router.lifespan_context(app), connection(app) as client:
        job = await enqueue(client, world)
        try:
            await asyncio.wait_for(started.wait(), 1)
            ack = (await client.delete("/jobs/" + job)).json()
            assert ack["status"] == "cancellation_requested"
            await asyncio.wait_for(cleaning.wait(), 1)
            record = (await client.get("/jobs/" + job)).json()
            assert record["status"] == "cancelling"
            assert not record["operation_finished"] and not record["terminal_state_confirmed"]
            assert record["prediction"] is None and store.lease("other-consumer") is None
            owner = store.job(job)["owner"]
            assert not store.finish_job(job, owner, {"forged": "late prediction"})
            assert not store.finish_cancelled_job(job, "other-consumer", None)
        finally:
            release.set()
        record = await wait_status(client, job, "cancelled")
        assert record["operation_finished"]
        assert record["terminal_state_confirmed"] == confirmed
        assert record["cleanup"]["terminal_state_confirmed"] == confirmed
        assert record["prediction"] is None and record["job_protocol_version"] == "2"
        assert store.job(job)["payload"]["cleanup"] == record["cleanup"]
        if not confirmed:
            assert record["cleanup"]["error"] == "PermissionError"


async def test_queued_cancel_is_confirmed_without_starting_or_leasing_work():
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")
    app = worker_app(LocalVerifier(world), store, "fixture-token")
    # No consumer lifespan: known queued work cannot have dispatched.
    async with connection(app) as client:
        job = await enqueue(client, world)
        await client.delete("/jobs/" + job)
        record = (await client.get("/jobs/" + job)).json()
        assert record["status"] == "cancelled"
        assert record["operation_finished"] and record["terminal_state_confirmed"]
        assert record["prediction"] is None and store.lease("consumer") is None


def test_expired_cancellation_is_interrupted_not_success_or_redispatched(tmp_path):
    url = "sqlite:///" + str(tmp_path / "jobs.db")
    first = Store(url)
    job = first.enqueue({"fixture": "request"})
    assert first.lease("original", 300)["id"] == job
    first.cancel_job(job)
    with first.db.begin() as conn:
        conn.execute(update(first.jobs).where(first.jobs.c.id == job).values(lease_until=0))
    second = Store(url)
    assert second.lease("restart") is None
    record = second.job(job)
    assert record["status"] == "interrupted" and record["error"] == "CancellationUnconfirmed"
    assert "prediction" not in record["payload"]
    assert not second.finish_job(job, "original", {"forged": "prediction"})
    assert not second.finish_cancelled_job(job, "restart", None)


async def test_engine_that_returns_after_cancel_cannot_publish_its_prediction():
    started = asyncio.Event()
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")

    class IgnoresCancellation(LocalVerifier):
        async def predict(self, request):
            result = await super().predict(request)
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return result

    app = worker_app(IgnoresCancellation(world), store, "fixture-token")
    async with app.router.lifespan_context(app), connection(app) as client:
        job = await enqueue(client, world)
        await asyncio.wait_for(started.wait(), 1)
        await client.delete("/jobs/" + job)
        record = await wait_status(client, job, "cancelled")
        assert record["prediction"] is None
        assert record["operation_finished"] and record["terminal_state_confirmed"]


async def test_failed_engine_cleanup_is_retained_without_terminal_confirmation():
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")

    class Failed(LocalVerifier):
        async def predict(self, request):
            error = EngineFailure("fixture-secret")
            error.preact_cleanup = {
                "operation_kind": "local_process",
                "request_accepted": True,
                "terminal_state_confirmed": False,
                "error": "TimeoutError",
            }
            raise error

    app = worker_app(Failed(world), store, "fixture-token")
    async with app.router.lifespan_context(app), connection(app) as client:
        job = await enqueue(client, world)
        record = await wait_status(client, job, "failed")
        assert record["operation_finished"] and not record["terminal_state_confirmed"]
        assert record["cleanup"]["error"] == "TimeoutError" and record["prediction"] is None
        assert "fixture-secret" not in json.dumps(record)


@pytest.mark.parametrize("confirmed", [True, False, None])
async def test_remote_never_infers_termination_from_cancelled_status_alone(confirmed):
    world = SoftwareWorld()
    state = await world.observe()
    request = PredictionRequest(state=state, actions=[(await world.propose(state, 2))[1]])

    def handle(req):
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        assert req.method == "GET"
        body = {"status": "cancelled"}
        if confirmed is not None:
            body["terminal_state_confirmed"] = confirmed
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        remote = RemoteEngine("http://worker", "fixture", LocalVerifier(world).capabilities, client)
        with pytest.raises(EngineFailure) as failure:
            await remote.predict(request)
        assert failure.value.preact_cleanup["terminal_state_confirmed"] == (confirmed is True)


async def test_remote_polls_cancelling_then_preserves_failed_cleanup():
    world = SoftwareWorld()
    state = await world.observe()
    request = PredictionRequest(state=state, actions=[(await world.propose(state, 2))[1]])
    polls = []

    def handle(req):
        if req.method == "POST":
            return httpx.Response(202, json={"id": "fixture-job"})
        assert req.method == "GET"
        polls.append(req.method)
        return httpx.Response(
            200,
            json={"status": "cancelling"}
            if len(polls) == 1
            else {
                "status": "cancelled",
                "terminal_state_confirmed": False,
                "cleanup": {
                    "operation_kind": "local_process",
                    "request_accepted": False,
                    "terminal_state_confirmed": False,
                    "error": "PermissionError",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        remote = RemoteEngine("http://worker", "fixture", LocalVerifier(world).capabilities, client)
        with pytest.raises(EngineFailure) as failure:
            await remote.predict(request)
        assert len(polls) == 2
        assert not failure.value.preact_cleanup["terminal_state_confirmed"]
        assert failure.value.preact_cleanup["error"] == "PermissionError"


@pytest.mark.parametrize("exceptional", [True, False])
async def test_worker_shutdown_drains_active_operation_and_retains_unknown_termination(exceptional):
    started, cancelled = asyncio.Event(), asyncio.Event()
    world, store = SoftwareWorld(), Store("sqlite:///:memory:")

    class Slow(LocalVerifier):
        async def predict(self, request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    app = worker_app(Slow(world), store, "fixture-token")

    async def exercise():
        async with app.router.lifespan_context(app), connection(app) as client:
            job = await enqueue(client, world)
            await asyncio.wait_for(started.wait(), 1)
            if exceptional:
                # Preserve identity for the post-shutdown durable-state check.
                app.state.fixture_job = job
                raise RuntimeError("fixture lifespan failure")
            return job

    if exceptional:
        with pytest.raises(RuntimeError, match="fixture lifespan failure"):
            await exercise()
        job = app.state.fixture_job
    else:
        job = await exercise()
    assert app.state.consumer.done() and cancelled.is_set()
    record = store.job(job)
    assert record["status"] == "cancelled" and record["payload"]["operation_finished"]
    assert not record["payload"]["terminal_state_confirmed"]
    assert record["payload"]["prediction"] is None
