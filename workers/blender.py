"""Fixed Blender entrypoint. Accept JSON only; never execute user-supplied Python."""

import json
import math
import resource
import struct
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


def validate(request):
    if set(request) != {"version", "settings", "frames"} or request["version"] != "1":
        raise ValueError("Unsupported request")
    s = request["settings"]
    if set(s) != {"width", "height", "samples", "seed", "threads", "camera", "engine", "device"}:
        raise ValueError("Invalid settings")
    for key, lo, hi in [
        ("width", 32, 512),
        ("height", 32, 512),
        ("samples", 1, 64),
        ("threads", 1, 8),
        ("seed", 0, 2147483647),
    ]:
        if type(s[key]) is not int or not lo <= s[key] <= hi:
            raise ValueError("Unbounded render setting")
    if s["engine"] != "CYCLES" or s["device"] != "CPU" or not 1 <= len(request["frames"]) <= 24:
        raise ValueError("Invalid render scope")
    c = s["camera"]
    if set(c) != {"position", "look_at", "focal_mm", "sensor_mm"}:
        raise ValueError("Invalid camera")
    for key in ("position", "look_at"):
        if len(c[key]) != 3 or any(not math.isfinite(x) or abs(x) > 10 for x in c[key]):
            raise ValueError("Invalid camera bounds")
    if not 10 <= c["focal_mm"] <= 150 or not 10 <= c["sensor_mm"] <= 70:
        raise ValueError("Invalid lens")
    for frame in request["frames"]:
        if set(frame) != {"state_id", "kind", "phase", "trajectory_index", "objects"}:
            raise ValueError("Invalid frame")
        if not 4 <= len(frame["objects"]) <= 36:
            raise ValueError("Unbounded geometry")
        ids, names = set(), set()
        for shape in frame["objects"]:
            if set(shape) != {
                "name",
                "object_id",
                "geometry",
                "center",
                "half",
                "color",
                "approximation",
            }:
                raise ValueError("Invalid shape")
            if shape["geometry"] not in {"box", "sphere"} or not 1 <= shape["object_id"] <= 100:
                raise ValueError("Invalid shape type")
            for key, lo, hi in [("center", -4, 4), ("half", 0, 4), ("color", 0, 1)]:
                if (
                    len(shape[key]) != 3
                    or any(not math.isfinite(x) or not lo <= x <= hi for x in shape[key])
                    or (key == "half" and min(shape[key]) <= 0)
                ):
                    raise ValueError("Invalid shape bounds")
            if shape["object_id"] in ids or shape["name"] in names:
                raise ValueError("Duplicate geometry")
            ids.add(shape["object_id"])
            names.add(shape["name"])


