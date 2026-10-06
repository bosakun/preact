import hashlib
import sys
import zipfile

import pytest

from preact.core.models import Action, State
from preact.engines.isaac import isaac_step
from preact.engines.sdk_bridge import MODULES, make_sdk_bundle


def test_action_fingerprint_survives_default_duration_json_round_trip():
    action = Action(name="move", kind="move", state_id="state", payload={"waypoints": [[0, 0, 1]]})
    assert action.fingerprint == Action.model_validate_json(action.model_dump_json()).fingerprint


def test_archive_is_exact_minimal_source_and_reproducible(tmp_path):
    from importlib.resources import files

    left, right = tmp_path / "left.zip", tmp_path / "right.zip"
    digest = make_sdk_bundle(left)
    assert make_sdk_bundle(right) == digest
    assert hashlib.sha256(left.read_bytes()).hexdigest() == digest
    with zipfile.ZipFile(left) as archive:
        assert set(archive.namelist()) == {f"preact/{p}" for p in MODULES} | {
            "preact-sdk-manifest.json"
        }
        for name in MODULES:
            assert archive.read(f"preact/{name}") == files("preact").joinpath(name).read_bytes()


async def test_launcher_imports_exact_shared_models_without_api_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_PYTHON", sys.executable)
    monkeypatch.setattr("preact.engines.isaac.files", lambda _: tmp_path)
    (tmp_path / "isaac_step.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "from preact.core import models\n"
        "bundle=json.loads(Path(sys.argv[1]).read_text())\n"
        "state=models.State.model_validate(bundle['state'])\n"
        "action=models.Action.model_validate(bundle['action'])\n"
        "assert models.__file__.startswith(os.environ['PREACT_ISAAC_BRIDGE']+'/')\n"
        "Path(sys.argv[2]).write_text(json.dumps({'state_id':state.id,'fingerprint':action.fingerprint}))\n"
    )
    state = State.create("physical", {"object": [0, 0, 1]}, "fixture")
    action = Action(name="lift", kind="lift", state_id=state.id, payload={"waypoints": [[0, 0, 2]]})
    result = await isaac_step({"state": state.model_dump(), "action": action.model_dump()})
    assert result == {"state_id": state.id, "fingerprint": action.fingerprint}


def test_checksum_failure_stops_before_dependency_or_gpu_import(tmp_path, monkeypatch):
    from preact.engines.sdk_bridge import sdk_import_evidence

    bundle = tmp_path / "bridge.zip"
    make_sdk_bundle(bundle)
    monkeypatch.setattr(sys, "version_info", (3, 11, 0))
    monkeypatch.setenv("PREACT_ISAAC_BRIDGE", str(bundle))
    monkeypatch.setenv("PREACT_ISAAC_BRIDGE_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        sdk_import_evidence()


def test_missing_encoder_fails_before_simulator_import(tmp_path, monkeypatch):
    from workers.isaac_step import step

    # Import preflight is injected only to exercise the missing encoder path on CPU.
    monkeypatch.setattr("preact.engines.sdk_bridge.sdk_import_evidence", lambda: {})
    monkeypatch.setattr("workers.isaac_step.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="requires FFmpeg"):
        step({}, tmp_path)
