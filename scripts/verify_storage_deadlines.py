"""Real PostgreSQL lock/cancellation proof in the isolated verification database."""

import argparse
import asyncio
import json
import os
import threading
import time
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from preact.cohorts import source_hash
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.software import SoftwareWorld


async def entered(event):
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.002)


async def proof():
    url = make_url(os.environ["PREACT_DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise ValueError("Requires actual PostgreSQL")
    url = url.set(database="preact_verification").render_as_string(hide_password=False)
    store = Store(url)
    with store.db.connect() as conn:
        settings = {
            key: conn.execute(text("SHOW " + key)).scalar_one()
            for key in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")
        }
        version = conn.execute(text("SHOW server_version")).scalar_one()
    run_id = await store.call("create_run", {"scope": "isolated row-lock probe"})
    locked, release = threading.Event(), threading.Event()

    def hold_row():
        with store.db.begin() as conn:
            conn.execute(text("SELECT id FROM runs WHERE id=:id FOR UPDATE"), {"id": run_id})
            locked.set()
            assert release.wait(4)

    holder = asyncio.create_task(asyncio.to_thread(hold_row))
    gaps, finished = [], asyncio.Event()

    async def heartbeat():
        previous = time.monotonic()
        while not finished.is_set():
            await asyncio.sleep(0.005)
            current = time.monotonic()
            gaps.append(current - previous)
            previous = current

    beat = asyncio.create_task(heartbeat())
    try:
        await entered(locked)
        clock = time.monotonic()
        pending = asyncio.create_task(store.call("append", run_id, "locked_write", {}))
        await store.call("healthy")
        try:
            await pending
        except OperationalError as error:
            assert error.orig.sqlstate == "55P03"  # PostgreSQL lock_not_available
        else:
            raise AssertionError("Locked write must time out without committing")
        lock_seconds = time.monotonic() - clock
        assert 0.8 < lock_seconds < 2.5
    finally:
        release.set()
        await holder
        finished.set()
        await beat
    assert len(gaps) > 20 and max(gaps) < 0.15
    assert not store.read_events(run_id)
    assert store.get_run(run_id)["next_seq"] == 0
    assert (await store.call("append", run_id, "after_release", {})).seq == 1

    intent_started = threading.Event()
    dispatched = []

    class DelayedIntent(Store):
        def intent(self, *args):
            intent_started.set()
            with self.db.begin() as conn:
                conn.execute(text("SELECT pg_sleep(0.4)"))
            return super().intent(*args)

    class Watched(SoftwareWorld):
        async def execute(self, action, receipt):
            dispatched.append(receipt)
            return await super().execute(action, receipt)

    delayed = DelayedIntent(url)
    runtime = Runtime(
        delayed, Artifacts(".preact/storage-proof"), Registry([]), Policy(max_seconds=0.1)
    )
    clock = time.monotonic()
    operation = asyncio.create_task(runtime.run(Watched(), direct=True))
    await entered(intent_started)
    await asyncio.sleep(0.15)
    assert not operation.done() and not dispatched
    try:
        await operation
    except TimeoutError:
        pass
    else:
        raise AssertionError("Episode deadline must expire")
    drained_seconds = time.monotonic() - clock
    assert delayed.get_run(runtime.run_id)["status"] == "interrupted"
    assert delayed.pending_execution(runtime.run_id)
    assert not dispatched
    assert not any(e["kind"] == "outcome" for e in delayed.read_events(runtime.run_id))
    with delayed.db.connect() as conn:
        intent = (
            conn.execute(
                select(delayed.executions).where(delayed.executions.c.run_id == runtime.run_id)
            )
            .mappings()
            .one()
        )
    try:
        delayed.intent(runtime.run_id, intent["state_id"], intent["action_hash"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("Pending intent must refuse blind retry")
    return {
        "scope": "actual local Docker PostgreSQL, not Nebius",
        "source_hash": source_hash(),
        "postgres_version": version,
        "connection_settings": settings,
        "lock_timeout_seconds": round(lock_seconds, 4),
        "heartbeat_count": len(gaps),
        "max_heartbeat_gap_seconds": round(max(gaps), 4),
        "timed_out_write_rolled_back": True,
        "subsequent_write_sequence": 1,
        "episode_budget_seconds": 0.1,
        "intent_drain_seconds": round(drained_seconds, 4),
        "action_dispatches": len(dispatched),
        "uncertain_intent_status": "interrupted",
        "blind_retry_rejected": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".preact/postgres-deadlines.json")
    args = parser.parse_args()
    result = asyncio.run(proof())
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
