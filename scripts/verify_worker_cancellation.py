"""Actual local PostgreSQL/HTTP job lifecycle with explicitly injected cleanup evidence."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import make_url

from preact.core.models import PredictionRequest
from preact.core.store import Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier
from preact.service.worker import worker_app


async def main():
    url = make_url(os.environ["PREACT_DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or url.host not in {"db", "localhost", "127.0.0.1"}:
        raise ValueError("This rehearsal requires local PostgreSQL, never production state")
    database = "preact_worker_cancellation_verification"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": database}
        ).scalar():
            conn.execute(text("CREATE DATABASE preact_worker_cancellation_verification"))
        version = conn.execute(text("SELECT version()")).scalar()
    admin.dispose()
    target = url.set(database=database).render_as_string(hide_password=False)
    stores = [Store(target) for _ in range(3)]
    store = stores[0]
    job_ids, cases = [], []
    for confirmed in (True, False):
        world = SoftwareWorld()
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

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

        app = worker_app(Slow(world), store, "boundary-fixture-token")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://worker",
                headers={"Authorization": "Bearer boundary-fixture-token"},
            ) as client,
        ):
            state = await world.observe()
            action = (await world.propose(state, 2))[1]
            request = PredictionRequest(state=state, actions=[action])
            job = (await client.post("/predictions", json=request.model_dump())).json()["id"]
            job_ids.append(job)
            try:
                await asyncio.wait_for(started.wait(), 2)
                await client.delete("/jobs/" + job)
                await asyncio.wait_for(cleaning.wait(), 2)
                await asyncio.gather(*(s.call("cancel_job", job) for s in stores for _ in range(3)))
                before = (await client.get("/jobs/" + job)).json()
                assert before["status"] == "cancelling"
                assert not before["operation_finished"] and not before["terminal_state_confirmed"]
                assert before["prediction"] is None
                assert (await store.call("job", job))["lease_until"] > time.time()
                assert await stores[1].call("lease", "competitor") is None
                assert not await stores[1].call("finish_cancelled_job", job, "competitor", None)
                owner = (await store.call("job", job))["owner"]
                assert not await stores[1].call("finish_job", job, owner, {"forged": "late"})
            finally:
                release.set()
            async with asyncio.timeout(3):
                while (after := (await client.get("/jobs/" + job)).json())["status"] != "cancelled":
                    await asyncio.sleep(0.005)
            assert after["operation_finished"]
            assert after["terminal_state_confirmed"] == confirmed
            assert after["cleanup"]["terminal_state_confirmed"] == confirmed
            assert after["prediction"] is None
            assert (await stores[2].call("job", job))["payload"]["cleanup"] == after["cleanup"]
            cases.append({"cleanup_confirmed": confirmed, "during": before, "after": after})
    queued = await store.call("enqueue", {"scope": "unstarted fixture"})
    job_ids.append(queued)
    await store.call("cancel_job", queued)
    assert (await store.call("job", queued))["status"] == "cancelled"
    assert await stores[1].call("lease", "never-dispatch") is None
    orphan = await store.call("enqueue", {"scope": "expired cancellation fixture"})
    job_ids.append(orphan)
    assert (await store.call("lease", "original", 300))["id"] == orphan
    await store.call("cancel_job", orphan)

    def expire():
        with store.db.begin() as conn:
            conn.execute(update(store.jobs).where(store.jobs.c.id == orphan).values(lease_until=0))

    await store.call(expire)
    assert await stores[2].call("lease", "restart") is None
    record = await stores[2].call("job", orphan)
    assert record["status"] == "interrupted" and record["error"] == "CancellationUnconfirmed"
    assert not await stores[2].call("finish_job", orphan, "original", {"forged": "completion"})
    for s in stores:
        s.db.dispose()
    reopened = Store(target)
    for case, job in zip(cases, job_ids):
        assert reopened.job(job)["payload"]["cleanup"] == case["after"]["cleanup"]
    assert reopened.job(orphan)["status"] == "interrupted"
    reopened.db.dispose()
    report = {
        "schema_version": 1,
        "scope": "Actual local PostgreSQL/ASGI worker; injected engine cleanup, no GPU/NVIDIA model",
        "postgres_version": version,
        "job_protocol_version": "2",
        "sources": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path("src/preact/core/store.py"),
                Path("src/preact/service/worker.py"),
                Path("src/preact/engines/remote.py"),
            ]
        },
        "job_ids": job_ids,
        "cases": cases,
        "late_prediction_publication_blocked": True,
        "wrong_owner_completion_blocked": True,
        "concurrent_cancellation_requests": 9,
        "expired_cancellation_interrupted_without_redispatch": True,
        "fresh_pool_preserved_cleanup_evidence": True,
    }
    destination = Path(".preact/postgres-worker-cancellation.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        # Do not expose connection settings or private SQL on a failed rehearsal.
        print(json.dumps({"verification": "failed", "error": type(error).__name__}))
        raise SystemExit(1) from None
