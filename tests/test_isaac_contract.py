"""CPU conformance only: injected SDK version/inventory are never live NVIDIA proof."""

import ast
import copy
from pathlib import Path

import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import PredictionRequest
from preact.domains.physical import PhysicalWorld, obstacles, robust_clearance
from preact.engines.isaac import Isaac, IsaacPerturbations
from workers.isaac_contract import (
    backend_digest,
    parse_gpu_inventory,
    scene_spec,
    sdk_build,
    waypoint_ticks,
)


def test_scene_geometry_preserves_cube_size_and_all_obstacles():
    p = PhysicalWorld().payload
    p.update(
        cube_half=0.035, extra_obstacles=[{"center": [0.1, 0.4, 0.1], "half": [0.06, 0.1, 0.1]}]
    )
    result = scene_spec(p)
    assert result["cube_half"] == 0.035
    assert [{"center": b["center"], "half": b["half"]} for b in result["obstacles"]] == obstacles(p)
    # Real analytic geometry: the second keep-out box blocks this segment even
    # though the original single-box check passes. No physics simulator is invoked.
    start, end = [-0.3, 0.4, 0.1], [0.3, 0.4, 0.1]
    assert robust_clearance(start, end, p["obstacle"], p["obstacle_half"])
    assert not all(
        robust_clearance(start, end, b["center"], b["half"]) for b in result["obstacles"]
    )
    assert len({b["path"] for b in result["obstacles"]}) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"cube_half": 0},
        {"cube_half": True},
        {"extra_obstacles": [{"center": [0, 0, 0], "half": [0, 0.1, 0.1]}]},
        {"extra_obstacles": [{"center": [0, float("nan"), 0], "half": [0.1, 0.1, 0.1]}]},
    ],
)
def test_invalid_geometry_does_not_reach_sdk(change):
    payload = copy.deepcopy(PhysicalWorld().payload)
    payload.update(change)
    with pytest.raises(ValueError):
        scene_spec(payload)


def test_sdk_build_is_measured_not_target_fallback():
    version = ("5.1.0", "rc.1", "5", "1", "0", "rc", "1", "fixture-build")
    assert sdk_build(version)["buildtag"] == "fixture-build"
    for bad in [("",) * 8, ("5.0.0", "", "5", "0", "0", "", "", ""), version[:7], (None,) * 8]:
        with pytest.raises(RuntimeError):
            sdk_build(bad)


def test_gpu_inventory_records_measurements_and_rejects_unknown_values():
    rows = parse_gpu_inventory("0, Fixture RTX, 999.1, 48000\n1, Fixture RTX, 999.1, 24000\n")
    assert rows[1]["memory_total_mib"] == 24000 and rows[1]["index"] == 1
    for bad in [
        "",
        "0, GPU, N/A, 48000",
        "0, GPU, 999.1, N/A",
        "0, GPU, 999.1, 1\n0, GPU, 999.1, 1",
    ]:
        with pytest.raises(RuntimeError):
            parse_gpu_inventory(bad)


def test_platform_failure_never_invents_hardware_or_exposes_cli_output(monkeypatch):
    import subprocess

    from workers.isaac_contract import platform_evidence

    monkeypatch.setattr("workers.isaac_contract.platform.system", lambda: "Linux")
    monkeypatch.setattr("workers.isaac_contract.platform.machine", lambda: "x86_64")

    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="private-diagnostic-sentinel")

    monkeypatch.setattr("workers.isaac_contract.subprocess.run", failed)
    with pytest.raises(RuntimeError) as error:
        platform_evidence()
    assert "private-diagnostic-sentinel" not in str(error.value)
    assert "CalledProcessError" in str(error.value)


def test_source_version_changes_when_worker_geometry_or_contract_changes(tmp_path, monkeypatch):
    import workers.isaac_contract as module

    for name in ["isaac_contract.py", "isaac_step.py"]:
        (tmp_path / name).write_bytes(Path(module.__file__).with_name(name).read_bytes())
    original = backend_digest()
    monkeypatch.setattr(module, "__file__", str(tmp_path / "isaac_contract.py"))
    assert backend_digest() == original
    (tmp_path / "isaac_step.py").write_text("changed controller geometry")
    assert backend_digest() != original


@pytest.mark.parametrize("kind", ["nominal", "perturbations"])
async def test_missing_or_mismatched_worker_identity_never_yields_prediction(monkeypatch, kind):
    engine = Isaac() if kind == "nominal" else IsaacPerturbations(samples=1)
    world = PhysicalWorld()
    state = await world.observe()
    action = (await world.propose(state, 3))[1]

    async def step(*args, **kwargs):
        return {"worker_backend_sha256": "0" * 64}

    monkeypatch.setattr("preact.engines.isaac.isaac_step", step)
    with pytest.raises(EngineFailure, match="provenance"):
        await engine.predict(PredictionRequest(state=state, actions=[action]))


def test_sdk_worker_and_contract_parse_under_actual_target_grammar():
    import workers.isaac_contract as module

    for name in ["isaac_contract.py", "isaac_step.py"]:
        ast.parse(Path(module.__file__).with_name(name).read_text(), feature_version=(3, 11))


def test_transport_duration_is_total_not_per_waypoint():
    assert waypoint_ticks(2, 120, 3) == [80, 80, 80]
    assert waypoint_ticks(2, 20, 3) == [14, 13, 13]
    for duration, rate, count in [(0.01, 20, 3), (float("nan"), 20, 1), (True, 20, 1), (1, 20, 0)]:
        with pytest.raises(ValueError):
            waypoint_ticks(duration, rate, count)


def test_grasp_requires_current_bilateral_contacts_not_endpoint_proximity():
    from workers.isaac_contract import bilateral_grasp

    fingers = {"/Franka/panda_leftfinger", "/Franka/panda_rightfinger"}
    active = {
        ("cube-collider", "left-collider"): ["/World/Cube", "/Franka/panda_leftfinger"],
        ("cube-collider", "right-collider"): ["/World/Cube", "/Franka/panda_rightfinger"],
    }
    assert bilateral_grasp(active, fingers)
    active.pop(("cube-collider", "right-collider"))  # CONTACT_LOST must invalidate grasp.
    assert not bilateral_grasp(active, fingers)
    assert not bilateral_grasp({}, fingers) and not bilateral_grasp(active, set())
