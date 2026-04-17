# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for gaia.installer.export_import."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest

from gaia.installer.export_import import (
    BUNDLE_FORMAT_VERSION,
    BUNDLE_JSON_NAME,
    MAX_ENTRIES,
    MAX_UNCOMPRESSED_PER_FILE,
    ExportResult,
    ImportResult,
    export_custom_agents,
    import_agent_bundle,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() and ~/.gaia/agents to a tmp location."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    agents_root = home / ".gaia" / "agents"
    agents_root.mkdir(parents=True)
    return home


@pytest.fixture
def agents_root(fake_home):
    return fake_home / ".gaia" / "agents"


def _make_agent(agents_root: Path, agent_id: str, body: str = "") -> Path:
    agent_dir = agents_root / agent_id
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text(
        body or f"# agent {agent_id}\nAGENT_ID = '{agent_id}'\n"
    )
    return agent_dir


def _write_bundle(
    path: Path,
    *,
    manifest: dict | None = None,
    files: dict[str, bytes] | None = None,
    include_manifest: bool = True,
    extra_infos: list[zipfile.ZipInfo] | None = None,
) -> None:
    """Craft a bundle zip for negative-path tests."""
    files = files or {}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if include_manifest:
            zf.writestr(
                BUNDLE_JSON_NAME,
                json.dumps(
                    manifest
                    or {
                        "format_version": BUNDLE_FORMAT_VERSION,
                        "exported_at": "2026-04-17T00:00:00Z",
                        "gaia_version": "test",
                        "agent_ids": [],
                    }
                ),
            )
        for name, data in files.items():
            zf.writestr(name, data)
        for info in extra_infos or []:
            zf.writestr(info, b"")


# ---------------------------------------------------------------------------
# 1. Round-trip
# ---------------------------------------------------------------------------


def test_export_import_round_trip(tmp_path, fake_home, agents_root):
    # Create a real agent dir with a couple of files.
    agent_dir = _make_agent(agents_root, "zoo-agent")
    (agent_dir / "notes.txt").write_text("hello")
    (agent_dir / "sub").mkdir()
    (agent_dir / "sub" / "helper.py").write_text("x = 1\n")

    out = tmp_path / "export.zip"
    result = export_custom_agents(out)
    assert isinstance(result, ExportResult)
    assert result.agent_ids == ["zoo-agent"]
    assert out.exists()

    # Remove the source then import and verify contents match.
    import shutil

    shutil.rmtree(agent_dir)
    assert not (agents_root / "zoo-agent").exists()

    imported = import_agent_bundle(out)
    assert isinstance(imported, ImportResult)
    assert imported.imported == ["zoo-agent"]
    assert imported.overwritten == []

    restored = agents_root / "zoo-agent"
    assert restored.is_dir()
    assert (restored / "agent.py").is_file()
    assert (restored / "notes.txt").read_text() == "hello"
    assert (restored / "sub" / "helper.py").read_text() == "x = 1\n"


# ---------------------------------------------------------------------------
# 2. Zip-slip
# ---------------------------------------------------------------------------


def test_zip_slip_rejected(tmp_path, fake_home):
    bundle = tmp_path / "evil.zip"
    _write_bundle(
        bundle,
        manifest={
            "format_version": BUNDLE_FORMAT_VERSION,
            "exported_at": "now",
            "gaia_version": "test",
            "agent_ids": ["a"],
        },
        files={"../../etc/passwd": b"root:x:0:0"},
    )
    with pytest.raises(ValueError, match="path traversal|absolute"):
        import_agent_bundle(bundle)


# ---------------------------------------------------------------------------
# 3. Symlink
# ---------------------------------------------------------------------------


def test_symlink_rejected(tmp_path, fake_home):
    bundle = tmp_path / "sym.zip"
    # Build manually so we can set external_attr to mark a symlink.
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr(
            BUNDLE_JSON_NAME,
            json.dumps(
                {
                    "format_version": BUNDLE_FORMAT_VERSION,
                    "exported_at": "now",
                    "gaia_version": "test",
                    "agent_ids": ["linky"],
                }
            ),
        )
        info = zipfile.ZipInfo("linky/agent.py")
        # 0o120000 in upper 16 bits marks a symlink on POSIX zips.
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b"/etc/passwd")

    with pytest.raises(ValueError, match="symlink"):
        import_agent_bundle(bundle)


