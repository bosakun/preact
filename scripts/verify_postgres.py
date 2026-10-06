"""Exercise real PostgreSQL transactions in a dedicated verification database.

Run inside Compose after creating preact_verification; never targets the application
database. --resume verifies the same marker after an actual database restart.
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.engine import make_url

from preact.core.models import identity, uid
from preact.core.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    url = make_url(os.environ["PREACT_DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise ValueError("This check requires real PostgreSQL")
    store = Store(url.set(database="preact_verification").render_as_string(hide_password=False))
    marker = Path(".preact/postgres-proof.json")
    if args.resume:
        proof = json.loads(marker.read_text())
        events = store.read_events(proof["event_run"])
        assert identity(events) == proof["event_hash"]
        store.recover()
        assert store.get_run(proof["pending_run"])["status"] == "interrupted"
        try:
            store.intent(proof["pending_run"], "uncertain-state", "uncertain-action")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Restart must never permit blind reexecution")
        print(
            json.dumps(
                {
                    "postgres_restart": "passed",
                    "events_preserved": len(events),
                    "pending_execution": "halted for reconciliation",
                },
                indent=2,
            )
        )
        return

    clients = [store] + [
        Store(url.set(database="preact_verification").render_as_string(hide_password=False))
        for _ in range(7)
    ]
    run = store.create_run({"scope": "isolated PostgreSQL conformance"})

    def write_events(index):
        client = clients[index]
        for item in range(100):
            client.append(run, "concurrency_probe", {"worker": index, "item": item})

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(write_events, range(8)))
    events = store.read_events(run)
    assert [e["seq"] for e in events] == list(range(1, 801))

    prediction_id = uid()
    with ThreadPoolExecutor(8) as pool:
        inserted = list(
            pool.map(
                lambda i: clients[i].record_error(
                    prediction_id,
                    "conformance@1",
                    "verification:fixture:h1",
                    0.9,
                    False,
                    0.1,
                    True,
                    source="conformance-fixture",
                ),
                range(8),
            )
        )
    assert sum(inserted) == 1

    jobs = {store.enqueue({"fixture": n}) for n in range(40)}

    def claim_jobs(index):
        claimed = []
        client, owner = clients[index], "conformance-" + str(index)
        while job := client.lease(owner, 60):
            claimed.append(job["id"])
            client.cancel_job(job["id"])
            assert not client.finish_job(job["id"], owner, {"forged": "completion"})
        return claimed

    with ThreadPoolExecutor(8) as pool:
        claimed = [job for group in pool.map(claim_jobs, range(8)) for job in group]
    assert len(claimed) == len(set(claimed)) == 40 and set(claimed) == jobs
    pending = store.create_run({"scope": "uncertain execution crash fixture"})
    store.set_status(pending, "running")
    store.intent(pending, "uncertain-state", "uncertain-action")
    proof = {
        "event_run": run,
        "event_hash": identity(events),
        "pending_run": pending,
        "concurrent_events": len(events),
        "deduplicated_error_writers": len(inserted),
        "exclusive_job_leases": len(claimed),
    }
    marker.write_text(json.dumps(proof, indent=2))
    print(json.dumps({"postgres_conformance": "passed", **proof}, indent=2))


if __name__ == "__main__":
    main()
