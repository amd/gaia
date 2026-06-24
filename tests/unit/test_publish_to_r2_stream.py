# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for hub/agents/python/email/packaging/publish_to_r2.py.

Focuses on the call-validity contract at the HTTP boundary:
  - package (platform_key == "package") → application/octet-stream with X-Gaia-* headers,
    body via data= (streaming, NOT files=)
  - non-package binaries → multipart/form-data via files=, no X-Gaia-* headers
  - 201 with matching sha → success
  - 409 with matching remote sha → idempotent success
  - 409 with differing sha → SystemExit
  - non-201/409 → SystemExit
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Locate the module under test (not installed as a package).
# ---------------------------------------------------------------------------
_MODULE_PATH = (
    Path(__file__).parent.parent.parent
    / "hub"
    / "agents"
    / "python"
    / "email"
    / "packaging"
    / "publish_to_r2"
)
sys.path.insert(0, str(_MODULE_PATH.parent))
import publish_to_r2 as pub  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_MANIFEST_YAML = """\
id: email
name: Email
version: 0.1.0
description: "Email triage agent"
author: AMD
license: MIT
language: python
category: productivity
icon: mail
security_tier: verified
models: [Gemma-4-E4B-it-GGUF]
tags: [email, triage]
requirements:
  min_memory_gb: 8
  platforms: [win-x64, linux-x64, darwin-arm64]
interfaces:
  cli: true
"""

SAMPLE_FILES_JSON = json.dumps(
    {
        "files": [
            {"name": "binaries/email-agent-linux-x64", "size_bytes": 35_000_000},
            {"name": "README.md", "size_bytes": 13_000},
        ]
    }
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_zip(tmp_path: Path, content: bytes = b"fake-zip-bytes") -> Path:
    p = tmp_path / "agent-email-0.1.0.zip"
    p.write_bytes(content)
    return p


def _make_binary(
    tmp_path: Path, name: str = "email-agent-linux-x64", content: bytes = b"bin"
) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _make_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "gaia-agent.yaml"
    p.write_text(SAMPLE_MANIFEST_YAML, encoding="utf-8")
    return p


def _make_resp(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.text = json.dumps(body or {})[:500]
    return r


# ---------------------------------------------------------------------------
# publish_streaming — call validity
# ---------------------------------------------------------------------------


class TestPublishStreamingCallValidity:
    """Assert the exact shape of the HTTP call the streaming function makes."""

    def test_uses_octet_stream_content_type(self, tmp_path):
        """application/octet-stream with X-Gaia-* headers, not multipart."""
        zip_path = _make_zip(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}
        zip_sha = _sha256(zip_path.read_bytes())

        fake_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": zip_sha}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=fake_resp) as mock_post:
            pub.publish_streaming(
                "https://hub.example.com",
                manifest_path,
                manifest,
                zip_path,
                "package",
                "tok_test",
            )

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        headers = kwargs["headers"]

        # Must be octet-stream, NOT multipart.
        assert headers["content-type"] == "application/octet-stream"
        # Required X-Gaia-* headers must be present.
        assert "x-gaia-manifest" in headers
        assert "x-gaia-filename" in headers
        assert headers["x-gaia-filename"] == zip_path.name
        assert "x-gaia-sha256" in headers
        assert headers["x-gaia-sha256"] == zip_sha
        # Content-Length must match file size.
        assert headers["content-length"] == str(zip_path.stat().st_size)
        # Body must be via data= (file handle for streaming), NOT files=.
        assert "data" in kwargs, "expected data= kwarg for streaming"
        assert "files" not in kwargs, "must NOT use files= on the streaming path"

    def test_includes_package_files_header_when_provided(self, tmp_path):
        """X-Gaia-Package-Files header present when package_files_bytes passed."""
        zip_path = _make_zip(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}
        zip_sha = _sha256(zip_path.read_bytes())

        fake_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": zip_sha}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=fake_resp) as mock_post:
            pub.publish_streaming(
                "https://hub.example.com",
                manifest_path,
                manifest,
                zip_path,
                "package",
                "tok_test",
                package_files_bytes=SAMPLE_FILES_JSON.encode(),
            )

        _, kwargs = mock_post.call_args
        assert "x-gaia-package-files" in kwargs["headers"]

    def test_omits_package_files_header_when_not_provided(self, tmp_path):
        """X-Gaia-Package-Files absent when package_files_bytes not passed."""
        zip_path = _make_zip(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}
        zip_sha = _sha256(zip_path.read_bytes())

        fake_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": zip_sha}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=fake_resp) as mock_post:
            pub.publish_streaming(
                "https://hub.example.com",
                manifest_path,
                manifest,
                zip_path,
                "package",
                "tok_test",
            )

        _, kwargs = mock_post.call_args
        assert "x-gaia-package-files" not in kwargs["headers"]

    def test_201_matching_sha_returns_summary(self, tmp_path):
        """201 with matching server sha → returns the summary dict."""
        zip_path = _make_zip(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}
        zip_sha = _sha256(zip_path.read_bytes())

        fake_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": zip_sha}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=fake_resp):
            result = pub.publish_streaming(
                "https://hub.example.com",
                manifest_path,
                manifest,
                zip_path,
                "package",
                "tok_test",
            )

        assert result["platform"] == "package"
        assert result["filename"] == zip_path.name
        assert result["sha256"] == zip_sha

    def test_201_mismatched_sha_raises(self, tmp_path):
        """201 with non-matching server sha → SystemExit (integrity failure)."""
        zip_path = _make_zip(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}

        fake_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": "a" * 64}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=fake_resp):
            with pytest.raises(SystemExit, match="integrity check FAILED"):
                pub.publish_streaming(
                    "https://hub.example.com",
                    manifest_path,
                    manifest,
                    zip_path,
                    "package",
                    "tok_test",
                )

    def test_409_matching_remote_is_idempotent_success(self, tmp_path):
        """409 with matching remote sha → idempotent no-op, returns summary."""
        content = b"zip-bytes"
        zip_path = _make_zip(tmp_path, content)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}
        zip_sha = _sha256(content)

        fake_post_resp = _make_resp(409)
        fake_get_resp = MagicMock()
        fake_get_resp.status_code = 200
        fake_get_resp.content = content

        with patch("requests.post", return_value=fake_post_resp):
            with patch("requests.get", return_value=fake_get_resp):
                result = pub.publish_streaming(
                    "https://hub.example.com",
                    manifest_path,
                    manifest,
                    zip_path,
                    "package",
                    "tok_test",
                )

        assert result["sha256"] == zip_sha

    def test_409_differing_remote_raises(self, tmp_path):
        """409 with different remote sha → SystemExit (immutability violation)."""
        zip_path = _make_zip(tmp_path, b"local-bytes")
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}

        fake_post_resp = _make_resp(409)
        fake_get_resp = MagicMock()
        fake_get_resp.status_code = 200
        fake_get_resp.content = b"different-remote-bytes"

        with patch("requests.post", return_value=fake_post_resp):
            with patch("requests.get", return_value=fake_get_resp):
                with pytest.raises(SystemExit, match="DIFFERENT sha256"):
                    pub.publish_streaming(
                        "https://hub.example.com",
                        manifest_path,
                        manifest,
                        zip_path,
                        "package",
                        "tok_test",
                    )

    def test_500_raises_systemexit(self, tmp_path):
        """Non-201/409 response → SystemExit with actionable message."""
        zip_path = _make_zip(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}

        fake_resp = _make_resp(500)
        fake_resp.text = "Internal Server Error"
        with patch("requests.post", return_value=fake_resp):
            with pytest.raises(SystemExit, match="HTTP 500"):
                pub.publish_streaming(
                    "https://hub.example.com",
                    manifest_path,
                    manifest,
                    zip_path,
                    "package",
                    "tok_test",
                )


