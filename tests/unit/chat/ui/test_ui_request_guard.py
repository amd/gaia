# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression tests for the Agent UI cross-origin request guard.

Covers the two holes that let any web page the user visited drive the
backend:

* a mutating route shipping without the ``X-Gaia-UI`` CSRF check, and
* a CORS policy that trusted every ``*.ngrok-free.app`` /
  ``*.use.devtunnels.ms`` subdomain -- shared, self-service namespaces --
  with credentials and ``allow_headers=["*"]``, which handed an attacker
  an approved preflight for the very header the CSRF check relies on.

Everything here drives the ASGI stack directly rather than through
``TestClient``: ``tests/unit/conftest.py``'s network guard breaks
``socket.socketpair()`` on Windows, so a ``TestClient`` in this directory
errors during setup on that platform (review finding C41). Raw ASGI needs
no sockets and exercises the same middleware.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from gaia.ui import security
from gaia.ui.server import create_app

#: ``tests/unit/conftest.py`` monkeypatches ``socket.connect`` to keep unit
#: tests offline, which also breaks the ``socketpair()`` the Windows asyncio
#: loop opens for itself. Nothing here touches the network -- only the event
#: loop needs the opt-out.
pytestmark = pytest.mark.allow_network


@pytest.fixture(scope="module")
def app():
    return create_app(db_path=":memory:")


# ── Raw ASGI driver ─────────────────────────────────────────────────────────


def _scope(app, method: str, path: str, headers: dict) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (k.lower().encode(), v.encode())
            for k, v in headers.items()
            if v is not None
        ],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 4200),
        "app": app,
    }


async def _request(stack, app, method, path, headers, body: bytes = b""):
    """Drive an ASGI app and return ``(status, headers_dict, body)``."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await stack(_scope(app, method, path, headers), receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    resp_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], resp_headers, payload


@pytest.fixture(scope="module")
def stack(app):
    """The app's real middleware stack, without a running lifespan."""
    return app.build_middleware_stack()


# ── Route-table introspection ───────────────────────────────────────────────


def _all_api_routes(router):
    """Every ``APIRoute`` reachable from ``app``, materialising lazy includes.

    FastAPI defers ``include_router`` into ``_IncludedRouter`` wrappers, so
    a naive ``for r in app.routes`` finds a single route on this version --
    which is exactly how a coverage test like this silently passes while
    asserting nothing. ``test_route_walk_finds_the_whole_surface`` pins the
    walk itself.
    """
    from fastapi.routing import _EffectiveRouteContext, _IncludedRouter

    found = {}

    def walk(routes):
        for route in routes:
            if isinstance(route, _IncludedRouter):
                walk(route.effective_candidates())
                walk(route.effective_low_priority_routes())
            elif isinstance(route, _EffectiveRouteContext):
                if isinstance(route.original_route, APIRoute):
                    found[(route.path, frozenset(route.methods))] = route
            elif isinstance(route, APIRoute):
                found[(route.path, frozenset(route.methods))] = route

    walk(router.routes)
    return found


def _mutating_paths(app):
    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    return sorted(
        {
            path
            for (path, methods), _ in _all_api_routes(app).items()
            if methods & mutating
        }
    )


def test_route_walk_finds_the_whole_surface(app):
    """Guard the guard: a walk that finds nothing would pass every test below."""
    assert len(_all_api_routes(app)) > 100
    assert len(_mutating_paths(app)) > 50


def test_every_mutating_route_is_covered_by_the_guard(app):
    """No mutating route may sit outside the middleware's reach.

    A router mounted at a new prefix -- ``/agents/...`` rather than
    ``/api/agents/...`` -- would silently escape the CSRF and Origin
    checks. Fail here rather than in production: either move the route
    under a guarded prefix, or widen ``GUARDED_PREFIXES``.
    """
    uncovered = [
        path
        for path in _mutating_paths(app)
        if not path.startswith(security.GUARDED_PREFIXES)
    ]
    assert (
        uncovered == []
    ), f"mutating routes outside {security.GUARDED_PREFIXES}: {uncovered}"