# ---------------------------------------------------------------------------
# 4. Absolute path
# ---------------------------------------------------------------------------


def test_absolute_path_rejected(tmp_path, fake_home):
    bundle = tmp_path / "abs.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr(
            BUNDLE_JSON_NAME,
            json.dumps(
                {
                    "format_version": BUNDLE_FORMAT_VERSION,
                    "exported_at": "now",
                    "gaia_version": "test",
                    "agent_ids": ["a"],
                }
            ),
        )
        # Use a raw ZipInfo so the name is preserved verbatim.
        info = zipfile.ZipInfo("/etc/passwd")
        zf.writestr(info, b"content")

    with pytest.raises(ValueError, match="absolute"):
        import_agent_bundle(bundle)


# ---------------------------------------------------------------------------
# 5. Oversized entry
# ---------------------------------------------------------------------------


def test_oversized_entry_rejected(tmp_path, fake_home, monkeypatch):
    bundle = tmp_path / "big.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr(
            BUNDLE_JSON_NAME,
            json.dumps(
                {
                    "format_version": BUNDLE_FORMAT_VERSION,
                    "exported_at": "now",
                    "gaia_version": "test",
                    "agent_ids": ["big"],
                }
            ),
        )
        zf.writestr("big/agent.py", b"tiny")

    # Spoof the reported file_size of that single entry.
    orig_infolist = zipfile.ZipFile.infolist

    def spoofed(self):
        infos = orig_infolist(self)
        for info in infos:
            if info.filename == "big/agent.py":
                info.file_size = MAX_UNCOMPRESSED_PER_FILE + 1
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", spoofed)

    with pytest.raises(ValueError, match="per-file limit|uncompressed"):
        import_agent_bundle(bundle)


# ---------------------------------------------------------------------------
# 6. Too many entries
# ---------------------------------------------------------------------------


def test_too_many_entries_rejected(tmp_path, fake_home):
    bundle = tmp_path / "many.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr(
            BUNDLE_JSON_NAME,
            json.dumps(
                {
                    "format_version": BUNDLE_FORMAT_VERSION,
                    "exported_at": "now",
                    "gaia_version": "test",
                    "agent_ids": ["a"],
                }
            ),
        )
        # MAX_ENTRIES total allowed; we need > MAX_ENTRIES entries overall.
        # bundle.json already counts as 1, so add MAX_ENTRIES more = 1001.
        for i in range(MAX_ENTRIES):
            zf.writestr(f"a/file_{i}.txt", b"")

    with pytest.raises(ValueError, match="too many entries"):
        import_agent_bundle(bundle)


# ---------------------------------------------------------------------------
# 7-9. Invalid agent IDs
# ---------------------------------------------------------------------------


def test_invalid_agent_id_dot_dot(tmp_path, fake_home):
    bundle = tmp_path / "dd.zip"
    _write_bundle(
        bundle,
        manifest={
            "format_version": BUNDLE_FORMAT_VERSION,
            "exported_at": "now",
            "gaia_version": "test",
            "agent_ids": ["../../evil"],
        },
        files={"harmless/agent.py": b"x=1"},
    )
    with pytest.raises(ValueError, match="path separators|invalid|traversal|absolute"):
        import_agent_bundle(bundle)


def test_invalid_agent_id_slash(tmp_path, fake_home):
    bundle = tmp_path / "slash.zip"
    _write_bundle(
        bundle,
        manifest={
            "format_version": BUNDLE_FORMAT_VERSION,
            "exported_at": "now",
            "gaia_version": "test",
            "agent_ids": ["a/b"],
        },
        files={"harmless/agent.py": b"x=1"},
    )
    with pytest.raises(ValueError, match="path separators|invalid"):
        import_agent_bundle(bundle)


