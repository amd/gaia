# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Who may call each Agent UI route, decided by the listener a request reached.

Every route is in exactly one class, and ``test_every_route_is_classified``
fails for a route nobody has decided about:

* ``pc``: the PC running GAIA only (``require_local_pc``).
* ``not_on_listener``: stays on the PC; never a paired device
  (``refuse_on_device_listener``, or not listed in ``DEVICE_ROUTES``).
* ``remote``: the PC and allowed devices; only what ``DEVICE_ROUTES`` lists.
* ``web``: the web UI and the API docs, which the device listener never serves.
* ``pair``: the pairing redemption, only on the device listener, with no token.
* ``device_self``: a paired device unpairing itself, pending or allowed.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.routing import Mount

from gaia.config import GaiaConfig
from gaia.ui import security
from gaia.ui.devices.access import DeviceAccess
from gaia.ui.devices.listener import DeviceGate
from gaia.ui.devices.routes import DEVICE_ROUTES
from gaia.ui.security import (
    DEVICE_SCOPE_KEY,
    LAN_LISTENER,
    LISTENER_SCOPE_KEY,
    DevicePrincipal,
    ListenerScopeMiddleware,
)
from gaia.ui.server import create_app

pytestmark = pytest.mark.allow_network  # TestClient only; nothing leaves the machine

PC = ("127.0.0.1", 50000)

PC_ONLY = {
    "DELETE /api/automations/{ref}",
    "DELETE /api/devices/listener",
    "DELETE /api/devices/pairing",
    "DELETE /api/devices/{device_id}",
    "GET /api/automations",
    "GET /api/devices",
    "GET /api/devices/status",
    "GET /api/messaging/discord",
    "POST /api/automations/{ref}/{action}",
    "POST /api/devices/listener",
    "POST /api/devices/pairing",
    "POST /api/devices/pairing/reset",
    "POST /api/devices/{device_id}/allow",
    "POST /api/gpu-memory/apply",
    "POST /api/gpu-memory/revert",
    "POST /api/messaging/discord/connect",
    "POST /api/messaging/discord/disconnect",
    "POST /api/messaging/discord/forget-always",
    "POST /api/messaging/discord/pair",
    "POST /api/messaging/discord/restart",
    "POST /api/messaging/discord/start",
    "POST /api/messaging/discord/stop",
    "POST /api/setup/npu/memlock-fix",
    "PUT /api/chat/permissions",
}

NOT_ON_LISTENER = {
    "DELETE /api/agents/{agent_id}",
    "DELETE /api/connectors/{connector_id}/activations/{agent_id:path}",
    "DELETE /api/providers/{provider}/key",
    "GET /api/connectors/_debug",
    "GET /api/connectors/eval-mailbox",
    "GET /api/documents/{doc_id}/file",
    "GET /api/logs",
    "GET /api/logs/{source}",
    "GET /api/memory/stream-discovery",
    "GET /api/memory/stream-inference",
    "GET /api/providers",
    "GET /api/setup/report/{name}",
    "GET /api/tunnel/status",
    "POST /api/agents/install",
    "POST /api/connectors/eval-mailbox",
    "POST /api/connectors/{connector_id}/authorize",
    "POST /api/connectors/{connector_id}/authorize-device",
    "POST /api/connectors/{connector_id}/configure",
    "POST /api/connectors/{connector_id}/enable",
    "POST /api/connectors/{connector_id}/test",
    "POST /api/documents/index-folder",
    "POST /api/documents/upload-path",
    "POST /api/eval/scripted-user",
    "POST /api/files/open",
    "POST /api/lemonade/target",
    "POST /api/memory/admin/clear",
    "POST /api/memory/admin/seed",
    "POST /api/memory/commit-discovery",
    "POST /api/memory/commit-inference",
    "POST /api/memory/refresh-system-context",
    "POST /api/memory/reinitialize",
    "POST /api/providers/key-store/retry",
    "POST /api/setup/report",
    "POST /api/skills/link/add",
    "POST /api/tunnel/start",
    "POST /api/tunnel/stop",
    "POST /v1/connections/{provider}",
    "PUT /api/connectors/{connector_id}/activations/{agent_id:path}",
    "PUT /api/connectors/{connector_id}/grants/{agent_id:path}",
}

