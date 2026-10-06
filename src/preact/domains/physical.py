from __future__ import annotations

import math

from preact.core.models import Action, Observation, State, Task


def robust_clearance(start, end, center, half, radius=0.055):
    # Exact slab intersection with a conservatively inflated obstacle (swept object bounds).
    lo, hi = 0.0, 1.0
    for axis in range(3):
        delta = end[axis] - start[axis]
        minimum, maximum = center[axis] - half[axis] - radius, center[axis] + half[axis] + radius
        if abs(delta) < 1e-12:
            if not minimum <= start[axis] <= maximum:
                return True
        else:
            a, b = (minimum - start[axis]) / delta, (maximum - start[axis]) / delta
            lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
            if lo > hi:
                return True
    return False


def obstacles(payload):
    return [
        {"center": payload["obstacle"], "half": payload["obstacle_half"]},
        *payload.get("extra_obstacles", []),
    ]


def scene_xml(payload):
    pos = " ".join(str(x) for x in payload["object"])
    boxes = "".join(
        f'<geom name="obstacle_{i}" type="box" pos="{" ".join(map(str, box["center"]))}" size="{" ".join(map(str, box["half"]))}"/>'
        for i, box in enumerate(obstacles(payload))
    )
    cube_half = payload.get("cube_half", 0.025)
    mass = payload.get("mass", 0.1)
    friction = payload.get("friction", 1.0)
    response = payload.get("grasp_response", 0.01)
    return f'''<mujoco model="preact-cartesian-manipulation">
      <option timestep="0.005" gravity="0 0 -9.81"/>
      <worldbody>
        <geom name="floor" type="plane" size="2 2 .1" friction="1 .01 .001"/>
        {boxes}
        <body name="object" pos="{pos}"><freejoint/><geom name="cube" type="box" size="{cube_half} {cube_half} {cube_half}" mass="{mass}" friction="{friction} .01 .001"/></body>
        <body name="gripper" mocap="true" pos="{pos}"><geom type="sphere" size=".018" contype="0" conaffinity="0"/></body>
      </worldbody>
      <equality><weld name="grasp" body1="gripper" body2="object" active="false" solref="{response} 1"/></equality>
    </mujoco>'''


def rollout(payload, action, seed=0):
    import mujoco
    import numpy as np

    model = mujoco.MjModel.from_xml_string(scene_xml(payload))
    data = mujoco.MjData(model)
    data.qpos[:3] = payload["object"]
    data.mocap_pos[0] = payload["object"]
    data.eq_active[0] = bool(payload.get("held", False))
    mujoco.mj_forward(model, data)
    waypoints = action.payload["waypoints"]
    if action.kind != "release":
        data.eq_active[0] = True
    collisions, trajectory = 0, []
    cube = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "cube")
    obstacle_ids = {
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"obstacle_{i}")
        for i in range(len(obstacles(payload)))
    }
    previous = list(payload["object"])
    clearance = True
    for waypoint in waypoints:
        clearance &= all(
            robust_clearance(
                previous,
                waypoint,
                box["center"],
                box["half"],
                max(0.05, 1.8 * payload.get("cube_half", 0.025)) + payload["pose_error"],
            )
            for box in obstacles(payload)
        )
        start = data.mocap_pos[0].copy()
        distance = np.linalg.norm(np.array(waypoint) - start)
        steps = max(80, int(distance / payload.get("actuator_step", 0.002)))
        for step in range(steps):
            data.mocap_pos[0] = start + (np.array(waypoint) - start) * ((step + 1) / steps)
            mujoco.mj_step(model, data)
            for c in data.contact:
                if cube in {int(c.geom1), int(c.geom2)} and bool(
                    {int(c.geom1), int(c.geom2)} & obstacle_ids
                ):
                    collisions += 1
            if step % 12 == 0:
                trajectory.append(data.qpos[:3].tolist())
        previous = waypoint
    if action.kind == "release":
        data.eq_active[0] = False
    for _ in range(100):
        mujoco.mj_step(model, data)
    object_pos = data.qpos[:3].tolist()
    target = payload["target"]
    delivered = action.kind == "release" and math.dist(object_pos[:2], target[:2]) < 0.07
    successor = {
        **payload,
        "object": object_pos,
        "held": action.kind != "release",
        "delivered": delivered,
        "trajectory": trajectory,
        "stage": action.payload["stage"],
        "contacts": collisions,
    }
    checks = {
        "clearance": bool(clearance and collisions == 0),
        "workspace": all(abs(v) <= 0.8 for p in waypoints for v in p),
        "controller": math.dist(object_pos, waypoints[-1]) < 0.08
        if action.kind != "release"
        else True,
    }
    return (
        successor,
        checks,
        {
            "contacts": float(collisions),
            "tracking_error": math.dist(object_pos, waypoints[-1]),
            "target_distance": math.dist(object_pos[:2], target[:2]),
            "goal_progress": 1.0
            if delivered
            else 0.8
            if math.dist(object_pos[:2], target[:2]) < 0.07
            else 0.65
            if abs(object_pos[1] - target[1]) > 0.3
            else 0.4
            if object_pos[2] > 0.25
            else 0.2,
        },
    )


