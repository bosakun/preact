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
from preact.visual.audit import audit_manifest, audit_postprocessing, audit_run
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


@pytest.mark.parametrize("mode", ["error", "timeout", "cancel"])
async def test_post_execution_failures_preserve_core_and_retry_real_blender(tmp_path, mode):
    from preact.visual.postprocess import VisualPostprocessor

    world = PhysicalWorld()
    world.payload.update(object=list(world.payload["target"]), held=True, stage="transported")
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    renderer = BlenderRenderer(artifacts, temp_root=tmp_path)
    adapter = VisualAdapter(renderer, Settings(width=96, height=72, samples=4), 1)
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False),
        visual_adapter=adapter,
    )
    original = adapter.current
    started = asyncio.Event()
    snapshots = {}

    async def failing(state, *, receipt=None):
        if receipt:
            snapshots["run"] = store.get_run(runtime.run_id)
            snapshots["errors"] = store.error_rows()
            assert snapshots["run"]["status"] == "complete"
            assert store.execution_record(receipt)["status"] == "complete"
            started.set()
            if mode == "error":
                raise RenderFailure("intentional failure after complete Core result")
            if mode == "timeout":
                renderer.timeout = 0.01
        return await original(state, receipt=receipt)

    adapter.current = failing
    if mode == "cancel":
        task = asyncio.create_task(runtime.run(world))
        await asyncio.wait_for(started.wait(), 30)
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await runtime.run(world)
        assert result["success"] and result["status"] == "complete"
    run_id = runtime.run_id
    saved = store.get_run(run_id)
    assert saved["status"] == snapshots["run"]["status"]
    assert saved["result"] == snapshots["run"]["result"]
    assert store.error_rows() == snapshots["errors"] and len(snapshots["errors"]) == 1
    events = store.read_events(run_id)
    for kind in [
        "observation_evidence",
        "comparison",
        "prediction_error",
        "trust_updated",
        "finished",
    ]:
        assert any(e["kind"] == kind for e in events)
    failure = next(e for e in events if e["kind"] == "visual_postprocess_failed")
    assert failure["data"]["stage"] == "render_observed" and failure["data"]["retryable"]
    assert failure["seq"] > next(e["seq"] for e in events if e["kind"] == "finished")
    assert not any(e["kind"] in {"visual_observed_result", "visual_comparison"} for e in events)
    if mode in {"timeout", "cancel"}:
        assert renderer.last_cleanup["terminal_state_confirmed"]
        assert not Path(renderer.last_cleanup["temporary_directory"]).exists()
    assert audit_postprocessing(store, artifacts, run_id)["core_preserved"]
    with pytest.raises(ValueError, match="Missing executed comparison"):
        audit_run(store, artifacts, run_id)
    # A new processor reads saved events, independent of the original Runtime object.
    adapter.current = original
    renderer.timeout = 120
    processor = VisualPostprocessor(store, artifacts, adapter)
    first = await processor.run(run_id)
    assert first["completed"] == 1 and first["failed"] == 0
    count = len(store.read_events(run_id))
    second, third = await asyncio.gather(processor.run(run_id), processor.run(run_id))
    assert second["reused"] == third["reused"] == 1
    assert len(store.read_events(run_id)) == count
    assert audit_run(store, artifacts, run_id)["passed"]
    assert store.get_run(run_id)["result"] == snapshots["run"]["result"]
    # Saved image corruption is rejected on retry; no stale successful comparison accepted.
    observed = next(e for e in store.read_events(run_id) if e["kind"] == "visual_observed_result")
    m = manifest(artifacts, observed["data"]["manifest"])
    (artifacts.root / m["frames"][0]["artifacts"]["rgb"]).write_bytes(b"tampered image")
    rejected = await processor.run(run_id)
    assert rejected["failed"] == 1
    last = store.read_events(run_id)[-1]
    assert last["kind"] == "visual_postprocess_failed" and not last["data"]["retryable"]
    assert store.get_run(run_id)["result"] == snapshots["run"]["result"]
    store.db.dispose()


