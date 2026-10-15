"""Audit retained Queue experiments and export allowlisted, read-only exhibit records."""

import argparse
import asyncio
import gzip
import hashlib
import json
import time
from pathlib import Path

from preact.core.models import RunEvent, identity, now
from preact.core.store import Artifacts, Store
from preact.domains.queue_playground import PRESETS
from preact.playground.evidence import artifact_details, read_sources
from preact.playground.models import PlaygroundRecording, PlaygroundSession
from preact.playground.recordings import validate_recording
from scripts.audit_common_learning import audit_case


def case_hash(folder):
    paths = [folder / "case.json", folder / "ledger.db", *sorted((folder / "artifacts").glob("*"))]
    return identity(
        {
            str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
            if p.is_file()
        }
    )


async def export_case(folder, protocol_hash, source_hash):
    before = case_hash(folder)
    cpu, wall = time.process_time(), time.perf_counter()
    audit = await audit_case(folder)
    if audit["status"] != "passed":
        raise ValueError("Independent source audit failed")
    metadata = json.loads((folder / "case.json").read_text())
    if metadata["domain"] != "queue":
        raise ValueError("Only reviewed Queue evidence is allowed in the exhibit")
    store = Store("sqlite:///" + str(folder / "ledger.db"))
    artifacts = Artifacts(str(folder / "artifacts"))
    sources = {r: "world" for runs in metadata["episodes"].values() for r in runs}
    sources[metadata["journal"]] = "learning"
    sources[metadata["backend_state"]["lifecycle_journal"]] = "lifecycle"
    events = await read_sources(store, sources)
    details = artifact_details(artifacts, events)
    for event in events:
        original = store.read_events(event.event.run_id, event.event.seq - 1)[0]
        if event.event.model_dump() != RunEvent.model_validate(original).model_dump():
            raise ValueError("Export projection changed a source event")
    if case_hash(folder) != before:
        raise ValueError("Source bundle changed during independent audit/export")
    strategy = metadata["strategy"]
    promotions = sum(e.event.kind == "learning_explicit_promotion" for e in events)
    label = PRESETS[metadata["condition"]][2]
    label += " · " + (f"回復あり・昇格{promotions}回" if strategy == "learned" else "回復なし")
    session = PlaygroundSession(
        id=folder.name,
        label=label,
        scenario=metadata["condition"],
        mode="recorded",
        status="recorded",
        rounds=audit["task_receipts"],
        event_count=len(events),
        provenance={
            "source": "Frozen common-learning-v2 reproduction, original events/receipts retained",
            "strategy": strategy,
            "promotions": promotions,
            "seed": metadata["seed"],
            "audit_status": "passed",
            "audit": audit,
            "audit_hash": identity(audit),
            "source_case_hash": before,
            "source_code_hash": source_hash,
            "protocol_hash": protocol_hash,
            "exported_at": now(),
            "export_audit_cpu_seconds": time.process_time() - cpu,
            "export_audit_wall_seconds": time.perf_counter() - wall,
            "scope": "CPU Queue; Goal999 continuous fixture, not task success/generalization",
            "order": "Presentation order by source timestamp; per-run sequence retained",
            "promotion": "Historical explicit operator calls; replay does not promote",
        },
    )
    record = PlaygroundRecording(session=session, events=events, artifacts=details, digest="")
    record.digest = identity(record.model_dump(exclude={"digest"}))
    validate_recording(record.model_dump())
    return record


async def export(bundle, output):
    # Provision all declared Queue conditions for seed11, including adverse/no-recovery cases.
    # This selection is fixed by the exhibition plan, not selected by observed reward.
    if output.exists():
        raise ValueError("Use a new export output directory")
    results = json.loads((bundle / "results.json").read_text())
    protocol = json.loads((bundle / "protocol.json").read_text())
    if identity(protocol) != results["protocol_hash"]:
        raise ValueError("Bundle protocol checksum differs")
    names = [
        f"queue-{scenario}-{strategy}-11"
        for scenario in PRESETS
        for strategy in (
            ("learned",) if scenario.startswith("stable") else ("learned", "no_recovery")
        )
    ]
    staged = []
    for name in names:
        record = await export_case(bundle / name, results["protocol_hash"], results["source_hash"])
        staged.append(record)
        print(name, record.session.rounds, record.digest, flush=True)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for record in staged:
        filename = record.session.id + ".json.gz"
        encoded = json.dumps(
            record.model_dump(), ensure_ascii=False, separators=(",", ":")
        ).encode()
        (output / filename).write_bytes(gzip.compress(encoded, mtime=0))
        manifest[filename] = record.digest
    (output / "catalog.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(export(args.bundle, args.output))


if __name__ == "__main__":
    main()
