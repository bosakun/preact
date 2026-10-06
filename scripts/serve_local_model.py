"""Own an authenticated loopback runner and publish measured non-secret model identity."""

import argparse
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx

from preact.engines.llamacpp import LocalModelManifest, load_configuration, sha256_file


def serve(prepared, output, port):
    metadata = json.loads(prepared.read_text())
    output.mkdir(parents=True, exist_ok=True)
    key_path, manifest_path, log_path = (
        output / "runner-token",
        output / "manifest.json",
        output / "runner.log",
    )
    if not key_path.exists():
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    if key_path.stat().st_mode & 0o077:
        raise ValueError("Token permissions must be private")
    key = key_path.read_text().strip()
    if len(key) < 32 or any(c.isspace() for c in key):
        raise ValueError("Invalid private token file")
    runner = Path(metadata["runner_dir"]) / "llama-server"
    for name, digest in metadata["runner_files"].items():
        if Path(name).name != name or sha256_file(runner.parent / name) != digest:
            raise ValueError("Runner file changed")
    if sha256_file(metadata["model_path"]) != metadata["model_sha256"]:
        raise ValueError("Model file changed")
    argv = [
        str(runner),
        "-m",
        metadata["model_path"],
        "--alias",
        metadata["model"],
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        "8192",
        "--parallel",
        "1",
        "--gpu-layers",
        "99",
        "--reasoning",
        "off",
        "--no-webui",
        "--n-predict",
        "2000",
        "--api-key-file",
        str(key_path.resolve()),
        "--cors-origins",
        "http://127.0.0.1",
        "--log-verbosity",
        "4",
    ]
    environment = {
        k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    }
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    with log_path.open("wb") as log:
        child = subprocess.Popen(
            argv, stdout=log, stderr=log, env=environment, start_new_session=True
        )

        def stop(*_):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, stop)
        try:
            props = None
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=2) as client:
                for _ in range(60):
                    if child.poll() is not None:
                        raise RuntimeError("Owned runner exited before startup")
                    try:
                        r = client.get(
                            f"http://127.0.0.1:{port}/props",
                            headers={"Authorization": "Bearer " + key},
                        )
                        if r.status_code == 200:
                            props = r.json()
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.25)
            if props is None or child.poll() is not None:
                raise RuntimeError("Owned runner startup deadline failed")
            startup = log_path.read_bytes().decode(errors="replace")
            device = re.search(r"using device MTL\d+ \(([^)]+)\)", startup)
            layers = re.search(r"offloaded (\d+)/(\d+) layers to GPU", startup)
            if (
                not device
                or not layers
                or int(layers[1]) == 0
                or f"listening on http://127.0.0.1:{port}" not in startup
            ):
                raise RuntimeError("Apple Metal offload was not measured")
            manifest = LocalModelManifest(
                **metadata,
                runner_build=props["build_info"],
                hardware=device[1],
                acceleration=f"Apple Metal; measured {layers[1]}/{layers[2]} layers offloaded",
            )
            manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
            load_configuration(manifest_path, key_path)
            print(
                json.dumps(
                    {
                        "status": "serving",
                        "manifest": str(manifest_path),
                        "key_file": str(key_path),
                        "url": f"http://127.0.0.1:{port}",
                        "hardware": device[1],
                        "cloud_validation": False,
                    }
                ),
                flush=True,
            )
            code = child.wait()
            if code not in (0, -signal.SIGTERM):
                raise RuntimeError("Owned runner failed")
        finally:
            stop()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", default=".cache/prepared-model.json", type=Path)
    parser.add_argument("--output", default=".cache/local-model", type=Path)
    parser.add_argument("--port", default=8125, type=int)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    try:
        serve(args.prepared, args.output, args.port)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
