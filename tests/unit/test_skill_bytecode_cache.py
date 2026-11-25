# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A bytecode cache never travels with a skill into GAIA.

Every path that copies a bundle in (install, import, capture, migrate) drops
``__pycache__`` and ``.pyc``/``.pyo``, so a cache in an installed skill was
written locally from audited source — and the audit, promote and publish all
treat one as the noise it is. Cold state throughout: every root is under
``tmp_path``.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from gaia.skills.audit import audit_skill
from gaia.skills.capture import capture_skill, import_bundle, promote_skill
from gaia.skills.migrate import install_migrated, migrate_skill_dir
from gaia.skills.publish import publish_skill
from tests.unit.skills_helpers import fake_hub, isolated_manager

_TOOLS = (
    "from gaia.agents.base.tools import tool\n"
    "\n"
    "\n"
    "@tool\n"
    "def count_words(text: str) -> int:\n"
    '    """Count words."""\n'
    "    return len(text.split())\n"
)


def _skill(root: Path, name: str = "notes") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                "description: A skill used to test bytecode caches.",
                'version: "1.0.0"',
                "metadata:",
                "  gaia:",
                "    security_tier: experimental",
                "    tools:",
                "      - name: count_words",
                "        description: Count words.",
                "        parameters:",
                "          text: {type: string, required: true}",
                "---",
                "",
                "# Notes",
                "",
                "Count the words.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (directory / "tools.py").write_text(_TOOLS, encoding="utf-8")
    return directory


def _ship_caches(directory: Path) -> None:
    cache = directory / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "tools.cpython-312.pyc").write_bytes(b"\x00\x01")
    (directory / "scripts").mkdir(exist_ok=True)
    (directory / "scripts" / "__pycache__").mkdir(exist_ok=True)
    (directory / "scripts" / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"\0")
    (directory / "helper.pyc").write_bytes(b"\x00")
    (directory / "legacy.pyo").write_bytes(b"\x00")


def _has_bytecode(directory: Path) -> list[str]:
    return sorted(
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.name == "__pycache__" or p.suffix in (".pyc", ".pyo")
    )


@pytest.fixture
def manager(tmp_path):
    return isolated_manager(tmp_path)


# --- every copy into GAIA drops the cache ------------------------------------


def test_import_drops_a_shipped_bytecode_cache(tmp_path, manager):
    source = _skill(tmp_path / "src")
    _ship_caches(source)

    import_bundle(source, origin=str(source), manager=manager, allow_experimental=True)

    landed = manager.user_root / "notes"
    assert (landed / "tools.py").is_file()
    assert _has_bytecode(landed) == []


def test_capture_drops_a_shipped_bytecode_cache(tmp_path, manager):
    source = _skill(tmp_path / "src")
    _ship_caches(source)

    capture_skill(str(source), manager=manager)

    landed = manager.user_root / "notes"
    assert (landed / "tools.py").is_file()
    assert _has_bytecode(landed) == []


def test_migrate_drops_a_shipped_bytecode_cache(tmp_path, manager):
    source = tmp_path / "src" / "notes"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: notes\ndescription: A test skill.\nversion: 1.0.0\n"
        'metadata:\n  openclaw:\n    emoji: "x"\n---\n\n# notes\n\nDo the thing.\n',
        encoding="utf-8",
    )
    (source / "scripts").mkdir()
    (source / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    _ship_caches(source)
    outcome = migrate_skill_dir(source, vendor="openclaw")

    target = install_migrated(outcome, manager.user_root, allow_experimental=True)

    assert (target / "scripts" / "run.py").is_file()
    assert _has_bytecode(target) == []


def test_install_drops_a_shipped_bytecode_cache(tmp_path, manager):
    hub = fake_hub(tmp_path)
    source = _skill(tmp_path / "src")
    _ship_caches(source)
    archive = tmp_path / "notes-1.0.0.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=f"notes/{path.relative_to(source)}")
    payload = archive.read_bytes()
    hub.put_artifact("notes", "1.0.0", archive.name, payload)
    hub.put_skill_doc("notes", "1.0.0", (source / "SKILL.md").read_text("utf-8"))
    hub.put_manifest(
        "notes",
        "1.0.0",
        filename=archive.name,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    hub.rebuild_index()

    from gaia.skills.install import install_skill

    result = install_skill(
        "notes",
        manager=manager,
        base_url=hub.BASE_URL,
        fetcher=hub.fetcher,
        allow_experimental=True,
    )

    assert (result.path / "tools.py").is_file()
    assert _has_bytecode(result.path) == []


# --- a locally written cache is not a finding --------------------------------


def test_a_loader_written_cache_still_audits_allow_and_promotes(tmp_path, manager):
    source = _skill(tmp_path / "src")
    capture_skill(str(source), manager=manager)
    landed = manager.user_root / "notes"
    (landed / "__pycache__").mkdir()
    (landed / "__pycache__" / "tools.cpython-312.pyc").write_bytes(b"\x00")

    assert audit_skill(landed).verdict == "ALLOW"
    assert promote_skill("notes", manager=manager).promoted is True


def test_dry_run_publish_from_a_folder_with_a_cache_succeeds(tmp_path):
    source = _skill(tmp_path / "src")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "x.pyc").write_bytes(b"\x00")

    result = publish_skill(source, token=None, unsigned=True, dry_run=True)

    assert result.audit.verdict == "ALLOW"
    with zipfile.ZipFile(_zip_of(result, tmp_path)) as bundle:
        assert not [n for n in bundle.namelist() if "__pycache__" in n]


def test_publish_audits_exactly_the_bytes_it_uploads(tmp_path):
    """Content outside the staged copy does not reach the audit or the digest."""
    from gaia.skills.audit.findings import content_digest

    source = _skill(tmp_path / "src")
    (source / ".venv").mkdir()
    (source / ".venv" / "vendor.zip").write_bytes(b"PK\x03\x04")

    result = publish_skill(source, token=None, unsigned=True, dry_run=True)

    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(_zip_of(result, tmp_path)) as bundle:
        bundle.extractall(unpacked)
    assert result.audit.verdict == "ALLOW"
    assert result.audit.binding["content_digest"] == content_digest(unpacked / "notes")


def _zip_of(result, tmp_path: Path) -> Path:
    path = tmp_path / "sent.zip"
    path.write_bytes(result.request.artifact_bytes)
    return path
