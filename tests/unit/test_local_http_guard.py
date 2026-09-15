# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Cross-origin and caller-auth guard for GAIA's local REST servers.

The review's theme was that GAIA had these primitives and call sites skipped
them. So the core tests here walk each app's route table and fail if any
route -- including one added next month -- ships without the guard, rather
than spot-checking the routes that exist today.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.readiness import AgentRequirements  # noqa: E402
from gaia.agents.base.server import AgentServer  # noqa: E402
from gaia.api import local_http  # noqa: E402

# TestClient needs socketpair() for its event loop, which the unit-suite
# network guard breaks on Windows. Nothing here leaves the process.
pytestmark = pytest.mark.allow_network

KEY = "k3y-for-tests"
EVIL = "https://evil.example"
LOCAL = "http://localhost:3000"
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class _EchoAgent(Agent):
    """Agent that skips the LLM-bound ``Agent.__init__``."""

    def __init__(self):  # noqa: D401 - deliberately skip super().__init__
        self.calls = []

    def _register_tools(self):
        pass

    def process_query(self, user_input, **kwargs):
        self.calls.append(user_input)
        return {"status": "success", "result": f"echo: {user_input}"}

    def get_tools_info(self):
        return {}


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    monkeypatch.delenv(local_http.API_KEY_ENV, raising=False)
    monkeypatch.delenv(local_http.CORS_ORIGINS_ENV, raising=False)


def _agent_server():
    # Declared requirements mount POST /v1/<id>/init -- the route that can
    # start a multi-GB model pull -- so the walk below sees it too.
    return AgentServer(
        _EchoAgent(),
        name="Echo",
        model_id="echo",
        agent_id="echo",
        requirements=AgentRequirements(model_id="Test-Model"),
    )


def _is_guard(dep) -> bool:
    return bool(getattr(dep.call, local_http.CALLER_GUARD_MARKER, False))


def _guarded(route: APIRoute) -> bool:
    """Whether the route's resolved dependency tree contains a caller guard."""
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if _is_guard(dep):
            return True
        stack.extend(dep.dependencies)
    return False


def _api_routes(app):
    """Every APIRoute reachable from ``app``, included routers too.

    Reuses the Agent UI guard's walker, which already copes with FastAPI
    versions that include routers lazily.
    """
    from tests.unit.chat.ui.test_ui_request_guard import _all_api_routes

    routes = [getattr(r, "original_route", r) for r in _all_api_routes(app).values()]
    return [r for r in routes if isinstance(r, APIRoute)]


