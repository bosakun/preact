"""Check report provenance/coverage, paired replication, accounting and aggregates.

This read-only summary audit does not turn a report into safety Evidence. Receipt
and Gate checks occur on original raw executions in the reproduction benchmark.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from preact.core.models import Action, Policy, identity
from preact.core.store import Store
from scripts.bench_information_seeking import aggregate


def without_policy_hash(value):
    if isinstance(value, dict):
        return {k: without_policy_hash(v) for k, v in value.items() if k != "policy_hash"}
    if isinstance(value, list):
        return [without_policy_hash(v) for v in value]
    return value


def audit_raw(row, protocol, directory):
    trace = json.loads((directory / "trace.json").read_text())
    assert identity(trace) == row["semantic_sha256"]
    events = json.loads((directory / "events.json").read_text())
    runs = list(dict.fromkeys(e["run_id"] for e in events))
    run_labels = {run: f"round-{index}" for index, run in enumerate(runs)}
    occurrences, references = Counter(), {}
    for event in events:
        run, kind = event["run_id"], event["kind"]
        references[f"{run_labels[run]}:{event['seq']}"] = (
            f"{run_labels[run]}/{kind}/{occurrences[(run, kind)]}"
        )
        occurrences[(run, kind)] += 1
    policy = protocol["no_probe_budget_control"] if row["budget_control"] else protocol["policy"]
    # Runtime revalidates a Policy dump, normalizing default ints to float fields.
    policy_values = Policy.model_validate(Policy.model_validate(policy).model_dump()).model_dump()
    store = Store(f"sqlite:///{directory / 'ledger.sqlite'}")
    nodes = {
        e["data"]["node"]["id"]: e["data"]["node"]
        for e in events
        if e["kind"] in {"node", "node_updated"}
    }
    try:
        for event in events:
            data = event["data"]
            if event["kind"] == "outcome":
                node = nodes[data["node_id"]]
                obs = data["observation"]
                execution = store.execution_record(obs["receipt"])
                assert execution["status"] == "complete" and execution["receipt"] == obs
                assert execution["run_id"] == event["run_id"]
                assert execution["state_id"] == node["state"]["id"]
                assert execution["action_hash"] == Action.model_validate(node["action"]).fingerprint
            elif event["kind"] == "gate_preview":
                gate = data["gate"]
                evaluation = nodes[data["node_id"]]["evaluation"]
                assert gate["policy_hash"] == identity(
                    {"policy": policy_values, "stakes": evaluation["stakes"]}
                )
                assert gate["evidence_hash"] == identity(evaluation)

        # Different width limits add unreferenced control events and shift ledger
        # sequence numbers. Preserve reference targets by event kind/occurrence.
        def canonical(value):
            if isinstance(value, dict):
                return {k: canonical(v) for k, v in value.items()}
            if isinstance(value, list):
                return [canonical(v) for v in value]
            if isinstance(value, str):
                return re.sub(r"round-\d+:\d+", lambda m: references[m.group()], value)
            return value

        return canonical(trace)
    finally:
        store.db.dispose()


def audit(protocol_path: Path, report_path: Path, raw: Path | None = None) -> dict:
    protocol, report = json.loads(protocol_path.read_text()), json.loads(report_path.read_text())
    assert report["protocol"] == protocol
    assert report["protocol_sha256"] == hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    for name, digest in report["source_sha256"].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name
    rows = report["episodes"]
    expected = {
        (case, seed, strategy, repeat, False)
        for case in protocol["cases"]
        for seed in protocol["seeds"]
        for strategy in protocol["strategies"]
        for repeat in range(protocol["repeats"])
    }
    if protocol.get("budget_control"):
        expected |= {
            (case, seed, "no_probe", 0, True)
            for case in protocol["cases"]
            for seed in protocol["seeds"]
        }
    indexed = {
        (r["case"], r["seed"], r["strategy"], r["repeat"], r["budget_control"]): r for r in rows
    }
    traces = {}
    assert len(indexed) == len(rows) and set(indexed) == expected
    for key, row in indexed.items():
        case, seed, strategy, repeat, control = key
        metrics = row["metrics"]
        config = protocol["world"] | protocol["cases"][case]
        assert metrics["ticks"] == config["ticks"]
        assert metrics["unsafe"] is False and metrics["abstains"] == 0
        assert metrics["reuse_hits"] == 0
        assert metrics["counts"]["observations"] == config["ticks"] * 7 + 1
        assert metrics["counts"]["retrieval"] == metrics["counts"]["inference"]
        assert metrics["engine_calls"] == config["ticks"] * (9 if strategy == "no_probe" else 12)
        assert abs(metrics["probe_cost"] - metrics["probe_count"] * config["probe_cost"]) < 1e-9
        original = indexed[(case, seed, strategy, 0, False)]
        if repeat:
            assert row["semantic_sha256"] == original["semantic_sha256"]
            for name, value in metrics.items():
                if name not in {"cpu_seconds", "wall_seconds", "proposal_seconds"}:
                    assert value == original["metrics"][name], (key, name)
        if control:
            assert row["behavior_sha256"] == original["behavior_sha256"]
            assert metrics["engine_calls"] == original["metrics"]["engine_calls"]
        if strategy == "no_probe":
            assert metrics["probe_count"] == 0
        if raw is not None:
            name = f"budget-{case}-{seed}" if control else f"{repeat}-{case}-{seed}-{strategy}"
            traces[key] = identity(without_policy_hash(audit_raw(row, protocol, raw / name)))
    for key, digest in traces.items():
        case, seed, strategy, repeat, control = key
        if control:
            assert digest == traces[(case, seed, strategy, 0, False)]
    assert aggregate(rows) == report["aggregate"]
    return {
        "episodes": len(rows),
        "paired_main_episodes": sum(not r["budget_control"] for r in rows),
        "budget_controls": sum(r["budget_control"] for r in rows),
        "status": "passed-provenance-coverage-reproduction-accounting-aggregate",
        "scope": "Summary plus original receipt/Gate and budget trace audit"
        if raw is not None
        else "Summary audit only; rerun benchmark for original receipt/Gate audit",
        "raw_receipt_gate_and_budget_semantics_audited": raw is not None,
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "auditor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--raw", type=Path, help="Optional private benchmark output directory")
    args = parser.parse_args()
    print(json.dumps(audit(args.protocol, args.report, args.raw), indent=2))


if __name__ == "__main__":
    main()
