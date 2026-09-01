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

import httpx
import pytest

from gaia.connectors.errors import AuthRequiredError
from gaia.connectors.providers.base import ConnectorRequirement
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
    forward_providers=("google", "microsoft", "microsoft_work"),
    forwarded_mode_env_var="GAIA_EMAIL_FORWARDED_CREDENTIALS",
)

# Real scope literals (#2730 D5) — must match
# gaia_agent_email/scopes.py::ALL_SCOPES/REQUIRED_SCOPES and
# src/gaia/daemon/sidecars/spec.py::_EMAIL_REQUIRED_CONNECTIONS["google"].
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
ALL_SCOPES = [GMAIL_MODIFY, GMAIL_SEND, CALENDAR_EVENTS, CALENDAR_READONLY]
REQUIRED_SCOPES = [GMAIL_MODIFY, GMAIL_SEND]
# A scope on the shared Google connection that belongs to some OTHER agent's
# grant entirely — never requested by email at all (AC-6c).
DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

# _SPEC has no required_connections (pre-existing tests don't need one). The
# #2730 D5 tests need a spec whose google requirement narrows required_scopes
# below scopes (calendar requested-but-optional) — built separately so it
# can't change what the pre-existing tests above assert against _SPEC.
_SPEC_WITH_REQUIRED_CONNECTIONS = AgentSidecarSpec(
    agent_id="email",
    service_id="gaia-agent-email",
    display_name="Email",
    expected_api_major="2",
    token_env_var="GAIA_EMAIL_SIDECAR_TOKEN",
    mode_env_var="GAIA_EMAIL_AGENT_MODE",
    cache_dir_name="email",
    grant_agent_id="installed:email",
    forward_providers=("google", "microsoft", "microsoft_work"),
    forwarded_mode_env_var="GAIA_EMAIL_FORWARDED_CREDENTIALS",
    required_connections=(
        ConnectorRequirement(
            connector_id="google",
            scopes=ALL_SCOPES,
            required_scopes=REQUIRED_SCOPES,
        ),
    ),
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
    get_connection=None,
    spec=None,
):
    grants = grants or {}
    http = http or _RecordingHTTP()
    spec = spec or _SPEC

    def _list_grants(provider):
        return grants.get(provider, {})

    def _mint(*, provider, scopes, agent_id):
        if mint is not None:
            return mint(provider=provider, scopes=scopes, agent_id=agent_id)
        return (f"token-{provider}", 1_900_000_000.0)

    kwargs = dict(
        mint=_mint,
        list_grants=_list_grants,
        connected_providers=lambda: list(connected),
        http_post=http.post,
        http_delete=http.delete,
    )
    # Only threaded when a test actually supplies it: ConnectionForwarder
    # does not accept this kwarg yet (#2730 D5), and every pre-existing test
    # below calls _forwarder() without it — they must keep passing unmodified
    # until the seam is added.
    if get_connection is not None:
        kwargs["get_connection"] = get_connection

    fwd = ConnectionForwarder({"email": spec}, **kwargs)
    return fwd, http


