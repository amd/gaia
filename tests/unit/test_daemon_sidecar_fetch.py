# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verified binary fetch — the SHA-256 security boundary for daemon-supervised
sidecar agents (ported from test_email_sidecar_fetch.py, issue #2142 T3)."""

import hashlib
import json
from pathlib import Path

import pytest

from gaia.daemon.sidecars import fetch as fetchmod
from gaia.daemon.sidecars.errors import IntegrityError, PlatformError

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
# Hub-installed binary short-circuit (#2095)
# ---------------------------------------------------------------------------


def _hub_install(cache: Path, data: bytes = REAL_BYTES, **overrides) -> Path:
    """Simulate a Hub install (#2086): verified binary + .installed sentinel."""
    cache.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "id": "email",
        "version": "0.5.0",
        "language": "python",
        "installed_at": "2026-07-15T00:00:00+00:00",
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "artifact_kind": "binary",
        "executable": "email-agent",
        **overrides,
    }
    binary = cache / (sentinel["executable"] or "email-agent")
    binary.write_bytes(data)
    (cache / ".installed").write_text(json.dumps(sentinel))
    return binary


def test_hub_installed_binary_spawns_despite_placeholder_lock(tmp_path):
    out = tmp_path / "email"
    binary = _hub_install(out)
    sess = _FakeSession(b"SHOULD-NOT-BE-USED")
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, "PENDING-1648-replace"),
        session=sess,
    )
    assert res.cached is True
    assert Path(res.binary_path) == binary
    assert res.sha256 == REAL_SHA
    assert sess.calls == []


def test_hub_installed_short_circuit_precedes_lock(tmp_path):
    # Pin the ordering: a verified Hub install is returned BEFORE the lock is
    # consulted at all — an unreadable lock must not block the spawn.
    out = tmp_path / "email"
    binary = _hub_install(out)
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=tmp_path / "does-not-exist.lock.json",
        session=_FakeSession(b"SHOULD-NOT-BE-USED"),
    )
    assert Path(res.binary_path) == binary


def test_placeholder_still_refuses_without_hub_install(tmp_path):
    out = tmp_path / "email"
    out.mkdir()
    with pytest.raises(PlatformError, match="binaries.lock.json.*placeholder"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_hub_installed_binary_tampered_raises(tmp_path):
    out = tmp_path / "email"
    _hub_install(out)
    (out / "email-agent").write_bytes(b"tampered-after-install")
    with pytest.raises(IntegrityError, match="reinstall"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_hub_sentinel_without_binary_kind_is_ignored(tmp_path):
    # A wheel-kind sentinel (pre-#2084 email install) vouches for no binary.
    out = tmp_path / "email"
    _hub_install(out, artifact_kind="wheel", executable="")
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_hub_sentinel_missing_binary_file_falls_through(tmp_path):
    out = tmp_path / "email"
    _hub_install(out)
    (out / "email-agent").unlink()
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_force_refetches_past_hub_install(tmp_path):
    # force=True is an explicit "re-fetch from the lock" — the short-circuit
    # must not mask it. With real lock SHAs that means a fresh download.
    out = tmp_path / "email"
    _hub_install(out, artifact_sha256="0" * 64)
    sess = _FakeSession(REAL_BYTES)
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
        force=True,
    )
    assert res.cached is False
    assert sess.calls == ["https://r2.example/email-agent-darwin-arm64"]
