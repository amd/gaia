# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``/daemon/v1/setup``: the TUI's door to the Agent UI's own setup handlers.

The point of the route is that it is the SAME code as ``/api/setup``, so each
test drives both and compares, or proves the daemon keeps the UI's contract
(status codes, the 409 confirmation shape) rather than re-deriving it.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui import setup_plan as sp
from gaia.ui.routers import setup as setup_router

pytestmark = pytest.mark.allow_network  # TestClient only; nothing leaves the machine

TOKEN = "daemon-client-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
UI = {"X-Gaia-UI": "1"}

PLAN = {
    "profile": "gaia",
    "lemonade": {"mode": "gaia", "url": "http://127.0.0.1:9/api/v1", "reachable": True},
    "chat_model": {"id": "Gemma-4-E4B-it-GGUF", "user_set": False},
    "catalog": [],
    "models": [],
}

NO_FIT = {
    "code": "no_fit",
    "message": "Qwen3.6-35B-A3B-GGUF may not run on this PC: needs ~27 GB.",
    "model_id": "Qwen3.6-35B-A3B-GGUF",
    "need_gb": 26.8,
    "capacity_gb": 16.0,
    "acknowledge": "acknowledge_no_fit",
}


@pytest.fixture
def daemon():
    from gaia.daemon.setup_routes import build_setup_router

    app = FastAPI()
    app.include_router(build_setup_router(TOKEN))
    return TestClient(app)


@pytest.fixture
def ui():
    app = FastAPI()
    app.include_router(setup_router.router)
    return TestClient(app)


@pytest.fixture
def plan(monkeypatch):
    calls = []

    def build_plan(*a, **k):
        calls.append((a, k))
        return dict(PLAN)

    monkeypatch.setattr(setup_router, "build_plan", build_plan)
    return calls


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/plan"),
        ("post", "/chat-model"),
        ("post", "/models/skip"),
        ("post", "/models/download"),
        ("post", "/models/remove"),
        ("post", "/run"),
        ("get", "/status"),
        ("post", "/cancel"),
    ],
)
def test_every_route_needs_the_daemon_token(daemon, method, path):
    r = getattr(daemon, method)("/daemon/v1/setup" + path)
    assert r.status_code == 401