def test_no_mutating_route_is_exempted(app):
    """The exemption list must stay empty -- every entry is a hole.

    The OAuth loopback callback, the one flow that cannot send a custom
    header, is a GET on its own aiohttp server in ``gaia.connectors.flow``.
    """
    assert security.CSRF_EXEMPT_PATHS == frozenset()
    assert not set(_mutating_paths(app)) & security.CSRF_EXEMPT_PATHS


def test_high_value_routes_are_in_the_covered_set(app):
    """Spot-check the routes the review found forgeable."""
    paths = set(_mutating_paths(app))
    for path in (
        "/api/tunnel/start",
        "/api/memory/prune",
        "/api/documents/upload",
        "/api/files/upload",
        "/v1/email/send",
        "/api/chat/send",
    ):
        assert path in paths, f"{path} disappeared from the route table"
        assert path.startswith(security.GUARDED_PREFIXES)


# ── CORS policy ─────────────────────────────────────────────────────────────


def test_cors_has_no_wildcard_origin_regex(app):
    """No regex over a namespace anyone can rent a subdomain in."""
    cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    assert len(cors) == 1
    assert cors[0].kwargs.get("allow_origin_regex") is None
    for origin in cors[0].kwargs["allow_origins"]:
        assert "ngrok" not in origin and "devtunnels" not in origin


async def test_preflight_from_a_rented_ngrok_subdomain_is_rejected(stack, app):
    """The exact attack C12 describes: someone else's free ngrok subdomain."""
    status, headers, _ = await _request(
        stack,
        app,
        "OPTIONS",
        "/api/memory/prune",
        {
            "host": "127.0.0.1:4200",
            "origin": "https://someone-else.ngrok-free.app",
            "access-control-request-method": "POST",
            "access-control-request-headers": "x-gaia-ui,content-type",
        },
    )
    assert status == 400
    assert "access-control-allow-origin" not in headers


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.use.devtunnels.ms",
        "https://evil.example",
        "http://localhost.evil.example",
    ],
)
async def test_preflight_from_other_untrusted_origins_is_rejected(stack, app, origin):
    status, headers, _ = await _request(
        stack,
        app,
        "OPTIONS",
        "/api/memory/prune",
        {
            "host": "127.0.0.1:4200",
            "origin": origin,
            "access-control-request-method": "POST",
            "access-control-request-headers": "x-gaia-ui",
        },
    )
    assert status == 400
    assert "access-control-allow-origin" not in headers


async def test_preflight_from_the_dev_origin_is_still_approved(stack, app):
    """The vite dev server must keep working."""
    status, headers, _ = await _request(
        stack,
        app,
        "OPTIONS",
        "/api/memory/prune",
        {
            "host": "127.0.0.1:4200",
            "origin": "http://localhost:5174",
            "access-control-request-method": "POST",
            "access-control-request-headers": "x-gaia-ui,content-type",
        },
    )
    assert status == 200
    assert headers["access-control-allow-origin"] == "http://localhost:5174"


# ── CSRF header ─────────────────────────────────────────────────────────────

_ATTACKER = {"host": "127.0.0.1:4200", "origin": "https://evil.example"}


async def test_body_less_cross_origin_post_is_rejected(stack, app):
    """C11's live proof, as a test: ``POST /api/tunnel/start`` took no body."""
    status, headers, body = await _request(
        stack, app, "POST", "/api/tunnel/start", _ATTACKER
    )
    assert status == 403
    assert b"Cross-origin" in body
    # A rejection must not be readable cross-origin either.
    assert "access-control-allow-origin" not in headers


async def test_cross_origin_post_is_rejected_even_with_the_header(stack, app):
    """Origin is checked independently, so a forged header is not enough."""
    status, _, body = await _request(
        stack, app, "POST", "/api/memory/prune", {**_ATTACKER, "x-gaia-ui": "1"}
    )
    assert status == 403
    assert b"Cross-origin" in body