PAIR = {"POST /api/devices/pair"}

DEVICE_SELF = {"DELETE /api/devices/me"}

WEB = {
    "GET /docs",
    "GET /docs/oauth2-redirect",
    "GET /openapi.json",
    "GET /redoc",
    "GET /{full_path:spa_path}",
    "HEAD /docs",
    "HEAD /docs/oauth2-redirect",
    "HEAD /openapi.json",
    "HEAD /redoc",
}

#: What an allowed device may call: ``DEVICE_ROUTES`` in
#: ``gaia.ui.devices.routes``, less its own unpairing (``DEVICE_SELF``).
REMOTE = {
    "GET /api/chat/active",
    "GET /api/chat/attach",
    "GET /api/goals",
    "GET /api/goals/{goal_id}",
    "GET /api/health",
    "GET /api/schedules",
    "GET /api/schedules/{name:path}",
    "GET /api/schedules/{name:path}/results",
    "GET /api/sessions",
    "GET /api/sessions/{session_id}",
    "GET /api/sessions/{session_id}/messages",
    "GET /api/system/status",
    "POST /api/chat/cancel",
    "POST /api/chat/confirm-tool",
    "POST /api/chat/send",
    "POST /api/chat/user-input",
    "POST /api/sessions",
}

#: Routes with no guard of their own that the device listener still refuses,
#: because ``DEVICE_ROUTES`` doesn't list them. The PC keeps every one.
UNLISTED = {
    "DELETE /api/chat/permissions/{session_id}/grants",
    "DELETE /api/connectors/_flows/{flow_id}",
    "DELETE /api/connectors/{connector_id}",
    "DELETE /api/connectors/{connector_id}/grants/{agent_id:path}",
    "DELETE /api/documents/{doc_id}",
    "DELETE /api/goals/{goal_id}",
    "DELETE /api/gpu-memory/pending",
    "DELETE /api/memory/all",
    "DELETE /api/memory/knowledge/{knowledge_id}",
    "DELETE /api/providers/local/custom/{model_id}",
    "DELETE /api/schedules/{name:path}",
    "DELETE /api/sessions/{session_id}",
    "DELETE /api/sessions/{session_id}/documents/{doc_id}",
    "DELETE /api/sessions/{session_id}/messages/{message_id}",
    "DELETE /api/sessions/{session_id}/messages/{message_id}/and-below",
    "DELETE /api/skills/{name}",
    "DELETE /api/skills/{name}/preferences",
    "DELETE /v1/connections/{provider}",
    "GET /api/agents",
    "GET /api/agents/catalog",
    "GET /api/agents/{agent_id}/install-status",
    "GET /api/chat/context/{session_id}",
    "GET /api/chat/followup/{session_id}",
    "GET /api/chat/performance/{session_id}",
    "GET /api/chat/permissions",
    "GET /api/chat/permissions/{session_id}",
    "GET /api/connectors",
    "GET /api/connectors/",
    "GET /api/connectors/agent-mcps",
    "GET /api/connectors/events",
    "GET /api/connectors/{connector_id}",
    "GET /api/connectors/{connector_id}/activations",
    "GET /api/connectors/{connector_id}/grants",
    "GET /api/documents",
    "GET /api/documents/{doc_id}/status",
    "GET /api/files/browse",
    "GET /api/files/image",
    "GET /api/files/preview",
    "GET /api/files/search",
    "GET /api/goals/health",
    "GET /api/goals/pending-approval",
    "GET /api/goals/stats",
    "GET /api/gpu-memory",
    "GET /api/lemonade/detect",
    "GET /api/lemonade/target",
    "GET /api/mcp/servers",
    "GET /api/mcp/servers/{name}/tools",
    "GET /api/mcp/status",
    "GET /api/media/{kind}/{name}",
    "GET /api/memory/activity",
    "GET /api/memory/conversations",
    "GET /api/memory/conversations/search",
    "GET /api/memory/conversations/{session_id}",
    "GET /api/memory/embedding-coverage",
    "GET /api/memory/entities",
    "GET /api/memory/entities/{entity}",
    "GET /api/memory/knowledge",
    "GET /api/memory/knowledge/{knowledge_id}",
    "GET /api/memory/settings",
    "GET /api/memory/stats",
    "GET /api/memory/tools",
    "GET /api/memory/upcoming",
    "GET /api/onboarding/preflight",
    "GET /api/onboarding/start",
    "GET /api/providers/active",
    "GET /api/providers/local/context",
    "GET /api/providers/local/status",
    "GET /api/providers/{provider}/models",
    "GET /api/sessions/events",
    "GET /api/sessions/search",
    "GET /api/sessions/{session_id}/capabilities",
    "GET /api/sessions/{session_id}/changes",
    "GET /api/sessions/{session_id}/changes/git",
    "GET /api/sessions/{session_id}/changes/total",
    "GET /api/sessions/{session_id}/changes/{turn_id}/diff",
    "GET /api/sessions/{session_id}/environment",
    "GET /api/sessions/{session_id}/export",
    "GET /api/sessions/{session_id}/outputs",
    "GET /api/settings",
    "GET /api/settings/undo",
    "GET /api/setup/check",
    "GET /api/setup/needs/{job}",
    "GET /api/setup/plan",
    "GET /api/setup/status",
    "GET /api/skills",
    "GET /api/skills/featured",
    "GET /api/skills/preferences",
    "GET /api/skills/{name}/preferences",
    "GET /api/speech/model",
    "GET /api/speech/settings",
    "GET /api/spending",
    "GET /api/spending/chat/{session_id}",
    "GET /api/spending/prices/{provider}",
    "GET /api/system/model-server",
    "GET /api/system/resources",
    "GET /api/system/warm-up",
    "GET /api/system/work-in-progress",
    "GET /v1/connections",
    "GET /v1/connections/",
    "GET /v1/connections/{provider}",
    "GET /v1/email/calendar/events",
    "GET /v1/email/health",
    "GET /v1/email/init",
    "GET /v1/email/version",
    "MOUNT /api/files/uploads",
    "PATCH /api/sessions/{session_id}/private",
    "POST /api/chat/followup",
    "POST /api/chat/retry-with",
    "POST /api/chat/retry-with/choose",
    "POST /api/connectors/{connector_id}/disable",
    "POST /api/documents/upload",
    "POST /api/documents/{doc_id}/cancel",
    "POST /api/documents/{doc_id}/reindex",
    "POST /api/environment/folders/check",
    "POST /api/files/status",
    "POST /api/files/upload",
    "POST /api/files/upload-media",
    "POST /api/goals",
    "POST /api/goals/store/reset",
    "POST /api/goals/{goal_id}/tasks",
    "POST /api/gpu-memory/pending",
    "POST /api/memory/consolidate",
    "POST /api/memory/knowledge",
    "POST /api/memory/prune",
    "POST /api/memory/rebuild-embeddings",
    "POST /api/memory/rebuild-fts",
    "POST /api/memory/reconcile",
    "POST /api/providers/local/custom",
    "POST /api/providers/local/custom/check",
    "POST /api/providers/local/restart",
    "POST /api/providers/select",
    "POST /api/providers/{provider}/connect",
    "POST /api/schedules",
    "POST /api/schedules/parse",
    "POST /api/schedules/{name:path}/run",
    "POST /api/sessions/{session_id}/activate",
    "POST /api/sessions/{session_id}/changes/git/commit",
    "POST /api/sessions/{session_id}/changes/git/pr",
    "POST /api/sessions/{session_id}/changes/undo",
    "POST /api/sessions/{session_id}/changes/{turn_id}/undo",
    "POST /api/sessions/{session_id}/documents",
    "POST /api/sessions/{session_id}/environment/folders",
    "POST /api/sessions/{session_id}/environment/folders/remove",
    "POST /api/sessions/{session_id}/read",
    "POST /api/setup/cancel",
    "POST /api/setup/chat-model",
    "POST /api/setup/models/download",
    "POST /api/setup/models/remove",
    "POST /api/setup/models/skip",
    "POST /api/setup/npu/recheck",
    "POST /api/setup/run",
    "POST /api/setup/vision-model",
    "POST /api/skills/link/preview",
    "POST /api/speech",
    "POST /api/spending/allow-month",
    "POST /api/system/download-model",
    "POST /api/system/load-model",
    "POST /api/system/unload-model",
    "POST /api/system/warm-up",
    "POST /v1/email/archive",
    "POST /v1/email/calendar/events",
    "POST /v1/email/calendar/events/preview",
    "POST /v1/email/calendar/events/respond",
    "POST /v1/email/confirm",
    "POST /v1/email/draft",
    "POST /v1/email/init",
    "POST /v1/email/prescan",
    "POST /v1/email/quarantine",
    "POST /v1/email/search",
    "POST /v1/email/send",
    "POST /v1/email/triage",
    "POST /v1/email/triage/batch",
    "POST /v1/email/unarchive",
    "POST /v1/email/unquarantine",
    "PUT /api/chat/permissions/{session_id}",
    "PUT /api/goals/{goal_id}/approve",
    "PUT /api/goals/{goal_id}/cancel",
    "PUT /api/goals/{goal_id}/reject",
    "PUT /api/goals/{goal_id}/status",
    "PUT /api/goals/{goal_id}/tasks/{task_id}",
    "PUT /api/memory/knowledge/{knowledge_id}",
    "PUT /api/memory/settings",
    "PUT /api/providers/local/context",
    "PUT /api/schedules/{name:path}",
    "PUT /api/sessions/{session_id}",
    "PUT /api/sessions/{session_id}/environment/internet",
    "PUT /api/sessions/{session_id}/pin",
    "PUT /api/settings",
    "PUT /api/settings/undo",
    "PUT /api/skills/{name}/preferences",
    "PUT /api/speech/settings",
    "PUT /api/spending/limit",
    "PUT /api/system/model-server/backend",
}

