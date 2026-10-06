"""Real local processes/storage exercise lifecycle, never the Cosmos model."""

import asyncio
import copy
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from preact.core.interfaces import EngineFailure, cleanup_evidence
from preact.core.models import Capabilities, EvidenceKind, PredictionRequest, State
from preact.core.store import Artifacts
from preact.domains.physical import PhysicalWorld
from preact.engines.cosmos import Cosmos, adapter_digest
from preact.engines.process import stop_process


@pytest.fixture
async def inputs(tmp_path, monkeypatch):
    repo = tmp_path / "launcher"
    (repo / "examples").mkdir(parents=True)
    monkeypatch.setenv("COSMOS_PYTHON", sys.executable)
    monkeypatch.setattr("preact.engines.cosmos.verify_checkout", lambda *args: None)
    # Bypass GPU setup only in this explicit lifecycle fixture. Production
    # construction still verifies the real pinned official repository.
    engine = Cosmos.__new__(Cosmos)
    engine.repo = repo
    engine.version = "lifecycle-fixture"
    engine.revision = "lifecycle-fixture"
    engine.adapter_hash = adapter_digest()
    engine.capabilities = Capabilities(
        engine_id="cosmos-action-conditioned",
        version=engine.version,
        family="cosmos-predict",
        domains=["physical"],
        evidence=EvidenceKind.VISUAL,
        tier=1,
        applicability="Lifecycle fixture, no model",
    )
    engine.artifacts = Artifacts(str(tmp_path / "artifacts"))
    camera = engine.artifacts.put(b"fixture camera bytes", "video/mp4")
    world = PhysicalWorld()
    original = await world.observe()
    payload = {
        **original.payload,
        "held": True,
        "grasp_evidence": {
            "method": "active-bilateral-finger-contact/v1",
            "bilateral_contact": True,
            "scope": "injected, not NVIDIA",
        },
        "tool_frame": "fixed-franka-downward-v1",
        "camera_video": camera,
        "joints": [0.0] * 9,
        "tool_position": [0, 0, 0.2],
        "tool_quaternion_wxyz": [0, 1, 0, 0],
        "sdk_runtime": {"scope": "injected lifecycle fixture, no SDK"},
    }
    payload["camera_observation"] = copy.deepcopy(
        {
            "video_digest": camera,
            "frame": "last",
            **{
                key: payload[key]
                for key in (
                    "object",
                    "joints",
                    "held",
                    "grasp_evidence",
                    "tool_frame",
                    "tool_position",
                    "tool_quaternion_wxyz",
                    "sdk_runtime",
                )
            },
        }
    )
    state = State.create("physical", payload, "lifecycle-fixture")
    action = (await world.propose(original, 2))[0].model_copy(update={"state_id": state.id})
    request = PredictionRequest(state=state, actions=[action])
    return engine, request


async def test_artifact_io_runs_off_loop_and_claims_remain_unknown(inputs):
    engine, request = inputs
    (engine.repo / "examples/action_conditioned.py").write_text(
        "import argparse\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser();p.add_argument('-i');p.add_argument('-o');"
        "p.add_argument('--seed');a=p.parse_args()\n"
        "out=Path(a.o);out.mkdir();(out/'fixture.mp4').write_bytes(b'fixture rollout')\n"
    )
    original = engine.artifacts
    threads, gaps = [], []
    done = asyncio.Event()
    main_thread = threading.get_ident()

    class SlowArtifacts:
        def read(self, *args):
            threads.append(threading.get_ident())
            time.sleep(0.08)
            return original.read(*args)

        def put(self, *args):
            threads.append(threading.get_ident())
            time.sleep(0.08)
            return original.put(*args)

    async def heartbeat():
        prior = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.005)
            current = time.monotonic()
            gaps.append(current - prior)
            prior = current

    engine.artifacts = SlowArtifacts()
    pulse = asyncio.create_task(heartbeat())
    try:
        prediction = await engine.predict(request)
    finally:
        done.set()
        await pulse
    assert threads and all(t != main_thread for t in threads)
    assert max(gaps) < 0.06
    assert original.read(prediction.artifacts["generated_video"]) == b"fixture rollout"
    assert prediction.success.value is None and prediction.risk.value is None
    assert not prediction.success.measured and not prediction.risk.measured
    assert not prediction.raw["cost_known"]
    assert prediction.engine_version == "lifecycle-fixture"


async def test_cancellation_drains_preparation_before_directory_removal(inputs, monkeypatch):
    engine, request = inputs
    loop = asyncio.get_running_loop()
    started, completed = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    paths, launched = [], []

    def prepare(root, _):
        paths.append(root)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(1)
        (root / "drained.txt").write_text("fixture")
        loop.call_soon_threadsafe(completed.set)

    async def launch(*args, **kwargs):
        launched.append(args)
        raise AssertionError("Cancelled preparation cannot launch a model")

    monkeypatch.setattr(engine, "_prepare", prepare)
    monkeypatch.setattr("preact.engines.cosmos.asyncio.create_subprocess_exec", launch)
    operation = asyncio.create_task(engine.predict(request))
    try:
        await asyncio.wait_for(started.wait(), 1)
        operation.cancel()
        operation.cancel()
        await asyncio.sleep(0.01)
        assert not operation.done() and paths[0].exists()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert completed.is_set() and not paths[0].exists() and not launched


