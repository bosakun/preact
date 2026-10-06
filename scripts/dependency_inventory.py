"""Inventory installed versions and license metadata; not a permissions attestation."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def generate(web_root=Path("web")):
    python = []
    for package in sorted(
        importlib.metadata.distributions(), key=lambda p: p.metadata["Name"].lower()
    ):
        notices = []
        for file in package.files or []:
            if file.name.lower().startswith(("license", "copying", "notice")):
                path = package.locate_file(file)
                if path.is_file():
                    notices.append(
                        {"file": str(file), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    )
        python.append(
            {
                "name": package.metadata["Name"],
                "version": package.version,
                "license_expression": package.metadata.get("License-Expression"),
                "license_metadata": package.metadata.get("License"),
                "notices": notices,
            }
        )
    javascript = []
    packages = (
        json.loads((web_root / "package-lock.json").read_text())["packages"] if web_root else {}
    )
    for location, entry in sorted(packages.items()):
        if not location:
            continue
        installed = web_root / location / "package.json"
        metadata = json.loads(installed.read_text()) if installed.is_file() else {}
        javascript.append(
            {
                "name": metadata.get("name", location.rsplit("node_modules/", 1)[-1]),
                "version": entry.get("version"),
                "integrity": entry.get("integrity"),
                "license": metadata.get("license", entry.get("license")),
                "development_only": entry.get("dev", False),
                "installed": installed.is_file(),
            }
        )
    return {
        "schema_version": 1,
        "scope": (
            "Installed local Python environment and locked frontend; GPU models/assets excluded"
            if web_root
            else "Installed optional Python tool environment; application/frontend/models/assets excluded"
        ),
        "python": python,
        "javascript": javascript,
        "warning": "Metadata and notice hashes assist license review; unknown fields do not imply permission.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/dependency-inventory.json")
    parser.add_argument("--python-only", action="store_true")
    args = parser.parse_args()
    Path(args.output).write_text(
        json.dumps(generate(None if args.python_only else Path("web")), indent=2) + "\n"
    )