EXPECTED = {
    **{r: "pc" for r in PC_ONLY},
    **{r: "not_on_listener" for r in NOT_ON_LISTENER},
    **{r: "pair" for r in PAIR},
    **{r: "device_self" for r in DEVICE_SELF},
    **{r: "web" for r in WEB},
    **{r: "remote" for r in REMOTE},
    **{r: "not_on_listener" for r in UNLISTED},
}

#: Who each class lets in.
CALLERS = ("pc", "device", "pending", "no_token")
LETS_IN = {
    "pc": {"pc"},
    "not_on_listener": {"pc"},
    "remote": {"pc", "device"},
    "web": {"pc"},
    "pair": {"device", "pending", "no_token"},
    "device_self": {"device", "pending"},
}
GUARDS = (
    security.require_local_pc,
    security.refuse_on_device_listener,
    security.require_device_listener,
    security.require_device,
)


def _routes(app):
    """``{"METHOD /path": route}`` for every route, through lazy includes."""
    found = {}

    def visit(routes):
        for route in routes:
            materialised = False
            for name in ("effective_candidates", "effective_low_priority_routes"):
                method = getattr(route, name, None)
                if callable(method):
                    visit(method())
                    materialised = True
            if materialised:
                continue
            if isinstance(route, Mount):
                found[f"MOUNT {route.path}"] = route
            elif isinstance(route, APIRoute) or hasattr(route, "original_route"):
                for method in route.methods:
                    found[f"{method} {route.path}"] = route
            elif hasattr(route, "routes"):
                visit(route.routes)
            else:
                for method in getattr(route, "methods", None) or ():
                    found[f"{method} {route.path}"] = route

    visit(app.routes)
    return found


