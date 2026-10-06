"""Exact shared Python sources for the separately installed Isaac SDK interpreter.

No vendor binaries, API site-packages, credentials or alternate Core implementation
cross this boundary. The SDK must supply its own compatible dependencies.
"""

import hashlib
import importlib
import importlib.metadata
import json
import os
import sys
import zipfile
from importlib.resources import files
from pathlib import Path

MODULES = (
    "__init__.py",
    "core/__init__.py",
    "core/models.py",
    "core/io.py",
    "core/store.py",
    "domains/__init__.py",
    "domains/physical.py",
    "engines/__init__.py",
    "engines/storage.py",
    "engines/sdk_bridge.py",
)
DEPENDENCIES = ("pydantic", "sqlalchemy", "boto3", "mediapy", "numpy")


def make_sdk_bundle(destination: Path):
    root = files("preact")
    entries = {f"preact/{name}": root.joinpath(name).read_bytes() for name in MODULES}
    manifest = {
        "schema_version": 1,
        "scope": "shared-source-imports-only",
        "modules": {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()},
    }
    entries["preact-sdk-manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as bundle:
        for name, data in sorted(entries.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o644 << 16
            bundle.writestr(entry, data)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def sdk_import_evidence():
    """Fail before GPU work if the official 5.1 interpreter/import contract is unmet."""
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Isaac 5.1 bridge requires its Python 3.11 SDK environment")
    path = Path(os.environ["PREACT_ISAAC_BRIDGE"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != os.environ["PREACT_ISAAC_BRIDGE_SHA256"]:
        raise RuntimeError("SDK bridge archive checksum mismatch")
    with zipfile.ZipFile(path) as bundle:
        manifest = json.loads(bundle.read("preact-sdk-manifest.json"))
        expected = {f"preact/{name}" for name in MODULES}
        if manifest["schema_version"] != 1 or set(manifest["modules"]) != expected:
            raise RuntimeError("SDK bridge module manifest mismatch")
        for name, checksum in manifest["modules"].items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != checksum:
                raise RuntimeError("SDK bridge source checksum mismatch")
            module_name = name.removesuffix(".py").removesuffix("/__init__")
            if module_name == "preact/__init__":
                module_name = "preact"
            module = importlib.import_module(module_name.replace("/", "."))
            if not str(module.__file__).startswith(str(path) + "/"):
                raise RuntimeError("SDK loaded a different shared PreAct source")
    versions = {}
    for dependency in DEPENDENCIES:
        importlib.import_module(dependency)
        versions[dependency] = importlib.metadata.version(dependency)
    return {
        **manifest,
        "bundle_sha256": digest,
        "python": sys.version.split()[0],
        "dependencies": versions,
        "nvidia_validated": False,
    }
