"""Reproduce private-cache inclusion and reject unsafe or incomplete releases."""

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "audit_distributions", Path(__file__).resolve().parents[1] / "scripts/audit_distributions.py"
)
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)


@pytest.mark.parametrize(
    "name",
    [
        ".cache/llama.cpp/tools/ui/.env.example",
        ".env",
        "web/.env.example",
        "../escape.py",
        "/absolute.py",
        "windows\\escape.py",
        "deploy/production.tfstate",
    ],
)
def test_archive_reader_rejects_private_and_escaping_members(tmp_path, name):
    path = tmp_path / "release.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, "non-secret regression sentinel")
    with pytest.raises(ValueError):
        auditor.read_archive(path)


def test_duplicate_archive_members_cannot_hide_different_content(tmp_path):
    path = tmp_path / "release.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("preact/core/runtime.py", "original")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("preact/core/runtime.py", "replacement")
    with pytest.raises(ValueError, match="Duplicate archive file"):
        auditor.read_archive(path)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_source_archive_links_cannot_redirect_release_contents(tmp_path, kind):
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("release/src/preact/core/runtime.py")
        member.type, member.linkname = kind, "/outside/source.py"
        archive.addfile(member)
    with pytest.raises(ValueError, match="Unsupported archive member"):
        auditor.read_archive(path, "release/")


def test_only_root_example_configuration_is_allowed(tmp_path):
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("release/.env.example")
        body = b"NEBIUS_API_KEY=\n"
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    assert auditor.read_archive(path, "release/") == {".env.example": body}


@pytest.mark.parametrize("packaged", [None, b"modified source"])
def test_missing_or_changed_python_source_fails(tmp_path, packaged):
    source = tmp_path / "src/preact/core/runtime.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"actual source")
    files = {} if packaged is None else {"preact/core/runtime.py": packaged}
    with pytest.raises(ValueError, match="Missing or changed source"):
        auditor.check_sources(tmp_path, files, wheel=True)