def _guards(route):
    original = getattr(route, "original_route", route)
    dependant = getattr(original, "dependant", None)
    calls = {d.call for d in dependant.dependencies} if dependant else set()
    return [guard for guard in GUARDS if guard in calls]


def _access_class(key, route):
    if key.startswith("MOUNT "):
        return "not_on_listener"
    if not key.split(" ", 1)[1].startswith(("/api/", "/v1/")):
        return "web"
    guards = _guards(route)
    if security.require_local_pc in guards:
        return "pc"
    if security.refuse_on_device_listener in guards:
        return "not_on_listener"
    if security.require_device_listener in guards:
        return "pair"
    if security.require_device in guards:
        return "device_self"
    return "remote" if key in DEVICE_ROUTES else "not_on_listener"


def _request_for(caller, device):
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "client": PC}
    if caller in ("device", "pending", "no_token"):
        scope[LISTENER_SCOPE_KEY] = LAN_LISTENER
        if caller != "no_token":
            scope[DEVICE_SCOPE_KEY] = DevicePrincipal(
                device.device_id, caller == "device"
            )
    return Request(scope)


def _request_path(key):
    method, path = key.split(" ", 1)
    if method == "MOUNT":
        return "GET", path + "/x"
    return method, re.sub(r"\{[^}]+\}", "x", path)


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    app = create_app(db_path=":memory:")
    access = DeviceAccess(app, folder=tmp_path_factory.mktemp("devices") / "devices")
    app.state.devices = access
    invite, _ = access.pairing.open()
    allowed = access.redeem(invite, "Phone", "ios", "192.168.1.20")
    access.allow(allowed["device_id"])
    invite, _ = access.pairing.open()
    pending = access.redeem(invite, "Tablet", "ipados", "192.168.1.21")

    # From loopback: on this listener even that is not the PC.
    listener = ListenerScopeMiddleware(DeviceGate(app, access, allow_loopback=True))
    clients = {
        "pc": TestClient(app, client=PC),
        "device": TestClient(
            listener,
            client=PC,
            headers={"Authorization": f"Bearer {allowed['token']}"},
        ),
        "pending": TestClient(
            listener,
            client=PC,
            headers={"Authorization": f"Bearer {pending['token']}"},
        ),
        "no_token": TestClient(listener, client=PC),
    }
    return app, clients, DevicePrincipal(allowed["device_id"], True)