async def test_same_origin_post_without_the_header_is_rejected(stack, app):
    """A form POST from a page on another local port still needs the header."""
    status, _, body = await _request(
        stack,
        app,
        "POST",
        "/api/memory/prune",
        {"host": "127.0.0.1:4200", "origin": "http://localhost:9999"},
    )
    assert status == 403
    assert b"X-Gaia-UI" in body


async def test_first_party_post_passes_the_guard(stack, app):
    """The Electron/SPA shape must reach its handler."""
    status, _, _ = await _request(
        stack,
        app,
        "POST",
        "/api/memory/prune",
        {"host": "127.0.0.1:4200", "origin": "http://127.0.0.1:4200", "x-gaia-ui": "1"},
    )
    assert status != 403


async def test_opaque_origin_with_the_header_passes(stack, app):
    """The packaged Electron shell serves the SPA from ``file://``."""
    status, _, _ = await _request(
        stack,
        app,
        "POST",
        "/api/memory/prune",
        {"host": "127.0.0.1:4200", "origin": "null", "x-gaia-ui": "1"},
    )
    assert status != 403


async def test_non_browser_client_with_the_header_passes(stack, app):
    """``gaia mcp`` and the eval harness send no Origin at all."""
    status, _, _ = await _request(
        stack,
        app,
        "POST",
        "/api/memory/prune",
        {"host": "127.0.0.1:4200", "x-gaia-ui": "1"},
    )
    assert status != 403


async def test_reads_are_not_blocked(stack, app):
    """``EventSource`` cannot set headers, so GET must stay open."""
    status, _, _ = await _request(
        stack, app, "GET", "/api/health", {"host": "127.0.0.1:4200"}
    )
    assert status == 200


# ── Host allowlist (DNS rebinding) ──────────────────────────────────────────


async def test_rebinding_host_is_rejected(stack, app):
    """``evil.example`` re-pointed at 127.0.0.1 must not reach any route."""
    status, _, body = await _request(
        stack, app, "GET", "/api/health", {"host": "evil.example"}
    )
    assert status == 400
    assert b"Invalid Host" in body


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:4200",
        "localhost:4200",
        "[::1]:4200",
        "192.168.1.9:4200",
        "0.0.0.0:4200",
    ],
)
async def test_local_and_ip_literal_hosts_are_accepted(stack, app, host):
    """LAN access via ``--host 0.0.0.0`` must keep working.

    An IP literal cannot be the target of DNS rebinding -- that needs a
    name whose resolution can be changed.
    """
    status, _, _ = await _request(stack, app, "GET", "/api/health", {"host": host})
    assert status == 200


def test_env_allowlist_admits_a_named_host(monkeypatch):
    monkeypatch.setenv(security.ENV_ALLOWED_HOSTS, "gaia.internal, other.example")
    assert security.is_allowed_host("gaia.internal:4200", None)
    assert security.is_allowed_host("other.example", None)
    assert not security.is_allowed_host("evil.example", None)


# ── Tunnel origin ───────────────────────────────────────────────────────────


class _FakeTunnel:
    active = True

    def get_status(self):
        return {"url": "https://mine-abc123.ngrok-free.dev"}


def test_live_tunnel_host_and_origin_are_accepted(app, monkeypatch):
    """The mobile SPA is served *from* the tunnel, so it is same-origin."""
    monkeypatch.setattr(app.state, "tunnel", _FakeTunnel(), raising=False)
    assert security.is_allowed_host("mine-abc123.ngrok-free.dev", app)
    assert security.is_allowed_origin(
        "https://mine-abc123.ngrok-free.dev", "mine-abc123.ngrok-free.dev", app
    )
    # A different tenant of the same namespace is not this session's tunnel.
    assert not security.is_allowed_host("someone-else.ngrok-free.dev", app)
    assert not security.is_allowed_origin(
        "https://someone-else.ngrok-free.dev", "mine-abc123.ngrok-free.dev", app
    )
