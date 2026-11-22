# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Agent UI API token gate: every /api and /v1 route needs it, local callers too."""

import os
import re
import sys
import time

import pytest
from starlette.testclient import TestClient

from gaia.ui import api_token
from gaia.ui.server import create_app
from tests.unit.chat.ui.test_ui_request_guard import _all_api_routes

LOOPBACK = ("127.0.0.1", 50000)


@pytest.fixture
def app():
    return create_app(db_path=":memory:")


def _client(app, *, token=True, **kwargs) -> TestClient:
    """A loopback client; ``token=False`` drops the default bearer header."""
    c = TestClient(app, client=LOOPBACK, **kwargs)
    if not token:
        c.headers.pop("authorization", None)
    return c


def _guarded_requests(app):
    """One (method, path) per guarded route, path params filled with dummies."""
    seen = [("GET", "/api/files/uploads/x.png")]
    for path, methods in _all_api_routes(app):
        if not path.startswith(("/api/", "/v1/")) or path == "/api/health":
            continue
        filled = re.sub(r"\{[^}:]+(:[^}]+)?\}", "x", path)
        seen.extend((m, filled) for m in sorted(methods - {"HEAD"}))
    return seen


def test_route_inventory_is_large(app):
    """Guard against the sweep below silently testing nothing."""
    paths = {p for _, p in _guarded_requests(app)}
    assert len(paths) > 100
    for group in (
        "/api/sessions",
        "/api/chat/permissions",
        "/api/chat/confirm-tool",
        "/api/files/uploads/x.png",
        "/v1/",
        "/api/goals/x/approve",
    ):
        assert any(p.startswith(group) for p in paths), group


def test_every_guarded_route_refuses_a_local_caller_without_the_token(app):
    c = _client(app, token=False)
    let_through = []
    for method, path in _guarded_requests(app):
        resp = c.request(method, path)
        if resp.status_code != 401 or resp.json().get("code") != "ui_token_required":
            let_through.append((method, path, resp.status_code))
    assert not let_through


def test_wrong_token_is_refused(app):
    c = _client(app, token=False)
    for header in ("Bearer nope", "Basic dXNlcjpwYXNz", "Bearer ", "nope"):
        resp = c.get("/api/sessions", headers={"Authorization": header})
        assert resp.status_code == 401, header
    c.cookies.set(api_token.COOKIE_NAME, "nope")
    assert c.get("/api/sessions").status_code == 401


def test_rejection_says_how_to_get_in(app):
    resp = _client(app, token=False).get("/api/sessions")
    assert resp.headers["www-authenticate"] == "Bearer"
    detail = resp.json()["detail"]
    assert "sign-in link" in detail and api_token.TOKEN_FILE_NAME in detail
    assert app.state.ui_api_token not in resp.text


def test_token_is_accepted_as_bearer_or_cookie(app):
    assert _client(app).get("/api/sessions").status_code == 200
    c = _client(app, token=False)
    c.cookies.set(api_token.COOKIE_NAME, app.state.ui_api_token)
    assert c.get("/api/sessions").status_code == 200


def test_token_also_covers_streams_and_uploads(app):
    c = _client(app, token=False)
    assert c.get("/api/files/uploads/nothing.png").status_code == 401
    assert c.get("/v1/connections").status_code == 401
    assert _client(app).get("/api/files/uploads/nothing.png").status_code == 404


def test_health_is_exempt_and_reveals_nothing(app):
    resp = _client(app, token=False).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "gaia-agent-ui"}


