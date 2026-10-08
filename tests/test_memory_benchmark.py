import json
from pathlib import Path

import pytest

from scripts.bench_cognitive_memory import benchmark


async def test_microbenchmark_compares_equal_experiences_and_counts_actual_sql(tmp_path):
    protocol = json.loads(Path("benchmarks/cognitive-memory-index-v1.json").read_text())
    protocol.update(receipt_counts=[4], iterations=2, warm_iterations=3, retrieve_limit=2)
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    output = tmp_path / "benchmark"
    report = await benchmark(output, path)
    assert len(report["results"]) == 2 and report["source_sha256"]
    assert json.loads((output / "report.json").read_text()) == report
    for row in report["results"]:
        assert len(row["returned_references"]) == 2
        assert all(row["forged_outcome_rejected"].values())
        measurements = row["measurements"]
        warm = measurements["indexed_warm"]
        assert warm["iterations"] == 3
        assert warm["sql_calls_per_retrieve"] == 1
        assert warm["store_calls_per_retrieve"] == {"run_heads": 1}
        assert warm["cpu_total_ms"] > 0 and warm["wall_p95_ms"] >= warm["wall_p50_ms"]
        assert (
            measurements["full_reread"]["sql_calls_per_retrieve"] > warm["sql_calls_per_retrieve"]
        )
        assert all(m["experience_equality"] for m in measurements.values())
        assert all(s["experience_equality"] for samples in row["samples"].values() for s in samples)
    with pytest.raises(FileExistsError):
        await benchmark(output, path)