def test_invalid_agent_id_reserved_windows_name(tmp_path, fake_home):
    bundle = tmp_path / "com1.zip"
    _write_bundle(
        bundle,
        manifest={
            "format_version": BUNDLE_FORMAT_VERSION,
            "exported_at": "now",
            "gaia_version": "test",
            "agent_ids": ["COM1"],
        },
        files={"harmless/agent.py": b"x=1"},
    )
    with pytest.raises(ValueError, match="reserved Windows|invalid"):
        import_agent_bundle(bundle)


# ---------------------------------------------------------------------------
# 10. Zero agents to export
# ---------------------------------------------------------------------------


def test_export_zero_agents_raises(tmp_path, fake_home, agents_root):
    # agents_root exists but is empty.
    out = tmp_path / "empty.zip"
    with pytest.raises(ValueError, match="No custom agents"):
        export_custom_agents(out)
    assert not out.exists()


# ---------------------------------------------------------------------------
# 11. Overwrite existing
# ---------------------------------------------------------------------------


def test_import_overwrites_existing(tmp_path, fake_home, agents_root):
    # Seed an existing agent with old content.
    existing = _make_agent(agents_root, "zoo-agent", body="OLD\n")
    (existing / "stale.txt").write_text("stale")

    # Build a bundle with the same id but new content.
    bundle = tmp_path / "new.zip"
    staging = tmp_path / "staging"
    staging.mkdir()
    new_agent = staging / "zoo-agent"
    new_agent.mkdir()
    (new_agent / "agent.py").write_text("NEW\n")

    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr(
            BUNDLE_JSON_NAME,
            json.dumps(
                {
                    "format_version": BUNDLE_FORMAT_VERSION,
                    "exported_at": "now",
                    "gaia_version": "test",
                    "agent_ids": ["zoo-agent"],
                }
            ),
        )
        zf.write(new_agent / "agent.py", arcname="zoo-agent/agent.py")

    result = import_agent_bundle(bundle)
    assert result.imported == ["zoo-agent"]
    assert result.overwritten == ["zoo-agent"]

    final = agents_root / "zoo-agent"
    assert (final / "agent.py").read_text() == "NEW\n"
    # Stale file from the previous install must be gone.
    assert not (final / "stale.txt").exists()


# ---------------------------------------------------------------------------
# 12. Atomicity of export
# ---------------------------------------------------------------------------


def test_export_atomicity(tmp_path, fake_home, agents_root, monkeypatch):
    _make_agent(agents_root, "keep")

    # Pre-existing export.zip with known good content.
    out = tmp_path / "export.zip"
    out.write_bytes(b"ORIGINAL_GOOD_CONTENT")
    original_bytes = out.read_bytes()

    # Force zip writing to fail mid-flight by monkeypatching zipfile.ZipFile.write.
    real_write = zipfile.ZipFile.write

    def boom(self, *args, **kwargs):
        raise OSError("simulated disk-full")

    monkeypatch.setattr(zipfile.ZipFile, "write", boom)

    with pytest.raises(OSError, match="simulated disk-full"):
        export_custom_agents(out)

    # Original file must be untouched.
    assert out.read_bytes() == original_bytes

    # Restore and ensure no temp file was left behind in the output directory.
    monkeypatch.setattr(zipfile.ZipFile, "write", real_write)
    leftover = [p for p in out.parent.iterdir() if p.name.startswith(".gaia-export-")]
    assert leftover == []


# ---------------------------------------------------------------------------
# 13. Missing bundle.json
# ---------------------------------------------------------------------------


def test_missing_bundle_json(tmp_path, fake_home):
    bundle = tmp_path / "nomanifest.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("foo/agent.py", b"x=1")

    with pytest.raises(ValueError, match="bundle.json"):
        import_agent_bundle(bundle)


# ---------------------------------------------------------------------------
# 14. Wrong format version
# ---------------------------------------------------------------------------


def test_wrong_format_version(tmp_path, fake_home):
    bundle = tmp_path / "v2.zip"
    _write_bundle(
        bundle,
        manifest={
            "format_version": 2,
            "exported_at": "now",
            "gaia_version": "test",
            "agent_ids": ["a"],
        },
        files={"a/agent.py": b"x=1"},
    )
    with pytest.raises(ValueError, match="format_version"):
        import_agent_bundle(bundle)
