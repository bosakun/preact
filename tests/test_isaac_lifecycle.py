"""Actual fixture launchers exercise process boundaries without NVIDIA SDK/GPU."""

import asyncio
import os
import sys
import time

import pytest

from preact.core.interfaces import EngineFailure, cleanup_evidence
from preact.core.models import PredictionRequest
from preact.domains.physical import PhysicalWorld
from preact.engines.isaac import Isaac, isaac_step


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("ISAAC_PYTHON", sys.executable)
    monkeypatch.setattr("preact.engines.isaac.files", lambda _: tmp_path)
    return tmp_path / "isaac_step.py"


async def test_actual_fixture_launcher_receives_bundle_and_authority_flag(launcher):
    launcher.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "bundle=json.loads(Path(sys.argv[1]).read_text())\n"
        "Path(sys.argv[2]).write_text(json.dumps({'bundle':bundle,'authority':'--authority' in sys.argv}))\n"
    )
    result = await isaac_step({"seed": 7, "scope": "launcher-fixture"}, authority=True)
    assert result == {"bundle": {"seed": 7, "scope": "launcher-fixture"}, "authority": True}


@pytest.mark.skipif(os.name != "posix", reason="Actual Linux/macOS process-group lifecycle")
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_fixture_child_pipes_close_on_cancel_or_deadline(launcher, stop):
    marker = launcher.parent / "started.txt"
    launcher.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        f"Path({str(marker)!r}).write_text('child started')\n"
        "time.sleep(30)\n"
    )
    operation = asyncio.create_task(isaac_step({}, timeout=0.2 if stop == "deadline" else 3))
    async with asyncio.timeout(3):
        while not marker.exists():
            await asyncio.sleep(0.005)
    clock = time.monotonic()
    if stop == "cancel":
        operation.cancel()
    with pytest.raises(asyncio.CancelledError if stop == "cancel" else TimeoutError) as failure:
        await asyncio.wait_for(operation, 1)
    assert time.monotonic() - clock < 0.5
    evidence = cleanup_evidence(failure.value)
    assert evidence["operation_kind"] == "local_process"
    assert evidence["request_accepted"] and evidence["terminal_state_confirmed"]


@pytest.mark.parametrize("mode", ["failure", "missing"])
async def test_failed_or_missing_sdk_output_never_becomes_a_prediction(launcher, mode):
    launcher.write_text("import sys;sys.exit(1)" if mode == "failure" else "pass")
    with pytest.raises(EngineFailure, match="no substitute"):
        await isaac_step({})


async def test_nominal_adapter_marks_gpu_cost_unknown_and_one_sample_uncertain(monkeypatch):
    engine = Isaac()
    world = PhysicalWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    successor = world.materialize(state, action)

    async def step(*args, **kwargs):
        return {
            "worker_backend_sha256": engine.backend_hash,
            "state": successor.model_dump(),
            "checks": {"fixture_check": True},
            "metrics": {},
            "scope": "Adapter fixture, no NVIDIA SDK/GPU",
        }

    monkeypatch.setattr("preact.engines.isaac.isaac_step", step)
    prediction = await engine.predict(PredictionRequest(state=state, actions=[action]))
    assert not prediction.raw["cost_known"]
    assert prediction.sample_count == 1 and prediction.risk.upper > 0.05
    assert prediction.success.lower < 0.5
