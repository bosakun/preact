"""One isolated Isaac Sim 5.1 Franka rollout. Run with /isaac-sim/python.sh.

GPU execution has not been validated on the local macOS development machine.
No kinematic teleport or surrogate simulation substitutes for measured contacts.
"""

import argparse
import json
import shutil
from pathlib import Path

if __package__:
    from .isaac_contract import (
        backend_digest,
        bilateral_grasp,
        platform_evidence,
        scene_spec,
        sdk_build,
        waypoint_ticks,
    )
else:
    from isaac_contract import (
        backend_digest,
        bilateral_grasp,
        platform_evidence,
        scene_spec,
        sdk_build,
        waypoint_ticks,
    )


def step(request, artifact_root, authority=False):
    from preact.engines.sdk_bridge import sdk_import_evidence

    bridge = sdk_import_evidence()
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("Isaac camera encoding requires FFmpeg on the SDK worker PATH")
    # Reject omitted/invalid geometry and unavailable provenance before GPU startup.
    scene_spec(request["state"]["payload"])
    platform = platform_evidence()
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    try:
        import numpy as np
        from isaacsim.core.api import World
        from isaacsim.core.api.materials import PhysicsMaterial
        from isaacsim.core.api.objects import DynamicCuboid, FixedCuboid
        from isaacsim.core.utils.rotations import euler_angles_to_quat
        from isaacsim.core.version import get_version
        from isaacsim.robot.manipulators.examples.franka import Franka
        from isaacsim.robot_motion.motion_generation import (
            ArticulationMotionPolicy,
            RmpFlow,
            interface_config_loader,
        )
        from isaacsim.sensors.camera import Camera
        from omni.physx import get_physx_simulation_interface
        from omni.physx.bindings._physx import ContactEventType
        from pxr import PhysicsSchemaTools, PhysxSchema, UsdPhysics

        from preact.core.models import Action, State
        from preact.domains.physical import robust_clearance
        from preact.engines.storage import artifact_store

        build = sdk_build(get_version())
        bridge["runtime"] = {**platform, "sdk_build": build}
        p = dict(request["state"]["payload"])
        action = Action.model_validate(request["actions"][0]) if request.get("actions") else None
        if authority and action and p.get("sdk_runtime") != bridge["runtime"]:
            raise RuntimeError("Isaac authority runtime changed; re-observation/reset required")
        world = World(stage_units_in_meters=1.0, physics_dt=1 / 120, rendering_dt=1 / 20)
        world.scene.add_default_ground_plane()
        perturbation = request.get("perturbation")
        rng = np.random.default_rng(
            perturbation["seed"] if perturbation else request.get("seed", 0)
        )
        if perturbation:
            radius = p["pose_error"] / np.sqrt(3)
            for key in ["object", "obstacle"]:
                p[key] = (np.asarray(p[key]) + rng.uniform(-radius, radius, 3)).tolist()
            p["extra_obstacles"] = [
                {
                    **box,
                    "center": (
                        np.asarray(box["center"]) + rng.uniform(-radius, radius, 3)
                    ).tolist(),
                }
                for box in p.get("extra_obstacles", [])
            ]
        scene = scene_spec(p)
        varied = authority or bool(perturbation)
        mass = float(rng.uniform(0.08, 0.16)) if varied else 0.1
        friction = float(rng.uniform(0.35, 0.65)) if varied else 0.5
        cube = world.scene.add(
            DynamicCuboid(
                prim_path="/World/Cube",
                name="cube",
                position=np.asarray(p["object"]),
                scale=np.full(3, 2 * scene["cube_half"]),
                mass=mass,
                color=np.array([0.2, 0.85, 0.2]),
            )
        )
        cube.apply_physics_material(
            PhysicsMaterial(
                prim_path="/World/CubeMaterial",
                dynamic_friction=friction,
                static_friction=friction + 0.1,
                restitution=0.05,
            )
        )
        for index, box in enumerate(scene["obstacles"]):
            world.scene.add(
                FixedCuboid(
                    prim_path=box["path"],
                    name=f"obstacle_{index}",
                    position=np.asarray(box["center"]),
                    scale=2 * np.asarray(box["half"]),
                    color=np.array([0.8, 0.15, 0.1]),
                )
            )
        robot = world.scene.add(
            Franka(prim_path="/World/Franka", name="franka", position=np.array([-0.6, -0.4, 0]))
        )
        camera = Camera(
            prim_path="/World/Camera",
            position=np.array([0, 0, 1.2]),
            orientation=euler_angles_to_quat(np.array([0, 90, 0]), degrees=True),
            resolution=(640, 480),
        )
        world.reset()
        world.set_block_on_render(True)
        camera.initialize()
        if p.get("joints"):
            robot.set_joint_positions(np.asarray(p["joints"]))
        world.stage.GetPrimAtPath("/World/Cube")
        for prim in world.stage.Traverse():
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                PhysxSchema.PhysxContactReportAPI.Apply(prim).CreateThresholdAttr().Set(0.0)
        contacts, active_contacts = [], {}
        obstacle_paths = {box["path"] for box in scene["obstacles"]}
        finger_paths = {
            str(prim.GetPath())
            for prim in world.stage.Traverse()
            if prim.GetName() in ("panda_leftfinger", "panda_rightfinger")
            and prim.HasAPI(UsdPhysics.RigidBodyAPI)
        }

        def on_contacts(headers, data):
            for header in headers:
                pair = [
                    str(PhysicsSchemaTools.intToSdfPath(header.actor0)),
                    str(PhysicsSchemaTools.intToSdfPath(header.actor1)),
                ]
                colliders = tuple(
                    sorted(
                        [
                            str(PhysicsSchemaTools.intToSdfPath(header.collider0)),
                            str(PhysicsSchemaTools.intToSdfPath(header.collider1)),
                        ]
                    )
                )
                if header.type == ContactEventType.CONTACT_LOST:
                    active_contacts.pop(colliders, None)
                else:
                    active_contacts[colliders] = pair
                if obstacle_paths.intersection(pair):
                    contacts.append(pair)

        subscription = get_physx_simulation_interface().subscribe_contact_report_events(on_contacts)
        config = interface_config_loader.load_supported_motion_policy_config("Franka", "RMPflow")
        motion = RmpFlow(**config)
        motion.set_robot_base_pose(*robot.get_world_pose())
        controller = ArticulationMotionPolicy(robot, motion, 1 / 120)
        quaternion = np.array([0.0, 1.0, 0.0, 0.0])
        trajectory, frames, phases = [], [], []

        def tick(target=None, count=360, interpolate_from=None, phase="settle"):
            phases.append({"name": phase, "physics_ticks": count, "duration_seconds": count / 120})
            for index in range(count):
                if target is not None:
                    commanded = np.asarray(target)
                    if interpolate_from is not None:
                        start = np.asarray(interpolate_from)
                        commanded = start + (commanded - start) * (index + 1) / count
                    motion.set_end_effector_target(commanded, quaternion)
                    robot.apply_action(controller.get_next_articulation_action(1 / 120))
                world.step(render=index % 6 == 0)
                if index % 6 == 0:
                    trajectory.append(cube.get_world_pose()[0].tolist())
                    frame = camera.get_rgba()
                    if frame is not None and frame.ndim == 3 and frame.shape[0] > 0:
                        frames.append(frame[:, :, :3].copy())

        # Grasp is physical finger actuation. Never attach/teleport the cube to imply success.
        if action and not p.get("held") and action.kind != "release":
            robot.gripper.open()
            tick(np.asarray(p["object"]) + np.array([0, 0, 0.16]), phase="grasp_approach")
            tick(np.asarray(p["object"]) + np.array([0, 0, 0.10]), phase="grasp_descent")
            robot.gripper.close()
            tick(count=120, phase="grasp_settle")
        elif p.get("held"):
            robot.gripper.close()
        if action:
            counts = waypoint_ticks(action.duration, 120, len(action.payload["waypoints"]))
            previous_command = robot.end_effector.get_world_pose()[0]
            for waypoint, count in zip(action.payload["waypoints"], counts, strict=True):
                target = np.asarray(waypoint) + np.array([0, 0, 0.10])
                tick(target, count, previous_command, phase="transport")
                previous_command = target
            if action.kind == "release":
                robot.gripper.open()
                tick(count=120, phase="release_settle")
        else:
            tick(count=120)
        # Flush the documented one-frame render lag without advancing physics.
        # Capture the endpoint, not the beginning of the previous motion, for Cosmos.
        for _ in range(2):
            world.render()
        frame = camera.get_rgba()
        if frame is None or frame.ndim != 3 or frame.shape[0] == 0:
            raise RuntimeError("Isaac camera produced no endpoint observation")
        frames.append(frame[:, :, :3].copy())
        position = cube.get_world_pose()[0].tolist()
        end_effector_position, end_effector_quaternion = robot.end_effector.get_world_pose()
        delivered = bool(
            action
            and action.kind == "release"
            and np.linalg.norm(np.asarray(position[:2]) - np.asarray(p["target"][:2])) < 0.07
        )
        clearance = not contacts
        if action:
            previous = p["object"]
            for waypoint in action.payload["waypoints"]:
                clearance &= all(
                    robust_clearance(
                        previous,
                        waypoint,
                        box["center"],
                        box["half"],
                        max(0.05, 1.8 * scene["cube_half"]) + p["pose_error"],
                    )
                    for box in scene["obstacles"]
                )
                previous = waypoint
        checks = {
            "clearance": bool(clearance),
            "workspace": all(abs(v) <= 0.8 for v in position),
            "controller": bool(
                not action
                or np.linalg.norm(
                    np.asarray(position) - np.asarray(action.payload["waypoints"][-1])
                )
                < 0.08
            ),
        }
        grasp_contact = bilateral_grasp(active_contacts, finger_paths)
        payload = {
            **p,
            "object": position,
            "joints": robot.get_joint_positions().tolist(),
            "held": bool(
                action and action.kind != "release" and checks["controller"] and grasp_contact
            ),
            "grasp_evidence": {
                "method": "active-bilateral-finger-contact/v1",
                "bilateral_contact": grasp_contact,
                "scope": "Simulated contact proxy; grasp stability requires GPU conformance",
            },
            "delivered": delivered,
            "trajectory": trajectory,
            "contacts": len(contacts),
            "stage": action.payload["stage"] if action else "initial",
            "tool_frame": "fixed-franka-downward-v1",
            "tool_position": end_effector_position.tolist(),
            "tool_quaternion_wxyz": end_effector_quaternion.tolist(),
            "simulator": "isaac-sim-" + build["core"],
            "sdk_runtime": bridge["runtime"],
            "action_timing": {
                "rate_hz": 120,
                "phases": phases,
                "transport_semantics": "linear-fixed-downward-v2",
            },
        }
        artifacts = artifact_store()
        if frames:
            import mediapy

            path = Path(artifact_root) / "camera.mp4"
            mediapy.write_video(str(path), frames, fps=20)
            payload["camera_video"] = artifacts.put(path.read_bytes(), "video/mp4")
            payload["camera_observation"] = json.loads(
                json.dumps(
                    {
                        "video_digest": payload["camera_video"],
                        "frame": "last",
                        "object": payload["object"],
                        "joints": payload["joints"],
                        "held": payload["held"],
                        "grasp_evidence": payload["grasp_evidence"],
                        "tool_frame": payload["tool_frame"],
                        "tool_position": payload["tool_position"],
                        "tool_quaternion_wxyz": payload["tool_quaternion_wxyz"],
                        "sdk_runtime": payload["sdk_runtime"],
                        "scope": "Endpoint render flushed at fixed physics state; GPU synchronization requires conformance",
                    }
                )
            )
        else:
            raise RuntimeError("Isaac camera produced no observation frames")
        kind = "observed" if authority else "hypothetical"
        state = State.create(
            "physical",
            payload,
            "isaac-authority" if authority else "isaac-reconstruction",
            kind=kind,
            parent_id=request["state"]["id"] if not authority else None,
            uncertainty=0 if authority else 0.1,
        )
        # Retain subscription until after simulation stepping and observations.
        del subscription
        return {
            "worker_backend_sha256": backend_digest(),
            "sdk_bridge": bridge,
            "state": state.model_dump(),
            "checks": checks,
            "metrics": {
                "contacts": float(len(contacts)),
                "goal_progress": 1.0
                if delivered
                else 0.8
                if np.linalg.norm(np.asarray(position[:2]) - np.asarray(p["target"][:2])) < 0.07
                else 0.4
                if position[2] > 0.25
                else 0.2,
            },
            "scope": "Franka simulated grasp/trajectory/contact measurements; not hardware safety",
            "parameters": {
                "mass_kg": mass,
                "dynamic_friction": friction,
                "perturbation": perturbation,
            },
        }
    finally:
        app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    parser.add_argument("output")
    parser.add_argument("--authority", action="store_true")
    args = parser.parse_args()
    result = step(
        json.loads(Path(args.request).read_text()), str(Path(args.output).parent), args.authority
    )
    Path(args.output).write_text(json.dumps(result))
