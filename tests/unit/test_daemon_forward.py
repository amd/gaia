# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit + HTTP-boundary spec for OAuth forward-out (issue #2154 / V2-14).

Covers:
  - ConnectionForwarder: forwards only GRANTED connectors, scopes the forward to
    the grant, skips ungranted/unconnected providers in the on-spawn push, and
    withdraws a stale forward when a mint fails (revocation path).
  - The /daemon/v1/agents/{id}/connections routes: ungranted forward is DENIED
    at the HTTP boundary (403); every route requires the client token.

Pure fakes — no keyring, no real sidecar, no subprocess.
"""

from __future__ import annotations

import pytest

from gaia.connectors.errors import AuthRequiredError
from gaia.daemon.forward import (
    ConnectionForwarder,
    ForwardDeliveryError,
    NotGrantedError,
)
from gaia.daemon.sidecars.spec import AgentSidecarSpec

_SPEC = AgentSidecarSpec(
    agent_id="email",
    service_id="gaia-agent-email",
    display_name="Email",
    expected_api_major="2",
    token_env_var="GAIA_EMAIL_SIDECAR_TOKEN",
    mode_env_var="GAIA_EMAIL_AGENT_MODE",
    cache_dir_name="email",
    grant_agent_id="installed:email",
    forward_providers=("google", "microsoft"),
    forwarded_mode_env_var="GAIA_EMAIL_FORWARDED_CREDENTIALS",
)


class _FakeResp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class _RecordingHTTP:
    """Records the POST/DELETE calls the forwarder makes to the sidecar."""

    def __init__(self, *, post_status=200, delete_status=200):
        self.posts = []
        self.deletes = []
        self._post_status = post_status
        self._delete_status = delete_status

    def post(self, url, *, json, headers, timeout):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResp(self._post_status)

    def delete(self, url, *, headers, timeout):
        self.deletes.append({"url": url, "headers": headers})
        return _FakeResp(self._delete_status)


def _forwarder(
    *,
    grants=None,
    connected=("google", "microsoft"),
    mint=None,
    http=None,
):
    grants = grants or {}
    http = http or _RecordingHTTP()

    def _list_grants(provider):
        return grants.get(provider, {})

    def _mint(*, provider, scopes, agent_id):
        if mint is not None:
            return mint(provider=provider, scopes=scopes, agent_id=agent_id)
        return (f"token-{provider}", 1_900_000_000.0)

    fwd = ConnectionForwarder(
        {"email": _SPEC},
        mint=_mint,
        list_grants=_list_grants,
        connected_providers=lambda: list(connected),
        http_post=http.post,
        http_delete=http.delete,
    )
    return fwd, http


# --- forward_provider -------------------------------------------------------


def test_forward_provider_forwards_granted_token_scoped_to_grant():
    fwd, http = _forwarder(
        grants={"google": {"installed:email": ["s1", "s2"]}},
    )
    result = fwd.forward_provider(
        "email", "google", base_url="http://127.0.0.1:9", bearer="ber"
    )
    assert result["forwarded"] is True
    assert result["scopes"] == ["s1", "s2"]
    assert len(http.posts) == 1
    post = http.posts[0]
    assert post["url"] == "http://127.0.0.1:9/v1/connections/google"
    assert post["headers"]["Authorization"] == "Bearer ber"
    # Token scoping: only the granted scopes are forwarded — never widened.
    assert post["json"]["scopes"] == ["s1", "s2"]
    assert post["json"]["access_token"] == "token-google"
    assert "refresh_token" not in post["json"]  # never forwarded
    assert "client_secret" not in post["json"]


def test_forward_provider_ungranted_raises_not_granted_and_posts_nothing():
    fwd, http = _forwarder(grants={})  # no grant for the email agent
    with pytest.raises(NotGrantedError) as exc:
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )
    assert "google" in str(exc.value)
    assert http.posts == []  # nothing forwarded when ungranted


def test_ungranted_error_is_headless_first_and_complete():
    """This is the FIRST error a cold headless box hits on `gaia email` (#2347),
    so it must lead with the CLI (connect + grant, matching scopes), point at
    where the OAuth-client setup surfaces, and only then mention the UI."""
    fwd, _ = _forwarder(grants={})
    with pytest.raises(NotGrantedError) as exc:
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )
    msg = str(exc.value)
    # One-flow connect+grant so the scopes can't drift (#2347).
    assert "gaia connectors connect google --scopes" in msg
    assert "--grant-agent installed:email" in msg
    # CLI leads; the UI is the fallback, not the headline.
    assert msg.index("gaia connectors connect") < msg.index("Settings -> Connections")


def test_forward_provider_unforwardable_provider_raises():
    fwd, _ = _forwarder(grants={"dropbox": {"installed:email": ["s"]}})
    with pytest.raises(NotGrantedError):
        fwd.forward_provider(
            "email", "dropbox", base_url="http://127.0.0.1:9", bearer="b"
        )


def test_forward_provider_mint_failure_withdraws_stale_forward_and_reraises():
    """Revocation path: the connection is gone, so the mint raises NOT_CONNECTED.
    The forwarder must re-raise loudly AND withdraw any stale token on the
    sidecar so it cannot keep operating on it."""

    def _mint(*, provider, scopes, agent_id):
        raise AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED, provider=provider
        )

    fwd, http = _forwarder(grants={"google": {"installed:email": ["s1"]}}, mint=_mint)
    with pytest.raises(AuthRequiredError):
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )
    # Stale forward withdrawn from the sidecar (DELETE), nothing newly POSTed.
    assert http.posts == []
    assert len(http.deletes) == 1
    assert http.deletes[0]["url"] == "http://127.0.0.1:9/v1/connections/google"


def test_forward_provider_delivery_failure_raises_forward_delivery_error():
    fwd, _ = _forwarder(
        grants={"google": {"installed:email": ["s1"]}},
        http=_RecordingHTTP(post_status=503),
    )
    with pytest.raises(ForwardDeliveryError) as exc:
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )
    assert "503" in str(exc.value)


# --- forward_all (on-spawn push) -------------------------------------------


def test_forward_all_forwards_granted_and_skips_ungranted_and_unconnected():
    fwd, http = _forwarder(
        grants={"google": {"installed:email": ["s1"]}},  # microsoft ungranted
        connected=("google",),  # microsoft not connected either
    )
    summary = fwd.forward_all("email", base_url="http://127.0.0.1:9", bearer="b")
    forwarded = {f["provider"] for f in summary["forwarded"]}
    skipped = {s["provider"]: s["reason"] for s in summary["skipped"]}
    assert forwarded == {"google"}
    assert skipped["microsoft"] == "not_granted"
    assert len(http.posts) == 1


def test_forward_all_skips_granted_but_unconnected_provider():
    fwd, http = _forwarder(
        grants={"google": {"installed:email": ["s1"]}},
        connected=(),  # granted but the mailbox is not connected
    )
    summary = fwd.forward_all("email", base_url="http://127.0.0.1:9", bearer="b")
    assert summary["forwarded"] == []
    assert {s["reason"] for s in summary["skipped"]} == {"not_granted", "not_connected"}
    assert http.posts == []


# --- withdraw ---------------------------------------------------------------


def test_running_connections_returns_only_running_with_base_url():
    """The re-forward timer (#2388) iterates this instead of the private manager
    map: only RUNNING sidecars that have a base_url are re-forwardable."""
    from gaia.daemon.sidecars.registry import SidecarRegistry

    class _Mgr:
        def __init__(self, running, base_url):
            self.is_running = running
            self.base_url = base_url
            self.auth_token = "bearer-x"

    reg = SidecarRegistry({"email": _SPEC})
    lock = __import__("threading").Lock()
    reg._managers = {
        "running": (_Mgr(True, "http://127.0.0.1:9"), lock),
        "stopped": (_Mgr(False, "http://127.0.0.1:10"), lock),
        "no_url": (_Mgr(True, None), lock),
    }
    conns = reg.running_connections()
    assert conns == [("running", "http://127.0.0.1:9", "bearer-x")]


def test_withdraw_deletes_from_sidecar():
    fwd, http = _forwarder(grants={"google": {"installed:email": ["s1"]}})
    result = fwd.withdraw("email", "google", base_url="http://127.0.0.1:9", bearer="b")
    assert result["withdrawn"] is True
    assert http.deletes[0]["url"] == "http://127.0.0.1:9/v1/connections/google"


def test_withdraw_tolerates_404_from_sidecar():
    fwd, _ = _forwarder(grants={}, http=_RecordingHTTP(delete_status=404))
    # 404 == nothing to withdraw == desired end state (idempotent).
    result = fwd.withdraw("email", "google", base_url="http://127.0.0.1:9", bearer="b")
    assert result["withdrawn"] is True


# --- HTTP boundary: /daemon/v1/agents/{id}/connections ---------------------


class _FakeRegistry:
    def __init__(self, forwarder):
        self._forwarder = forwarder

    def connection(self, agent_id):
        return "http://127.0.0.1:9", "sidecar-bearer"


def _routes_client(forwarder, token="secret-tok"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.daemon.connections_routes import build_connections_router

    app = FastAPI()
    app.include_router(
        build_connections_router(token, _FakeRegistry(forwarder), forwarder)
    )
    return TestClient(app, raise_server_exceptions=False)


def _auth(token="secret-tok"):
    return {"Authorization": f"Bearer {token}"}


def test_boundary_ungranted_forward_is_denied_403():
    fwd, _ = _forwarder(grants={})  # ungranted
    client = _routes_client(fwd)
    r = client.post(
        "/daemon/v1/agents/email/connections/google/forward", headers=_auth()
    )
    assert r.status_code == 403
    assert "google" in r.json()["detail"]


def test_boundary_granted_forward_succeeds_200():
    fwd, http = _forwarder(grants={"google": {"installed:email": ["s1"]}})
    client = _routes_client(fwd)
    r = client.post(
        "/daemon/v1/agents/email/connections/google/forward", headers=_auth()
    )
    assert r.status_code == 200
    assert r.json()["forwarded"] is True
    assert len(http.posts) == 1


def test_boundary_forward_all_ungranted_agent_maps_to_403():
    """forward_all raises NotGrantedError before its per-provider loop when the
    agent has no grant_agent_id; the route must map that to 403, not fall through
    to a 500."""
    spec = AgentSidecarSpec(
        agent_id="email",
        service_id="gaia-agent-email",
        display_name="Email",
        expected_api_major="2",
        token_env_var="GAIA_EMAIL_SIDECAR_TOKEN",
        mode_env_var="GAIA_EMAIL_AGENT_MODE",
        cache_dir_name="email",
        grant_agent_id="",  # no grant configured → NotGrantedError
        forward_providers=("google",),
        forwarded_mode_env_var="GAIA_EMAIL_FORWARDED_CREDENTIALS",
    )
    fwd = ConnectionForwarder({"email": spec})
    client = _routes_client(fwd)
    r = client.post("/daemon/v1/agents/email/connections/forward", headers=_auth())
    assert r.status_code == 403
    assert "grant_agent_id" in r.json()["detail"]


def test_boundary_delivery_failure_maps_to_502():
    fwd, _ = _forwarder(
        grants={"google": {"installed:email": ["s1"]}},
        http=_RecordingHTTP(post_status=500),
    )
    client = _routes_client(fwd)
    r = client.post(
        "/daemon/v1/agents/email/connections/google/forward", headers=_auth()
    )
    assert r.status_code == 502


@pytest.mark.parametrize(
    "method,url",
    [
        ("post", "/daemon/v1/agents/email/connections/forward"),
        ("post", "/daemon/v1/agents/email/connections/google/forward"),
        ("delete", "/daemon/v1/agents/email/connections/google"),
    ],
)
def test_boundary_all_routes_require_a_valid_token(method, url):
    fwd, _ = _forwarder(grants={})
    client = _routes_client(fwd)
    r = getattr(client, method)(url)  # no Authorization header
    assert r.status_code == 401


def test_boundary_withdraw_succeeds_200():
    fwd, http = _forwarder(grants={"google": {"installed:email": ["s1"]}})
    client = _routes_client(fwd)
    r = client.delete("/daemon/v1/agents/email/connections/google", headers=_auth())
    assert r.status_code == 200
    assert r.json()["withdrawn"] is True
    assert len(http.deletes) == 1
