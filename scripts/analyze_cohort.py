"""Post-run analysis of the frozen local cohort without modifying its source or policies."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from preact.benchmarks import probability_scores
from preact.core.calibration import Calibration


def analyze(root):
    groups = defaultdict(lambda: {"raw": [], "calibrated": [], "risk": []})
    for path in sorted(root.glob("*.json")):
        if path.name == "report.json":
            continue
        archive = json.loads(path.read_text())
        events = archive["events"]
        trusts = {}
        current = {}
        for event in events:
            if event["kind"] == "trust_snapshot":
                current = event["data"]["engines"]
            elif event["kind"] == "prediction":
                trusts[event["data"]["prediction"]["id"]] = current
        row = archive["result"]
        for error in archive["errors"]:
            group = groups[(row["domain"], row["variant"], error["engine"])]
            stats = trusts[error["prediction_id"]].get(error["engine"])
            group["raw"].append((error["value"], error["label"]))
            group["calibrated"].append(
                (Calibration.calibrate(error["value"], stats), error["label"])
            )
            if error["risk_value"] is not None:
                group["risk"].append((error["risk_value"], error["risk_label"]))
    return {
        "scope": "Executed action postconditions only; task completion is scored separately; selection bias applies",
        "metrics": {
            "/".join(key): {name: probability_scores(points) for name, points in values.items()}
            for key, values in groups.items()
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", default="reports/cohort-engine-scores.json")
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(analyze(args.directory), indent=2) + "\n")
