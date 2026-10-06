import asyncio

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from preact.core.models import PredictionRequest
from preact.core.store import Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.engines.remote import RemoteEngine
from preact.service.worker import worker_app


def test_leases_are_exclusive_and_cancelled_jobs_cannot_publish():
    store = Store("sqlite:///:memory:")
    job = store.enqueue({"input": "state"})
    assert store.lease("worker-one")["id"] == job
    assert store.lease("worker-two") is None
    store.cancel_job(job)
    assert not store.finish_job(job, "worker-one", {"forged": "result"})
    assert store.job(job)["status"] == "cancelling"


async def test_real_local_worker_round_trip_through_remote_protocol(tmp_path):
    world = SoftwareWorld()
    app = worker_app(LocalVerifier(world), Store("sqlite:///:memory:"), "test-token")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://worker"
        ) as client:
            assert (await client.get("/capabilities")).status_code == 401
            remote = await RemoteEngine.connect("http://worker", "test-token", client)
            state = await world.observe()
            action = (await world.propose(state, 2))[1]
            result = await remote.predict(PredictionRequest(state=state, actions=[action]))
            assert result.mandatory_checks["regressions"]
            assert result.evidence == "executable"


async def test_worker_cancellation_reaches_active_engine():
    world = SoftwareWorld()
    started, cancelled = asyncio.Event(), asyncio.Event()

    class Slow(LocalVerifier):
        async def predict(self, request):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    app = worker_app(Slow(world), Store("sqlite:///:memory:"), "test-token")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://worker",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            state = await world.observe()
            action = (await world.propose(state, 2))[1]
            response = await client.post(
                "/predictions", json=PredictionRequest(state=state, actions=[action]).model_dump()
            )
            job = response.json()["id"]
            await asyncio.wait_for(started.wait(), 2)
            await client.delete(f"/jobs/{job}")
            await asyncio.wait_for(cancelled.wait(), 2)
            async with asyncio.timeout(2):
                while (await client.get(f"/jobs/{job}")).json()["status"] != "cancelled":
                    await asyncio.sleep(0.01)


@pytest.mark.parametrize("failure", ["lease", "finish"])
async def test_worker_recovers_storage_failure_without_publishing_success(failure):
    failed = asyncio.Event()
    loop = asyncio.get_running_loop()

    class Outage(Store):
        def lease(self, *args):
            if failure == "lease" and not failed.is_set():
                loop.call_soon_threadsafe(failed.set)
                raise OperationalError("private SQL", {}, Exception("outage"))
            return super().lease(*args)

        def finish_job(self, *args):
            if failure == "finish" and not failed.is_set():
                loop.call_soon_threadsafe(failed.set)
                raise OperationalError("private SQL", {}, Exception("outage"))
            return super().finish_job(*args)

    store = Outage("sqlite:///:memory:")
    world = SoftwareWorld()
    app = worker_app(LocalVerifier(world), store, "test-token")
    state = await world.observe()
    request = PredictionRequest(state=state, actions=[(await world.propose(state, 2))[1]])
    job_id = store.enqueue(request.model_dump())
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(failed.wait(), 2)
        assert store.job(job_id)["status"] != "complete"
        if failure == "finish":
            # Explicitly expire the failed write's lease, as real time would do.
            with store.db.begin() as conn:
                conn.execute(
                    update(store.jobs).where(store.jobs.c.id == job_id).values(lease_until=0)
                )
        async with asyncio.timeout(3):
            while (await store.call("job", job_id))["status"] != "complete":
                await asyncio.sleep(0.02)
        job = store.job(job_id)
        assert job["payload"]["prediction"]["mandatory_checks"]["regressions"]
        assert not app.state.consumer.done()