def _chat(client, **headers):
    return client.post(
        "/v1/chat/completions",
        json={"model": "echo", "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )


# -- Route-table introspection (enforce by construction) ---------------------


def test_every_agent_server_route_carries_the_guard():
    """A route added to AgentServer tomorrow is covered without anyone remembering."""
    app = _agent_server().build_api_app()
    routes = _api_routes(app)
    paths = {r.path for r in routes}
    # Guard the guard: an empty walk would pass trivially.
    assert {"/v1/chat/completions", "/v1/echo/init", "/v1/models"} <= paths
    unguarded = [f"{sorted(r.methods)} {r.path}" for r in routes if not _guarded(r)]
    assert unguarded == []


def test_every_mutating_openai_server_route_carries_a_guard():
    """Chat completions and the whole /v1/<agent>/* relay must be gated."""
    from gaia.api.openai_server import app

    mutating = [r for r in _api_routes(app) if r.methods & _MUTATING]
    assert any(r.path == "/v1/chat/completions" for r in mutating)
    assert len(mutating) >= 4, "route walk found too little to be meaningful"
    unguarded = [f"{sorted(r.methods)} {r.path}" for r in mutating if not _guarded(r)]
    assert unguarded == []


def test_no_server_configures_wildcard_origins_with_credentials():
    """``*`` + credentials makes Starlette reflect any Origin -- the C10 bug."""
    from gaia.api.openai_server import app as openai_app

    for app in (openai_app, _agent_server().build_api_app()):
        cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
        assert len(cors) == 1
        kwargs = cors[0].kwargs
        assert not ("*" in kwargs["allow_origins"] and kwargs["allow_credentials"])


# -- AgentServer behaviour ---------------------------------------------------


@pytest.fixture
def agent_client():
    return TestClient(_agent_server().build_api_app())


def test_foreign_origin_cannot_drive_the_agent(agent_client):
    resp = _chat(agent_client, Origin=EVIL)
    assert resp.status_code == 403
    assert local_http.CORS_ORIGINS_ENV in resp.json()["detail"]


def test_foreign_origin_preflight_is_not_approved(agent_client):
    resp = agent_client.options(
        "/v1/chat/completions",
        headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in resp.headers


def test_foreign_origin_read_is_not_reflected(agent_client):
    resp = agent_client.get("/v1/models", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in resp.headers


def test_loopback_origin_still_works(agent_client):
    resp = _chat(agent_client, Origin=LOCAL)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == LOCAL


def test_no_key_keeps_existing_consumers_working_and_warns(agent_client, caplog):
    """Backward compatible: unset key -> allowed, with one loud warning."""
    local_http._WARNED_SURFACES.clear()
    with caplog.at_level("WARNING"):
        assert _chat(agent_client).status_code == 200
        assert _chat(agent_client).status_code == 200
    warnings = [
        r for r in caplog.records if "NO caller authentication" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert local_http.API_KEY_ENV in warnings[0].getMessage()


def test_configured_key_is_enforced(agent_client, monkeypatch):
    monkeypatch.setenv(local_http.API_KEY_ENV, KEY)
    assert _chat(agent_client).status_code == 401
    assert _chat(agent_client, Authorization="Bearer nope").status_code == 401
    assert _chat(agent_client, Authorization=KEY).status_code == 401
    assert _chat(agent_client, Authorization=f"Bearer {KEY}").status_code == 200
    # Readiness endpoints are guarded too; only /health is public.
    assert agent_client.get("/v1/models").status_code == 401
    assert agent_client.get("/health").status_code == 200


def test_credentialed_cross_origin_read_gets_no_permissive_cors(agent_client):
    """The C10 bug: ``*`` + credentials reflected any Origin with ACAC: true."""
    resp = agent_client.get(
        "/v1/models", headers={"Origin": EVIL, "Cookie": "session=abc"}
    )
    # Starlette still emits ACAC: true, but without a matching ACAO the
    # browser's CORS check fails and the response stays unreadable.
    assert "access-control-allow-origin" not in resp.headers


def test_credentialed_preflight_from_unlisted_origin_is_not_approved(agent_client):
    resp = agent_client.options(
        "/v1/chat/completions",
        headers={
            "Origin": EVIL,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_operator_listed_origin_works_with_credentials(monkeypatch):
    listed = "https://app.example"
    monkeypatch.setenv(local_http.CORS_ORIGINS_ENV, listed)
    client = TestClient(_agent_server().build_api_app())
    resp = _chat(client, Origin=listed)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == listed
    assert resp.headers["access-control-allow-credentials"] == "true"


@pytest.mark.xfail(
    strict=True,
    reason="Caller auth for --api consumers is a breaking change; this release "
    "only warns when GAIA_API_KEY is unset (report section 10 item 7).",
)
def test_unauthenticated_loopback_call_is_refused_once_auth_is_mandatory(
    agent_client,
):
    assert _chat(agent_client).status_code == 401


# -- gaia api (openai_server) chat completions --------------------------------


@pytest.fixture
def openai_client():
    from gaia.api.openai_server import app

    return TestClient(app, raise_server_exceptions=False)


def _openai_chat(client, **headers):
    return client.post(
        "/v1/chat/completions",
        json={
            "model": "does-not-exist",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers=headers,
    )


def test_chat_completions_without_a_key_is_401_when_a_key_is_configured(
    openai_client, monkeypatch
):
    monkeypatch.setenv(local_http.API_KEY_ENV, KEY)
    resp = _openai_chat(openai_client)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert local_http.API_KEY_ENV in resp.json()["detail"]
    assert _openai_chat(openai_client, Authorization="Bearer wrong").status_code == 401


def test_chat_completions_with_the_key_reaches_the_handler(openai_client, monkeypatch):
    monkeypatch.setenv(local_http.API_KEY_ENV, KEY)
    resp = _openai_chat(openai_client, Authorization=f"Bearer {KEY}")
    assert resp.status_code == 404  # the handler's own unknown-model answer


def test_chat_completions_refuses_a_foreign_origin_even_with_the_key(
    openai_client, monkeypatch
):
    monkeypatch.setenv(local_http.API_KEY_ENV, KEY)
    resp = _openai_chat(openai_client, Origin=EVIL, Authorization=f"Bearer {KEY}")
    assert resp.status_code == 403


def test_chat_completions_stays_open_on_loopback_without_a_key(openai_client):
    """Backward compatible: no key configured -> the handler still runs."""
    assert _openai_chat(openai_client).status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="Mandatory caller auth on gaia api is deferred; unset GAIA_API_KEY "
    "only warns in this release (report section 10 item 7).",
)
def test_chat_completions_requires_a_key_once_auth_is_mandatory(openai_client):
    assert _openai_chat(openai_client).status_code == 401


def test_run_api_refuses_lan_bind_without_a_key(monkeypatch):
    started = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(k))
    with pytest.raises(local_http.UnauthenticatedBindError) as exc:
        _agent_server().run_api(host="0.0.0.0", port=8123)  # nosec B104
    assert local_http.API_KEY_ENV in str(exc.value)
    assert started == []

    monkeypatch.setenv(local_http.API_KEY_ENV, KEY)
    _agent_server().run_api(host="0.0.0.0", port=8123)  # nosec B104
    assert started


# -- Shared helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    "host,loopback",
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),  # nosec B104
        ("::", False),
        ("", False),
        ("192.168.1.20", False),
        ("my-workstation", False),
    ],
)
def test_is_loopback_bind(host, loopback):
    assert local_http.is_loopback_bind(host) is loopback


@pytest.mark.parametrize(
    "origin,allowed",
    [
        ("http://localhost:3000", True),
        ("http://127.0.0.1:4200", True),
        ("http://[::1]:8080", True),
        ("https://evil.example", False),
        ("http://localhost.evil.example", False),
        ("http://127.0.0.1.evil.example", False),
        ("null", False),
    ],
)
def test_is_allowed_origin(origin, allowed):
    assert local_http.is_allowed_origin(origin) is allowed


def test_missing_origin_is_not_rejected():
    """curl and SDK clients send no Origin; they must keep working."""
    assert local_http.origin_is_rejected("") is False
    assert local_http.origin_is_rejected(EVIL) is True


def test_required_surface_is_disabled_without_a_key():
    status, detail = local_http.check_api_key(None, surface="relay", required=True)
    assert status == 503
    assert local_http.API_KEY_ENV in detail