def test_a_wrong_token_is_refused(daemon, plan):
    r = daemon.get("/daemon/v1/setup/plan", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert plan == []


def test_plan_is_the_same_answer_as_the_ui(daemon, ui, plan):
    mine = daemon.get("/daemon/v1/setup/plan", headers=AUTH)
    theirs = ui.get("/api/setup/plan", headers=UI)
    assert mine.status_code == theirs.status_code == 200
    assert mine.json() == theirs.json() == PLAN


def test_plan_that_cannot_be_worked_out_is_503(daemon, monkeypatch):
    def broken():
        raise sp.SetupPlanError("Lemonade answered but could not list its models")

    monkeypatch.setattr(setup_router, "build_plan", broken)
    r = daemon.get("/daemon/v1/setup/plan", headers=AUTH)
    assert r.status_code == 503
    # The UI's own fixed wording; the exception text stays in the log.
    assert "couldn't work out the setup plan" in r.json()["detail"]
    assert "could not list" not in r.json()["detail"]


def test_chat_model_keeps_the_409_confirmation_shape(daemon, plan, monkeypatch):
    chosen = []
    monkeypatch.setattr(sp, "chat_model_warning", lambda model_id: dict(NO_FIT))
    monkeypatch.setattr(
        setup_router,
        "choose_chat_model",
        lambda model_id, acknowledge_no_fit=False: chosen.append(model_id),
    )
    body = {"model_id": "Qwen3.6-35B-A3B-GGUF"}
    r = daemon.post("/daemon/v1/setup/chat-model", json=body, headers=AUTH)
    assert r.status_code == 409
    # A structured detail, so the TUI can show "needs ~X GB; this PC has Y GB".
    assert r.json()["detail"] == NO_FIT
    assert chosen == []

    r = daemon.post(
        "/daemon/v1/setup/chat-model",
        json={**body, "acknowledge_no_fit": True},
        headers=AUTH,
    )
    assert r.status_code == 200 and r.json() == PLAN
    assert chosen == ["Qwen3.6-35B-A3B-GGUF"]


def test_chat_model_refusal_is_422(daemon, monkeypatch):
    def refuse(model_id):
        raise sp.UnknownModel(f"{model_id} is not a chat model GAIA knows")

    monkeypatch.setattr(sp, "chat_model_warning", refuse)
    r = daemon.post(
        "/daemon/v1/setup/chat-model", json={"model_id": "nope"}, headers=AUTH
    )
    assert r.status_code == 422
    assert "isn't a chat model" in r.json()["detail"]


def test_skip_saves_and_returns_the_plan(daemon, plan, monkeypatch):
    saved = []
    monkeypatch.setattr(sp, "set_model_skipped", lambda m, s: saved.append((m, s)))
    r = daemon.post(
        "/daemon/v1/setup/models/skip",
        json={"model_id": "Gemma-4-E4B-it-GGUF", "skipped": True},
        headers=AUTH,
    )
    assert r.status_code == 200 and r.json() == PLAN
    assert saved == [("Gemma-4-E4B-it-GGUF", True)]


def test_download_is_202_and_asks_about_low_disk_first(daemon, monkeypatch):
    low = {"code": "low_disk", "acknowledge": "acknowledge_low_disk", "drop": []}
    started = []
    monkeypatch.setattr(sp, "download_disk_warning", lambda m: dict(low))
    monkeypatch.setattr(sp, "prepare_download", lambda m, **k: ("client", m))
    monkeypatch.setattr(
        setup_router.runner,
        "download_model",
        lambda c, m, keep: started.append((m, keep)) or {"state": "idle"},
    )
    body = {"model_id": "Gemma-4-E4B-it-GGUF", "keep_trying": True}
    r = daemon.post("/daemon/v1/setup/models/download", json=body, headers=AUTH)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "low_disk"
    assert started == []

    r = daemon.post(
        "/daemon/v1/setup/models/download",
        json={**body, "acknowledge_low_disk": True},
        headers=AUTH,
    )
    assert r.status_code == 202
    assert started == [("Gemma-4-E4B-it-GGUF", True)]


def test_run_passes_keep_trying_through(daemon, monkeypatch):
    seen = []
    monkeypatch.setattr(sp, "run_disk_warning", lambda **k: None)
    monkeypatch.setattr(
        setup_router.runner,
        "start",
        lambda *a, **k: seen.append((a, k)) or {"state": "running"},
    )
    r = daemon.post("/daemon/v1/setup/run", json={"keep_trying": True}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"state": "running"}
    assert seen == [((False, False), {"allow_low_disk": False, "keep_trying": True})]


def test_status_is_idle_with_nothing_running(daemon, monkeypatch):
    monkeypatch.setattr(setup_router.runner, "status", lambda: None)
    r = daemon.get("/daemon/v1/setup/status", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"state": "idle"}


def test_cancel_with_and_without_a_model(daemon, monkeypatch):
    seen = []
    monkeypatch.setattr(
        setup_router.runner,
        "cancel",
        lambda model_id=None: seen.append(model_id) or True,
    )
    assert daemon.post("/daemon/v1/setup/cancel", headers=AUTH).json() == {
        "cancelled": True
    }
    r = daemon.post(
        "/daemon/v1/setup/cancel",
        json={"model_id": "Gemma-4-E4B-it-GGUF"},
        headers=AUTH,
    )
    assert r.json() == {"cancelled": True}
    assert seen == [None, "Gemma-4-E4B-it-GGUF"]


def test_the_daemon_app_mounts_the_setup_routes():
    from gaia.daemon.app import create_app

    app = create_app(token=TOKEN, port=1234, pid=111, started_at=0.0)
    routes = {getattr(r, "path", "") for r in app.routes}
    assert "/daemon/v1/setup/plan" in routes
    assert "/daemon/v1/setup/status" in routes
