"""Independently compare exhibit projections with retained source events and receipts."""

import argparse
import asyncio
import json
from pathlib import Path

from preact.core.models import RunEvent, identity
from preact.core.store import Artifacts, Store
from preact.learning.transitions import TransitionDataset
from preact.playground.evidence import KINDS, artifact_details
from preact.playground.recordings import RecordingCatalog
from scripts.export_playground import case_hash


async def audit(bundle, recordings):
    catalog = RecordingCatalog(recordings)
    if catalog.errors or len(catalog.records) != 6:
        raise ValueError("Exhibition catalog failed validation")
    results = json.loads((bundle / "results.json").read_text())
    checked = []
    for identifier, record in catalog.records.items():
        folder = bundle / identifier
        metadata = json.loads((folder / "case.json").read_text())
        provenance = record.session.provenance
        if (
            provenance["source_case_hash"] != case_hash(folder)
            or provenance["source_code_hash"] != results["source_hash"]
            or provenance["protocol_hash"] != results["protocol_hash"]
            or provenance["audit_hash"] != identity(provenance["audit"])
        ):
            raise ValueError("Projection/source/audit manifest binding changed")
        store = Store("sqlite:///" + str(folder / "ledger.db"))
        snapshot = await TransitionDataset(store, metadata["episodes"]).snapshot()
        outcomes = {r.outcome_reference: r for r in snapshot.transitions}
        sources = {r for runs in metadata["episodes"].values() for r in runs}
        sources.update([metadata["journal"], metadata["backend_state"]["lifecycle_journal"]])
        expected = {
            f"{run}:{e['seq']}": RunEvent.model_validate(e).model_dump()
            for run in sources
            for e in store.read_events(run)
            if e["kind"] in KINDS
        }
        if set(expected) != {e.reference for e in record.events}:
            raise ValueError("Projection omitted or fabricated source events")
        for item in record.events:
            if expected[item.reference] != item.event.model_dump():
                raise ValueError("Projected data differs from source evidence")
            if item.event.kind == "outcome":
                row = outcomes[item.reference]
                if row.after.model_dump() != item.event.data["observation"]:
                    raise ValueError("Projected actual differs from the complete receipt")
        expected_artifacts = artifact_details(Artifacts(str(folder / "artifacts")), record.events)
        if record.artifacts != expected_artifacts:
            raise ValueError("Projected lifecycle summaries differ from source artifacts")
        checked.append(
            {
                "case": identifier,
                "events": len(expected),
                "complete_receipts": len(outcomes),
                "status": "passed",
            }
        )
    return {
        "status": "passed-projection-audit",
        "cases": checked,
        "scope": "Exact source event/complete receipt projection; source learning audit retained separately",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--recordings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(audit(args.bundle, args.recordings))
    with args.output.open("x") as target:
        target.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
