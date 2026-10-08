"""Read-only coverage, summary and optional original receipt audit for Software runs."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from preact.core.models import identity
from preact.core.store import Store
from scripts.bench_software_cognition import aggregate, audit_ledger, canonical, source_hashes


async def audit(protocol_path, report_path, raw=None):
    protocol = json.loads(protocol_path.read_text())
    report = json.loads(report_path.read_text())
    assert report["protocol"] == protocol
    assert report["protocol_sha256"] == hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    assert report["source_sha256"] == source_hashes()
    expected = {
        (task, seed, condition, repeat)
        for task in protocol["tasks"]
        for seed in protocol["seeds"]
        for condition in protocol["conditions"]
        for repeat in range(protocol["repeats"])
    }
    rows = report["episodes"]
    actual = [(r["task"], r["seed"], r["condition"], r["repeat"]) for r in rows]
    assert len(actual) == len(expected) and set(actual) == expected
    assert report["aggregate"] == aggregate(rows)
    pairs = {}
    for row in rows:
        key = row["task"], row["seed"]
        pairs.setdefault(key, set()).add(row["semantic_sha256"])
        if raw is None:
            continue
        task, seed, condition, repeat = (row["task"], row["seed"], row["condition"], row["repeat"])
        directory = raw / f"{task}-{seed}-{condition}-{repeat}"
        semantic = json.loads((directory / "semantic.json").read_text())
        assert identity(semantic) == row["semantic_sha256"]
        # Read existing files only; do not let Store create a missing authority DB.
        database = directory / "ledger.sqlite"
        assert database.is_file()
        store = Store("sqlite:///" + str(database))
        try:
            runs = store.list_runs()
            run_ids = [r["id"] for r in reversed(runs)]
            records, events, labels = await audit_ledger(store, run_ids)
            assert semantic["actions"] == [canonical(r.action.model_dump()) for r in records]
            assert semantic["observations"] == [
                canonical(r.observation.model_dump()) for r in records
            ]
            assert semantic["learning"] == labels
            assert semantic["final_state"] == canonical(runs[0]["result"]["final_state"])
            assert semantic["success"] == runs[0]["result"]["success"]
            assert semantic["unsafe"] == any(r["result"]["unsafe"] for r in runs)
            assert row["metrics"]["executed_actions"] == len(records)
            assert row["metrics"]["action_fingerprints"] == [r.action.fingerprint for r in records]
            assert row["metrics"]["engine_calls"] == sum(r["result"]["calls"] for r in runs)
            assert row["metrics"]["success"] == semantic["success"]
            assert row["metrics"]["unsafe"] == semantic["unsafe"]
            assert row["metrics"]["verifications"] == sum(
                e["kind"] == "prediction" and e["data"]["prediction"]["engine_id"] == "local-tests"
                for e in events
            )
            assert row["metrics"]["abstains"] == sum(
                e["kind"] == "decision" and e["data"].get("decision") == "abstain" for e in events
            )
            assert row["metrics"]["memory_retry_signals"] == sum(
                bool(e["data"]["belief"]["inferred"])
                for e in events
                if e["kind"] == "cognitive_update"
            )
        finally:
            store.db.dispose()
    equal = all(len(digests) == 1 for digests in pairs.values())
    assert report["semantic_pairs_equal"] == equal and equal
    return {
        "episodes": len(rows),
        "semantic_pairs_equal": equal,
        "raw_authority_checked": raw is not None,
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "scope": "Measured bounded software executions; no authorization from summaries",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol", type=Path, default=Path("benchmarks/software-cognition-v1.json")
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--raw", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(audit(args.protocol, args.report, args.raw)), indent=2))


if __name__ == "__main__":
    main()
