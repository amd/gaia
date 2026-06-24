# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the R2 publisher (packaging/publish_to_r2.py).

Covers the issue #1848 split: the whole-package ``.zip`` is STREAMED as a raw
``application/octet-stream`` body with metadata in ``x-gaia-*`` headers (so neither
the client nor the Cloudflare Worker buffers the 177 MB file), while per-platform
binaries keep using the proven buffered multipart path. Also guards the chunked
SHA-256 and the fail-loud integrity check.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest

# publish_to_r2.py is a packaging script, not part of the gaia_agent_email
# package — load it by path (same pattern as test_gen_binaries_lock.py).
_PUB_PATH = Path(__file__).resolve().parents[1] / "packaging" / "publish_to_r2.py"
_spec = importlib.util.spec_from_file_location("publish_to_r2", _PUB_PATH)
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)


class _FakeResponse:
    def __init__(self, status_code: int, sha256: str):
        self.status_code = status_code
        self._sha256 = sha256
        self.text = ""

    def json(self) -> dict:
        return {
            "published": {"artifact": {"sha256": self._sha256}, "version_artifacts": 1}
        }


def _capture_post(monkeypatch, *, server_sha: str):
    """Patch requests.post to record the outgoing call and return a 201.

    When a file handle is streamed via ``data=``, read it inside the patch (as
    real requests would) so the handle is consumed before the ``with`` closes.
    """
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        data = kwargs.get("data")
        streamed = data.read() if hasattr(data, "read") else data
        calls.append(
            {
                "url": url,
                "headers": kwargs.get("headers", {}),
                "files": kwargs.get("files"),
                "streamed": streamed,
            }
        )
        return _FakeResponse(201, server_sha)

    monkeypatch.setattr(pub.requests, "post", fake_post)
    return calls


def _manifest(tmp_path: Path) -> tuple[Path, dict]:
    text = "id: email\nversion: 0.2.1\n"
    p = tmp_path / "gaia-agent.yaml"
    p.write_bytes(text.encode("utf-8"))
    return p, {"id": "email", "version": "0.2.1"}


def test_sha256_file_is_chunked_and_correct(tmp_path: Path):
    data = b"x" * (3 * pub._CHUNK + 17)  # spans multiple chunks
    f = tmp_path / "blob.bin"
    f.write_bytes(data)
    sha, size = pub._sha256_file(f)
    assert sha == hashlib.sha256(data).hexdigest()
    assert size == len(data)


def test_zip_artifact_streams_with_octet_stream_headers(tmp_path, monkeypatch):
    manifest_path, manifest = _manifest(tmp_path)
    zip_bytes = b"WHOLE-PACKAGE-ZIP-BYTES" * 100
    zip_path = tmp_path / "agent-email-0.2.1.zip"
    zip_path.write_bytes(zip_bytes)
    expected_sha = hashlib.sha256(zip_bytes).hexdigest()
    package_files = b'{"files":[{"name":"x","size_bytes":1}]}'

    calls = _capture_post(monkeypatch, server_sha=expected_sha)
    pub.publish_one(
        "https://hub.example",
        manifest_path,
        manifest,
        zip_path,
        "package",
        "tok",
        package_files_bytes=package_files,
    )

    assert len(calls) == 1
    call = calls[0]
    # Streamed as a raw body, NOT multipart.
    assert call["files"] is None
    assert call["streamed"] == zip_bytes
    h = call["headers"]
    assert h["content-type"] == "application/octet-stream"
    assert h["authorization"] == "Bearer tok"
    assert h["x-gaia-artifact-filename"] == "agent-email-0.2.1.zip"
    assert h["x-gaia-artifact-sha256"] == expected_sha
    # Manifest + package-files ride as base64 headers, decoding back exactly.
    assert base64.b64decode(h["x-gaia-manifest-b64"]) == manifest_path.read_bytes()
    assert base64.b64decode(h["x-gaia-package-files-b64"]) == package_files


def test_binary_artifact_stays_multipart(tmp_path, monkeypatch):
    manifest_path, manifest = _manifest(tmp_path)
    bin_bytes = b"linux-binary-bytes"
    bin_path = tmp_path / "email-agent-linux-x64"
    bin_path.write_bytes(bin_bytes)
    expected_sha = hashlib.sha256(bin_bytes).hexdigest()

    calls = _capture_post(monkeypatch, server_sha=expected_sha)
    pub.publish_one(
        "https://hub.example",
        manifest_path,
        manifest,
        bin_path,
        "linux-x64",
        "tok",
    )

    assert len(calls) == 1
    call = calls[0]
    # Multipart form, no streaming headers.
    assert call["files"] is not None
    assert "artifact" in call["files"]
    assert "x-gaia-artifact-sha256" not in call["headers"]
    assert "content-type" not in call["headers"]  # requests sets the multipart CT


def test_streaming_integrity_mismatch_fails_loudly(tmp_path, monkeypatch):
    manifest_path, manifest = _manifest(tmp_path)
    zip_path = tmp_path / "agent-email-0.2.1.zip"
    zip_path.write_bytes(b"ZIPBYTES")

    # Worker reports a DIFFERENT sha than we hold → must raise, never pass silently.
    _capture_post(monkeypatch, server_sha="deadbeef" * 8)
    with pytest.raises(SystemExit, match="integrity check FAILED"):
        pub.publish_one(
            "https://hub.example",
            manifest_path,
            manifest,
            zip_path,
            "package",
            "tok",
        )
