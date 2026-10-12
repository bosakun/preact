"""Core isolation regressions: real MuJoCo, explicitly stubbed visual I/O."""

import asyncio

import pytest

from preact.core.models import Policy, Prediction
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.physical import PhysicalWorld
from preact.engines.local import LocalVerifier
from preact.visual.contracts import Settings
from preact.visual.renderer import RenderFailure
from preact.visual.runtime import VisualRuntime


class FailingOutcomeAdapter:
    """No real-render claim: isolate the post-outcome lifecycle with a visual stub."""

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.settings = Settings()
        self.error = RenderFailure("intentional post-execution render failure")
        self.started = asyncio.Event()

    async def current(self, state, *, receipt=None):
        if receipt:
            self.started.set()
            if self.error is None:
                await asyncio.Event().wait()
            raise self.error
        return self.artifacts.json({"test_stub": True, "state": state.model_dump()})

    async def prediction(self, state, action, source):
        provenance = {
            "state": self.artifacts.json(state.model_dump()),
            "action": self.artifacts.json(action.model_dump()),
            "action_fingerprint": action.fingerprint,
            "mujoco_prediction": self.artifacts.json(source.model_dump()),
        }
        digest = self.artifacts.json(
            {
                "test_stub": True,
                "provenance": provenance,
                "request": {"settings": self.settings.model_dump(mode="json")},
                "frames": [],
            }
        )
        return Prediction(
            engine_id="blender-visual",
            engine_version="1",
            family="mujoco-visualization",
            state_id=state.id,
            action_ids=[action.id],
            evidence="generated_visual",
            artifacts={"manifest": digest},
        )


def ready_world():
    world = PhysicalWorld()
    world.payload.update(object=list(world.payload["target"]), held=True, stage="transported")
    return world


async def test_post_execution_render_error_preserves_complete_core_lifecycle(tmp_path):
    world = ready_world()
    artifacts, store = Artifacts(str(tmp_path)), Store("sqlite:///:memory:")
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False),
        visual_adapter=FailingOutcomeAdapter(artifacts),
    )
    try:
        result = await runtime.run(world)
    except RenderFailure:
        # Before the repair: execution is committed but all following Core work is absent.
        events = store.read_events(runtime.run_id)
        outcome = next(e for e in events if e["kind"] == "outcome")
        assert (
            store.execution_record(outcome["data"]["observation"]["receipt"])["status"]
            == "complete"
        )
        assert not any(
            e["kind"]
            in {
                "observation_evidence",
                "comparison",
                "prediction_error",
                "trust_updated",
                "finished",
            }
            for e in events
        )
        raise
    assert result["status"] == "complete" and result["success"]
    events = store.read_events(runtime.run_id)
    outcome = next(e for e in events if e["kind"] == "outcome")
    observation = outcome["data"]["observation"]
    record = store.execution_record(observation["receipt"])
    assert record["status"] == "complete" and record["receipt"] == observation
    assert store.get_run(runtime.run_id)["result"] == result
    for kind in [
        "observation_evidence",
        "comparison",
        "prediction_error",
        "trust_updated",
        "finished",
    ]:
        assert any(e["kind"] == kind for e in events)
    assert len(store.error_rows()) == 1
    failure = next(e for e in events if e["kind"] == "visual_postprocess_failed")
    assert failure["seq"] > next(e["seq"] for e in events if e["kind"] == "finished")
    assert failure["data"]["receipt"] == observation["receipt"]
    assert failure["data"]["exception_type"] == "RenderFailure"
    assert not any(e["kind"] in {"visual_observed_result", "visual_comparison"} for e in events)
    store.db.dispose()


@pytest.mark.parametrize(
    "error,retryable",
    [
        (TimeoutError("post timeout"), True),
        (OSError("post I/O"), True),
        (RuntimeError("post bug"), False),
    ],
)
async def test_post_errors_do_not_rewrite_core_results(tmp_path, error, retryable):
    world, store = ready_world(), Store("sqlite:///:memory:")
    artifacts = Artifacts(str(tmp_path))
    adapter = FailingOutcomeAdapter(artifacts)
    adapter.error = error
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False),
        visual_adapter=adapter,
    )
    result = await runtime.run(world)
    assert result["status"] == "complete" and store.get_run(runtime.run_id)["result"] == result
    assert len(store.error_rows()) == 1
    failures = [
        e for e in store.read_events(runtime.run_id) if e["kind"] == "visual_postprocess_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["data"]["exception_type"] == type(error).__name__
    assert failures[0]["data"]["retryable"] is retryable
    assert "observed_rgb" in failures[0]["data"]["missing_artifacts"]
    store.db.dispose()


async def test_post_cancel_propagates_without_cancelling_completed_core(tmp_path):
    world, store = ready_world(), Store("sqlite:///:memory:")
    artifacts = Artifacts(str(tmp_path))
    adapter = FailingOutcomeAdapter(artifacts)
    adapter.error = None
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(search=False),
        visual_adapter=adapter,
    )
    task = asyncio.create_task(runtime.run(world))
    await asyncio.wait_for(adapter.started.wait(), 10)
    saved = store.get_run(runtime.run_id)
    assert saved["status"] == "complete"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get_run(runtime.run_id)["status"] == saved["status"]
    assert store.get_run(runtime.run_id)["result"] == saved["result"]
    events = store.read_events(runtime.run_id)
    assert len(store.error_rows()) == 1
    outcome = next(e for e in events if e["kind"] == "outcome")
    assert store.execution_record(outcome["data"]["observation"]["receipt"])["status"] == "complete"
    failure = next(e for e in events if e["kind"] == "visual_postprocess_failed")
    assert failure["data"]["exception_type"] == "CancelledError"
    assert not any(e["kind"] == "visual_comparison" for e in events)
    store.db.dispose()
