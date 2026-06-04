# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Agent Hub HTTP router (gaia.ui.routers.hub)."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gaia.agents.registry import AgentRegistry
from gaia.hub import catalog as catalog_mod
from gaia.hub import installer as installer_mod
from gaia.hub.catalog import UnifiedCatalog
from gaia.hub.installer import InstalledAgent, InstallError, NotInstalledError
from gaia.ui.routers import hub as hub_router
from gaia.ui.server import create_app

UI = {"X-Gaia-UI": "1"}


@pytest.fixture
def app():
    app = create_app(db_path=":memory:")
    app.state.agent_registry = MagicMock(spec=AgentRegistry)
    # Bypass the localhost guard (TestClient host is "testclient").
    app.dependency_overrides[hub_router._require_localhost] = lambda: None
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    installer_mod.clear_progress()
    installer_mod._IN_PROGRESS.clear()  # noqa: SLF001
    yield
    installer_mod.clear_progress()
    installer_mod._IN_PROGRESS.clear()  # noqa: SLF001


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_catalog_returns_merged_list(client, monkeypatch):
    fake = UnifiedCatalog(
        agents=[{"id": "demo", "status": "available"}],
        offline=False,
        generated_at="2026-06-03T00:00:00Z",
    )
    monkeypatch.setattr(catalog_mod, "build_catalog", lambda *a, **k: fake)
    resp = client.get("/api/agents/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["agents"][0]["id"] == "demo"
    assert body["offline"] is False


def test_catalog_not_swallowed_by_agents_route(client, monkeypatch):
    # Regression guard: /api/agents/catalog must hit the hub router, not the
    # greedy GET /api/agents/{agent_id:path} in routers/agents.py.
    monkeypatch.setattr(
        catalog_mod,
        "build_catalog",
        lambda *a, **k: UnifiedCatalog(agents=[], offline=False),
    )
    resp = client.get("/api/agents/catalog")
    assert resp.status_code == 200


def test_catalog_offline_503_when_no_cache(client, monkeypatch):
    def boom(*a, **k):
        raise catalog_mod.CatalogError("no network and no cache")

    monkeypatch.setattr(catalog_mod, "build_catalog", boom)
    resp = client.get("/api/agents/catalog")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Install + status
# ---------------------------------------------------------------------------


def test_install_returns_202_and_schedules(client, monkeypatch):
    called = {}

    def fake_install(agent_id, **kwargs):
        called["id"] = agent_id

    monkeypatch.setattr(installer_mod, "install", fake_install)
    resp = client.post("/api/agents/install", json={"id": "demo"}, headers=UI)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    # BackgroundTasks run after the response in TestClient.
    assert called.get("id") == "demo"


def test_install_duplicate_returns_409(client, monkeypatch):
    monkeypatch.setattr(installer_mod, "is_installing", lambda _id: True)
    resp = client.post("/api/agents/install", json={"id": "demo"}, headers=UI)
    assert resp.status_code == 409


def test_install_requires_ui_header(client):
    resp = client.post("/api/agents/install", json={"id": "demo"})
    assert resp.status_code == 403


def test_install_status_polling(client):
    installer_mod._set_progress(  # noqa: SLF001
        "demo", status="running", phase="downloading", percent=30
    )
    resp = client.get("/api/agents/demo/install-status")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "downloading"


def test_install_status_unknown_404(client):
    resp = client.get("/api/agents/nope/install-status")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def test_uninstall_success(client, monkeypatch):
    monkeypatch.setattr(installer_mod, "uninstall", lambda *a, **k: None)
    resp = client.delete("/api/agents/demo", headers=UI)
    assert resp.status_code == 200
    assert resp.json()["status"] == "uninstalled"


def test_uninstall_builtin_refused(client):
    # Real uninstall refuses builtins -> 400 (no mock needed).
    resp = client.delete("/api/agents/chat", headers=UI)
    assert resp.status_code == 400


def test_uninstall_not_installed_404(client, monkeypatch):
    def boom(*a, **k):
        raise NotInstalledError("not installed")

    monkeypatch.setattr(installer_mod, "uninstall", boom)
    resp = client.delete("/api/agents/demo", headers=UI)
    assert resp.status_code == 404


def test_uninstall_requires_ui_header(client):
    resp = client.delete("/api/agents/demo")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def test_rollback_success(client, monkeypatch):
    restored = InstalledAgent(
        id="demo", version="1.0.0", language="python", installed_at="now"
    )
    monkeypatch.setattr(installer_mod, "rollback", lambda *a, **k: restored)
    resp = client.post("/api/agents/demo/rollback", headers=UI)
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.0.0"


def test_rollback_no_backup_400(client, monkeypatch):
    def boom(*a, **k):
        raise InstallError("no backup")

    monkeypatch.setattr(installer_mod, "rollback", boom)
    resp = client.post("/api/agents/demo/rollback", headers=UI)
    assert resp.status_code == 400
