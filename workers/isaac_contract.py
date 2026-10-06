"""SDK-independent scene and provenance contracts; no simulated hardware evidence."""

import csv
import hashlib
import io
import math
import platform
import subprocess
from pathlib import Path

SDK_TARGET = "5.1.0"


def backend_digest():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("isaac_contract.py", "isaac_step.py"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def sdk_build(values):
    # Official isaacsim.core.version.get_version returns these eight strings.
    keys = ("core", "prerelease", "major", "minor", "patch", "pretag", "prebuild", "buildtag")
    if not isinstance(values, (list, tuple)) or len(values) != len(keys):
        raise RuntimeError("Isaac SDK version measurement is malformed")
    if any(not isinstance(v, str) or len(v) > 256 or any(ord(c) < 32 for c in v) for v in values):
        raise RuntimeError("Isaac SDK version measurement is malformed")
    result = dict(zip(keys, values, strict=True))
    if result["core"] != SDK_TARGET or ".".join(values[2:5]) != result["core"]:
        raise RuntimeError("Actual Isaac SDK does not match the pinned compatibility target")
    return result


def parse_gpu_inventory(output):
    devices = []
    for row in csv.reader(io.StringIO(output)):
        if len(row) != 4:
            raise RuntimeError("NVIDIA inventory measurement is malformed")
        index, name, driver, memory = (cell.strip() for cell in row)
        if not index.isdecimal() or not memory.isdecimal() or int(memory) <= 0:
            raise RuntimeError("NVIDIA inventory has unavailable device measurements")
        if not name or not driver or name == "N/A" or driver == "N/A":
            raise RuntimeError("NVIDIA inventory has unavailable device measurements")
        devices.append(
            {"index": int(index), "name": name, "driver": driver, "memory_total_mib": int(memory)}
        )
    if not devices or len({d["index"] for d in devices}) != len(devices):
        raise RuntimeError("NVIDIA inventory is empty or inconsistent")
    return devices


def platform_evidence():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Pinned Isaac worker requires Linux x86-64 GPU conformance")
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        # Never copy command output, a private environment or an invented device.
        raise RuntimeError(f"NVIDIA inventory unavailable: {type(error).__name__}") from None
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "gpu_inventory": parse_gpu_inventory(result.stdout),
        "scope": "Observed device inventory; active renderer device and RTX conformance not certified",
        "worker_backend_sha256": backend_digest(),
    }


def vector(value, positive=False):
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 3
        or any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or (positive and v <= 0)
            for v in value
        )
    ):
        raise ValueError("Invalid axis-aligned scene geometry")
    return list(value)


def waypoint_ticks(duration, rate, count):
    """Allocate the rounded total duration exactly, with at least one tick per segment."""
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
        or not isinstance(rate, int)
        or isinstance(rate, bool)
        or rate < 1
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 1
    ):
        raise ValueError("Invalid transport timing")
    total = round(duration * rate)
    if total < count or total > 120 * 600:
        raise ValueError("Transport duration cannot represent every waypoint within bounds")
    quotient, remainder = divmod(total, count)
    return [quotient + (index < remainder) for index in range(count)]


def bilateral_grasp(active_contacts, finger_paths, cube_path="/World/Cube"):
    """Current collider-pair events, not historical touches, must cover both finger bodies."""
    if len(finger_paths) != 2:
        return False
    touching = set()
    for actors in active_contacts.values():
        if cube_path in actors:
            touching.update(set(actors).intersection(finger_paths))
    return touching == set(finger_paths)


def scene_spec(payload):
    cube_half = payload.get("cube_half", 0.025)
    if (
        isinstance(cube_half, bool)
        or not isinstance(cube_half, (int, float))
        or not math.isfinite(cube_half)
        or cube_half <= 0
    ):
        raise ValueError("Invalid cube dimensions")
    extras = payload.get("extra_obstacles", [])
    if not isinstance(extras, list) or len(extras) > 32:
        raise ValueError("Invalid or excessive extra obstacles")
    boxes = [{"center": payload["obstacle"], "half": payload["obstacle_half"]}, *extras]
    return {
        "cube_half": cube_half,
        "obstacles": [
            {
                "center": vector(box["center"]),
                "half": vector(box["half"], positive=True),
                "path": "/World/Obstacle" if index == 0 else f"/World/Obstacle{index}",
            }
            for index, box in enumerate(boxes)
        ],
    }
