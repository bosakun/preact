import json
from pathlib import Path

import pytest

from scripts.audit_learned_dynamics import audit
from scripts.benchmark_learned_dynamics import benchmark


async def test_benchmark_reproducible_scores_and_receipt_audit(tmp_path):
    protocol = json.loads(Path("benchmarks/learned-dynamics-v1.json").read_text())
    protocol.update(
        ticks=4, training_seeds=[11, 12], evaluation_seeds=[101], training_sizes=[0, 4, 8]
    )
    protocol["training_environment"]["shift_tick"] = 4
    for name, environment in protocol["evaluation_environments"].items():
        environment["shift_tick"] = 2 if name == "low_to_high" else 4
    protocol_path = tmp_path / "smoke-protocol.json"
    protocol_path.write_text(json.dumps(protocol))
    first = await benchmark(protocol_path, tmp_path / "first")
    second = await benchmark(protocol_path, tmp_path / "second")
    for scenario, conditions in first["predictions"].items():
        for condition, metrics in conditions.items():
            for metric, value in metrics.items():
                if metric not in {"inference_cpu_seconds", "inference_wall_seconds"}:
                    assert second["predictions"][scenario][condition][metric] == value
    assert first["runtime_semantics_equal"] and second["runtime_semantics_equal"]
    report = await audit(tmp_path / "first", protocol_path)
    assert report["status"] == "passed-receipt-backed-dynamics-audit"
    summary_path = tmp_path / "first/summary.json"
    summary = json.loads(summary_path.read_text())
    summary["predictions"]["stable_low"]["0"]["mae_with_explicit_midpoint_fallback"] = 999
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="score mismatch"):
        await audit(tmp_path / "first", protocol_path)
    with pytest.raises(ValueError, match="new output"):
        await benchmark(protocol_path, tmp_path / "first")


async def test_benchmark_rejects_replayed_training_seed(tmp_path):
    protocol = json.loads(Path("benchmarks/learned-dynamics-v1.json").read_text())
    protocol["evaluation_seeds"] = [protocol["training_seeds"][0]]
    path = tmp_path / "leaking-protocol.json"
    path.write_text(json.dumps(protocol))
    with pytest.raises(ValueError, match="seeds must be distinct"):
        await benchmark(path, tmp_path / "unused")
    assert not (tmp_path / "unused").exists()
