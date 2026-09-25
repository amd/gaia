# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_doc_links.py.

Covers the classification bug behind #2925: a GitHub anti-abuse connection
reset raises a raw ``http.client.RemoteDisconnected`` (not wrapped in
``urllib.error.URLError``), which used to fall through to the catch-all
``except Exception`` branch and get reported as "broken" instead of
"warning" (unverified). Also covers the retry wrapper and the github.com
issue/PR -> REST API rewrite added to reduce how often that reset happens
at all.
"""

import http.client
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_doc_links as cdl  # noqa: E402


def test_remote_disconnected_is_warning_not_broken():
    """The exact bug from #2925: RemoteDisconnected must not be 'broken'."""
    with patch(
        "urllib.request.urlopen",
        side_effect=http.client.RemoteDisconnected(
            "Remote end closed connection without response"
        ),
    ):
        status, detail = cdl.check_external_link("https://example.org/page")
    assert status == "warning"
    assert "connection error" in detail


def test_generic_os_error_is_warning():
    with patch("urllib.request.urlopen", side_effect=ConnectionAbortedError("reset")):
        status, _ = cdl.check_external_link("https://example.org/page")
    assert status == "warning"


def test_real_404_is_still_broken():
    err = urllib.error.HTTPError(
        "https://example.org/gone", 404, "Not Found", hdrs=None, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        status, detail = cdl.check_external_link("https://example.org/gone")
    assert status == "broken"
    assert "404" in detail


def test_unrelated_exception_stays_broken():
    """The catch-all must still fire for genuinely unexpected errors."""
    with patch("urllib.request.urlopen", side_effect=ValueError("bad url")):
        status, _ = cdl.check_external_link("https://example.org/page")
    assert status == "broken"


def test_github_issue_url_rewritten_to_api():
    assert (
        cdl.github_api_equivalent("https://github.com/amd/gaia/issues/888")
        == "https://api.github.com/repos/amd/gaia/issues/888"
    )
    assert (
        cdl.github_api_equivalent("https://github.com/amd/gaia/pull/1642")
        == "https://api.github.com/repos/amd/gaia/issues/1642"
    )


def test_non_github_url_not_rewritten():
    assert cdl.github_api_equivalent("https://example.org/issues/1") is None
    assert cdl.github_api_equivalent("https://github.com/amd/gaia") is None


def test_github_token_used_when_present(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-value")
    headers = cdl._request_headers("https://api.github.com/repos/amd/gaia/issues/1")
    assert headers["Authorization"] == "Bearer test-token-value"


def test_no_github_token_no_auth_header(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    headers = cdl._request_headers("https://api.github.com/repos/amd/gaia/issues/1")
    assert "Authorization" not in headers


def test_retry_gives_up_after_max_attempts_and_stays_warning(monkeypatch):
    monkeypatch.setattr(cdl.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always_warning(url, timeout=15):
        calls["n"] += 1
        return "warning", "timeout"

    monkeypatch.setattr(cdl, "check_external_link", always_warning)
    status, detail = cdl.check_external_link_with_retries(
        "https://example.org/page", retries=2, backoff=0
    )
    assert status == "warning"
    assert calls["n"] == 3  # initial attempt + 2 retries
    assert "2 retry" in detail


def test_retry_stops_early_on_success(monkeypatch):
    monkeypatch.setattr(cdl.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def warning_then_ok(url, timeout=15):
        calls["n"] += 1
        if calls["n"] < 2:
            return "warning", "timeout"
        return "ok", "HTTP 200"

    monkeypatch.setattr(cdl, "check_external_link", warning_then_ok)
    status, _ = cdl.check_external_link_with_retries(
        "https://example.org/page", retries=2, backoff=0
    )
    assert status == "ok"
    assert calls["n"] == 2


def test_retry_never_escalates_broken(monkeypatch):
    """A genuine 404 must not be retried into something else, or delayed."""
    calls = {"n": 0}

    def broken(url, timeout=15):
        calls["n"] += 1
        return "broken", "HTTP 404"

    monkeypatch.setattr(cdl, "check_external_link", broken)
    status, _ = cdl.check_external_link_with_retries(
        "https://example.org/gone", retries=2, backoff=0
    )
    assert status == "broken"
    assert calls["n"] == 1  # no retries spent on a real failure


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
