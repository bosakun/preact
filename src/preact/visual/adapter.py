"""MuJoCo-to-Blender adapter; no advertised safety claims, no Registry authority."""

import io
import json
import math

import numpy as np

from preact.core.models import Action, EvidenceKind, Observation, Prediction, State
from preact.core.store import Artifacts, Store
from preact.visual.contracts import RenderRequest, Settings, project, translate
from preact.visual.renderer import BlenderRenderer


def prediction_frames(state, action, prediction, intermediate_frames=3):
    state = State.model_validate(state.model_dump())
    action = Action.model_validate(action.model_dump())
    prediction = Prediction.model_validate(prediction.model_dump())
    if (
        state.domain != "physical"
        or action.state_id != state.id
        or prediction.state_id != state.id
        or prediction.action_ids != [action.id]
        or prediction.engine_id != "mujoco"
        or prediction.evidence != EvidenceKind.SIMULATION
        or prediction.horizon != 1
        or prediction.successor is None
        or prediction.successor.kind != "hypothetical"
        or prediction.successor.parent_id != state.id
    ):
        raise ValueError("Unaligned immediate MuJoCo Prediction")
    if not 0 <= intermediate_frames <= 20:
        raise ValueError("Too many intermediate frames")
    trajectory = prediction.raw.get("trajectory", [])
    if trajectory != prediction.successor.payload.get("trajectory", []):
        raise ValueError("Trajectory provenance mismatch")
    if len(trajectory) > 10000:
        raise ValueError("Unbounded trajectory")
    frames = [translate(state)]
    indices = sorted(
        set(
            np.linspace(
                0, len(trajectory) - 1, min(intermediate_frames, len(trajectory)), dtype=int
            )
        )
    )
    for index in indices:
        sample = State.create(
            "physical",
            {**state.payload, "object": trajectory[index]},
            "mujoco-position-trace",
            kind="hypothetical",
            parent_id=state.id,
        )
        frames.append(translate(sample, phase="trajectory", trajectory_index=int(index)))
    frames.append(translate(prediction.successor, phase="predicted"))
    return tuple(frames)


class VisualAdapter:
    def __init__(self, renderer: BlenderRenderer, settings=None, intermediate_frames=3):
        self.renderer = renderer
        self.artifacts = renderer.artifacts
        self.settings = settings or Settings()
        self.intermediate_frames = intermediate_frames

    async def current(self, state, *, receipt=None):
        if state.kind != "observed":
            raise ValueError("Current render requires an observation")
        return await self.renderer.render(
            RenderRequest(
                settings=self.settings,
                frames=(translate(state, phase="observed_result" if receipt else "current"),),
            ),
            {"state": self.artifacts.json(state.model_dump()), "receipt": receipt},
        )

    async def prediction(self, state, action, source):
        frames = prediction_frames(state, action, source, self.intermediate_frames)
        provenance = {
            "state": self.artifacts.json(state.model_dump()),
            "action": self.artifacts.json(action.model_dump()),
            "action_fingerprint": action.fingerprint,
            "mujoco_prediction": self.artifacts.json(source.model_dump()),
            "mujoco_prediction_id": source.id,
        }
        digest = await self.renderer.render(
            RenderRequest(settings=self.settings, frames=frames), provenance
        )
        return Prediction(
            engine_id="blender-visual",
            engine_version="1",
            family="mujoco-visualization",
            state_id=state.id,
            action_ids=[action.id],
            evidence=EvidenceKind.VISUAL,
            artifacts={"manifest": digest},
            raw=provenance,
            assumptions=[
                "Synthetic representation of MuJoCo; no independent physics evidence",
                "Hypothetical future; not actual observation or safety authority",
            ],
        )


def manifest(artifacts, digest):
    return json.loads(artifacts.read(digest))


def dense(artifacts, frame, name):
    return np.load(io.BytesIO(artifacts.read(frame["artifacts"][name])), allow_pickle=False)


def comparison(
    artifacts: Artifacts,
    store: Store,
    run_id,
    visual: Prediction,
    observation: Observation,
    actual_manifest,
    *,
    visual_artifact=None,
):
    if visual_artifact is not None and manifest(artifacts, visual_artifact) != visual.model_dump():
        raise ValueError("Visual source artifact differs from the saved prediction")
    predicted = manifest(artifacts, visual.artifacts["manifest"])
    source = Prediction.model_validate(
        manifest(artifacts, predicted["provenance"]["mujoco_prediction"])
    )
    action = Action.model_validate(manifest(artifacts, predicted["provenance"]["action"]))
    execution = store.execution_record(observation.receipt)
    if (
        execution["status"] != "complete"
        or execution["run_id"] != run_id
        or execution["state_id"] != source.state_id
        or execution["action_hash"] != action.fingerprint
        or execution["receipt"] != observation.model_dump()
        or visual.action_ids != [action.id]
    ):
        raise ValueError("Outcome must match a complete Runtime receipt for this candidate")
    actual = manifest(artifacts, actual_manifest)
    if actual["provenance"].get("receipt") != observation.receipt:
        raise ValueError("Actual rendering receipt mismatch")
    actual_state = State.model_validate(manifest(artifacts, actual["provenance"]["state"]))
    if actual_state != observation.state or actual_state.kind != "observed":
        raise ValueError("Actual rendering is not the receipt observation")
    settings = Settings.model_validate(predicted["request"]["settings"])
    if actual["request"]["settings"] != predicted["request"]["settings"]:
        raise ValueError("Comparison requires the same camera/render conditions")
    before, after = source.successor.payload["object"], observation.state.payload["object"]
    predicted_frame, actual_frame = predicted["frames"][-1], actual["frames"][0]
    pd, ad = dense(artifacts, predicted_frame, "depth"), dense(artifacts, actual_frame, "depth")
    ps, ass = (
        dense(artifacts, predicted_frame, "segmentation"),
        dense(artifacts, actual_frame, "segmentation"),
    )
    pp, ap = project(before, settings), project(after, settings)
    return {
        "version": "1",
        "run_id": run_id,
        "receipt": observation.receipt,
        "execution": execution,
        "observation": artifacts.json(observation.model_dump()),
        "visual_prediction": visual_artifact or artifacts.json(visual.model_dump()),
        "predicted_manifest": visual.artifacts["manifest"],
        "actual_manifest": actual_manifest,
        "action_fingerprint": action.fingerprint,
        "predicted_position_m": before,
        "observed_position_m": after,
        "position_error_m": math.dist(before, after),
        "predicted_projection": pp,
        "observed_projection": ap,
        "projection_error_px": math.dist(pp["uv"], ap["uv"]),
        "depth_mae_m": float(np.mean(np.abs(pd - ad))),
        "segmentation_changed_pixels": int(np.count_nonzero(ps != ass)),
        "rgb_equal": predicted_frame["artifacts"]["rgb"] == actual_frame["artifacts"]["rgb"],
        "interpretation": "Both images synthetic. Same deterministic MuJoCo model for forecast/execution; real-world model error unknown. Geometry error measured separately by visual auditor.",
    }
