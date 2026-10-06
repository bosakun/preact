"""Inspect built PreAct distributions without installing or executing archive contents."""

import argparse
import hashlib
import json
import stat
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


def checked_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("Unsafe archive path")
    if any(
        part in {".cache", ".venv", ".preact", ".git", "node_modules", "__pycache__"}
        for part in path.parts
    ):
        raise ValueError("Private/generated archive path")
    if path.name == ".env" or path.name.endswith((".pyc", ".tfvars", ".tfvars.json")):
        raise ValueError("Private configuration in archive")
    if ".tfstate" in path.name:
        raise ValueError("Terraform state in archive")
    return path


def read_archive(path, prefix=""):
    files = {}
    total = 0

    def add(name, body):
        nonlocal total
        checked_path(name)
        if prefix and not name.startswith(prefix):
            raise ValueError("Unexpected source archive root")
        name = name.removeprefix(prefix)
        if PurePosixPath(name).name.startswith(".env") and name != ".env.example":
            raise ValueError("Nested or private environment configuration")
        if name in files:
            raise ValueError("Duplicate archive file")
        total += len(body)
        if total > 64 * 1024 * 1024:
            raise ValueError("Distribution exceeds inspection size limit")
        files[name] = body

    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    checked_path(member.filename)
                    continue
                if stat.S_ISLNK(member.external_attr >> 16) or member.file_size > 4 * 1024 * 1024:
                    raise ValueError("Unsupported archive member")
                add(member.filename, archive.read(member))
    else:
        with tarfile.open(path) as archive:
            for member in archive:
                if member.isdir():
                    checked_path(member.name)
                    continue
                if not member.isfile() or member.size > 4 * 1024 * 1024:
                    raise ValueError("Unsupported archive member")
                add(member.name, archive.extractfile(member).read())
    return files


def check_sources(root, files, wheel):
    count = 0
    for directory in (root / "src/preact", root / "workers"):
        for source in sorted(directory.rglob("*.py")):
            relative = source.relative_to(
                root / "src" if wheel and directory.name == "preact" else root
            ).as_posix()
            if files.get(relative) != source.read_bytes():
                raise ValueError(f"Missing or changed source: {relative}")
            count += 1
    if not count:
        raise ValueError("No repository sources inspected")
    return count


def audit(root, directory):
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    base = project["name"].replace("-", "_") + "-" + project["version"]
    paths = [directory / (base + "-py3-none-any.whl"), directory / (base + ".tar.gz")]
    result = {}
    for path in paths:
        wheel = path.suffix == ".whl"
        files = read_archive(path, "" if wheel else base + "/")
        matched = check_sources(root, files, wheel)
        metadata_path = base + ".dist-info/METADATA" if wheel else "PKG-INFO"
        metadata = BytesParser().parsebytes(files[metadata_path])
        if (
            metadata["Name"] != project["name"]
            or metadata["Version"] != project["version"]
            or metadata["License-Expression"] != "Apache-2.0"
        ):
            raise ValueError("Distribution identity/license mismatch")
        license_path = base + ".dist-info/licenses/LICENSE" if wheel else "LICENSE"
        if files.get(license_path) != (root / "LICENSE").read_bytes():
            raise ValueError("Missing or changed project license")
        if not wheel:
            required = [
                "README.md",
                "pyproject.toml",
                "uv.lock",
                ".env.example",
                "web/package.json",
                "web/package-lock.json",
                "web/index.html",
                "assets/software/checkout.py",
                "deploy/Dockerfile",
            ]
            required += [
                p.relative_to(root).as_posix()
                for folder in ("web/src", "docs/licenses")
                for p in sorted((root / folder).rglob("*"))
                if p.is_file()
            ]
            for name in required:
                if files.get(name) != (root / name).read_bytes():
                    raise ValueError(f"Missing or changed release file: {name}")
        result[path.name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "files": len(files),
            "matched_python_sources": matched,
        }
    return {
        "status": "passed-distribution-integrity",
        "scope": "Source/assets/license/private-path integrity; not installed execution, secret scanning or sponsor validation",
        "artifacts": result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root.resolve(), args.directory)
    with args.output.open("x") as target:
        target.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
