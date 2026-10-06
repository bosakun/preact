"""Real evaluator execution behind an SDK fixture, explicitly not live Nebius proof."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import PredictionRequest
from preact.core.registry import Registry
from preact.core.runtime import Runtime
from preact.core.store import Artifacts, Store
from preact.domains.cloud import SandboxRepositoryWorld
from preact.engines.local import LocalHeuristic
from tests.test_sandbox_checkpoints import sandbox_fixture


class ReleaseImage:
    def __init__(self, calls, inherited=None):
        self.calls, self.files = calls, dict(inherited or {})
        self.uuid, self.exit_code = uuid4(), 0
        self.result = SimpleNamespace(truncated=False, cost=0)

    async def run(self, **kwargs):
        assert kwargs["command"] == "python3"
        assert kwargs["preserve_env"] is False and kwargs["disposable"] is False
        self.calls.append((str(self.uuid), kwargs))
        child = ReleaseImage(self.calls, {**self.files, **kwargs["files"]})
        if "/release.py" in kwargs["files"]:
            with tempfile.TemporaryDirectory(prefix="preact-sdk-release-test-") as directory:
                root = Path(directory)
                for name, data in child.files.items():
                    (root / name.removeprefix("/")).write_bytes(data)
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    str(root / "release.py"),
                    str(root / "request.json"),
                    str(root / "protected.json"),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={"PATH": os.defpath},
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                assert process.returncode == 0, stderr.decode()
                child.stdout = stdout.decode()
        return child


def release_fixture(world):
    engine, _, _ = sandbox_fixture(world)
    calls, base = [], ReleaseImage([])
    base.calls = calls

    async def use(_):
        return base

    engine.sdk = SimpleNamespace(images=SimpleNamespace(use=use))
    world.sandbox = engine
    return engine, calls, base


async def test_cloud_release_boundary_executes_all_four_steps_through_shared_core(tmp_path):
    world = SandboxRepositoryWorld(3)
    engine, calls, base = release_fixture(world)
    store = Store("sqlite:///:memory:")
    runtime = Runtime(store, Artifacts(str(tmp_path)), Registry([LocalHeuristic(world), engine]))
    result = await runtime.run(world)
    assert result["success"] and not result["unsafe"] and result["steps"] == 4
    assert result["final_state"]["provenance"] == "token-factory-release-authority"
    assert result["cost_known"] is False
    assert result["final_state"]["payload"]["build_bytecode_hash"]
    assert base.files == {}  # no sibling mutated its parent
    events = store.read_events(runtime.run_id)
    assert [
        e["data"]["action"]["kind"]
        for e in events
        if e["kind"] == "decision" and e["data"]["decision"] == "execute"
    ] == ["patch", "migration", "configuration", "command"]
    requests = [c[1]["files"] for c in calls if "/protected.json" in c[1]["files"]]
    seeds = [json.loads(files["/protected.json"])["checkout_probe"] for files in requests]
    assert len(seeds) == len(set(seeds))
    assert all(
        "protected" not in json.loads(files["/request.json"])["payload"] for files in requests
    )
    receipts = [e["data"]["observation"]["receipt"] for e in events if e["kind"] == "outcome"]
    before = len(calls)
    assert await world.execute(None, receipts[-1]) == world.receipts[receipts[-1]]
    assert len(calls) == before


async def test_release_siblings_share_complete_database_checkpoint_and_budget_guard():
    world = SandboxRepositoryWorld()
    engine, calls, _ = release_fixture(world)
    state = await world.observe()
    unsafe, safe = await world.propose(state, 2)
    with pytest.raises(EngineFailure, match="two operation"):
        await engine.measure_release(state, safe, sample_budget=1)
    assert not calls
    registry = Registry([engine])
    first, _ = await registry.predict(
        engine, PredictionRequest(state=state, actions=[unsafe], sample_budget=2)
    )
    second, _ = await registry.predict(
        engine, PredictionRequest(state=state, actions=[safe], sample_budget=2)
    )
    assert first.violations and not second.violations
    assert first.raw["parent_checkpoint"] == second.raw["parent_checkpoint"]
    assert first.sample_count == 2 and second.sample_count == 1
    assert (await world.observe()).id == state.id
    assert "/database.b64" in calls[0][1]["files"]
