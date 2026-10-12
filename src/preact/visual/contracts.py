"""Bounded structured input: metres, right-handed XYZ, Z up, no executable scene input."""

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from preact.core.models import State
from preact.domains.physical import obstacles


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Camera(Strict):
    position: tuple[float, float, float] = (1.15, -1.6, 1.25)
    look_at: tuple[float, float, float] = (0, 0, 0.10)
    focal_mm: float = Field(default=42, ge=10, le=150)
    sensor_mm: float = Field(default=36, ge=10, le=70)

    @model_validator(mode="after")
    def bounded(self):
        if any(abs(x) > 10 for x in (*self.position, *self.look_at)):
            raise ValueError("Camera exceeds scene bounds")
        forward = np.array(self.look_at) - self.position
        if np.linalg.norm(forward) < 0.01 or np.linalg.norm(np.cross(forward, [0, 0, 1])) < 0.01:
            raise ValueError("Degenerate camera")
        return self

    def matrices(self, width, height):
        forward = np.array(self.look_at) - self.position
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, [0, 0, 1])
        right /= np.linalg.norm(right)
        down = np.cross(forward, right)
        rotation = np.array([right, down, forward])
        extrinsic = np.eye(4)
        extrinsic[:3, :3] = rotation
        extrinsic[:3, 3] = -rotation @ self.position
        focal = width * self.focal_mm / self.sensor_mm
        intrinsic = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]])
        return intrinsic, extrinsic


class Settings(Strict):
    width: int = Field(default=192, ge=32, le=512)
    height: int = Field(default=144, ge=32, le=512)
    samples: int = Field(default=8, ge=1, le=64)
    seed: int = Field(default=0, ge=0, le=2147483647)
    threads: int = Field(default=2, ge=1, le=8)
    camera: Camera = Field(default_factory=Camera)
    engine: Literal["CYCLES"] = "CYCLES"
    device: Literal["CPU"] = "CPU"


class Shape(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,48}$")
    object_id: int = Field(ge=1, le=100)
    geometry: Literal["box", "sphere"] = "box"
    center: tuple[float, float, float]
    half: tuple[float, float, float]
    color: tuple[float, float, float]
    approximation: str = Field(default="", max_length=256)

    @model_validator(mode="after")
    def bounds(self):
        if any(abs(x) > 4 for x in self.center) or any(not 0 < x <= 4 for x in self.half):
            raise ValueError("Geometry exceeds bounds")
        if any(not 0 <= x <= 1 for x in self.color):
            raise ValueError("Invalid color")
        return self


class SceneFrame(Strict):
    state_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["observed", "hypothetical"]
    phase: Literal["current", "trajectory", "predicted", "observed_result"]
    trajectory_index: int | None = Field(default=None, ge=0, le=10000)
    objects: tuple[Shape, ...] = Field(min_length=4, max_length=36)

    @model_validator(mode="after")
    def identities(self):
        if len({s.name for s in self.objects}) != len(self.objects) or len(
            {s.object_id for s in self.objects}
        ) != len(self.objects):
            raise ValueError("Duplicate object identity")
        return self


class RenderRequest(Strict):
    version: Literal["1"] = "1"
    settings: Settings = Field(default_factory=Settings)
    frames: tuple[SceneFrame, ...] = Field(min_length=1, max_length=24)


def translate(state: State, *, phase="current", trajectory_index=None):
    # Revalidate content even if a caller has mutated the nested State payload.
    state = State.model_validate(state.model_dump())
    if state.domain != "physical":
        raise ValueError("Only physical State is supported")
    if (phase in {"predicted", "trajectory"}) != (state.kind == "hypothetical"):
        raise ValueError("Frame phase must preserve observation/hypothesis distinction")
    p = state.payload
    half = p.get("cube_half", 0.025)
    shapes = [
        Shape(
            name="floor",
            object_id=1,
            center=(0, 0, -0.05),
            half=(2, 2, 0.05),
            color=(0.23, 0.26, 0.30),
            approximation="Finite visual slab; MuJoCo floor is an infinite plane at z=0",
        ),
        Shape(
            name="cube",
            object_id=2,
            center=p["object"],
            half=(half, half, half),
            color=(0.06, 0.42, 0.9),
            approximation="State retains position, not free-joint rotation; axis-aligned glyph",
        ),
        Shape(
            name="target",
            object_id=3,
            center=p["target"],
            half=(0.04, 0.04, 0.002),
            color=(0.05, 0.8, 0.22),
            approximation="Goal marker at exact target coordinate, not a MuJoCo collision geom",
        ),
        Shape(
            name="gripper",
            object_id=4,
            geometry="sphere",
            center=p["object"],
            half=(0.018, 0.018, 0.018),
            color=(0.8, 0.8, 0.84),
            approximation="Position proxy; mocap trajectory is not retained in State; no offset",
        ),
    ]
    for index, box in enumerate(obstacles(p)):
        shapes.append(
            Shape(
                name=f"obstacle_{index}",
                object_id=5 + index,
                center=box["center"],
                half=box["half"],
                color=(0.85, 0.12, 0.05),
            )
        )
    return SceneFrame(
        state_id=state.id,
        kind=state.kind,
        phase=phase,
        trajectory_index=trajectory_index,
        objects=tuple(shapes),
    )


def project(position, settings):
    intrinsic, extrinsic = settings.camera.matrices(settings.width, settings.height)
    camera = extrinsic @ np.array([*position, 1])
    pixel = intrinsic @ camera[:3]
    return {"uv": (pixel[:2] / pixel[2]).tolist(), "axial_depth_m": float(camera[2])}