def test_every_route_is_classified(setup):
    """Each route's class, checked against every kind of caller.

    A refused caller is sent the request, which must be refused (401, 403 or
    404) before anything runs. A caller let in is checked against the route's
    guards without sending it, since the route would do its work.
    """
    app, clients, device = setup
    # On, so the device routes' own guards decide rather than the switch.
    GaiaConfig.save_key("device_pairing", True)
    routes = _routes(app)
    actual = {key: _access_class(key, route) for key, route in routes.items()}
    assert len(actual) > 200, "the route walk found too little to check"
    unclassified = sorted(set(actual) - set(EXPECTED))
    gone = sorted(set(EXPECTED) - set(actual))
    changed = sorted(
        f"{key}: {EXPECTED[key]} -> {actual[key]}"
        for key in set(actual) & set(EXPECTED)
        if actual[key] != EXPECTED[key]
    )
    assert not unclassified, f"decide who may call these routes: {unclassified}"
    assert not gone, f"these routes no longer exist: {gone}"
    assert not changed, f"these routes changed class: {changed}"

    wrong, sent, guarded = [], 0, 0
    for key, route in sorted(routes.items()):
        access_class = actual[key]
        method, path = _request_path(key)
        refusals = {404} if access_class in ("web", "pair") else {401, 403}
        if path.startswith("/api/devices"):
            # Reads are GETs; everything that changes state is POST or DELETE.
            assert method in ("GET", "POST", "DELETE"), key
        for caller in CALLERS:
            if caller in LETS_IN[access_class]:
                request = _request_for(caller, device)
                for guard in _guards(route):
                    guarded += 1
                    try:
                        guard(request)
                    except HTTPException as e:
                        wrong.append(f"{key}: {caller} refused ({e.status_code})")
                continue
            sent += 1
            status = clients[caller].request(method, path, json={}).status_code
            if status not in refusals:
                wrong.append(f"{key}: {caller} got {status}")
    assert not wrong, "\n".join(wrong)
    assert sent > 400 and guarded > 40, (sent, guarded)


