# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the binaries.lock.json generator (packaging/gen_binaries_lock.py).

Guards the rclone-era ``--meta`` contract (renamed from ``--summary``): a verbatim
``--base-url``, meta-driven per-platform entries, untouched entries for absent
platforms, and fail-loud rejection of bad input (non-http URL, unsupported
platform, placeholder hash, malformed record).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# gen_binaries_lock.py is a packaging script, not part of the gaia_agent_email
# package — load it by path.
_GEN_PATH = Path(__file__).resolve().parents[1] / "packaging" / "gen_binaries_lock.py"
_spec = importlib.util.spec_from_file_location("gen_binaries_lock", _GEN_PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

_VALID_SHA = "a" * 64


def _lock(tmp_path: Path) -> Path:
    """A starter lock with two placeholder platforms."""
    p = tmp_path / "binaries.lock.json"
    p.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "agentVersion": "0.0.0",
                "baseUrl": "https://PENDING/x",
                "binaries": {
                    "win32-x64": {
                        "filename": "email-agent-win32-x64.exe",
                        "executable": "email-agent.exe",
                        "sha256": "PENDING-replace-with-real-sha256",
                        "size": 0,
                    },
                    "darwin-x64": {
                        "filename": "email-agent-darwin-x64",
                        "executable": "email-agent",
                        "sha256": "PENDING-replace-with-real-sha256",
                        "size": 0,
                    },
                },
            },
            indent=2,
        )
    )
    return p


def _meta(tmp_path: Path, name: str, **rec) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps([rec]))
    return p


def _win_meta(tmp_path: Path, sha: str = _VALID_SHA, size: int = 123) -> Path:
    return _meta(
        tmp_path,
        "win.meta.json",
        platform="win32-x64",
        filename="email-agent-win32-x64.exe",
        executable="email-agent.exe",
        sha256=sha,
        size=size,
    )


def test_meta_drives_entry_and_base_url_is_verbatim(tmp_path):
    lock = _lock(tmp_path)
    base = "https://hub.amd-gaia.ai/agents/email/1.2.3"
    rc = gen.main(
        [
            "--base-url",
            base,
            "--version",
            "1.2.3",
            "--lock",
            str(lock),
            "--meta",
            str(_win_meta(tmp_path)),
        ]
    )
    assert rc == 0
    d = json.loads(lock.read_text())
    assert d["baseUrl"] == base  # verbatim — no /agents/email/<ver> suffix appended
    assert d["agentVersion"] == "1.2.3"
    assert d["binaries"]["win32-x64"]["sha256"] == _VALID_SHA
    assert d["binaries"]["win32-x64"]["size"] == 123


def test_trailing_slash_is_stripped(tmp_path):
    lock = _lock(tmp_path)
    rc = gen.main(
        [
            "--base-url",
            "https://x/y/",
            "--version",
            "1.0.0",
            "--lock",
            str(lock),
            "--meta",
            str(_win_meta(tmp_path)),
        ]
    )
    assert rc == 0
    assert json.loads(lock.read_text())["baseUrl"] == "https://x/y"


def test_absent_platform_keeps_existing_entry(tmp_path):
    lock = _lock(tmp_path)
    gen.main(
        [
            "--base-url",
            "https://x/y",
            "--version",
            "1.0.0",
            "--lock",
            str(lock),
            "--meta",
            str(_win_meta(tmp_path)),
        ]
    )
    # Only win32 was supplied — darwin-x64 must be untouched.
    darwin = json.loads(lock.read_text())["binaries"]["darwin-x64"]
    assert darwin["sha256"] == "PENDING-replace-with-real-sha256"
    assert darwin["size"] == 0


def test_non_http_base_url_fails(tmp_path):
    lock = _lock(tmp_path)
    with pytest.raises(SystemExit):
        gen.main(
            [
                "--base-url",
                "ftp://nope",
                "--version",
                "1.0.0",
                "--lock",
                str(lock),
                "--meta",
                str(_win_meta(tmp_path)),
            ]
        )


def test_unsupported_platform_fails(tmp_path):
    lock = _lock(tmp_path)
    meta = _meta(
        tmp_path,
        "bad.meta.json",
        platform="linux-arm64",
        filename="email-agent-linux-arm64",
        executable="email-agent",
        sha256=_VALID_SHA,
        size=1,
    )
    with pytest.raises(SystemExit):
        gen.main(
            [
                "--base-url",
                "https://x/y",
                "--version",
                "1.0.0",
                "--lock",
                str(lock),
                "--meta",
                str(meta),
            ]
        )


def test_placeholder_sha_in_meta_fails(tmp_path):
    lock = _lock(tmp_path)
    with pytest.raises(SystemExit):
        gen.main(
            [
                "--base-url",
                "https://x/y",
                "--version",
                "1.0.0",
                "--lock",
                str(lock),
                "--meta",
                str(_win_meta(tmp_path, sha="PENDING-replace")),
            ]
        )


def test_malformed_meta_missing_size_fails_loudly(tmp_path):
    lock = _lock(tmp_path)
    meta = _meta(
        tmp_path,
        "nosize.meta.json",
        platform="win32-x64",
        filename="email-agent-win32-x64.exe",
        executable="email-agent.exe",
        sha256=_VALID_SHA,  # 'size' deliberately omitted
    )
    with pytest.raises(SystemExit):
        gen.main(
            [
                "--base-url",
                "https://x/y",
                "--version",
                "1.0.0",
                "--lock",
                str(lock),
                "--meta",
                str(meta),
            ]
        )
