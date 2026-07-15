# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verified binary fetch — the SHA-256 security boundary for the email sidecar."""

import hashlib
import json
from pathlib import Path

import pytest

from gaia.ui.email_sidecar import fetch as fetchmod
from gaia.ui.email_sidecar.errors import IntegrityError, PlatformError

REAL_BYTES = b"fake-binary-payload"
REAL_SHA = hashlib.sha256(REAL_BYTES).hexdigest()


def _lock(tmp_path: Path, sha: str, base="https://r2.example") -> Path:
    p = tmp_path / "binaries.lock.json"
    p.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "agentVersion": "0.2.2",
                "baseUrl": base,
                "binaries": {
                    "darwin-arm64": {
                        "filename": "email-agent-darwin-arm64",
                        "executable": "email-agent",
                        "sha256": sha,
                        "size": len(REAL_BYTES),
                    }
                },
            }
        )
    )
    return p


class _FakeResp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status
        self.reason = "OK" if status == 200 else "ERR"
        self.text = ""

    @property
    def ok(self):
        return self.status_code == 200


class _FakeSession:
    def __init__(self, content, status=200):
        self._content, self._status = content, status
        self.calls = []

    def get(self, url, timeout=None, headers=None, stream=False):
        self.calls.append(url)
        return _FakeResp(self._content, self._status)


def test_verify_sha256_tamper_raises():
    with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
        fetchmod.verify_sha256(b"tampered", REAL_SHA, "test")


def test_verify_sha256_match_returns_hash():
    assert fetchmod.verify_sha256(REAL_BYTES, REAL_SHA, "test") == REAL_SHA


def test_fetch_downloads_and_verifies(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(REAL_BYTES)
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
    )
    assert res.cached is False
    assert res.sha256 == REAL_SHA
    assert Path(res.binary_path).read_bytes() == REAL_BYTES
    assert sess.calls == ["https://r2.example/email-agent-darwin-arm64"]


def test_fetch_cache_hit_skips_download(tmp_path):
    out = tmp_path / "cache"
    out.mkdir()
    (out / "email-agent").write_bytes(REAL_BYTES)
    sess = _FakeSession(b"SHOULD-NOT-BE-USED")
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
    )
    assert res.cached is True
    assert sess.calls == []  # no network on cache hit


def test_fetch_tampered_download_raises_and_leaves_no_file(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(b"corrupted-bytes")
    with pytest.raises(IntegrityError):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, REAL_SHA),
            session=sess,
        )
    # A failed verify must NOT leave a binary on disk.
    assert not (out / "email-agent").exists()


def test_fetch_placeholder_sha_refuses(tmp_path):
    sess = _FakeSession(REAL_BYTES)
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache",
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=sess,
        )
    # And no download was attempted for an unpublished platform.
    assert sess.calls == []


def test_fetch_http_error_raises(tmp_path):
    sess = _FakeSession(b"", status=404)
    with pytest.raises(RuntimeError, match="download failed"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache",
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, REAL_SHA),
            session=sess,
        )


def test_fetch_base_url_override(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(REAL_BYTES)
    fetchmod.fetch_binary(
        out_dir=out,
        base_url="https://mirror.example/dir/",
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
    )
    assert sess.calls == ["https://mirror.example/dir/email-agent-darwin-arm64"]


def test_default_cache_dir():
    p = fetchmod.default_cache_dir()
    assert p.parts[-3:] == (".gaia", "agents", "email")


# ---------------------------------------------------------------------------
# Hub-installed binary short-circuit (#2095): a checksum-verified install must
# win over the in-repo placeholder-SHA lock, before the lock is ever consulted.
# ---------------------------------------------------------------------------

PLACEHOLDER = "PENDING-1648-replace-with-real-sha256"


def _install_binary(
    tmp_path,
    monkeypatch,
    *,
    sha=REAL_SHA,
    executable="email-agent",
    content=REAL_BYTES,
    kind=None,
) -> Path:
    """Prime a hub-installed email binary + .installed sentinel under tmp_path."""
    from gaia.hub import installer

    root = tmp_path / "install_root"
    monkeypatch.setattr(installer, "default_install_root", lambda: root)
    install_dir = installer.agent_install_dir("email")
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / executable).write_bytes(content)
    installer._write_sentinel(
        "email",
        "0.4.0",
        "python",
        sha,
        install_dir,
        artifact_kind=kind or installer.ARTIFACT_KIND_BINARY,
        executable=executable,
    )
    return install_dir


def test_fetch_hub_installed_binary_short_circuits_placeholder_lock(
    tmp_path, monkeypatch
):
    install_dir = _install_binary(tmp_path, monkeypatch)
    sess = _FakeSession(b"SHOULD-NOT-BE-USED")
    res = fetchmod.fetch_binary(
        out_dir=tmp_path / "cache",
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, PLACEHOLDER),  # placeholder would else refuse
        session=sess,
    )
    assert res.cached is True
    assert res.sha256 == REAL_SHA
    assert Path(res.binary_path) == install_dir / "email-agent"
    assert sess.calls == []  # never touched the network or the placeholder gate


def test_fetch_no_install_placeholder_lock_still_refuses(tmp_path, monkeypatch):
    from gaia.hub import installer

    # Empty install root -> no sentinel -> the placeholder gate must still fire.
    monkeypatch.setattr(
        installer, "default_install_root", lambda: tmp_path / "empty_root"
    )
    sess = _FakeSession(REAL_BYTES)
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache",
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, PLACEHOLDER),
            session=sess,
        )
    assert sess.calls == []


def test_fetch_hub_installed_binary_sha_mismatch_raises(tmp_path, monkeypatch):
    # On-disk bytes drift from the sentinel SHA -> loud refusal, never spawned.
    _install_binary(tmp_path, monkeypatch, content=b"tampered-on-disk")
    with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache",
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, PLACEHOLDER),
        )


def test_fetch_non_binary_install_falls_through_to_lock(tmp_path, monkeypatch):
    from gaia.hub import installer

    # A wheel-kind sentinel is not a spawnable binary -> the lock still governs.
    _install_binary(tmp_path, monkeypatch, kind=installer.ARTIFACT_KIND_WHEEL)
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache",
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, PLACEHOLDER),
        )
