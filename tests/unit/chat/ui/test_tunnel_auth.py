# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Mobile access: a paired phone reaches /api with the tunnel token.

The phone never holds the install's Agent UI API token, so every client here
drops it. The tunnel token works only while mobile access is on; nothing,
loopback included, gets in with neither token.
"""

import uuid

import pytest
from fastapi.testclient import TestClient as _TestClient

from gaia.ui.server import create_app
from gaia.ui.tunnel import TunnelManager

#: ngrok adds these to every tunnelled request.
_VIA_TUNNEL = {"x-forwarded-for": "203.0.113.7", "x-forwarded-proto": "https"}


def TestClient(app, **kwargs):
    """A phone over the tunnel: forwarded headers, no Agent UI API token."""
    client = _TestClient(app, **kwargs)
    client.headers.pop("authorization", None)
    client.headers.update(_VIA_TUNNEL)
    return client


def _refused(resp) -> bool:
    return resp.status_code == 401 and resp.json()["code"] == "ui_token_required"


class _FakeProcess:
    """Simulates a running subprocess (poll() returns None)."""

    def poll(self):
        return None


def _activate_tunnel(app) -> str:
    """Put the tunnel manager into an active state and return its token.

    Returns:
        The valid authentication token.
    """
    tunnel: TunnelManager = app.state.tunnel
    tunnel._url = "https://fake-tunnel.ngrok-free.app"
    tunnel._token = str(uuid.uuid4())
    tunnel._process = _FakeProcess()
    assert tunnel.active, "Tunnel should report active after setup"
    return tunnel._token


@pytest.fixture
def app():
    """Create FastAPI app with in-memory database."""
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    """TestClient for the app (requests come from 'testclient' host)."""
    return TestClient(app)


# ── Tests: no token at all ──────────────────────────────────────────────


class TestNoToken:
    """Without either token, nothing under /api gets in, loopback included."""

    def test_refused_while_tunnel_off(self, client):
        assert _refused(client.get("/api/sessions"))

    def test_refused_from_loopback_while_tunnel_on(self, app):
        _activate_tunnel(app)
        client = TestClient(app, client=("127.0.0.1", 50000))
        for name in _VIA_TUNNEL:
            client.headers.pop(name)
        assert _refused(client.get("/api/sessions"))

    def test_tunnel_token_counts_only_on_tunnelled_requests(self, app):
        token = _activate_tunnel(app)
        client = TestClient(app, client=("127.0.0.1", 50000))
        for name in _VIA_TUNNEL:
            client.headers.pop(name)
        client.headers["Authorization"] = f"Bearer {token}"
        assert _refused(client.get("/api/sessions"))

    def test_tunnel_token_cannot_change_permissions(self, app):
        token = _activate_tunnel(app)
        client = TestClient(app, client=("127.0.0.1", 50000))
        client.headers["Authorization"] = f"Bearer {token}"
        resp = client.put("/api/chat/permissions", json={"mode": "full"})
        assert resp.status_code == 403

    def test_health_always_allowed_when_tunnel_active(self, app):
        """The /api/health endpoint is exempt even for remote callers."""
        _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── Tests: tunnel active, remote requests (auth required) ───────────────


class TestRemoteAuth:
    """Remote requests through the tunnel require a valid Bearer token."""

    def test_missing_auth_header_returns_401(self, app):
        _activate_tunnel(app)
        client = TestClient(app)
        # TestClient host is "testclient" which is not in _LOCAL_HOSTS,
        # so this simulates a remote caller.
        resp = client.get("/api/sessions")
        assert _refused(resp)

    def test_malformed_auth_header_returns_401(self, app):
        _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get(
            "/api/sessions",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert _refused(resp)

    def test_wrong_token_returns_401(self, app):
        _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get(
            "/api/sessions",
            headers={"Authorization": "Bearer wrong-token-value"},
        )
        assert _refused(resp)

    def test_valid_token_allows_request(self, app):
        token = _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_valid_token_case_insensitive_bearer(self, app):
        """The 'Bearer' prefix should be case-insensitive per RFC 6750."""
        token = _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get(
            "/api/sessions",
            headers={"Authorization": f"bearer {token}"},
        )
        assert resp.status_code == 200

    def test_health_exempt_with_no_token(self, app):
        """Health endpoint never requires auth, even via tunnel."""
        _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_non_api_path_not_gated(self, app):
        """Paths outside /api/* are not subject to tunnel auth."""
        _activate_tunnel(app)
        client = TestClient(app)
        # The root path serves the static frontend (or 404 if no static
        # files are mounted), but should NOT return 401.
        resp = client.get("/")
        assert resp.status_code != 401

    def test_system_status_requires_token(self, app):
        """Verify /api/system/status is gated when tunnel is active."""
        _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get("/api/system/status")
        assert resp.status_code == 401

    def test_system_status_with_valid_token(self, app):
        token = _activate_tunnel(app)
        client = TestClient(app)
        resp = client.get(
            "/api/system/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


# ── Tests: tunnel deactivated after being active ────────────────────────


class TestTunnelDeactivated:
    """Once mobile access stops, the tunnel token stops working."""

    def test_tunnel_token_refused_after_tunnel_stopped(self, app):
        token = _activate_tunnel(app)
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {token}"
        assert client.get("/api/sessions").status_code == 200

        tunnel: TunnelManager = app.state.tunnel
        tunnel._url = None
        tunnel._process = None
        assert not tunnel.active

        assert _refused(client.get("/api/sessions"))


# ── Tests: cookie-based auth (set by serve_spa ?token= bootstrap) ───────


class TestCookieAuth:
    """Remote requests can authenticate via the gaia_tunnel_token cookie."""

    def test_valid_cookie_allows_request(self, app):
        """A request with the correct cookie is allowed through."""
        token = _activate_tunnel(app)
        client = TestClient(app, cookies={"gaia_tunnel_token": token})
        resp = client.get("/api/sessions")
        assert resp.status_code == 200

    def test_wrong_cookie_rejected(self, app):
        """A request with an incorrect cookie value is rejected."""
        _activate_tunnel(app)
        client = TestClient(app, cookies={"gaia_tunnel_token": "bogus"})
        resp = client.get("/api/sessions")
        assert _refused(resp)

    def test_cookie_fallback_when_header_missing(self, app):
        """Cookie is accepted when Authorization header is absent."""
        token = _activate_tunnel(app)
        client = TestClient(app, cookies={"gaia_tunnel_token": token})
        resp = client.get("/api/system/status")
        assert resp.status_code == 200

    def test_header_and_cookie_both_valid(self, app):
        """Valid header wins / both-valid also succeeds."""
        token = _activate_tunnel(app)
        client = TestClient(app, cookies={"gaia_tunnel_token": token})
        resp = client.get(
            "/api/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_valid_cookie_with_invalid_header(self, app):
        """Invalid Bearer header with valid cookie: header takes precedence -> 401.

        This is intentional: we read the header first, and an explicitly
        invalid header should surface a 401 rather than being silently
        overridden by a cookie from an earlier session.
        """
        token = _activate_tunnel(app)
        client = TestClient(app, cookies={"gaia_tunnel_token": token})
        resp = client.get(
            "/api/sessions",
            headers={"Authorization": "Bearer not-the-right-token"},
        )
        assert resp.status_code == 401


# ── Tests: serve_spa cookie bootstrap (?token= -> Set-Cookie) ───────────


class TestSpaCookieBootstrap:
    """serve_spa sets gaia_tunnel_token cookie when ?token=<valid> is present.

    Bootstrap response is a 303 redirect (with the cookie attached) to the
    same path with the token stripped from the query string. This ensures
    the token never lingers in the browser address bar, history, or
    outbound ``Referer`` headers. Cookies use ``SameSite=Strict`` to
    prevent cross-site attachment (CSRF defence-in-depth).
    """

    @pytest.fixture
    def app_with_frontend(self, tmp_path):
        """App with a minimal webui dist so serve_spa is registered."""
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html><body>gaia</body></html>")
        return create_app(db_path=":memory:", webui_dist=str(dist))

    def test_valid_token_query_redirects_and_sets_cookie(self, app_with_frontend):
        """Opening /?token=<valid> redirects to / with HttpOnly cookie set."""
        token = _activate_tunnel(app_with_frontend)
        client = TestClient(app_with_frontend)
        resp = client.get(f"/?token={token}", follow_redirects=False)
        # 303 See Other: token-stripping redirect to the bare path.
        assert resp.status_code == 303
        # Redirect target must be the same path with NO ?token=.
        location = resp.headers.get("location", "")
        assert location == "/", f"Expected '/', got {location!r}"
        # Cookie attached to the redirect response.
        set_cookie = resp.headers.get("set-cookie", "")
        assert "gaia_tunnel_token" in set_cookie
        assert token in set_cookie
        assert "HttpOnly" in set_cookie
        # SameSite=Strict — cookie must NOT cross-site (CSRF defence).
        assert "samesite=strict" in set_cookie.lower()
        # TestClient parses the cookie into the jar.
        assert client.cookies.get("gaia_tunnel_token") == token

    def test_token_strip_preserves_other_query_params(self, app_with_frontend):
        """``?token=X&session=Y`` redirect must keep ``?session=Y``, drop ``token``."""
        token = _activate_tunnel(app_with_frontend)
        client = TestClient(app_with_frontend)
        resp = client.get(f"/?session=abc123&token={token}", follow_redirects=False)
        assert resp.status_code == 303
        location = resp.headers.get("location", "")
        assert "token=" not in location
        assert "session=abc123" in location

    def test_invalid_token_query_does_not_set_cookie(self, app_with_frontend):
        """Opening /?token=<wrong> serves the index normally, no cookie."""
        _activate_tunnel(app_with_frontend)
        client = TestClient(app_with_frontend)
        resp = client.get("/?token=not-the-token")
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "gaia_tunnel_token" not in set_cookie

    def test_no_token_query_does_not_set_cookie(self, app_with_frontend):
        """Opening / without a token query does NOT set the cookie."""
        _activate_tunnel(app_with_frontend)
        client = TestClient(app_with_frontend)
        resp = client.get("/")
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "gaia_tunnel_token" not in set_cookie

    def test_token_query_when_tunnel_inactive_does_not_set_cookie(
        self, app_with_frontend
    ):
        """Bootstrap only happens when the tunnel is actually active."""
        # Don't activate -- tunnel is inactive by default.
        client = TestClient(app_with_frontend)
        resp = client.get("/?token=anything")
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "gaia_tunnel_token" not in set_cookie

    def test_token_on_static_asset_does_NOT_set_cookie(self, app_with_frontend):
        """A static-asset path (e.g. ``/index.html?token=...``) must NOT bootstrap.

        Security: the cookie-bootstrap path runs only on the SPA-index branch,
        so a request to any real static file (favicon, JS, CSS) ignores the
        ``?token=`` entirely. This shrinks the surface where a cookie can
        be planted to the single legitimate landing path.
        """
        token = _activate_tunnel(app_with_frontend)
        client = TestClient(app_with_frontend)
        # index.html exists in the fake dist directory and is reachable as
        # a static asset (the ``:path`` route resolves it via _sanitize_static_path).
        resp = client.get(f"/index.html?token={token}")
        # Static-file branch returns the file directly (200), no redirect.
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "gaia_tunnel_token" not in set_cookie

    def test_referrer_policy_no_referrer_on_index(self, app_with_frontend):
        """Index responses carry ``Referrer-Policy: no-referrer``.

        Defence-in-depth: even if a token transiently appears in the URL
        (between QR-scan landing and the redirect), no outbound request
        the page makes will leak it via the ``Referer`` header.
        """
        _activate_tunnel(app_with_frontend)
        client = TestClient(app_with_frontend)
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers.get("referrer-policy", "").lower() == "no-referrer"

    def test_bootstrap_then_subsequent_api_call_succeeds(self, app_with_frontend):
        """End-to-end: GET /?token=<x> redirects + sets cookie, /api/sessions works."""
        token = _activate_tunnel(app_with_frontend)
        # The tunnel is HTTPS, so the cookie is Secure.
        client = TestClient(app_with_frontend, base_url="https://testserver")

        # Step 1: visit bootstrap URL -- should redirect (303) and set cookie.
        resp = client.get(f"/?token={token}", follow_redirects=False)
        assert resp.status_code == 303
        assert client.cookies.get("gaia_tunnel_token") == token

        # Step 2: subsequent API call reuses the cookie -- must succeed.
        resp = client.get("/api/sessions")
        assert resp.status_code == 200, (
            f"Expected 200 after cookie bootstrap, got {resp.status_code}: "
            f"{resp.text}"
        )


# ── Tests: spoof-resistant localhost bypass ─────────────────────────────


class TestForwardedHeaderSpoofRejected:
    """Claiming a local origin through ``X-Forwarded-*`` earns nothing."""

    @pytest.mark.parametrize(
        "header",
        [
            {"X-Forwarded-For": "127.0.0.1"},
            {"X-Forwarded-Host": "fake.ngrok-free.app"},
            {"X-Forwarded-Proto": "https"},
        ],
    )
    def test_forwarded_header_without_token_is_refused(self, app, header):
        _activate_tunnel(app)
        client = TestClient(app, client=("127.0.0.1", 50000))
        assert _refused(client.get("/api/sessions", headers=header))


class TestConnectedDevice:
    """Phone access in the session panel names the device that connected."""

    def test_authenticated_remote_request_names_the_device(self, app, client):
        token = _activate_tunnel(app)
        resp = client.get(
            "/api/sessions",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)",
            },
        )
        assert resp.status_code == 200
        device = app.state.tunnel.get_status()["device"]
        assert device["name"] == "iPhone" and device["lastSeen"]

    def test_rejected_request_records_nothing(self, app, client):
        _activate_tunnel(app)
        client.get("/api/sessions", headers={"Authorization": "Bearer wrong"})
        assert app.state.tunnel.get_status()["device"] is None


# ── Tests: installable-app files reach a phone without a cookie ─────────


class TestPwaFilesOverTunnel:
    """A phone fetches the manifest without credentials, and the service worker
    script must arrive as JavaScript or the browser refuses to register it."""

    @pytest.fixture
    def app_with_pwa(self, tmp_path):
        dist = tmp_path / "dist"
        (dist / "icons").mkdir(parents=True)
        (dist / "index.html").write_text("<html><body>gaia</body></html>")
        (dist / "manifest.json").write_text('{"name": "GAIA"}')
        (dist / "sw.js").write_text("self.addEventListener('fetch', () => {});")
        (dist / "icons" / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return create_app(db_path=":memory:", webui_dist=str(dist))

    _REMOTE = {"x-forwarded-for": "203.0.113.7", "x-forwarded-proto": "https"}

    @pytest.mark.parametrize(
        "path, content_type",
        [
            ("/manifest.json", "application/json"),
            ("/sw.js", "javascript"),
            ("/icons/icon-192.png", "image/png"),
        ],
    )
    def test_served_to_a_remote_client_with_no_cookie(
        self, app_with_pwa, path, content_type
    ):
        _activate_tunnel(app_with_pwa)
        resp = TestClient(app_with_pwa).get(path, headers=self._REMOTE)
        assert resp.status_code == 200
        assert content_type in resp.headers["content-type"]

    def test_token_on_the_worker_script_does_not_set_cookie(self, app_with_pwa):
        token = _activate_tunnel(app_with_pwa)
        resp = TestClient(app_with_pwa).get(
            f"/sw.js?token={token}", headers=self._REMOTE, follow_redirects=False
        )
        assert resp.status_code == 200
        assert "gaia_tunnel_token" not in resp.headers.get("set-cookie", "")

    def test_api_still_needs_the_token(self, app_with_pwa):
        _activate_tunnel(app_with_pwa)
        resp = TestClient(app_with_pwa).get("/api/sessions", headers=self._REMOTE)
        assert resp.status_code == 401