def test_device_routes_name_real_routes(setup):
    """A listed route that doesn't exist would open nothing and hide a typo."""
    app, _, _ = setup
    routes = _routes(app)
    assert DEVICE_ROUTES <= set(routes), sorted(DEVICE_ROUTES - set(routes))
    assert DEVICE_ROUTES == REMOTE | DEVICE_SELF


@pytest.mark.parametrize(
    "path", ["/api/sessions/search", "/api/goals/stats", "/api/goals/health"]
)
def test_a_listed_template_opens_no_unlisted_route(setup, path):
    """``GET /api/sessions/{session_id}`` mustn't let a device reach a sibling."""
    _, clients, _ = setup
    response = clients["device"].get(path)
    assert response.status_code == 403
    assert response.json()["code"] == "pc_only"


def test_an_unlisted_route_is_refused_before_the_app_runs(setup):
    _, clients, _ = setup
    response = clients["device"].delete("/api/memory/all")
    assert response.status_code == 403
    assert response.json()["code"] == "pc_only"


def test_a_paired_device_reconnects_a_provider_only_as_the_pc_saved_it(
    setup, monkeypatch
):
    """Keys and connection settings are entered on the PC, never on a phone."""
    from gaia.ui.routers import providers

    app, clients, _ = setup
    handed = []
    monkeypatch.setattr(providers, "_install", lambda *a, **k: handed.append(a))
    for fields in (
        {"api_key": "fw-secret-key"},
        {"base_url": "https://gw.example/v1"},
        {"auth_header_name": "X-Key"},
    ):
        resp = clients["device"].post("/api/providers/fireworks/connect", json=fields)
        assert resp.status_code == 403, fields
        assert "fw-secret-key" not in resp.text
    assert handed == []


def test_troubleshooting_report_stays_on_the_pc(setup):
    """The report bundles every log; a paired device neither writes nor reads it."""
    from gaia.home import gaia_home

    app, clients, _ = setup
    ui = {"X-Gaia-UI": "1"}
    name = "diagnostics-20261112-101010.tgz"
    report = gaia_home() / name
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"logs that quote a conversation")
    try:
        got = clients["device"].get(f"/api/setup/report/{name}", headers=ui)
        assert got.status_code == 403
        assert b"conversation" not in got.content
        assert (
            clients["device"].post("/api/setup/report", headers=ui).status_code == 403
        )
        pc = clients["pc"].get(f"/api/setup/report/{name}", headers=ui)
        assert pc.status_code == 200
        assert pc.content == b"logs that quote a conversation"
    finally:
        report.unlink()


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/memory/stream-discovery", None),
        ("GET", "/api/memory/stream-inference?include_browser=true", None),
        ("POST", "/api/memory/commit-discovery", {"items": [{"content": "x"}]}),
        ("POST", "/api/memory/commit-inference", {"insights": [{"content": "x"}]}),
    ],
)
def test_memory_profile_scan_stays_on_the_pc(setup, monkeypatch, method, path, body):
    """The scan reads the PC's files and browsing; a phone can't start or save it."""
    from gaia.agents.base import discovery
    from gaia.ui.routers import memory

    app, clients, _ = setup
    touched = []

    class _Discovery:
        def __init__(self):
            touched.append("scan")
            raise RuntimeError("scanned")

    class _Store:
        def store(self, **kwargs):
            touched.append("store")

        def replace_by_source(self, *args, **kwargs):
            touched.append("store")
            return []

    monkeypatch.setattr(discovery, "SystemDiscovery", _Discovery)
    monkeypatch.setattr(memory, "_get_store", _Store)
    ui = {"X-Gaia-UI": "1"}
    resp = clients["device"].request(method, path, json=body, headers=ui)
    assert resp.status_code == 403, resp.text
    assert touched == []
    assert clients["pc"].request(method, path, json=body, headers=ui).status_code == 200
    assert touched