def test_cors_preflight_needs_no_token(app):
    resp = _client(app, token=False).options(
        "/api/sessions",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200


def test_permission_and_approval_routes_need_the_token_from_this_pc(app):
    c = _client(app, token=False)
    for method, path, body in (
        ("PUT", "/api/chat/permissions", {"mode": "full"}),
        ("PUT", "/api/chat/permissions/s1", {"mode": "full"}),
        ("DELETE", "/api/chat/permissions/s1/grants", None),
        ("POST", "/api/chat/confirm-tool", {"session_id": "s", "approved": True}),
        ("PUT", "/api/goals/g1/approve", {}),
    ):
        resp = c.request(method, path, json=body)
        assert resp.status_code == 401, (method, path, resp.status_code)


class TestOtherGuardsStillApply:
    def test_untrusted_host_is_refused_even_with_the_token(self, app):
        resp = _client(app).get("/api/sessions", headers={"Host": "evil.example"})
        assert resp.status_code == 400

    def test_cross_origin_write_is_refused_even_with_the_token(self, app):
        resp = _client(app).post(
            "/api/sessions", json={}, headers={"Origin": "https://evil.example"}
        )
        assert resp.status_code == 403

    def test_write_without_ui_header_is_refused_even_with_the_token(self, app):
        c = _client(app)
        c.headers.pop("x-gaia-ui", None)
        assert c.post("/api/sessions", json={}).status_code == 403


class TestLaunchLink:
    @pytest.fixture
    def app(self, tmp_path):
        (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
        return create_app(db_path=":memory:", webui_dist=str(tmp_path))

    def test_minting_a_code_needs_the_token(self, app):
        assert (
            _client(app, token=False).post("/api/auth/launch-code").status_code == 401
        )
        body = _client(app).post("/api/auth/launch-code").json()
        assert body["path"].startswith("/?launch=")
        assert body["expires_in"] == api_token.LAUNCH_CODE_TTL_S

    def test_code_signs_the_browser_in_once(self, app):
        path = _client(app).post("/api/auth/launch-code").json()["path"]
        browser = _client(app, token=False, follow_redirects=False)

        resp = browser.get(path + "&session=abc")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/?session=abc"
        cookie = resp.headers["set-cookie"].lower()
        assert f"{api_token.COOKIE_NAME}=" in cookie
        for flag in ("httponly", "samesite=strict", "path=/", "max-age="):
            assert flag in cookie
        assert browser.get("/api/sessions").status_code == 200

        stranger = _client(app, token=False, follow_redirects=False)
        resp = stranger.get(path)
        assert resp.status_code == 200
        assert "set-cookie" not in resp.headers
        assert stranger.get("/api/sessions").status_code == 401

    def test_unknown_code_sets_nothing(self, app):
        resp = _client(app, token=False, follow_redirects=False).get("/?launch=bogus")
        assert resp.status_code == 200
        assert "set-cookie" not in resp.headers

    def test_code_on_a_static_asset_sets_nothing(self, app, tmp_path):
        (tmp_path / "logo.png").write_bytes(b"png")
        path = _client(app).post("/api/auth/launch-code").json()["path"]
        code = path.split("=", 1)[1]
        resp = _client(app, token=False, follow_redirects=False).get(
            f"/logo.png?launch={code}"
        )
        assert "set-cookie" not in resp.headers

    def test_mobile_access_cannot_reach_the_install_token(self, app):
        tunnel = app.state.tunnel
        tunnel._url, tunnel._token = "https://t.ngrok-free.app", "tunnel-secret"
        tunnel._process = type("P", (), {"poll": lambda self: None})()
        phone = _client(app, token=False, follow_redirects=False)
        phone.headers.update(
            {"x-forwarded-for": "203.0.113.7", "authorization": "Bearer tunnel-secret"}
        )
        assert phone.get("/api/sessions").status_code == 200
        assert phone.post("/api/auth/launch-code").status_code == 403

        path = _client(app).post("/api/auth/launch-code").json()["path"]
        resp = phone.get(path)
        assert api_token.COOKIE_NAME not in resp.headers.get("set-cookie", "")

    def test_expired_code_is_refused(self, monkeypatch):
        codes = api_token.LaunchCodes(ttl_s=60)
        code = codes.mint()
        now = time.monotonic()
        monkeypatch.setattr(api_token.time, "monotonic", lambda: now + 61)
        assert not codes.redeem(code)
        assert not codes.redeem(None)


class TestTokenFile:
    def test_created_once_and_reused(self, tmp_path, monkeypatch):
        monkeypatch.delenv(api_token.ENV_TOKEN_FILE, raising=False)
        monkeypatch.setenv("GAIA_HOME", str(tmp_path))
        first = api_token.load_or_create_token()
        assert len(first) >= 40
        assert api_token.load_or_create_token() == first
        assert api_token.read_token() == first
        path = tmp_path / api_token.TOKEN_FILE_NAME
        assert path.read_text(encoding="utf-8") == first
        if sys.platform != "win32":
            assert (os.stat(path).st_mode & 0o777) == 0o600

    def test_a_second_backend_keeps_the_first_token(self, tmp_path, monkeypatch):
        monkeypatch.delenv(api_token.ENV_TOKEN_FILE, raising=False)
        monkeypatch.setenv("GAIA_HOME", str(tmp_path))
        path = tmp_path / api_token.TOKEN_FILE_NAME
        real_link = os.link

        def first_backend_wins(src, dst):
            path.write_text("first", encoding="utf-8")
            real_link(src, dst)

        monkeypatch.setattr(api_token.os, "link", first_backend_wins)
        assert api_token.load_or_create_token() == "first"
        assert [p.name for p in tmp_path.iterdir()] == [api_token.TOKEN_FILE_NAME]

    def test_explicit_file_must_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv(api_token.ENV_TOKEN_FILE, str(tmp_path / "missing"))
        with pytest.raises(api_token.UITokenError, match="Cannot read"):
            api_token.load_or_create_token()
        assert not (tmp_path / "missing").exists()

    def test_empty_file_is_refused(self, tmp_path, monkeypatch):
        empty = tmp_path / "token"
        empty.write_text("  \n", encoding="utf-8")
        monkeypatch.setenv(api_token.ENV_TOKEN_FILE, str(empty))
        with pytest.raises(api_token.UITokenError, match="empty"):
            api_token.read_token()

    def test_client_headers(self):
        headers = api_token.auth_headers()
        assert headers["X-Gaia-UI"] == "1"
        assert headers["Authorization"] == f"Bearer {api_token.read_token()}"


class TestNoBypass:
    def test_lookalikes_of_the_exempt_health_path_need_the_token(self, app):
        c = _client(app, token=False)
        for path in ("/api/health/", "/api//sessions", "/api/sessions/"):
            assert c.get(path).status_code == 401, path
        assert c.get("/api/health?probe=1").status_code == 200

    def test_head_requests_need_the_token(self, app):
        assert _client(app, token=False).head("/api/sessions").status_code == 401

    def test_the_token_in_a_query_string_is_not_accepted(self, app):
        token = app.state.ui_api_token
        c = _client(app, token=False)
        assert c.get(f"/api/sessions?token={token}").status_code == 401
        assert c.get(f"/api/sessions?launch={token}").status_code == 401

    def test_a_token_prefix_or_padded_token_is_refused(self, app):
        token = app.state.ui_api_token
        c = _client(app, token=False)
        for value in (token[:-1], token + "x", token.upper()):
            resp = c.get("/api/sessions", headers={"Authorization": f"Bearer {value}"})
            assert resp.status_code == 401, value

    def test_the_tunnel_token_is_refused_from_a_direct_local_caller(self, app):
        tunnel = app.state.tunnel
        tunnel._url, tunnel._token = "https://t.ngrok-free.app", "tunnel-secret"
        tunnel._process = type("P", (), {"poll": lambda self: None})()
        c = _client(app, token=False)
        resp = c.get("/api/sessions", headers={"Authorization": "Bearer tunnel-secret"})
        assert resp.status_code == 401


def test_standalone_runner_prints_a_token_file_error_without_a_traceback(
    tmp_path, monkeypatch, capsys
):
    from gaia.ui.server import main as server_main

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(api_token.ENV_TOKEN_FILE, str(tmp_path / "missing"))
    monkeypatch.setattr(
        "sys.argv", ["gaia.ui.server", "--host", "127.0.0.1", "--port", "4200"]
    )
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *a, **k: pytest.fail("create_app() should have refused the token file"),
    )
    with pytest.raises(SystemExit) as exc_info:
        server_main()
    assert exc_info.value.code == 64
    err = capsys.readouterr().err
    assert "Cannot read the Agent UI API token" in err
    assert "Traceback" not in err