class PhysicalWorld:
    # Successful local proposal generation is pure; overrides using external work
    # must preserve complete usage receipts or withdraw this certification.
    proposal_usage_complete = True

    def __init__(self, seed=0):
        self.task = Task(
            id="cube-transfer",
            domain="physical",
            title="Move a cube around an obstacle",
            goal="Place the cube in the target without contact with the keep-out obstacle",
            seed=seed,
            required_checks=["clearance", "workspace", "controller"],
            metric_scales={"contacts": 1, "goal_progress": 1},
            max_steps=5,
        )
        y = ((seed % 5) - 2) * 0.025
        self.payload = {
            "object": [-0.3, y, 0.03],
            "target": [0.3, y, 0.03],
            "obstacle": [0.0, y, 0.1],
            "obstacle_half": [0.07, 0.11, 0.1],
            "pose_error": 0.01,
            "held": False,
            "delivered": False,
            "stage": "initial",
            "trajectory": [],
            "contacts": 0,
        }
        self.receipts = {}

    async def observe(self):
        return State.create("physical", self.payload.copy(), "mujoco-cartesian-lab")

    async def propose(self, state, width):
        p = state.payload
        x, y, z = p["object"]
        tx, ty, _ = p["target"]
        if math.dist([x, y], [tx, ty]) < 0.07:
            options = [("Place and release", "release", [[tx, ty, 0.04]], "placed")]
        elif z > 0.25:
            options = [
                ("Transport above the obstacle", "move", [[tx, ty, 0.32]], "transported"),
                ("Descend too soon", "move", [[tx, ty, 0.06]], "low-transfer"),
            ]
        elif p["stage"] == "detour":
            options = [
                ("Transport along the perimeter", "move", [[tx, 0.35, 0.08]], "perimeter"),
                ("Cut diagonally back to target", "move", [[tx, ty, 0.06]], "diagonal"),
            ]
        elif p["stage"] == "perimeter":
            options = [("Approach target along the edge", "move", [[tx, ty, 0.06]], "transported")]
        else:
            options = [
                ("Take the shortest route", "move", [[tx, ty, 0.06]], "direct"),
                ("Lift before transferring", "move", [[x, y, 0.32]], "lifted"),
                (
                    "Route around the obstacle",
                    "move",
                    [[x, 0.35, 0.08]],
                    "detour",
                ),
            ]
        return [
            Action(
                name=name,
                kind=kind,
                state_id=state.id,
                payload={"waypoints": points, "stage": stage},
                duration=2.0,
                rationale="Choose a trajectory using visual and executable evidence",
            )
            for name, kind, points, stage in options[:width]
        ]

    def validate(self, state, action):
        if action.state_id != state.id or action.kind not in {"move", "release"}:
            raise ValueError("Invalid physical action")
        points = action.payload.get("waypoints", [])
        if not 1 <= len(points) <= 8 or any(
            len(p) != 3
            or not all(
                isinstance(x, (int, float)) and math.isfinite(x) and abs(x) <= 0.8 for x in p
            )
            for p in points
        ):
            raise ValueError("Trajectory exceeds workspace contract")

    def materialize(self, state, action):
        self.validate(state, action)
        p = {
            **state.payload,
            "object": action.payload["waypoints"][-1],
            "held": action.kind != "release",
            "delivered": action.kind == "release",
            "stage": action.payload["stage"],
        }
        return State.create(
            "physical",
            p,
            "kinematic-hypothesis",
            kind="hypothetical",
            parent_id=state.id,
            uncertainty=0.15,
        )

    def complete(self, state):
        return bool(state.payload.get("delivered"))

    async def execute(self, action, receipt):
        import asyncio

        if receipt in self.receipts:
            return self.receipts[receipt]
        state = await self.observe()
        self.validate(state, action)
        payload, checks, metrics = await asyncio.to_thread(
            rollout, state.payload, action, self.task.seed
        )
        self.payload = payload
        result = Observation(
            cost_usd=0,
            state=await self.observe(),
            success=bool(payload["delivered"]),
            unsafe=not checks["clearance"],
            checks={**checks, "action_success": all(checks.values())},
            metrics=metrics,
            vectors={"object_position": payload["object"]},
            receipt=receipt,
        )
        self.receipts[receipt] = result
        return result
