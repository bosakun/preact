"""Frozen Blender visual integration protocol. Retain successes and failures."""

import argparse
import asyncio
import json
import os
import platform
import signal
import tempfile
import time
from pathlib import Path

from preact.core.models import Policy, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.datasets.scenes import split_scenes
from preact.domains.physical import PhysicalWorld
from preact.domains.scene import SceneWorld
from preact.engines.local import LocalVerifier
from preact.visual.adapter import VisualAdapter, manifest
from preact.visual.audit import audit_manifest, audit_run
from preact.visual.contracts import Settings
from preact.visual.gallery import build_gallery
from preact.visual.renderer import BlenderRenderer, RenderFailure
from preact.visual.runtime import VisualRuntime


async def benchmark(output, protocol_path, binary=None):
    output.mkdir(parents=True, exist_ok=False)
    protocol = json.loads(protocol_path.read_text())
    (output / "protocol.json").write_text(json.dumps(protocol, sort_keys=True, indent=2))
    artifacts = Artifacts(str(output / "artifacts"))
    settings = Settings.model_validate(protocol["settings"])
    renderer = BlenderRenderer(
        artifacts, binary, timeout=protocol["timeout_seconds"], temp_root=output
    )
    adapter = VisualAdapter(renderer, settings, protocol["intermediate_frames"])
    report = {
        "protocol_hash": identity(protocol),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "blender": renderer.binary,
        },
        "cases": [],
        "expected_failure_checks": [],
        "unexpected_failures": [],
    }
    started = time.monotonic()

    def save():
        (output / "results.json").write_text(json.dumps(report, sort_keys=True, indent=2))

    save()
    for split, indices in protocol["scene_samples"].items():
        for index in indices:
            world = SceneWorld(split_scenes(split)[index], seed=protocol["seed"])
            state = await world.observe()
            case = {
                "split": split,
                "scene": world.spec.name,
                "family": world.spec.family,
                "state": artifacts.json(state.model_dump()),
                "translation_success": False,
                "render_success": False,
                "audit_success": False,
            }
            report["cases"].append(case)
            try:
                from preact.visual.contracts import translate

                translate(state)
                case["translation_success"] = True
                digest = await adapter.current(state)
                case.update(manifest=digest, render_success=True)
                case["audit"] = audit_manifest(artifacts, digest)
                case["audit_success"] = True
            except Exception as error:
                case["error"] = f"{type(error).__name__}: {error}"
                report["unexpected_failures"].append(case["scene"])
            save()
    try:
        state = await PhysicalWorld().observe()
        first, second = await adapter.current(state), await adapter.current(state)
        checks = [
            a["artifacts"] == b["artifacts"]
            for a, b in zip(
                manifest(artifacts, first)["frames"],
                manifest(artifacts, second)["frames"],
                strict=True,
            )
        ]
        report["reproducibility"] = {
            "manifests": [first, second],
            "exact_modalities_equal": all(checks),
        }
        if not all(checks):
            report["unexpected_failures"].append("reproducibility")
    except Exception as error:
        report["unexpected_failures"].append(f"reproducibility: {error}")
    save()
    world = PhysicalWorld()
    world.task = world.task.model_copy(update={"max_steps": protocol["runtime_steps"]})
    store = Store("sqlite:///" + str((output / "ledger.db").resolve()))
    runtime = VisualRuntime(
        store,
        artifacts,
        Registry([LocalVerifier(world)]),
        Policy(**protocol["runtime_policy"]),
        visual_adapter=adapter,
    )
    try:
        report["runtime_result"] = await runtime.run(world)
        report["run_id"] = runtime.run_id
        report["runtime_audit"] = audit_run(store, artifacts, runtime.run_id)
        build_gallery(store, artifacts, runtime.run_id, output / "gallery.html")
    except Exception as error:
        report["unexpected_failures"].append(f"runtime: {type(error).__name__}: {error}")
    finally:
        if hasattr(runtime, "run_id"):
            report["run_id"] = runtime.run_id
            (output / "events.json").write_text(
                json.dumps(store.read_events(runtime.run_id), indent=2)
            )
    save()
    # Real Blender process timeout/cancellation: no successful manifest permitted.
    from preact.visual.contracts import RenderRequest, translate

    request = RenderRequest(settings=settings, frames=(translate(await PhysicalWorld().observe()),))
    limited = BlenderRenderer(artifacts, renderer.binary, timeout=0.01, temp_root=output)
    try:
        await limited.render(request, {})
        report["unexpected_failures"].append("timeout returned success")
    except RenderFailure as error:
        report["expected_failure_checks"].append(
            {
                "kind": "timeout",
                "error": str(error),
                "real_blender": True,
                "cleanup": limited.last_cleanup,
            }
        )
    task = asyncio.create_task(renderer.render(request, {}))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
        report["unexpected_failures"].append("cancellation returned success")
    except asyncio.CancelledError:
        report["expected_failure_checks"].append(
            {"kind": "cancellation", "real_blender": True, "cleanup": renderer.last_cleanup}
        )
    for entry in report["expected_failure_checks"]:
        cleanup = entry["cleanup"]
        if (
            not cleanup
            or not cleanup["terminal_state_confirmed"]
            or Path(cleanup["temporary_directory"]).exists()
        ):
            report["unexpected_failures"].append(f"undrained {entry['kind']}")
    # Real fixed-worker error; structured malformed input, never arbitrary Python.
    with tempfile.TemporaryDirectory(prefix="preact-blender-negative-", dir=output) as job:
        bad = Path(job) / "request.json"
        bad.write_text(json.dumps({"version": "1", "settings": {}, "frames": []}))
        worker = Path(__file__).resolve().parents[1] / "workers/blender.py"
        process = await asyncio.create_subprocess_exec(
            renderer.binary,
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(worker),
            "--",
            str(bad),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.wait(), 30)
            passed = process.returncode == 1 and not (Path(job) / "result.json").exists()
            report["expected_failure_checks"].append(
                {
                    "kind": "nonzero_exit",
                    "real_blender": True,
                    "returncode": process.returncode,
                    "passed": passed,
                }
            )
            if not passed:
                report["unexpected_failures"].append("malformed worker input returned success")
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
    # Use a separate store: corrupting test evidence must not alter actual products.
    with tempfile.TemporaryDirectory(prefix="preact-visual-tamper-", dir=output) as job:
        probe = Artifacts(job)
        digest = probe.put(b"owned integrity probe")
        (probe.root / digest).write_bytes(b"tampered")
        try:
            probe.read(digest)
            report["unexpected_failures"].append("tampered artifact was accepted")
        except ValueError:
            report["expected_failure_checks"].append(
                {"kind": "artifact_tamper", "real_blender": False, "passed": True}
            )
    report["wall_seconds"] = time.monotonic() - started
    digests = [c["manifest"] for c in report["cases"] if c.get("render_success")]
    digests += report.get("reproducibility", {}).get("manifests", [])
    if hasattr(runtime, "run_id"):
        for event in store.read_events(runtime.run_id):
            if event["kind"] in {"visual_current", "visual_observed_result"}:
                digests.append(event["data"]["manifest"])
            elif event["kind"] == "visual_prediction":
                digests.append(event["data"]["prediction"]["artifacts"]["manifest"])
    manifests = [manifest(artifacts, d) for d in dict.fromkeys(digests)]
    report["resources"] = {
        "render_jobs": len(manifests),
        "render_frames": sum(len(m["frames"]) for m in manifests),
        "cpu_seconds": sum(m["renderer"]["cpu_seconds"] for m in manifests),
        "render_seconds": sum(f["render_seconds"] for m in manifests for f in m["frames"]),
        "max_peak_rss_bytes": max((m["renderer"]["peak_rss_bytes"] for m in manifests), default=0),
        "mean_startup_shutdown_seconds": sum(m["startup_shutdown_seconds"] for m in manifests)
        / max(1, len(manifests)),
    }
    report["failure_counts"] = {
        "unexpected": len(report["unexpected_failures"]),
        "expected_timeout": sum(c["kind"] == "timeout" for c in report["expected_failure_checks"]),
        "expected_cancellation": sum(
            c["kind"] == "cancellation" for c in report["expected_failure_checks"]
        ),
        "expected_nonzero_exit": sum(
            c["kind"] == "nonzero_exit" for c in report["expected_failure_checks"]
        ),
        "expected_artifact_tamper": sum(
            c["kind"] == "artifact_tamper" for c in report["expected_failure_checks"]
        ),
        "render_errors": sum(not c["render_success"] for c in report["cases"]),
        "incomplete_outputs": sum(
            "Missing" in c.get("error", "") or "Incomplete" in c.get("error", "")
            for c in report["cases"]
        ),
    }
    report["artifact_count"] = len(list(artifacts.root.iterdir()))
    report["artifact_bytes"] = sum(p.stat().st_size for p in artifacts.root.iterdir())
    report["success_rates"] = {
        key: sum(c[key] for c in report["cases"]) / len(report["cases"])
        for key in ("translation_success", "render_success", "audit_success")
    }
    report["passed"] = not report["unexpected_failures"]
    save()
    store.db.dispose()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=Path("benchmarks/blender-visual-v2.json"))
    parser.add_argument("--blender", default=os.environ.get("PREACT_BLENDER"))
    args = parser.parse_args()
    report = asyncio.run(benchmark(args.output, args.protocol, args.blender))
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "success_rates": report["success_rates"],
                "unexpected_failures": report["unexpected_failures"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
