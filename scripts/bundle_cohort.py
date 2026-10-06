"""Package a completed audited cohort for recorded replay, never live execution."""

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from preact.core.models import identity
from preact.core.store import Artifacts
from scripts.summarize_cohort import summarize

MAX_MEMBER_BYTES = 32 * 1024**2
MAX_TOTAL_BYTES = 4 * 1024**3


def build_bundle(protocol, directory, output, calibration=None):
    if output.exists():
        raise FileExistsError("Refusing to replace an existing evidence bundle")
    protocol_digest = hashlib.sha256(protocol.read_bytes()).hexdigest()
    manifest_input = json.loads(protocol.read_text())
    prior_bytes = calibration.read_bytes() if calibration else None
    prior = json.loads(prior_bytes) if prior_bytes is not None else None
    if (identity(prior) if prior is not None else None) != manifest_input["calibration_hash"]:
        raise ValueError("Exact frozen calibration snapshot is required")
    if prior is not None and (
        prior["split"] != "calibration"
        or prior["source_hash"] != manifest_input["source_hash"]
        or prior.get("model_profile") != manifest_input.get("model_profile")
    ):
        raise ValueError("Calibration source/model/split mismatch")
    findings = summarize(protocol, directory)
    root = Path(__file__).resolve().parents[1]
    sources = {
        "protocol.json": protocol,
        "cohort/report.json": directory / "report.json",
        "LICENSE": root / "LICENSE",
        "third-party.md": root / "docs/third-party.md",
    }
    expected = {"protocol.json": protocol_digest, "cohort/report.json": findings["report_sha256"]}
    if calibration is not None:
        sources["calibration-report.json"] = calibration
        expected["calibration-report.json"] = hashlib.sha256(prior_bytes).hexdigest()
    references = set()
    for name, digest in findings["ledger_audit"]["archive_sha256"].items():
        path = directory / name
        sources[f"cohort/{name}"] = path
        expected[f"cohort/{name}"] = digest
        data = json.loads(path.read_text())
        for event in data["events"]:
            if event["kind"] == "prediction":
                references.add(event["data"]["artifact"])
                references.update(event["data"]["prediction"]["artifacts"].values())
            elif event["kind"] == "outcome":
                references.update(event["data"]["observation"]["artifacts"].values())
    artifact_directory = directory / "artifacts"
    if references and (artifact_directory.is_symlink() or not artifact_directory.is_dir()):
        raise ValueError("Referenced cohort artifact directory is unavailable")
    artifacts = Artifacts(str(artifact_directory)) if references else None
    for digest in references:
        # Existing content-addressed storage validates digest syntax and content.
        path = artifacts.root / digest
        if path.is_symlink():
            raise ValueError("Artifact must be a regular cohort file")
        artifacts.read(digest)
        sources[f"cohort/artifacts/{digest}"] = path
        expected[f"cohort/artifacts/{digest}"] = digest
    total = sum(path.stat().st_size for path in sources.values())
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Cohort exceeds bundle inspection limit")
    manifest = {
        "schema_version": 1,
        "scope": "Audited recorded evidence; no new inference, execution, deployment or sponsor validation",
        "source_hash": findings["source_hash"],
        "protocol_hash": findings["protocol_hash"],
        "calibration_hash": manifest_input["calibration_hash"],
        "completed_episodes": findings["completed_episodes"],
        "artifact_count": len(references),
        "members": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="preact-bundle-", dir=output.parent) as temporary:
        staged = Path(temporary) / "bundle.zip"
        with zipfile.ZipFile(staged, "w") as archive:

            def add(name, data):
                if len(data) > MAX_MEMBER_BYTES:
                    raise ValueError("Cohort member exceeds inspection limit")
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
                manifest["members"][name] = {
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }

            for name, path in sorted(sources.items()):
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Bundle source must be a regular file")
                body = path.read_bytes()
                if name in expected and hashlib.sha256(body).hexdigest() != expected[name]:
                    raise ValueError("Cohort changed after audit")
                add(name, body)
            add("findings.json", (json.dumps(findings, indent=2) + "\n").encode())
            encoded = (json.dumps(manifest, indent=2) + "\n").encode()
            # The manifest lists every other member; it cannot hash itself.
            info = zipfile.ZipInfo("bundle-manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, encoded, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        os.link(staged, output)  # Atomic exclusive publication, preserving existing evidence.
    return {
        **{
            key: manifest[key]
            for key in (
                "scope",
                "source_hash",
                "protocol_hash",
                "completed_episodes",
                "artifact_count",
            )
        },
        "status": "complete-recorded-cohort-bundled",
        "bundle_bytes": output.stat().st_size,
        "bundle_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "member_count": len(manifest["members"]) + 1,
        "publication": "private local artifact; not uploaded",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.protocol, args.directory, args.output, args.calibration)))
