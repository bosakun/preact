"""Scene-conditioned motion proposals; authority uses actual MuJoCo dynamics."""

import asyncio
import copy
import math

from preact.core.models import Action, State, identity
from preact.datasets.scenes import Scene
from preact.domains.physical import PhysicalWorld, obstacles, robust_clearance, rollout


class SceneWorld(PhysicalWorld):
    def __init__(self, spec: Scene, seed=0):
        super().__init__(seed)
        self.spec = spec
        self.task = self.task.model_copy(
            update={
                "id": spec.name,
                "title": spec.name.replace("-", " "),
                "max_steps": 5,
                "metric_scales": {
                    "contacts": 1,
                    "goal_progress": 1,
                    "tracking_error": 0.08,
                    "target_distance": 1,
                },
            }
        )
        self.payload.update(copy.deepcopy(spec.configuration))
        # Repeated seeds perturb the same scene; they never manufacture new tasks.
        self.payload["object"][1] += (seed - 2) * 0.003
        self.payload["task_title"] = self.task.title

    async def observe(self):
        return State.create(
            "physical",
            copy.deepcopy(self.payload),
            "mujoco-scene:" + self.spec.family,
            uncertainty=min(1, self.payload["pose_error"] / 0.2),
        )

    async def propose(self, state, width):
        p = state.payload
        x, y, z = p["object"]
        tx, ty, tz = p["target"]
        ground = p["cube_half"] + 0.02
        # Exclude overhead ceilings from the target lift height; they remain
        # explicit obstacles for both approximate reasoning and actual verification.
        height = min(
            0.7,
            max(
                box["center"][2] + box["half"][2] + 0.10
                for box in obstacles(p)
                if box["half"][0] < 0.5
            ),
        )
        stage = p["stage"]
        if math.dist([x, y], [tx, ty]) < 0.07:
            options = [("Place and release", "release", [[tx, ty, ground]], "placed")]
        elif stage == "raised":
            options = [
                ("Transfer above obstacles", "move", [[tx, ty, height]], "at_target"),
                ("Descend during transfer", "move", [[tx, ty, ground]], "at_target"),
            ]
        elif stage == "side":
            options = [
                ("Cross along side waypoint", "move", [[tx, y, ground]], "crossed"),
                ("Approach target diagonally", "move", [[tx, ty, ground]], "at_target"),
            ]
        elif stage == "crossed":
            options = [("Approach target", "move", [[tx, ty, ground]], "at_target")]
        else:
            extent = max(
                abs(box["center"][1]) + box["half"][1]
                for box in obstacles(p)
                if box["half"][0] < 0.5
            )
            side = min(0.7, extent + p["cube_half"] + 0.04)
            options = [
                ("Move directly", "move", [[tx, ty, ground]], "at_target"),
                ("Lift before transfer", "move", [[x, y, height]], "raised"),
                ("Use positive side waypoint", "move", [[x, side, ground]], "side"),
                ("Use negative side waypoint", "move", [[x, -side, ground]], "side"),
            ]

        def rank(option):
            points = option[2]
            start = p["object"]
            length = 0
            blocked = False
            for point in points:
                length += math.dist(start, point)
                # A cheap nominal planner uses the visible object radius. It does
                # not execute simulation or use protected observed outcomes.
                blocked |= not all(
                    robust_clearance(start, point, box["center"], box["half"], p["cube_half"])
                    for box in obstacles(p)
                )
                start = point
            return int(blocked), length, identity({"option": option, "seed": self.task.seed})

        return [
            Action(
                name=name,
                kind=kind,
                state_id=state.id,
                payload={"waypoints": points, "stage": stage},
                duration=2,
                rationale="Shared cheap nominal-geometry proposer; executable verification measures protected clearance and tracking",
            )
            for name, kind, points, stage in sorted(options, key=rank)[:width]
        ]

    async def verify_future(self, state, action, seed):
        self.validate(state, action)
        payload, checks, metrics = await asyncio.to_thread(rollout, state.payload, action, seed)
        return (
            payload,
            checks,
            metrics,
            {
                "trajectory": payload["trajectory"],
                "scope": "Actual Cartesian MuJoCo with observed mass/friction/controller parameters; not Isaac/hardware",
                "scene_hash": self.spec.fingerprint,
            },
        )