@pytest.mark.skipif(os.name != "posix", reason="Actual Linux/macOS process-group lifecycle")
async def test_cancel_reaps_launcher_and_closes_inherited_child_pipes(inputs):
    engine, request = inputs
    marker = engine.repo / "started.txt"
    (engine.repo / "examples/action_conditioned.py").write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        "Path('started.txt').write_text('fixture child started')\ntime.sleep(30)\n"
    )
    operation = asyncio.create_task(engine.predict(request))
    async with asyncio.timeout(3):
        while not marker.exists():
            await asyncio.sleep(0.01)
    clock = time.monotonic()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError) as failure:
        await asyncio.wait_for(operation, 1)
    assert time.monotonic() - clock < 0.5
    evidence = cleanup_evidence(failure.value)
    assert evidence["operation_kind"] == "local_process"
    assert evidence["request_accepted"] and evidence["terminal_state_confirmed"]


@pytest.mark.parametrize("mode", ["failure", "missing"])
async def test_failed_launcher_never_returns_visual_success(inputs, mode):
    engine, request = inputs
    (engine.repo / "examples/action_conditioned.py").write_text(
        "import sys;sys.exit(1)" if mode == "failure" else "pass"
    )
    with pytest.raises(EngineFailure, match="failed or produced no rollout"):
        await engine.predict(request)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
async def test_repeated_cancellation_drains_process_reaping(inputs, monkeypatch):
    engine, request = inputs
    waiting, reaping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    kills = []

    async def communicate():
        waiting.set()
        await asyncio.Event().wait()

    async def wait():
        reaping.set()
        await release.wait()

    process = SimpleNamespace(pid=123456, communicate=communicate, wait=wait)

    async def launch(*args, **kwargs):
        assert kwargs["start_new_session"] == (os.name == "posix")
        return process

    monkeypatch.setattr("preact.engines.cosmos.asyncio.create_subprocess_exec", launch)
    monkeypatch.setattr("preact.engines.process.os.killpg", lambda pid, sig: kills.append(pid))
    operation = asyncio.create_task(engine.predict(request))
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        operation.cancel()
        await asyncio.wait_for(reaping.wait(), 1)
        operation.cancel()
        await asyncio.sleep(0.01)
        assert not operation.done() and kills == [123456]
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
async def test_process_reap_timeout_is_explicit_and_bounded(monkeypatch):
    async def wait():
        await asyncio.Event().wait()

    monkeypatch.setattr("preact.engines.process.os.killpg", lambda *_: None)
    clock = time.monotonic()
    evidence = await stop_process(SimpleNamespace(pid=123456, wait=wait))
    assert 4.8 < time.monotonic() - clock < 5.8
    assert evidence["request_accepted"] and not evidence["terminal_state_confirmed"]
    assert evidence["error"] == "TimeoutError"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group lifecycle")
async def test_denied_process_cleanup_never_becomes_confirmed(monkeypatch):
    def deny(*_):
        raise PermissionError("fixture-secret")

    monkeypatch.setattr("preact.engines.process.os.killpg", deny)
    evidence = await stop_process(SimpleNamespace(pid=123456))
    assert not evidence["request_accepted"] and not evidence["terminal_state_confirmed"]
    assert evidence["error"] == "PermissionError" and "fixture-secret" not in str(evidence)


@pytest.mark.parametrize(
    "fault", ["stale-camera", "unheld", "release", "orientation", "changed-adapter"]
)
async def test_incompatible_conditioning_never_launches_model(inputs, monkeypatch, fault):
    import copy

    engine, request = inputs
    payload = copy.deepcopy(request.state.payload)
    action = request.actions[0]
    if fault == "stale-camera":
        payload["object"][0] += 0.1
    elif fault == "unheld":
        payload["held"] = False
    elif fault == "release":
        action = action.model_copy(update={"kind": "release"})
    elif fault == "orientation":
        payload["tool_quaternion_wxyz"] = [1, 0, 0, 0]
        payload["camera_observation"]["tool_quaternion_wxyz"] = [1, 0, 0, 0]
    else:
        engine.adapter_hash = "0" * 64
    state = State.create("physical", payload, "fixture")
    action = action.model_copy(update={"state_id": state.id})
    launched = []

    async def launch(*args, **kwargs):
        launched.append(args)
        raise AssertionError("Incompatible model launch")

    monkeypatch.setattr("preact.engines.cosmos.asyncio.create_subprocess_exec", launch)
    with pytest.raises(EngineFailure):
        await engine.predict(PredictionRequest(state=state, actions=[action]))
    assert not launched


async def test_prepared_command_uses_measured_tool_pose_and_reports_extended_horizon(
    inputs, tmp_path
):
    import json

    engine, request = inputs
    timing = engine._prepare(tmp_path, request)
    annotation = json.loads((tmp_path / "annotations/0.json").read_text())
    actions = __import__("numpy").asarray(annotation["actions"])
    assert timing["commanded_frames"] == round(request.actions[0].duration * 20)
    assert len(actions) % 12 == 0 and actions[-1, 6] == 1
    # Fixed-downward rotation flips y/z; deltas integrate to the commanded EEF endpoint.
    expected = __import__("numpy").asarray(request.actions[0].payload["waypoints"][-1]) + [
        0,
        0,
        0.10,
    ]
    actual = request.state.payload["tool_position"] + (
        actions[:, :3].sum(axis=0) / 20 * [1, -1, -1]
    )
    assert __import__("numpy").allclose(actual, expected)
    assert timing["generated_horizon_seconds"] >= timing["commanded_seconds"]
