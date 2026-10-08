import json
from pathlib import Path

import pytest

from preact.cognition.benchmark import benchmark
from scripts.summarize_cognition import audit


async def test_experiment_report_is_reproducible_and_rejects_fabricated_scores(tmp_path):
    protocol = json.loads(Path("benchmarks/cognitive-queue-v1.json").read_text())
    protocol["development"].update(seeds=[91], ticks=4, target=3, shift_tick=2, noise=0)
    protocol_path = tmp_path / "test-protocol.json"
    protocol_path.write_text(json.dumps(protocol))
    directory = tmp_path / "evidence"
    await benchmark(protocol_path, directory, ["development"])
    report, manifest = audit(directory)
    assert len(report["episodes"]) == 6 and len(manifest["raw_episode_sha256"]) == 6
    row = next(r for r in report["episodes"] if r["condition"] == "cognitive")
    raw_path = Path(row["evidence"])
    raw = json.loads(raw_path.read_text())
    row["metrics"]["reward"] += 100
    raw["metrics"]["reward"] += 100
    raw_path.write_text(json.dumps(raw))
    (directory / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="reproduced from observed"):
        audit(directory)


async def test_audit_rejects_future_labels_and_partial_experiment(tmp_path):
    protocol = json.loads(Path("benchmarks/cognitive-queue-v1.json").read_text())
    protocol["development"].update(seeds=[92], ticks=3, target=3, shift_tick=1, noise=0)
    protocol_path = tmp_path / "test-protocol.json"
    protocol_path.write_text(json.dumps(protocol))
    directory = tmp_path / "evidence"
    await benchmark(protocol_path, directory, ["development"])
    report, _ = audit(directory)
    row = next(r for r in report["episodes"] if r["condition"] == "cognitive")
    raw_path = Path(row["evidence"])
    raw = json.loads(raw_path.read_text())
    future = next(
        e["data"]["prediction"]
        for e in raw["events"]
        if e["kind"] == "prediction" and e["data"]["prediction"]["horizon"] == 3
    )
    original = raw_path.read_text()
    raw["prediction_errors"].append({"prediction_id": future["id"]})
    raw_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="future prediction received"):
        audit(directory)
    raw_path.write_text(original)
    report["episodes"].pop()
    (directory / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="incomplete"):
        audit(directory)
