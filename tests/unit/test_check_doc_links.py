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
import json
import re
import ssl
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


def test_request_timeout_408_is_warning_and_retried(monkeypatch):
    """shields.io answers 408 under CI load; that is a blip, not a dead badge."""
    err = urllib.error.HTTPError(
        "https://img.shields.io/x.svg", 408, "Request Timeout", hdrs=None, fp=None
    )
    monkeypatch.setattr(cdl.time, "sleep", lambda s: None)
    with patch("urllib.request.urlopen", side_effect=err) as urlopen:
        status, detail = cdl.check_external_link_with_retries(
            "https://img.shields.io/x.svg"
        )
    assert status == "warning"
    assert "408" in detail
    assert urlopen.call_count == 1 + cdl.EXTERNAL_LINK_RETRIES


def _cert_error(message):
    err = ssl.SSLCertVerificationError(
        1, f"[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {message}"
    )
    err.verify_message = message
    return urllib.error.URLError(err)


def test_an_expired_site_certificate_is_unverified_not_broken():
    """cmake.org let its certificate lapse; the link itself was still right."""
    with patch(
        "urllib.request.urlopen", side_effect=_cert_error("certificate has expired")
    ):
        status, detail = cdl.check_external_link("https://cmake.org/download/")
    assert status == "warning"
    assert "expired" in detail


@pytest.mark.parametrize(
    "message",
    [
        "Hostname mismatch, certificate is not valid for 'example.org'",
        "self-signed certificate",
    ],
)
def test_other_certificate_failures_stay_broken(message):
    """A wrong host or a self-signed certificate can mean the link points somewhere wrong."""
    with patch("urllib.request.urlopen", side_effect=_cert_error(message)):
        status, _ = cdl.check_external_link("https://example.org/page")
    assert status == "broken"


# ── batched GitHub issue/PR lookups ──────────────────────────────────────────
# One REST call per issue/PR link spent the workflow token's shared hourly
# quota (~1,760 calls a run), failing unrelated jobs across the repo.


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, *args):
        return self._body

    def getcode(self):
        return 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _GraphQL:
    """A fake urlopen: answers GraphQL from ``existing``, records every call."""

    def __init__(self, existing, repos=None, fail=None):
        self.existing = existing
        self.repos = repos
        self.fail = fail
        self.graphql = []
        self.rest = []

    def __call__(self, req, timeout=None):
        if req.full_url != cdl.GITHUB_GRAPHQL_URL:
            self.rest.append(req.full_url)
            return _Resp(b"{}")
        body = json.loads(req.data)
        self.graphql.append(body)
        if isinstance(self.fail, BaseException):
            raise self.fail
        if self.fail is not None:
            return _Resp(json.dumps(self.fail).encode())
        key = (body["variables"]["owner"], body["variables"]["name"])
        if self.repos is not None and key not in self.repos:
            payload = {
                "data": {"repository": None},
                "errors": [{"type": "NOT_FOUND", "message": "no repo"}],
            }
            return _Resp(json.dumps(payload).encode())
        repo, errors = {}, []
        for alias, number in re.findall(
            r"(n\d+): issueOrPullRequest\(number: (\d+)\)", body["query"]
        ):
            if int(number) in self.existing:
                repo[alias] = {"__typename": "Issue"}
            else:
                repo[alias] = None
                errors.append({"type": "NOT_FOUND", "path": ["repository", alias]})
        payload = {"data": {"repository": repo}}
        if errors:
            payload["errors"] = errors
        return _Resp(json.dumps(payload).encode())


def _gaia(n, suffix=""):
    return f"https://github.com/amd/gaia/issues/{n}{suffix}"


OK = ("ok", "exists (GitHub GraphQL)")


def test_issue_links_are_checked_in_batches_not_one_call_each():
    urls = [_gaia(n) for n in range(1, 251)]
    urls.append("https://github.com/other/repo/pull/7")
    fake = _GraphQL(existing=set(range(1, 251)))
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs(urls, "tok")
    assert len(fake.graphql) == 3 + 1  # 100+100+50 for amd/gaia, 1 for other/repo
    assert fake.rest == []
    assert answers == {u: OK for u in urls}


def test_fragments_of_one_number_share_one_lookup():
    urls = [_gaia(5), _gaia(5, "#issuecomment-1"), "https://github.com/amd/gaia/pull/5"]
    fake = _GraphQL(existing={5})
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs(urls, "tok")
    assert len(fake.graphql) == 1
    assert fake.graphql[0]["query"].count("issueOrPullRequest") == 1
    assert answers == {u: OK for u in urls}


