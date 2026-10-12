"""Real Blender opt-in tests. Never replace this module with mocked rendering."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from preact.core.models import Policy, PredictionRequest
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.physical import PhysicalWorld
from preact.engines.local import LocalVerifier
from preact.visual.adapter import VisualAdapter, manifest
from preact.visual.audit import audit_manifest, audit_run
from preact.visual.contracts import RenderRequest, Settings, translate
from preact.visual.gallery import build_gallery
from preact.visual.renderer import BlenderRenderer, RenderFailure
from preact.visual.runtime import VisualRuntime

pytestmark = [
    pytest.mark.blender,
    pytest.mark.skipif(
        not os.environ.get("PREACT_BLENDER"), reason="requires actual PREACT_BLENDER binary"
    ),
]


async def test_real_render_reproduction_geometry_and_distinct_candidates(tmp_path):
    world = PhysicalWorld()
    state = await world.observe()
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    adapter = VisualAdapter(BlenderRenderer(artifacts), Settings(width=96, height=72, samples=4), 2)
    first, second = await adapter.current(state), await adapter.current(state)
    assert (
        manifest(artifacts, first)["frames"][0]["artifacts"]
        == manifest(artifacts, second)["frames"][0]["artifacts"]
    )
    assert audit_manifest(artifacts, first)["passed"]
    forged = manifest(artifacts, first)
    forged["frames"][0]["objects"][1]["center"][0] += 0.01
    # Even a newly hashed, syntactically valid manifest cannot move source geometry.
    with pytest.raises(ValueError, match="coordinate/dimension"):
        audit_manifest(artifacts, artifacts.json(forged))
    images = []
    for action in await world.propose(state, 3):
        source = await LocalVerifier(world).predict(
            PredictionRequest(state=state, actions=[action])
        )
        visual = await adapter.prediction(state, action, source)
        m = manifest(artifacts, visual.artifacts["manifest"])
        assert audit_manifest(artifacts, visual.artifacts["manifest"])["passed"]
        images.append(m["frames"][-1]["artifacts"])
    assert len({i["rgb"] for i in images}) == 3
    assert len({i["segmentation"] for i in images}) == 3


async def test_real_runtime_receipts_audit_and_gallery(tmp_path):
    world = PhysicalWorld()
    world.task = world.task.model_copy(update={"max_steps": 2})
    artifacts, store = Artifacts(str(tmp_path / "artifacts")), Store("sqlite:///:memory:")
    adapter = VisualAdapter(BlenderRenderer(artifacts), Settings(width=96, height=72, samples=4), 1)
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False, max_depth=1, max_nodes=4, max_seconds=120),
        visual_adapter=adapter,
    )
    result = await runtime.run(world)
    assert result["steps"] == 2 and not result["unsafe"]
    audit = audit_run(store, artifacts, runtime.run_id)
    assert audit["executed_candidates"] == 2
    assert audit["unexecuted_candidates"] >= 2
    gallery = tmp_path / "gallery.html"
    build_gallery(store, artifacts, runtime.run_id, gallery)
    assert "outcome unavailable" in gallery.read_text()
    events = store.read_events(runtime.run_id)
    for event in events:
        if event["kind"] == "visual_comparison":
            c = manifest(artifacts, event["data"]["artifact"])
            assert c["position_error_m"] == 0
            assert c["projection_error_px"] == 0
            assert c["depth_mae_m"] == 0
            assert c["segmentation_changed_pixels"] == 0
    # Auditor detects a manifest altered in-place, independent of production reader.
    visual = next(e for e in events if e["kind"] == "visual_prediction")
    digest = visual["data"]["prediction"]["artifacts"]["manifest"]
    (artifacts.root / digest).write_bytes(b"{}")
    with pytest.raises(ValueError, match="integrity"):
        audit_run(store, artifacts, runtime.run_id)
    store.db.dispose()


async def test_real_blender_timeout_cancellation_and_invalid_input(tmp_path):
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    renderer = BlenderRenderer(artifacts, timeout=0.01, temp_root=tmp_path)
    request = RenderRequest(frames=(translate(await PhysicalWorld().observe()),))
    with pytest.raises(RenderFailure, match="timed out"):
        await renderer.render(request, {})
    assert renderer.last_cleanup["terminal_state_confirmed"]
    renderer.timeout = 120
    task = asyncio.create_task(renderer.render(request, {}))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert renderer.last_cleanup["terminal_state_confirmed"]
    assert not Path(renderer.last_cleanup["temporary_directory"]).exists()
    # Actual Blender executes the fixed worker; malformed JSON never executes Python input.
    bad = tmp_path / "request.json"
    bad.write_text(json.dumps({"version": "1", "settings": {}, "frames": []}))
    worker = Path(__file__).resolve().parents[1] / "workers/blender.py"
    process = await asyncio.create_subprocess_exec(
        renderer.binary,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(worker),
        "--",
        str(bad),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(process.wait(), 30)
    assert process.returncode == 1
    assert not (tmp_path / "result.json").exists()
    assert not list(artifacts.root.iterdir())
