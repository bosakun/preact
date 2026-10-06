import json
from collections import defaultdict

import pytest
import pytest_asyncio

from preact.benchmarks import metrics, paired_analysis
from preact.cohorts import cases, episode, freeze
from preact.core.models import identity
from scripts.summarize_cohort import summarize, write_summary


@pytest_asyncio.fixture
async def completed_cohort(tmp_path):
    protocol = tmp_path / "protocol.json"
    manifest = freeze(protocol, seeds=1)
    manifest.pop("protocol_hash")
    manifest["policies"] = {k: manifest["policies"][k] for k in ("direct", "preact", "single")}
    for domain in ("software", "physical"):
        case = manifest["fixtures"][domain]["development"][0]
        manifest["fixtures"][domain]["held_out"] = [case]
    manifest["protocol_hash"] = identity(manifest)
    protocol.write_text(json.dumps(manifest))
    directory = tmp_path / "cohort"
    directory.mkdir()
    rows, errors = [], []
    groups = defaultdict(lambda: {"rows": [], "errors": []})
    for domain in ("software", "physical"):
        for variant in ("direct", "preact", "single"):
            archive = await episode(cases(domain, "development")[0], domain, 0, variant, directory)
            archive["protocol_hash"] = manifest["protocol_hash"]
            (directory / f"{domain}-{variant}.json").write_text(json.dumps(archive))
            rows.append(archive["result"])
            errors.extend(archive["errors"])
            groups[f"{domain}/{variant}"]["rows"].append(archive["result"])
            groups[f"{domain}/{variant}"]["errors"].extend(archive["errors"])
    report = {
        "protocol_hash": manifest["protocol_hash"],
        "source_hash": manifest["source_hash"],
        "scope": manifest["scope"],
        "limitations": manifest["limitations"],
        "split": "held_out",
        "episodes": rows,
        "errors": errors,
        "metrics": {k: metrics(v["rows"], v["errors"]) for k, v in groups.items()},
        "paired_analysis": paired_analysis(rows),
    }
    (directory / "report.json").write_text(json.dumps(report))
    return protocol, directory


async def test_summary_reproduces_actual_two_world_archives_and_retains_failures(completed_cohort):
    protocol, directory = completed_cohort
    summary = summarize(protocol, directory)
    assert summary["completed_episodes"] == 6
    assert summary["ledger_audit"]["status"] == "complete-cohort-alignment-audited"
    assert summary["failures"]
    assert all(r["variant"] == "single" and r["status"] == "abstained" for r in summary["failures"])
    assert all(
        m["mean_cost_usd"] is None for m in summary["metrics"].values() if m["cost_coverage"] < 1
    )
    assert all(m["allocated_infrastructure_cost_usd"] is None for m in summary["metrics"].values())
    assert summary["engine_scores"]["metrics"]
    output = directory / "summary.txt"
    write_summary(protocol, directory, output)
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        write_summary(protocol, directory, output)
    assert output.read_bytes() == original


@pytest.mark.parametrize(
    "field", ["metrics", "episodes", "errors", "paired_analysis", "model_profile"]
)
async def test_summary_rejects_inconsistent_aggregate(completed_cohort, field):
    protocol, directory = completed_cohort
    path = directory / "report.json"
    report = json.loads(path.read_text())
    report[field] = [] if field in ("episodes", "errors") else {"inconsistent": True}
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="mismatch|differ|reproduce"):
        summarize(protocol, directory)


async def test_summary_rejects_incomplete_archives(completed_cohort):
    protocol, directory = completed_cohort
    (directory / "physical-preact.json").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        summarize(protocol, directory)
