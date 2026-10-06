"""Reproduce complete cohort findings from audited archives without running agents."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from preact.benchmarks import metrics, paired_analysis
from scripts.analyze_cohort import analyze
from scripts.audit_prediction_ledger import audit, require


def episode_key(row):
    return row["domain"], row["task_id"], row["seed"], row["variant"]


def summarize(protocol_path, directory):
    manifest = json.loads(protocol_path.read_text())
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text())
    require(report["split"] == "held_out", "Expected a held-out report")
    for field in ("source_hash", "protocol_hash", "scope", "limitations", "model_profile"):
        require(report.get(field) == manifest.get(field), f"Report {field} mismatch")
    alignment = audit(protocol_path, directory)
    archives = [json.loads((directory / name).read_text()) for name in alignment["archive_sha256"]]
    archived_rows = sorted((a["result"] for a in archives), key=episode_key)
    require(
        sorted(report["episodes"], key=episode_key) == archived_rows,
        "Report episodes differ from audited archives",
    )
    archived_errors = [error for archive in archives for error in archive["errors"]]
    require(
        sorted(report["errors"], key=lambda e: e["prediction_id"])
        == sorted(archived_errors, key=lambda e: e["prediction_id"]),
        "Report errors differ from audited archives",
    )
    groups = defaultdict(lambda: {"rows": [], "errors": []})
    for archive in archives:
        row = archive["result"]
        group = groups[f"{row['domain']}/{row['variant']}"]
        group["rows"].append(row)
        group["errors"].extend(archive["errors"])
    recalculated = {key: metrics(group["rows"], group["errors"]) for key, group in groups.items()}
    require(recalculated == report["metrics"], "Report metrics do not reproduce")
    paired = paired_analysis(report["episodes"])
    require(paired == report["paired_analysis"], "Report paired analysis does not reproduce")
    return {
        "status": "complete-cohort-findings-reproduced",
        "scope": report["scope"],
        "source_hash": report["source_hash"],
        "protocol_hash": report["protocol_hash"],
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "model_profile": report.get("model_profile"),
        "seeds": manifest["seeds"],
        "max_episode_seconds": manifest.get("max_episode_seconds"),
        "completed_episodes": len(archived_rows),
        "conditions": sorted({r["variant"] for r in archived_rows}),
        "metrics": recalculated,
        "paired_analysis": paired,
        "engine_scores": analyze(directory),
        "failures": [
            {
                field: row[field]
                for field in (
                    "domain",
                    "task_id",
                    "seed",
                    "variant",
                    "run_id",
                    "status",
                    "steps",
                    "unsafe",
                    "unsafe_observation_known",
                    "latency_ms",
                )
            }
            for row in archived_rows
            if not row["success"]
        ],
        "ledger_audit": alignment,
        "limitations": report["limitations"]
        + [
            "Repetition of authored instances does not create a newly blind task population.",
            "Selected-action probability errors do not score unexecuted alternatives or task completion.",
            "Simulation and model errors retain separate engine scores; shared simulator implementation can yield zero error.",
            "Latency may include shared-host load, sequential execution and cache effects.",
            "Unknown prices and allocated hardware/energy cost remain unknown, not verified zero.",
            "Archive analysis makes no new live integration, deployment or submission certification.",
        ],
    }


def write_summary(protocol_path, directory, output):
    if output.exists():
        raise FileExistsError("Refusing to replace existing evidence")
    result = summarize(protocol_path, directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = write_summary(args.protocol, args.directory, args.output)
    print(json.dumps({k: result[k] for k in ("status", "protocol_hash", "completed_episodes")}))