def test_a_missing_number_is_left_for_the_rest_check():
    fake = _GraphQL(existing={1})
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs([_gaia(1), _gaia(999999)], "tok")
    assert answers == {_gaia(1): OK}


def test_an_inaccessible_repo_is_left_for_the_rest_check():
    fake = _GraphQL(existing={3}, repos={("amd", "gaia")})
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs(
            ["https://github.com/private/thing/issues/3"], "tok"
        )
    assert answers == {}


def test_a_rate_limited_batch_warns_and_never_falls_back_to_rest():
    err = urllib.error.HTTPError(
        cdl.GITHUB_GRAPHQL_URL, 403, "Forbidden", hdrs=None, fp=None
    )
    urls = [_gaia(n) for n in range(1, 151)]
    fake = _GraphQL(existing=set(), fail=err)
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs(urls, "tok")
    assert set(answers) == set(urls)
    assert {status for status, _ in answers.values()} == {"warning"}
    assert "HTTP 403" in answers[urls[0]][1]
    assert fake.rest == []


def test_a_graphql_level_rate_limit_is_a_warning_not_ok():
    body = {"data": None, "errors": [{"type": "RATE_LIMITED", "message": "slow down"}]}
    fake = _GraphQL(existing={1}, fail=body)
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs([_gaia(1)], "tok")
    assert answers[_gaia(1)][0] == "warning"
    assert "slow down" in answers[_gaia(1)][1]


def test_a_non_not_found_error_beside_data_is_not_trusted():
    body = {
        "data": {"repository": {"n1": {"__typename": "Issue"}}},
        "errors": [{"type": "SERVICE_UNAVAILABLE", "message": "partial"}],
    }
    fake = _GraphQL(existing={1}, fail=body)
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs([_gaia(1)], "tok")
    assert answers[_gaia(1)][0] == "warning"


def test_a_network_error_on_a_batch_is_a_warning():
    fake = _GraphQL(existing=set(), fail=ConnectionResetError("reset"))
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs([_gaia(1)], "tok")
    assert answers[_gaia(1)][0] == "warning"


def test_no_token_means_no_batch_and_no_network():
    def boom(*args, **kwargs):
        raise AssertionError("no network without a token")

    with patch("urllib.request.urlopen", boom):
        assert cdl.prefetch_github_refs([_gaia(1)], None) == {}
        assert cdl.prefetch_github_refs([_gaia(1)], "") == {}


def test_owner_and_repo_travel_as_variables_not_query_text():
    fake = _GraphQL(existing={1})
    with patch("urllib.request.urlopen", fake):
        cdl.prefetch_github_refs(['https://github.com/a"b/c){x}/issues/1'], "tok")
    sent = fake.graphql[0]
    assert sent["variables"] == {"owner": 'a"b', "name": "c){x}"}
    assert 'a"b' not in sent["query"] and "c){x}" not in sent["query"]


def test_an_out_of_range_number_does_not_poison_its_batch():
    fake = _GraphQL(existing={1})
    with patch("urllib.request.urlopen", fake):
        answers = cdl.prefetch_github_refs([_gaia(1), _gaia(2**31)], "tok")
    assert answers == {_gaia(1): OK}
    assert str(2**31) not in fake.graphql[0]["query"]


def test_non_github_links_are_ignored_by_the_batch():
    fake = _GraphQL(existing=set())
    with patch("urllib.request.urlopen", fake):
        assert cdl.prefetch_github_refs(["https://example.org/issues/1"], "t") == {}
    assert fake.graphql == []


def test_check_links_sends_only_unresolved_links_to_rest(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(
        f"[x]({_gaia(1)}) [y]({_gaia(2)}) [z]({_gaia(424242)}) "
        "[w](https://example.org/page)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(cdl.time, "sleep", lambda *_: None)
    fake = _GraphQL(existing={1, 2})
    with patch("urllib.request.urlopen", fake):
        results = cdl.check_links(str(tmp_path), external_only=True, max_workers=2)
    assert len(fake.graphql) == 1
    assert sorted(fake.rest) == [
        "https://api.github.com/repos/amd/gaia/issues/424242",
        "https://example.org/page",
    ]
    by_url = {r.url: r for r in results}
    assert by_url[_gaia(1)].status == by_url[_gaia(2)].status == "ok"
    assert len(results) == 4


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
