# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Caller authentication on the flagship sidecar's local REST API.

This sidecar binds loopback and drives an agent with shell and file tools plus a
bypass-permissions mode, so "loopback only" is not access control. These tests
pin the three controls that make the port safe to open:

1. **Per-session bearer token** — a non-exempt request without a valid token is
   401; with the right token it reaches the route; with a wrong one it is 401.
2. **Host allowlist** — a non-loopback ``Host`` is 400. This is the control that
   defeats DNS rebinding, where the browser resolves ``evil.com`` → 127.0.0.1
   and the token is not in play at all.
3. **Origin rejection** — a non-loopback browser ``Origin`` is 403, which stops
   a drive-by page from ``fetch``-ing the port directly.

Requests go through the real ``build_app()`` over real HTTP via ``TestClient``,
not a mocked dependency: a stub would prove the check was *called*, not that an
unauthenticated request is actually refused by the app the binary serves.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402

_TOKEN = "s3cret-session-token"
_BASE_URL = "http://127.0.0.1:8141"

# A syntactically valid /query body. These tests never let a request reach the
# agent loop — every one is rejected by the auth layer first, or (the positive
# case) is asserted only to have got *past* auth.
_QUERY_BODY = {
    "query": "hello",
    "run_id": "0f9c2b6e-2c4a-4b1e-9d6a-1e2f3a4b5c6d",
    "context": [],
}


@pytest.fixture(autouse=True)
def _isolate_auth():
    """Each test installs its own policy; never inherit one."""
    caller_auth.reset()
    yield
    caller_auth.reset()


def _client(monkeypatch, *, token: str | None) -> TestClient:
    """Build the real sidecar app with (or without) a token in the environment."""
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    if token is None:
        monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(caller_auth.TOKEN_ENV_VAR, token)
    from gaia_agent.server import build_app

    return TestClient(build_app(), base_url=_BASE_URL)


# -- 1. bearer token ---------------------------------------------------------


def test_query_without_a_token_is_rejected(monkeypatch):
    """The hole this closes: an unauthenticated POST driving shell/file tools."""
    client = _client(monkeypatch, token=_TOKEN)
    r = client.post("/v1/gaia/query", json=_QUERY_BODY)
    assert r.status_code == 401, r.text
    assert "Authorization: Bearer" in r.json()["detail"]


def test_query_with_a_wrong_token_is_rejected(monkeypatch):
    client = _client(monkeypatch, token=_TOKEN)
    r = client.post(
        "/v1/gaia/query",
        json=_QUERY_BODY,
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert r.status_code == 401, r.text


def test_a_malformed_authorization_header_is_rejected(monkeypatch):
    """Basic-auth or a bare token must not satisfy a Bearer requirement."""
    client = _client(monkeypatch, token=_TOKEN)
    for header in ("Basic " + _TOKEN, _TOKEN, "Bearer", "Bearer   "):
        r = client.post(
            "/v1/gaia/query", json=_QUERY_BODY, headers={"Authorization": header}
        )
        assert r.status_code == 401, f"{header!r} was accepted: {r.text}"


def test_the_correct_token_gets_past_auth(monkeypatch):
    """The positive case — proves the gate is not simply refusing everything.

    The run then fails on its own terms (no Lemonade in a unit env); all that is
    asserted here is that it was NOT turned away at the door.
    """
    client = _client(monkeypatch, token=_TOKEN)
    r = client.post(
        "/v1/gaia/query",
        json=_QUERY_BODY,
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code != 401, r.text


def test_cancel_and_respond_are_also_gated(monkeypatch):
    """Every route on the router, not just /query — cancel drives a live run."""
    client = _client(monkeypatch, token=_TOKEN)
    run = _QUERY_BODY["run_id"]
    assert client.post(f"/v1/gaia/query/{run}/cancel").status_code == 401
    assert (
        client.post(
            f"/v1/gaia/query/{run}/respond",
            json={"request_id": "x", "response": "yes"},
        ).status_code
        == 401
    )


# -- 2. Host allowlist (DNS rebinding) ---------------------------------------


def test_a_rebound_host_is_rejected(monkeypatch):
    """DNS rebinding: the browser resolves evil.com to 127.0.0.1 and posts here.

    The token cannot help — the attacker never needs to read it — so this must be
    refused on the Host header alone.
    """
    client = _client(monkeypatch, token=_TOKEN)
    r = client.post(
        "/v1/gaia/query",
        json=_QUERY_BODY,
        headers={"Host": "evil.com", "Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 400, r.text
    assert "rebinding" in r.json()["detail"].lower()


def test_host_check_also_covers_the_exempt_probes(monkeypatch):
    """/health skips the token, but must not skip rebinding protection."""
    client = _client(monkeypatch, token=_TOKEN)
    assert client.get("/health", headers={"Host": "evil.com"}).status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8141", "localhost:8141", "[::1]:8141"])
def test_loopback_hosts_are_accepted(monkeypatch, host):
    """Including the IPv6 literal form, which the port-stripping must handle."""
    client = _client(monkeypatch, token=_TOKEN)
    r = client.get("/health", headers={"Host": host})
    assert r.status_code == 200, f"{host} rejected: {r.text}"


# -- 3. Origin rejection (drive-by page) -------------------------------------


def test_a_cross_origin_browser_request_is_rejected(monkeypatch):
    client = _client(monkeypatch, token=_TOKEN)
    r = client.post(
        "/v1/gaia/query",
        json=_QUERY_BODY,
        headers={"Origin": "https://evil.com", "Authorization": f"Bearer {_TOKEN}"},
    )
    assert r.status_code == 403, r.text


def test_a_loopback_origin_is_allowed(monkeypatch):
    """The Agent UI legitimately calls from a loopback origin."""
    client = _client(monkeypatch, token=_TOKEN)
    r = client.get("/health", headers={"Origin": "http://localhost:5174"})
    assert r.status_code == 200, r.text


# -- dev mode: no token configured -------------------------------------------


def test_without_a_configured_token_requests_pass_but_browsers_still_cannot(
    monkeypatch,
):
    """A hand-run dev sidecar stays usable, and still refuses a web page.

    This is the deliberate trade in the shared module: the token check goes off
    (and is warned about at startup), the transport controls do not.
    """
    client = _client(monkeypatch, token=None)
    assert client.get("/health").status_code == 200
    assert client.post("/v1/gaia/query", json=_QUERY_BODY).status_code != 401
    assert (
        client.get("/health", headers={"Origin": "https://evil.com"}).status_code == 403
    )
    assert client.get("/health", headers={"Host": "evil.com"}).status_code == 400


# -- exempt paths ------------------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/version", "/v1/gaia/version"])
def test_probe_paths_do_not_require_a_token(monkeypatch, path):
    """The attach handshake polls these before it can know the token."""
    client = _client(monkeypatch, token=_TOKEN)
    assert client.get(path).status_code == 200, path
