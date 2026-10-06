"""Prepare pinned official NVIDIA weights and a Metal runner; never starts cloud resources."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
REVISION = "dfaf35de3e30f1867dd8dbc38a7fc9fb52d3914f"
WEIGHTS_SHA = "55d4e2519456c4a9bddf596b0748d630e3b2ce6ff6f4c2b7ed3e07e2b00dad42"
RUNNER_REVISION = "0faee5004297c3bcfa40b7bf11750127b8c1fd7d"
LICENSE_URL = "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/"
LICENSE_SHA = "2ffd837856bb99d4cee13d17f0b597ecfeb95c38e30abc08d3dffef7d589881d"
LICENSE_PDF = "https://www.nvidia.com/content/dam/en-zz/Solutions/license-agreements/enterprise-software/NVIDIA-Nemotron-Open-Model-License-12-12-25.pdf"
BF16_SHA = "c0346eec0780acd592782ced994719694da95ab178fe8db62c78e0352e184daf"
QUANTIZED_SHA = "ca5e1bd8663465a81579274345f045f7a49cb5a96f350d859bb0ec725c1a7c59"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def run(argv, log):
    with log.open("ab") as output:
        subprocess.run(argv, check=True, stdout=output, stderr=output)


def prepare(root):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("This tested preparation recipe requires Apple Silicon macOS")
    root.mkdir(parents=True, exist_ok=True)
    source, weights, build = (
        root / "llama.cpp",
        root / "nvidia-nemotron-4b",
        root / "llama.cpp/build",
    )
    log = root / "prepare.log"
    license_path = root / "NVIDIA-Nemotron-Open-Model-License.pdf"
    if not license_path.exists():
        with urlopen(LICENSE_PDF, timeout=30) as response:
            license_path.write_bytes(response.read(1048576))
    if digest(license_path) != LICENSE_SHA:
        raise RuntimeError("Governing license changed; reverify its official terms")
    if not source.exists():
        run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "https://github.com/ggml-org/llama.cpp.git",
                str(source),
            ],
            log,
        )
        run(["git", "-C", str(source), "checkout", RUNNER_REVISION], log)
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip()
    if revision != RUNNER_REVISION or dirty:
        raise RuntimeError("Runner source does not match the clean pinned revision")
    run(
        [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(build),
            "-DGGML_METAL=ON",
            "-DGGML_CUDA=OFF",
            "-DLLAMA_BUILD_TESTS=OFF",
            "-DLLAMA_BUILD_UI=OFF",
            "-DLLAMA_USE_PREBUILT_UI=OFF",
        ],
        log,
    )
    run(
        [
            "cmake",
            "--build",
            str(build),
            "--config",
            "Release",
            "-j",
            "4",
            "--target",
            "llama-server",
            "llama-quantize",
        ],
        log,
    )
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    from huggingface_hub import snapshot_download

    snapshot_download(
        MODEL,
        revision=REVISION,
        token=False,
        local_dir=weights,
        allow_patterns=["*.json", "*.jinja", "model.safetensors", "README.md", "LICENSE"],
        max_workers=2,
    )
    if digest(weights / "model.safetensors") != WEIGHTS_SHA:
        raise RuntimeError("Official checkpoint content hash mismatch")
    original, quantized = root / "nemotron-4b-bf16.gguf", root / "nemotron-4b-Q4_K_M.gguf"
    if not original.exists():
        run(
            [
                sys.executable,
                str(source / "convert_hf_to_gguf.py"),
                str(weights),
                "--outfile",
                str(original),
                "--outtype",
                "bf16",
            ],
            log,
        )
    if digest(original) != BF16_SHA:
        raise RuntimeError("Converted checkpoint differs from the measured pinned derivative")
    if not quantized.exists():
        run([str(build / "bin/llama-quantize"), str(original), str(quantized), "Q4_K_M", "4"], log)
    if digest(quantized) != QUANTIZED_SHA:
        raise RuntimeError("Quantized checkpoint differs from the measured pinned derivative")
    files = sorted((build / "bin").glob("*.dylib")) + [build / "bin/llama-server"]
    record = {
        "model": MODEL,
        "checkpoint_revision": REVISION,
        "original_weights_sha256": WEIGHTS_SHA,
        "model_path": str(quantized.resolve()),
        "model_sha256": digest(quantized),
        "quantization": "Q4_K_M",
        "runner_dir": str((build / "bin").resolve()),
        "runner_revision": RUNNER_REVISION,
        "runner_files": {f.name: digest(f) for f in files},
        "license_url": LICENSE_URL,
        "license_sha256": LICENSE_SHA,
    }
    path = root / "prepared-model.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"status": "prepared", "metadata": str(path), "live_inference": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".cache")
    args = parser.parse_args()
    try:
        prepare(Path(args.root))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