def main():
    started = time.monotonic()
    path = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Input exceeds limit")
    request = json.loads(path.read_text())
    validate(request)
    # Files are written only in the host-created private job directory.
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (4800, 4800))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    s, c = request["settings"], request["settings"]["camera"]
    results = []
    initialized = time.monotonic()
    for index, frame in enumerate(request["frames"]):
        frame_started = time.monotonic()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = s["samples"]
        scene.cycles.seed = s["seed"]
        scene.cycles.use_denoising = False
        scene.cycles.use_adaptive_sampling = False
        scene.render.threads_mode = "FIXED"
        scene.render.threads = s["threads"]
        scene.render.resolution_x = s["width"]
        scene.render.resolution_y = s["height"]
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGB"
        scene.view_settings.view_transform = "Standard"
        scene.world = bpy.data.worlds.new("world")
        scene.world.use_nodes = True
        scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.18, 1)
        actual_objects = []
        for shape in frame["objects"]:
            if shape["geometry"] == "box":
                bpy.ops.mesh.primitive_cube_add(size=2, location=shape["center"])
            else:
                bpy.ops.mesh.primitive_uv_sphere_add(
                    segments=24, ring_count=12, radius=1, location=shape["center"]
                )
            obj = bpy.context.object
            obj.name = shape["name"]
            obj.scale = shape["half"]
            obj.pass_index = shape["object_id"]
            material = bpy.data.materials.new(shape["name"])
            material.diffuse_color = (*shape["color"], 1)
            material.use_nodes = True
            material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
                *shape["color"],
                1,
            )
            material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.7
            obj.data.materials.append(material)
            actual_objects.append(
                {
                    "name": obj.name,
                    "object_id": obj.pass_index,
                    "center": list(obj.location),
                    "half": list(obj.scale),
                    "geometry": shape["geometry"],
                }
            )
        bpy.ops.object.camera_add(location=c["position"])
        camera = bpy.context.object
        camera.rotation_euler = (
            (Vector(c["look_at"]) - camera.location).to_track_quat("-Z", "Y").to_euler()
        )
        camera.data.lens = c["focal_mm"]
        camera.data.sensor_width = c["sensor_mm"]
        camera.data.sensor_fit = "HORIZONTAL"
        camera.data.clip_start = 0.01
        camera.data.clip_end = 20
        scene.camera = camera
        bpy.ops.object.light_add(type="AREA", location=(0.5, -0.6, 2.5))
        light = bpy.context.object
        light.data.energy = 180
        light.data.size = 3
        light.rotation_euler = (-light.location).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        scene.render.filepath = str(path.parent / f"{index:03d}-rgb.png")
        bpy.ops.render.render(write_still=True)
        # Blender embeds wall-clock dates and Cycles timings even without visible
        # stamps. Remove only ancillary text chunks, retaining RGB pixels verbatim;
        # version, configuration and timing remain in the content-addressed manifest.
        rgb_path = Path(scene.render.filepath)
        png_data = rgb_path.read_bytes()
        canonical = bytearray(png_data[:8])
        offset = 8
        while offset < len(png_data):
            length = struct.unpack(">I", png_data[offset : offset + 4])[0]
            end = offset + length + 12
            if png_data[offset + 4 : offset + 8] not in {b"tEXt", b"iTXt", b"zTXt"}:
                canonical.extend(png_data[offset:end])
            offset = end
        rgb_path.write_bytes(canonical)
        render_seconds = time.monotonic() - frame_started
        # Dense, unfiltered pixel-center ray intersection in the evaluated Blender scene.
        # Depth is positive camera-Z in metres, zero for background. Segmentation 0=background.
        w, h = s["width"], s["height"]
        focal = w * c["focal_mm"] / c["sensor_mm"]
        intrinsic = [[focal, 0, w / 2], [0, focal, h / 2], [0, 0, 1]]
        camera_to_world = camera.matrix_world.copy()
        world_to_cv = np.diag([1, -1, -1, 1]) @ np.array(camera_to_world.inverted())
        depth = np.zeros((h, w), dtype=np.float32)
        segmentation = np.zeros((h, w), dtype=np.uint16)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for y in range(h):
            for x in range(w):
                direction = camera_to_world.to_3x3() @ Vector(
                    ((x + 0.5 - w / 2) / focal, -(y + 0.5 - h / 2) / focal, -1)
                )
                hit, position, _, _, obj, _ = scene.ray_cast(
                    depsgraph, camera.location, direction.normalized(), distance=20
                )
                if hit:
                    depth[y, x] = (world_to_cv @ np.array([*position, 1]))[2]
                    segmentation[y, x] = obj.pass_index
        np.save(path.parent / f"{index:03d}-depth.npy", depth, allow_pickle=False)
        np.save(path.parent / f"{index:03d}-segmentation.npy", segmentation, allow_pickle=False)
        results.append(
            {
                "objects": actual_objects,
                "intrinsic": intrinsic,
                "world_to_camera_cv": world_to_cv.tolist(),
                "camera_to_world_blender": [list(row) for row in camera_to_world],
                "render_seconds": render_seconds,
                "frame_seconds": time.monotonic() - frame_started,
            }
        )
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "blender_version": bpy.app.version_string,
        "build_hash": bpy.app.build_hash.decode(),
        "frames": results,
        "initialization_seconds": initialized - started,
        "worker_seconds": time.monotonic() - started,
        "cpu_seconds": usage.ru_utime + usage.ru_stime,
        "peak_rss_bytes": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
        "lighting": {"type": "AREA", "position": [0.5, -0.6, 2.5], "energy": 180, "size": 3},
        "depth": "camera axial Z metres; background 0; pixel centers; evaluated Blender ray_cast",
        "segmentation": "uint16 object pass_index; background 0; unfiltered pixel centers",
        "rgb": "Cycles CPU PNG Standard color transform; antialiased; no denoising",
    }
    (path.parent / "result.json").write_text(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
