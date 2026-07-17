# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Platform-key resolution and binaries.lock.json loading for daemon-supervised
sidecar agents (ported from test_email_sidecar_platform.py, issue #2142 T3)."""

import json
from pathlib import Path

import pytest

from gaia.daemon.sidecars import platform as plat
from gaia.daemon.sidecars.errors import PlatformError


def _write_lock(tmp_path: Path, binaries: dict, base_url="https://example/r2") -> Path:
    p = tmp_path / "binaries.lock.json"
    p.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "agentVersion": "0.2.2",
                "baseUrl": base_url,
                "binaries": binaries,
            }
        )
    )
    return p


def test_current_platform_key_shape():
    key = plat.current_platform_key()
    assert "-" in key  # e.g. darwin-arm64


def test_current_platform_key_normalizes():
    assert plat.current_platform_key("darwin", "arm64") == "darwin-arm64"
    assert plat.current_platform_key("linux", "x86_64") == "linux-x64"
    assert plat.current_platform_key("win32", "AMD64") == "win32-x64"
    assert plat.current_platform_key("darwin", "aarch64") == "darwin-arm64"


def test_is_placeholder_sha():
    assert plat.is_placeholder_sha("PENDING-1648-replace-with-real-sha256")
    assert plat.is_placeholder_sha("0" * 64)
    assert not plat.is_placeholder_sha("a" * 64)
    assert not plat.is_placeholder_sha("")  # empty handled by resolve_entry, not here


def test_load_and_resolve_entry(tmp_path):
    lock_path = _write_lock(
        tmp_path,
        {
            "darwin-arm64": {
                "filename": "email-agent-darwin-arm64",
                "executable": "email-agent",
                "sha256": "a" * 64,
                "size": 10,
            }
        },
    )
    lock = plat.load_lock(lock_path)
    assert lock.base_url == "https://example/r2"
    entry = plat.resolve_entry(lock, "darwin-arm64")
    assert entry.filename == "email-agent-darwin-arm64"
    assert entry.executable == "email-agent"
    assert entry.sha256 == "a" * 64


def test_resolve_entry_unknown_platform_raises(tmp_path):
    lock = plat.load_lock(
        _write_lock(
            tmp_path,
            {"linux-x64": {"filename": "f", "executable": "e", "sha256": "a" * 64}},
        )
    )
    with pytest.raises(PlatformError, match="no email-agent binary for platform"):
        plat.resolve_entry(lock, "plan9-sparc")


def test_resolve_entry_incomplete_raises(tmp_path):
    lock = plat.load_lock(
        _write_lock(tmp_path, {"darwin-arm64": {"filename": "f", "executable": "e"}})
    )
    with pytest.raises(PlatformError, match="incomplete"):
        plat.resolve_entry(lock, "darwin-arm64")


def test_load_lock_missing_file_raises(tmp_path):
    with pytest.raises(PlatformError, match="cannot read binaries.lock.json"):
        plat.load_lock(tmp_path / "nope.json")


def test_load_lock_bad_json_raises(tmp_path):
    p = tmp_path / "binaries.lock.json"
    p.write_text("{not json")
    with pytest.raises(PlatformError, match="not valid JSON"):
        plat.load_lock(p)


def test_default_lock_path_points_at_repo_lock():
    p = plat.default_lock_path()
    assert p.name == "binaries.lock.json"
    assert p.parts[-3:] == ("npm", "agent-email", "binaries.lock.json")


def test_default_lock_path_exists_and_loads():
    # The real repo lock must load (it ships with placeholder SHAs today).
    lock = plat.load_lock(plat.default_lock_path())
    assert "darwin-arm64" in lock.binaries
