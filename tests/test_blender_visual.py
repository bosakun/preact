"""Contracts and process negatives. These do not claim real Blender validation."""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from preact.core.decision import evaluate, gate
from preact.core.models import Decision, Policy, PredictionRequest, State
from preact.core.store import Artifacts, Store
from preact.datasets.scenes import all_scenes
from preact.domains.physical import PhysicalWorld, scene_xml
from preact.domains.scene import SceneWorld
from preact.engines.local import LocalVerifier
from preact.visual.adapter import comparison, prediction_frames
from preact.visual.audit import analytic_image, expected_geometry
from preact.visual.contracts import Camera, RenderRequest, Settings, project, translate
from preact.visual.renderer import BlenderRenderer, RenderFailure


@pytest.mark.parametrize("spec", all_scenes(), ids=lambda spec: spec.name)
async def test_all_scene_families_translate_exact_mujoco_geometry(spec):
    import mujoco

    world = SceneWorld(spec)
    state = await world.observe()
    frame = translate(state)
    model = mujoco.MjModel.from_xml_string(scene_xml(state.payload))
    for shape in frame.objects:
        if shape.name in {"target", "gripper", "floor"}:
            continue
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, shape.name)
        if shape.name == "cube":
            body = model.geom_bodyid[gid]
            assert tuple(model.body_pos[body]) == shape.center
        else:
            assert tuple(model.geom_pos[gid]) == shape.center
        assert tuple(model.geom_size[gid]) == shape.half
    assert len(frame.objects) == len(expected_geometry(state.payload))


async def test_translation_rejects_mutated_unbounded_or_wrong_kind_state():
    state = await PhysicalWorld().observe()
    state.payload["object"][0] = 100
    with pytest.raises(ValidationError, match="identity"):
        translate(state)
    state = State.create("physical", state.payload, "invalid-bounds")
    with pytest.raises(ValidationError, match="bounds"):
        translate(state)
    with pytest.raises(ValueError, match="phase"):
        translate(
            State.create(
                "physical",
                (await PhysicalWorld().observe()).payload,
                "hypothesis",
                kind="hypothetical",
            )
        )


@pytest.mark.parametrize(
    "kwargs", [{"width": 513}, {"samples": 65}, {"threads": 9}, {"seed": -1}, {"engine": "EVEE"}]
)
def test_bounded_settings(kwargs):
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_camera_projection_and_axial_depth_units():
    settings = Settings(camera=Camera(position=(0, -2, 1), look_at=(0, 0, 1)))
    assert project((0, 0, 1), settings) == {"uv": [96, 72], "axial_depth_m": 2.0}
    assert project((1, 0, 1), settings)["uv"] == [208, 72]
    with pytest.raises(ValidationError, match="Degenerate"):
        Camera(position=(0, 0, 1), look_at=(0, 0, 0))
    objects = [{"geometry": "box", "center": [0, 0, 1], "half": [0.5] * 3, "object_id": 2}]
    depth, ids = analytic_image(objects, settings.model_dump())
    assert depth[72, 96] == pytest.approx(1.5)
    assert ids[72, 96] == 2


async def test_prediction_alignment_and_distinct_retained_futures():
    world = PhysicalWorld()
    state = await world.observe()
    actions = await world.propose(state, 3)
    predictions = [
        await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[a]))
        for a in actions
    ]
    finals = []
    for action, source in zip(actions, predictions, strict=True):
        frames = prediction_frames(state, action, source)
        assert frames[0].kind == "observed"
        assert all(frame.kind == "hypothetical" for frame in frames[1:])
        assert frames[-1].state_id == source.successor.id
        finals.append(frames[-1].objects[1].center)
    assert len(set(finals)) == 3
    with pytest.raises(ValueError, match="Unaligned"):
        prediction_frames(state, actions[1], predictions[0])
    bad = predictions[0].model_copy(deep=True)
    bad.raw["trajectory"] = [[0, 0, 0]]
    with pytest.raises(ValueError, match="Trajectory"):
        prediction_frames(state, actions[0], bad)


async def test_even_visual_positive_claims_cannot_authorize_dangerous_action():
    from preact.core.models import Estimate, EvidenceKind, Prediction

    world = PhysicalWorld()
    state = await world.observe()
    action = (await world.propose(state, 3))[0]
    visual = Prediction(
        engine_id="blender-visual",
        engine_version="1",
        family="mujoco-visualization",
        state_id=state.id,
        action_ids=[action.id],
        evidence=EvidenceKind.VISUAL,
        success=Estimate(value=1, lower=1, upper=1, uncertainty=0, measured=True),
        risk=Estimate(value=0, lower=0, upper=0, uncertainty=0, measured=True),
        mandatory_checks={k: True for k in world.task.required_checks},
    )
    e = evaluate([visual], world.task, {}, state=state, action=action)
    assert gate(e, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN
    unsafe = await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[action]))
    e = evaluate([unsafe, visual], world.task, {}, state=state, action=action)
    assert gate(e, state.id, action.fingerprint, Policy(), False).decision == Decision.ABSTAIN


