"""Bounded cancellable subprocess renderer and content-addressed output publisher."""

import asyncio
import hashlib
import io
import json
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path

import numpy as np

from preact.core.models import identity
from preact.core.store import Artifacts
from preact.visual.contracts import RenderRequest


class RenderFailure(RuntimeError):
    pass


class BlenderRenderer:
    def __init__(self, artifacts: Artifacts, binary=None, *, timeout=120, temp_root=None):
        self.artifacts = artifacts
        self.binary = binary or os.environ.get("PREACT_BLENDER") or shutil.which("blender")
        if not self.binary or not Path(self.binary).is_file():
            raise RenderFailure("Blender unavailable; set PREACT_BLENDER to an official binary")
        if not 0 < timeout <= 600:
            raise ValueError("Timeout must be in (0, 600]")
        self.timeout = timeout
        self.temp_root = temp_root
        self.last_cleanup = None

    async def render(self, request: RenderRequest, provenance: dict):
        request = RenderRequest.model_validate(request.model_dump())
        self.last_cleanup = None
        started = time.monotonic()
        process = None
        # No input path, output path, Python expression, or blend file is accepted from callers.
        with tempfile.TemporaryDirectory(prefix="preact-blender-", dir=self.temp_root) as job:
            directory = Path(job)
            input_path = directory / "request.json"
            body = request.model_dump(mode="json")
            input_path.write_text(json.dumps(body, sort_keys=True, allow_nan=False))
            if input_path.stat().st_size > 1024 * 1024:
                raise ValueError("Request exceeds 1 MiB")
            worker = Path(__file__).resolve().parents[3] / "workers" / "blender.py"
            # Installed wheel has preact/ and workers/ as siblings.
            if not worker.exists():
                import workers

                worker = Path(workers.__file__).parent / "blender.py"
            log_path = directory / "process.log"
            try:
                with log_path.open("wb") as log:
                    spawning = asyncio.create_task(
                        asyncio.create_subprocess_exec(
                            str(self.binary),
                            "--background",
                            "--factory-startup",
                            "--disable-autoexec",
                            "--python-exit-code",
                            "1",
                            "--threads",
                            str(request.settings.threads),
                            "--python",
                            str(worker),
                            "--",
                            str(input_path),
                            stdout=log,
                            stderr=asyncio.subprocess.STDOUT,
                            start_new_session=True,
                            env={**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1"},
                        )
                    )
                    try:
                        process = await asyncio.shield(spawning)
                    except asyncio.CancelledError:
                        # Spawn may have created a child before cancellation was delivered.
                        # Obtain its handle so finally can kill/drain it before cleanup.
                        process = await spawning
                        raise
                    async with asyncio.timeout(self.timeout):
                        while process.returncode is None:
                            await asyncio.sleep(0.05)
                            files = list(directory.iterdir())
                            if sum(p.stat().st_size for p in files) > 256 * 1024 * 1024 or any(
                                p.stat().st_size > 16 * 1024 * 1024 for p in files
                            ):
                                raise RenderFailure("Worker output exceeds size limit")
                        await process.wait()
                if process.returncode:
                    detail = log_path.read_bytes()[-4096:].decode(errors="replace")
                    raise RenderFailure(f"Blender exited {process.returncode}: {detail}")
                result_path = directory / "result.json"
                if not result_path.exists():
                    raise RenderFailure("Missing completion manifest")
                result = json.loads(result_path.read_text())
                if len(result.get("frames", [])) != len(request.frames):
                    raise RenderFailure("Incomplete frame set")
                # Validate every product before publishing any successful manifest.
                products = []
                for index, frame in enumerate(request.frames):
                    frame_products = {}
                    for modality, extension in [
                        ("rgb", "png"),
                        ("depth", "npy"),
                        ("segmentation", "npy"),
                    ]:
                        path = directory / f"{index:03d}-{modality}.{extension}"
                        if (
                            path.is_symlink()
                            or not path.is_file()
                            or path.stat().st_size > 16 * 1024 * 1024
                        ):
                            raise RenderFailure("Missing, unsafe or oversized output")
                        data = path.read_bytes()
                        if modality == "rgb":
                            import struct

                            if data[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(
                                ">II", data[16:24]
                            ) != (request.settings.width, request.settings.height):
                                raise RenderFailure("Invalid RGB image")
                        else:
                            array = np.load(io.BytesIO(data), allow_pickle=False)
                            if array.shape != (request.settings.height, request.settings.width):
                                raise RenderFailure("Invalid dense output shape")
                            if modality == "depth" and (
                                array.dtype != np.float32
                                or not np.all(np.isfinite(array) & (array >= 0) & (array <= 20))
                            ):
                                raise RenderFailure("Invalid metric depth")
                            if modality == "segmentation" and (
                                array.dtype != np.uint16
                                or not set(np.unique(array))
                                <= {0, *(s.object_id for s in frame.objects)}
                            ):
                                raise RenderFailure("Invalid segmentation IDs")
                        frame_products[modality] = data
                    products.append(frame_products)
                entries = [
                    {
                        **frame.model_dump(mode="json"),
                        **result["frames"][index],
                        "artifacts": {
                            name: self.artifacts.put(data) for name, data in product.items()
                        },
                    }
                    for index, (frame, product) in enumerate(
                        zip(request.frames, products, strict=True)
                    )
                ]
                manifest = {
                    "version": "1",
                    "synthetic": True,
                    "authority": "none",
                    "request": body,
                    "request_hash": identity(body),
                    "provenance": provenance,
                    "coordinate_system": "MuJoCo XYZ = Blender XYZ, right-handed Z-up, metres",
                    "frames": entries,
                    "renderer": {k: v for k, v in result.items() if k != "frames"},
                    "process_wall_seconds": time.monotonic() - started,
                    "startup_shutdown_seconds": max(
                        0, time.monotonic() - started - result["worker_seconds"]
                    ),
                    "binary": str(Path(self.binary).resolve()),
                    "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
                }
                return self.artifacts.json(manifest)
            except TimeoutError as error:
                raise RenderFailure("Blender render timed out") from error
            finally:
                if process is not None:
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    await asyncio.shield(process.wait())
                    self.last_cleanup = {
                        "pid": process.pid,
                        "returncode": process.returncode,
                        "terminal_state_confirmed": process.returncode is not None,
                        "temporary_directory": job,
                    }