def _scope_checking_mint(stored_scopes):
    """A fake mint that mimics the real ``get_access_token_with_expiry_sync``
    boundary: it raises ``AuthRequiredError(CONNECTION_MISSING_SCOPES)`` naming
    whichever of the *requested* ``scopes`` the connection doesn't actually
    carry (``stored_scopes``), and otherwise mints normally. Lets a test
    assert on exactly what ``forward_provider`` requested without depending
    on which internal mechanism (get_connection pre-check vs. mint's own
    boundary check) the real implementation ends up using."""
    stored = set(stored_scopes)

    def _mint(*, provider, scopes, agent_id):
        missing = [s for s in scopes if s not in stored]
        if missing:
            raise AuthRequiredError(
                AuthRequiredError.Reason.CONNECTION_MISSING_SCOPES,
                provider=provider,
                missing_scopes=missing,
            )
        return (f"token-{provider}", 1_900_000_000.0)

    return _mint


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
    where the OAuth-client setup surfaces, and only then mention the UI.

    Uses a spec WITH a declared ConnectorRequirement (#2730 D5/MF-3/MF-4) —
    the real production email spec always has one (verified against
    ``builtin_specs()["email"]``), so this is the shape a real headless user
    actually hits. The no-requirement shape is a distinct, separately-tested
    case (see ``test_ungranted_error_with_no_declared_requirement_names_the_gap``)."""
    fwd, _ = _forwarder(grants={}, spec=_SPEC_WITH_REQUIRED_CONNECTIONS)
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


def test_ungranted_error_with_no_declared_requirement_names_the_gap():
    """#2730 MF-3: a spec with no ConnectorRequirement for the provider must
    NOT print an uncopyable `--scopes <scopes>` placeholder (AC-9a scans
    source for exactly that literal). It must instead name the real gap —
    the missing AgentSidecarSpec declaration — so the message stays
    actionable without a placeholder."""
    fwd, _ = _forwarder(grants={})  # default _SPEC has no required_connections
    with pytest.raises(NotGrantedError) as exc:
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )
    msg = str(exc.value)
    assert "<scope" not in msg
    assert "ConnectorRequirement" in msg
    assert "google" in msg


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


def test_forward_provider_transport_mint_failure_retains_stale_forward(caplog):
    """A transient transport failure must leave a valid stale forward alone."""

    def _mint(*, provider, scopes, agent_id):
        raise httpx.ConnectTimeout("OAuth provider timed out")

    fwd, http = _forwarder(grants={"google": {"installed:email": ["s1"]}}, mint=_mint)
    with caplog.at_level("WARNING"):
        with pytest.raises(httpx.ConnectTimeout):
            fwd.forward_provider(
                "email", "google", base_url="http://127.0.0.1:9", bearer="b"
            )

    assert http.posts == []
    assert http.deletes == []
    assert "retaining any existing forward" in caplog.text


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


# --- forward_provider: required-scope mint + intersection forwarding
# (#2730 D5) --------------------------------------------------------------


def test_forward_provider_ac4_mint_scoped_to_required_subset_names_missing_scope():
    """AC-4 (#2730): the mint must be scoped to the REQUIRED subset of scopes
    (gmail.modify + gmail.send) — not the full 4-item ledger claim
    (+calendar.events + calendar.readonly). The connection stores only
    gmail.modify; the ledger grants all four. If forward_provider still fed
    the mint the full ledger claim, the connection would be missing THREE
    scopes (send + both calendar); scoping to the required subset means only
    ONE is missing. Asserting the list is exactly that one item is what
    falsifies the old all-of-the-ledger behavior."""
    fwd, http = _forwarder(
        grants={"google": {"installed:email": list(ALL_SCOPES)}},
        spec=_SPEC_WITH_REQUIRED_CONNECTIONS,
        mint=_scope_checking_mint([GMAIL_MODIFY]),
    )
    with pytest.raises(AuthRequiredError) as exc:
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )
    assert exc.value.missing_scopes == [GMAIL_SEND]
    assert http.posts == []


def test_forward_provider_ac6b_forwards_intersection_not_all_or_nothing():
    """AC-6b (#2730): a connection carrying mail-only scopes with a ledger
    claiming mail+calendar must STILL forward the mail scopes it actually
    has, and must NOT withdraw the forward just because the optional
    calendar scopes aren't there (no all-or-nothing mint). Falsifies an
    implementation that keeps posting the ledger's full claim: the sidecar
    must see exactly the mail-only intersection the connection carries, not
    ALL_SCOPES."""
    fwd, http = _forwarder(
        grants={"google": {"installed:email": list(ALL_SCOPES)}},
        get_connection=lambda provider: {"scopes": [GMAIL_MODIFY, GMAIL_SEND]},
        spec=_SPEC_WITH_REQUIRED_CONNECTIONS,
    )
    result = fwd.forward_provider(
        "email", "google", base_url="http://127.0.0.1:9", bearer="b"
    )
    assert result["forwarded"] is True
    assert http.deletes == []  # no all-or-nothing withdraw
    assert http.posts[0]["json"]["scopes"] == [GMAIL_MODIFY, GMAIL_SEND]


def test_forward_provider_ac6b_missing_required_scope_still_raises_loudly():
    """AC-6b (#2730) companion: a connection missing a *required* scope (not
    merely the optional calendar scopes) must still fail loudly, naming that
    scope, rather than the intersection logic silently forwarding whatever it
    does have. Distinguishes this from AC-6b's main case: here gmail.send
    itself — a required scope — is absent from the connection."""
    fwd, http = _forwarder(
        grants={"google": {"installed:email": list(ALL_SCOPES)}},
        get_connection=lambda provider: {
            "scopes": [GMAIL_MODIFY, CALENDAR_EVENTS, CALENDAR_READONLY]
        },
        spec=_SPEC_WITH_REQUIRED_CONNECTIONS,
        mint=_scope_checking_mint([GMAIL_MODIFY, CALENDAR_EVENTS, CALENDAR_READONLY]),
    )
    with pytest.raises(AuthRequiredError) as exc:
        fwd.forward_provider(
            "email", "google", base_url="http://127.0.0.1:9", bearer="b"
        )
    assert exc.value.missing_scopes == [GMAIL_SEND]
    assert http.posts == []


def test_forward_provider_ac6c_forwarded_scopes_never_exceed_agent_grant():
    """AC-6c (#2730): even when the shared Google connection carries MORE
    scopes than this agent was granted (e.g. drive.readonly granted to some
    other agent sharing the same connection), forwarding must be capped to
    the intersection with THIS agent's ledger grant — never widened by
    whatever else the connection happens to carry. Without this case an
    implementation could satisfy AC-6b and still ship the over-grant."""
    fwd, http = _forwarder(
        grants={"google": {"installed:email": list(ALL_SCOPES)}},
        get_connection=lambda provider: {"scopes": list(ALL_SCOPES) + [DRIVE_READONLY]},
        spec=_SPEC_WITH_REQUIRED_CONNECTIONS,
    )
    result = fwd.forward_provider(
        "email", "google", base_url="http://127.0.0.1:9", bearer="b"
    )
    assert result["forwarded"] is True
    posted_scopes = http.posts[0]["json"]["scopes"]
    assert sorted(posted_scopes) == sorted(ALL_SCOPES)
    assert DRIVE_READONLY not in posted_scopes


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