async def test_partial_postprocessing_retry_reuses_render_and_rejects_wrong_binding(
    tmp_path, monkeypatch
):
    import preact.visual.postprocess as postprocess

    world = PhysicalWorld()
    world.payload.update(object=list(world.payload["target"]), held=True, stage="transported")
    artifacts, store = Artifacts(str(tmp_path / "artifacts")), Store("sqlite:///:memory:")
    adapter = VisualAdapter(BlenderRenderer(artifacts), Settings(width=96, height=72, samples=4), 1)
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False),
        visual_adapter=adapter,
    )
    original = postprocess.comparison

    def interrupted_comparison(*args, **kwargs):
        raise OSError("owned comparison I/O failure")

    monkeypatch.setattr(postprocess, "comparison", interrupted_comparison)
    result = await runtime.run(world)
    assert result["status"] == "complete"
    events = store.read_events(runtime.run_id)
    rendered = [e for e in events if e["kind"] == "visual_observed_result"]
    assert len(rendered) == 1 and not any(e["kind"] == "visual_comparison" for e in events)
    failure = next(e for e in events if e["kind"] == "visual_postprocess_failed")
    assert failure["data"]["stage"] == "compare"
    assert failure["data"]["missing_artifacts"] == ["comparison"]
    assert audit_postprocessing(store, artifacts, runtime.run_id)["core_preserved"]
    monkeypatch.setattr(postprocess, "comparison", original)

    async def must_not_render(*args, **kwargs):
        raise AssertionError("Retry must reuse saved actual images")

    monkeypatch.setattr(adapter, "current", must_not_render)
    assert (await runtime.postprocessor.run(runtime.run_id))["completed"] == 1
    assert audit_run(store, artifacts, runtime.run_id)["passed"]
    # A conflicting successful record cannot be accepted by the cache or auditor.
    wrong = dict(rendered[0]["data"])
    wrong["receipt"] = "0" * 64
    store.append(runtime.run_id, "visual_observed_result", wrong)
    assert (await runtime.postprocessor.run(runtime.run_id))["failed"] == 1
    with pytest.raises(ValueError):
        audit_postprocessing(store, artifacts, runtime.run_id)
    assert store.get_run(runtime.run_id)["result"] == result
    store.db.dispose()


async def test_post_cancel_after_comparison_commit_reconciles_retry(tmp_path, monkeypatch):
    world = PhysicalWorld()
    world.payload.update(object=list(world.payload["target"]), held=True, stage="transported")
    artifacts, store = Artifacts(str(tmp_path / "artifacts")), Store("sqlite:///:memory:")
    adapter = VisualAdapter(BlenderRenderer(artifacts), Settings(width=96, height=72, samples=4), 1)
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False),
        visual_adapter=adapter,
    )
    original = store.call
    committed = asyncio.Event()

    async def cancel_ack(method, *args, **kwargs):
        value = await original(method, *args, **kwargs)
        if method == "append" and args[1] == "visual_comparison":
            committed.set()
            await asyncio.Event().wait()
        return value

    monkeypatch.setattr(store, "call", cancel_ack)
    task = asyncio.create_task(runtime.run(world))
    await asyncio.wait_for(committed.wait(), 30)
    saved = store.get_run(runtime.run_id)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    last = store.read_events(runtime.run_id)[-1]
    assert last["kind"] == "visual_postprocess_failed"
    assert last["data"]["comparison_committed"] and last["data"]["missing_artifacts"] == []
    monkeypatch.setattr(store, "call", original)
    count = len(store.read_events(runtime.run_id))
    assert (await runtime.postprocessor.run(runtime.run_id))["reused"] == 1
    assert len(store.read_events(runtime.run_id)) == count
    assert audit_run(store, artifacts, runtime.run_id)["passed"]
    assert store.get_run(runtime.run_id)["result"] == saved["result"]
    store.db.dispose()
