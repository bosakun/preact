"""Standalone replay of saved visual sources, geometry and complete receipts."""

import argparse
import json
from pathlib import Path

from preact.core.models import identity
from preact.core.store import Artifacts, Store
from preact.visual.audit import audit_manifest, audit_run


def audit(directory):
    report = json.loads((directory / "results.json").read_text())
    protocol = json.loads((directory / "protocol.json").read_text())
    if identity(protocol) != report["protocol_hash"]:
        raise ValueError("Protocol changed after evaluation")
    artifacts = Artifacts(str(directory / "artifacts"))
    store = Store("sqlite:///" + str((directory / "ledger.db").resolve()))
    try:
        results = [
            audit_manifest(artifacts, c["manifest"])
            for c in report["cases"]
            if c.get("render_success")
        ]
        runtime = audit_run(store, artifacts, report["run_id"])
        if json.loads((directory / "events.json").read_text()) != store.read_events(
            report["run_id"]
        ):
            raise ValueError("Exported events differ from durable Store")
        reproduction = report["reproducibility"]["manifests"]
        a, b = [json.loads(artifacts.read(d))["frames"] for d in reproduction]
        if [f["artifacts"] for f in a] != [f["artifacts"] for f in b]:
            raise ValueError("Reproduction mismatch")
        # Hash verification of the entire retained artifact corpus, including all modality files.
        for path in artifacts.root.iterdir():
            artifacts.read(path.name)
        return {
            "passed": True,
            "protocol_hash": report["protocol_hash"],
            "scenes": results,
            "runtime": runtime,
            "reproducibility": True,
            "artifact_count": len(list(artifacts.root.iterdir())),
        }
    finally:
        store.db.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.directory)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "scenes": len(result["scenes"]),
                "rendered_candidates": result["runtime"]["rendered_candidates"],
                "executed_candidates": result["runtime"]["executed_candidates"],
                "artifact_count": result["artifact_count"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
