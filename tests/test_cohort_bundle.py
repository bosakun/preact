import hashlib
import json
import zipfile

import pytest

from preact.core.models import identity
from preact.core.store import Artifacts, Store
from preact.replay import import_archive
from scripts.bundle_cohort import build_bundle
from tests.test_cohort_summary import completed_cohort as completed_cohort


async def test_complete_bundle_is_reproducible_and_replays_without_new_truth(
    completed_cohort, tmp_path
):
    protocol, directory = completed_cohort
    (directory / "notes.txt").write_text("Unrelated owned fixture, excluded from export")
    (directory / "artifacts" / hashlib.sha256(b"unused").hexdigest()).write_bytes(b"unused")
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    receipt = build_bundle(protocol, directory, first)
    assert receipt["completed_episodes"] == 6
    assert receipt["artifact_count"] > 0
    assert build_bundle(protocol, directory, second)["bundle_sha256"] == receipt["bundle_sha256"]
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("bundle-manifest.json"))
        assert set(archive.namelist()) == set(manifest["members"]) | {"bundle-manifest.json"}
        assert archive.read("protocol.json") == protocol.read_bytes()
        assert "cohort/notes.txt" not in archive.namelist()
        assert f"cohort/artifacts/{hashlib.sha256(b'unused').hexdigest()}" not in archive.namelist()
        replay_root = tmp_path / "replay"
        for name, metadata in manifest["members"].items():
            body = archive.read(name)
            assert hashlib.sha256(body).hexdigest() == metadata["sha256"]
            assert len(body) == metadata["bytes"]
            if name.startswith("cohort/"):
                target = replay_root / name.removeprefix("cohort/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
    store = Store("sqlite:///:memory:")
    imported = import_archive(
        str(replay_root / "physical-preact.json"), store, Artifacts(str(tmp_path / "destination"))
    )
    assert store.get_run(imported)["status"] == "recorded"
    assert not store.error_rows() and not store.pending_execution(imported)
    store.db.dispose()


@pytest.mark.parametrize("missing", ["episode", "artifact"])
async def test_incomplete_evidence_cannot_publish_bundle(completed_cohort, tmp_path, missing):
    protocol, directory = completed_cohort
    path = directory / "physical-preact.json"
    if missing == "episode":
        path.unlink()
    else:
        data = json.loads(path.read_text())
        digest = next(e["data"]["artifact"] for e in data["events"] if e["kind"] == "prediction")
        (directory / "artifacts" / digest).unlink()
    output = tmp_path / "incomplete.zip"
    with pytest.raises((ValueError, FileNotFoundError)):
        build_bundle(protocol, directory, output)
    assert not output.exists()


def test_existing_bundle_is_preserved_without_reading_new_inputs(tmp_path):
    output = tmp_path / "existing.zip"
    output.write_bytes(b"existing owned evidence")
    with pytest.raises(FileExistsError):
        build_bundle(tmp_path / "unused-protocol.json", tmp_path / "unused-cohort", output)
    assert output.read_bytes() == b"existing owned evidence"


async def test_declared_size_bound_prevents_publication(completed_cohort, tmp_path, monkeypatch):
    protocol, directory = completed_cohort
    monkeypatch.setattr("scripts.bundle_cohort.MAX_TOTAL_BYTES", 1)
    output = tmp_path / "over-budget.zip"
    with pytest.raises(ValueError, match="inspection limit"):
        build_bundle(protocol, directory, output)
    assert not output.exists()


async def test_frozen_calibration_snapshot_is_required_and_preserved(completed_cohort, tmp_path):
    protocol, directory = completed_cohort
    manifest = json.loads(protocol.read_text())
    prior = {"split": "calibration", "source_hash": manifest["source_hash"], "errors": []}
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps(prior))
    # A different declared prior is rejected before any output can be produced.
    output = tmp_path / "mismatched-prior.zip"
    with pytest.raises(ValueError, match="Exact frozen calibration"):
        build_bundle(protocol, directory, output, calibration)
    assert not output.exists()
    manifest["calibration_hash"] = identity(prior)
    manifest.pop("protocol_hash")
    manifest["protocol_hash"] = identity(manifest)
    protocol.write_text(json.dumps(manifest))
    for path in directory.glob("*.json"):
        data = json.loads(path.read_text())
        data["protocol_hash"] = manifest["protocol_hash"]
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Exact frozen calibration"):
        build_bundle(protocol, directory, output)
    build_bundle(protocol, directory, output, calibration)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("calibration-report.json") == calibration.read_bytes()