async def test_no_blender_binary_is_explicit_failure(tmp_path):
    with pytest.raises(RenderFailure, match="unavailable"):
        BlenderRenderer(Artifacts(str(tmp_path)), str(tmp_path / "missing"))


def fake_binary(tmp_path, body):
    path = tmp_path / "fake-blender"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return str(path)


@pytest.mark.parametrize(
    "body, message",
    [
        ("raise SystemExit(7)\n", "exited 7"),
        ("pass\n", "Missing completion"),
        (
            "import json,pathlib,sys\np=pathlib.Path(sys.argv[-1]).parent\n(p/'result.json').write_text(json.dumps({'frames':[{}]}))\n",
            "Missing, unsafe",
        ),
    ],
)
async def test_error_and_incomplete_worker_never_publish_success(tmp_path, body, message):
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    renderer = BlenderRenderer(artifacts, fake_binary(tmp_path, body), temp_root=tmp_path)
    request = RenderRequest(frames=(translate(await PhysicalWorld().observe()),))
    with pytest.raises(RenderFailure, match=message):
        await renderer.render(request, {})
    assert list(artifacts.root.iterdir()) == []
    assert renderer.last_cleanup["terminal_state_confirmed"]
    assert not Path(renderer.last_cleanup["temporary_directory"]).exists()


async def test_timeout_and_cancellation_kill_process_and_remove_temporary_files(tmp_path):
    binary = fake_binary(tmp_path, "import time\ntime.sleep(30)\n")
    renderer = BlenderRenderer(
        Artifacts(str(tmp_path / "artifacts")), binary, timeout=0.05, temp_root=tmp_path
    )
    request = RenderRequest(frames=(translate(await PhysicalWorld().observe()),))
    with pytest.raises(RenderFailure, match="timed out"):
        await renderer.render(request, {})
    assert renderer.last_cleanup["terminal_state_confirmed"]
    renderer.timeout = 10
    task = asyncio.create_task(renderer.render(request, {}))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = renderer.last_cleanup["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not Path(renderer.last_cleanup["temporary_directory"]).exists()
    assert list(renderer.artifacts.root.iterdir()) == []


def test_content_hash_detects_image_manifest_tampering(tmp_path):
    artifacts = Artifacts(str(tmp_path))
    digest = artifacts.put(b"real saved visual bytes")
    (tmp_path / digest).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        artifacts.read(digest)


async def test_pending_receipt_cannot_label_a_visual_prediction(tmp_path):
    from preact.core.models import Observation, Prediction

    world = PhysicalWorld()
    state = await world.observe()
    action = (await world.propose(state, 3))[1]
    source = await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[action]))
    artifacts = Artifacts(str(tmp_path))
    provenance = {
        "mujoco_prediction": artifacts.json(source.model_dump()),
        "action": artifacts.json(action.model_dump()),
    }
    digest = artifacts.json({"provenance": provenance})
    visual = Prediction(
        engine_id="blender-visual",
        engine_version="1",
        family="mujoco-visualization",
        evidence="generated_visual",
        state_id=state.id,
        action_ids=[action.id],
        artifacts={"manifest": digest},
    )
    store = Store("sqlite:///:memory:")
    run = store.create_run({})
    receipt = store.intent(run, state.id, action.fingerprint)
    obs = Observation(state=state, receipt=receipt, success=False, unsafe=False)
    with pytest.raises(ValueError, match="complete Runtime receipt"):
        comparison(artifacts, store, run, visual, obs, "")
    store.db.dispose()


async def test_cancel_during_process_spawn_is_drained(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    started = asyncio.Event()

    async def delayed_spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        started.set()
        await asyncio.sleep(0.1)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    binary = fake_binary(tmp_path, "import time\ntime.sleep(30)\n")
    renderer = BlenderRenderer(Artifacts(str(tmp_path / "artifacts")), binary, temp_root=tmp_path)
    request = RenderRequest(frames=(translate(await PhysicalWorld().observe()),))
    task = asyncio.create_task(renderer.render(request, {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert renderer.last_cleanup["terminal_state_confirmed"]
    assert not Path(renderer.last_cleanup["temporary_directory"]).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(renderer.last_cleanup["pid"], 0)


async def test_visual_failure_does_not_dispatch_an_action(tmp_path):
    from preact.core.registry import Registry
    from preact.visual.adapter import VisualAdapter
    from preact.visual.runtime import VisualRuntime

    world, store = PhysicalWorld(), Store("sqlite:///:memory:")
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    adapter = VisualAdapter(
        BlenderRenderer(artifacts, fake_binary(tmp_path, "raise SystemExit(7)\n"))
    )
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False),
        visual_adapter=adapter,
    )
    with pytest.raises(RenderFailure, match="exited 7"):
        await runtime.run(world)
    events = store.read_events(runtime.run_id)
    assert not any(e["kind"] in {"decision", "execution_intent", "outcome"} for e in events)
    assert world.receipts == {}
    assert store.get_run(runtime.run_id)["status"] == "failed"
    store.db.dispose()