# ---------------------------------------------------------------------------
# publish_one — regression: non-package binary stays multipart
# ---------------------------------------------------------------------------


class TestPublishOneBinaryStaysMultipart:
    """The multipart path must be untouched for per-platform binaries."""

    def test_binary_uses_multipart_files_kwarg(self, tmp_path):
        """Per-platform binary uses files= (multipart), no X-Gaia-* headers."""
        binary = _make_binary(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        manifest = {"id": "email", "version": "0.1.0"}
        bin_sha = _sha256(binary.read_bytes())

        fake_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": bin_sha}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=fake_resp) as mock_post:
            pub.publish_one(
                "https://hub.example.com",
                manifest_path,
                manifest,
                binary,
                "linux-x64",
                "tok_test",
            )

        _, kwargs = mock_post.call_args
        # Must use files= (multipart), not data=.
        assert "files" in kwargs, "expected files= kwarg for multipart path"
        assert "data" not in kwargs, "must NOT use data= on the multipart path"
        # Must NOT have X-Gaia-* headers.
        headers = kwargs.get("headers", {})
        for key in headers:
            assert not key.lower().startswith(
                "x-gaia-"
            ), f"Unexpected X-Gaia-* header on multipart path: {key}"


# ---------------------------------------------------------------------------
# main() routing: package → streaming, binary → multipart
# ---------------------------------------------------------------------------


class TestMainRouting:
    """Integration-level test: main() routes zip to streaming, binary to multipart."""

    def _run_main(self, argv: list[str], post_resp, get_resp=None) -> None:
        with patch("requests.post", return_value=post_resp) as mock_post:
            if get_resp is not None:
                with patch("requests.get", return_value=get_resp):
                    with patch.dict(
                        "os.environ", {"AGENT_HUB_PUBLISH_TOKEN": "tok_test"}
                    ):
                        pub.main(argv)
            else:
                with patch.dict("os.environ", {"AGENT_HUB_PUBLISH_TOKEN": "tok_test"}):
                    pub.main(argv)
        return mock_post

    def test_zip_routes_to_streaming(self, tmp_path):
        zip_path = _make_zip(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        zip_sha = _sha256(zip_path.read_bytes())

        post_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": zip_sha}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=post_resp) as mock_post:
            with patch.dict("os.environ", {"AGENT_HUB_PUBLISH_TOKEN": "tok_test"}):
                pub.main(
                    [
                        "--base-url",
                        "https://hub.example.com",
                        "--manifest",
                        str(manifest_path),
                        "--artifact",
                        f"{zip_path}=package",
                    ]
                )

        _, kwargs = mock_post.call_args
        # Zip → streaming path → data=, NOT files=.
        assert "data" in kwargs
        assert "files" not in kwargs

    def test_binary_routes_to_multipart(self, tmp_path):
        binary = _make_binary(tmp_path)
        manifest_path = _make_manifest(tmp_path)
        bin_sha = _sha256(binary.read_bytes())

        post_resp = _make_resp(
            201,
            {"published": {"artifact": {"sha256": bin_sha}, "version_artifacts": 1}},
        )
        with patch("requests.post", return_value=post_resp) as mock_post:
            with patch.dict("os.environ", {"AGENT_HUB_PUBLISH_TOKEN": "tok_test"}):
                pub.main(
                    [
                        "--base-url",
                        "https://hub.example.com",
                        "--manifest",
                        str(manifest_path),
                        "--artifact",
                        f"{binary}=linux-x64",
                    ]
                )

        _, kwargs = mock_post.call_args
        # Binary → multipart path → files=, NOT data=.
        assert "files" in kwargs
        assert "data" not in kwargs
