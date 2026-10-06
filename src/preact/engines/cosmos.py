"""Action-conditioned Cosmos CLI adapter for the separate Linux GPU environment.

No generated image is promoted into collision or hardware safety evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import workers.isaac_contract as physical_contract
from preact.core.interfaces import EngineFailure
from preact.core.io import durable_io
from preact.core.models import Capabilities, Estimate, EvidenceKind, Prediction
from preact.engines.process import reap_cancelled
from workers.isaac_contract import waypoint_ticks


def adapter_digest():
    digest = hashlib.sha256()
    for path in (Path(__file__), Path(physical_contract.__file__)):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verify_checkout(repo, revision):
    try:
        actual = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
        clean = (
            subprocess.run(
                ["git", "-C", str(repo), "diff", "--quiet", "HEAD", "--"],
                timeout=5,
                capture_output=True,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        raise EngineFailure("Cosmos checkout identity cannot be measured") from None
    if actual != revision or not clean:
        raise EngineFailure("Cosmos checkout differs from the pinned clean revision")


def conditioning(payload, action):
    """Only measured, already-held fixed-orientation transport has a defined transform."""
    observation = payload.get("camera_observation")
    fields = (
        "object",
        "joints",
        "held",
        "grasp_evidence",
        "tool_frame",
        "tool_position",
        "tool_quaternion_wxyz",
        "sdk_runtime",
    )
    if (
        payload.get("tool_frame") != "fixed-franka-downward-v1"
        or payload.get("held") is not True
        or payload.get("grasp_evidence", {}).get("method") != "active-bilateral-finger-contact/v1"
        or payload.get("grasp_evidence", {}).get("bilateral_contact") is not True
        or action.kind == "release"
        or not payload.get("camera_video")
        or not isinstance(observation, dict)
        or observation.get("video_digest") != payload["camera_video"]
        or observation.get("frame") != "last"
        or any(key not in payload or observation.get(key) != payload[key] for key in fields)
    ):
        raise EngineFailure(
            "Cosmos requires an aligned endpoint camera and held transport; grasp/release unsupported"
        )
    from scipy.spatial.transform import Rotation

    quaternion = np.asarray(payload["tool_quaternion_wxyz"], dtype=float)
    if (
        quaternion.shape != (4,)
        or not np.isfinite(quaternion).all()
        or not np.isclose(np.linalg.norm(quaternion), 1, atol=1e-3)
    ):
        raise EngineFailure("Invalid measured tool orientation")
    rotation = Rotation.from_quat(quaternion, scalar_first=True)
    downward = Rotation.from_quat([0, 1, 0, 0], scalar_first=True)
    if (downward.inv() * rotation).magnitude() > 0.05:
        raise EngineFailure("Tool orientation is outside the fixed downward transform")
    return payload["tool_position"], quaternion


def pad_chunks(actions, size=12):
    """Official inference pads with zeros (opening fingers); instead append explicit closed holds."""
    missing = (-len(actions)) % size
    hold = np.zeros((missing, 7), dtype=np.float32)
    hold[:, 6] = actions[-1, 6]
    return np.concatenate([actions, hold]), missing


def action_sequence(
    start,
    waypoints,
    duration,
    fps=20,
    scale=20.0,
    release=False,
    tool_quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
):
    """World-frame fixed-orientation Cartesian deltas → checkpoint-scaled local deltas.

    Input: meters, seconds. Output: [dx,dy,dz,dr,dp,dyaw,closed] at fps.
    Identity end-effector orientation is explicit; rotated tool frames require a
    different versioned transform and are rejected by the calling worker.
    """
    if duration <= 0 or fps < 1 or scale <= 0:
        raise ValueError("Invalid action timing/scale")
    from scipy.spatial.transform import Rotation

    rotation = Rotation.from_quat(tool_quaternion_wxyz, scalar_first=True).as_matrix()
    previous = np.asarray(start, dtype=float)
    chunks = []
    counts = waypoint_ticks(duration, fps, len(waypoints))
    for waypoint, frames in zip(waypoints, counts, strict=True):
        target = np.asarray(waypoint, dtype=float)
        delta = rotation.T @ (target - previous) / frames * scale
        chunks.extend(
            [np.r_[delta, [0.0, 0.0, 0.0, 0.0 if release else 1.0]].tolist() for _ in range(frames)]
        )
        previous = target
    result = np.asarray(chunks, dtype=np.float32)
    if result.ndim != 2 or result.shape[1] != 7 or not np.isfinite(result).all():
        raise ValueError("Invalid conditioned actions")
    return result


def load_actions():
    # Official custom loader signature; GPU-only mediapy imported lazily.
    def load(annotation, video_path, args):
        import mediapy

        video = mediapy.read_video(video_path)
        actions = np.asarray(annotation["actions"], dtype=np.float32)
        return {
            "actions": actions,
            "initial_frame": video[-1],
            "video_array": video,
            "video_path": video_path,
        }

    return load


class Cosmos:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.repo = Path(os.environ["COSMOS_REPO_PATH"]).resolve()
        self.revision = os.environ["COSMOS_REVISION"]
        self.adapter_hash = adapter_digest()
        self.version = f"{self.revision}+preact.{self.adapter_hash}"
        verify_checkout(self.repo, self.revision)
        self.capabilities = Capabilities(
            engine_id="cosmos-action-conditioned",
            version=self.version,
            family="cosmos-predict",
            domains=["physical"],
            evidence=EvidenceKind.VISUAL,
            tier=1,
            applicability="Already-held fixed downward transport from aligned endpoint camera; GPU transfer unverified",
        )

    async def predict(self, request):
        action = request.actions[0]
        p = request.state.payload
        conditioning(p, action)
        if adapter_digest() != self.adapter_hash:
            raise EngineFailure("Cosmos adapter source changed after registration")
        await durable_io(verify_checkout, self.repo, self.revision)
        with tempfile.TemporaryDirectory(prefix="preact-cosmos-") as directory:
            root = Path(directory)
            # Artifact storage may be remote. Drain preparation/persistence off
            # the loop before deleting the temporary directory on cancellation.
            timing = await durable_io(self._prepare, root, request)
            python = os.getenv("COSMOS_PYTHON", "python")
            process = await asyncio.create_subprocess_exec(
                python,
                "examples/action_conditioned.py",
                "-i",
                str(root / "params.json"),
                "-o",
                str(root / "output"),
                "--seed",
                str(request.seed),
                cwd=self.repo,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), request.deadline_seconds
                )
            except (TimeoutError, asyncio.CancelledError) as error:
                await reap_cancelled(process, error)
            video_digest, log_digest = await durable_io(
                self._persist, root, process.returncode, stdout + stderr
            )
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version=self.version,
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[action.id],
            evidence=EvidenceKind.VISUAL,
            success=Estimate(),
            risk=Estimate(),
            artifacts={"generated_video": video_digest, "worker_log": log_digest},
            assumptions=[
                "Generated visual evidence, not collision geometry or hardware certification",
                "Native model provides no calibrated task-success probability",
                "Camera/robot applicability and visual predicate extractor require M8 validation",
            ],
            raw={
                "expected_variant": "robot/action-cond",
                "checkpoint_identity": "unmeasured; requires real model conformance",
                "revision": self.revision,
                "adapter_sha256": self.adapter_hash,
                "action_transform": "linear-fixed-downward-v2",
                "translation_units": "meters*20",
                "rotation_units": "radians*20",
                "gripper": "1 closed, 0 open",
                "fps": 20,
                "conditioning_state": request.state.id,
                "conditioned_action": action.fingerprint,
                "timing": timing,
                "requested_seed": request.seed,
                "native_seed_scheme": "official generate_vid2world seed=chunk_index; requested CLI seed does not control chunks",
                "cost_known": False,
            },
        )

    def _prepare(self, root, request):
        action = request.actions[0]
        start, quaternion = conditioning(request.state.payload, action)
        actions = action_sequence(
            start,
            (np.asarray(action.payload["waypoints"]) + [0, 0, 0.10]).tolist(),
            action.duration,
            release=action.kind == "release",
            tool_quaternion_wxyz=quaternion,
        )
        commanded_frames = len(actions)
        actions, padding = pad_chunks(actions)
        (root / "annotations").mkdir()
        (root / "camera.mp4").write_bytes(
            self.artifacts.read(request.state.payload["camera_video"])
        )
        annotation = {"videos": ["camera.mp4"], "actions": actions.tolist()}
        (root / "annotations" / "0.json").write_text(json.dumps(annotation))
        params = {
            "name": "preact",
            "input_root": str(root),
            "input_json_sub_folder": "annotations",
            "save_root": str(root / "output"),
            "chunk_size": 12,
            "camera_id": 0,
            "start": 0,
            "end": 1,
            "save_fps": 20,
            "action_load_fn": "preact.engines.cosmos.load_actions",
        }
        (root / "params.json").write_text(json.dumps(params))
        return {
            "commanded_frames": commanded_frames,
            "trailing_closed_hold_frames": padding,
            "commanded_seconds": commanded_frames / 20,
            "generated_horizon_seconds": len(actions) / 20,
            "scope": "Rounded 20Hz commanded transport plus explicit stationary chunk padding; not measured motion",
        }

    def _persist(self, root, returncode, log):
        videos = sorted((root / "output").glob("*.mp4"))
        if returncode or not videos:
            raise EngineFailure("Cosmos generation failed or produced no rollout")
        video_digest = self.artifacts.put(videos[0].read_bytes(), "video/mp4")
        # Native console output can contain private paths or provider diagnostics.
        # Retain bounded non-secret process facts instead of publishing raw output.
        log_digest = self.artifacts.put(
            json.dumps(
                {
                    "returncode": returncode,
                    "console_bytes": len(log),
                    "scope": "Raw native console output omitted",
                }
            ).encode(),
            "application/json",
        )
        return video_digest, log_digest
