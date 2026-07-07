# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Agent Hub HTTP router (gaia.ui.routers.hub)."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gaia.agents.registry import AgentRegistry
from gaia.hub import catalog as catalog_mod
from gaia.hub import installer as installer_mod
from gaia.hub import lifecycle as lifecycle_mod
from gaia.hub.catalog import UnifiedCatalog
from gaia.hub.installer import InstalledAgent, InstallError, NotInstalledError
from gaia.hub.lifecycle import AgentStatus, HealthStatus
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
        called["trust_native"] = kwargs.get("trust_native")

    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {"id": "demo", "language": "python"},
    )
    monkeypatch.setattr(installer_mod, "install", fake_install)
    resp = client.post(
        "/api/agents/install", json={"id": "demo", "trust_native": True}, headers=UI
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    # BackgroundTasks run after the response in TestClient.
    assert called.get("id") == "demo"
    assert called.get("trust_native") is True


def test_install_native_non_verified_refused_403(client, monkeypatch):
    # A community native agent without trust_native is refused synchronously.
    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {
            "id": "native",
            "language": "cpp",
            "security_tier": "community",
        },
    )
    resp = client.post("/api/agents/install", json={"id": "native"}, headers=UI)
    assert resp.status_code == 403
    assert "trust" in resp.json()["detail"].lower()


def test_install_native_non_verified_allowed_with_trust(client, monkeypatch):
    called = {}
    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {
            "id": "native",
            "language": "cpp",
            "security_tier": "community",
        },
    )
    monkeypatch.setattr(
        installer_mod, "install", lambda agent_id, **k: called.update(id=agent_id)
    )
    resp = client.post(
        "/api/agents/install",
        json={"id": "native", "trust_native": True},
        headers=UI,
    )
    assert resp.status_code == 202
    assert called.get("id") == "native"


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
    # Real uninstall refuses builtins -> 400 (no mock needed). ``builder`` is the
    # only remaining framework builtin after the #1102 hub migrations.
    resp = client.delete("/api/agents/builder", headers=UI)
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


# ---------------------------------------------------------------------------
# Lifecycle: configure / health / status (#465)
# ---------------------------------------------------------------------------


def test_set_config_success(client, monkeypatch):
    captured = {}

    def fake_configure(agent_id, config, *, merge):
        captured["id"] = agent_id
        captured["config"] = config
        captured["merge"] = merge
        return config

    monkeypatch.setattr(lifecycle_mod, "configure", fake_configure)
    resp = client.post(
        "/api/agents/demo/config",
        json={"config": {"model": "m1"}},
        headers=UI,
    )
    assert resp.status_code == 200
    assert resp.json()["config"] == {"model": "m1"}
    assert captured["merge"] is True


def test_set_config_replace_flag(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        lifecycle_mod,
        "configure",
        lambda agent_id, config, *, merge: captured.update(merge=merge) or config,
    )
    client.post(
        "/api/agents/demo/config",
        json={"config": {"model": "m1"}, "replace": True},
        headers=UI,
    )
    assert captured["merge"] is False


def test_set_config_requires_ui_header(client):
    resp = client.post("/api/agents/demo/config", json={"config": {"a": 1}})
    assert resp.status_code == 403


def test_get_config(client, monkeypatch):
    monkeypatch.setattr(lifecycle_mod, "read_config", lambda *a, **k: {"model": "m1"})
    resp = client.get("/api/agents/demo/config")
    assert resp.status_code == 200
    assert resp.json()["config"] == {"model": "m1"}


def test_health_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        lifecycle_mod,
        "health_check",
        lambda *a, **k: HealthStatus(id="demo", state="healthy", detail="ok"),
    )
    resp = client.get("/api/agents/demo/health")
    assert resp.status_code == 200
    assert resp.json()["state"] == "healthy"


def test_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        lifecycle_mod,
        "status",
        lambda *a, **k: AgentStatus(
            id="demo",
            installed=True,
            installed_version="1.2.3",
            health="healthy",
            config={"model": "m1"},
            source="installed",
        ),
    )
    resp = client.get("/api/agents/demo/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed_version"] == "1.2.3"
    assert body["health"] == "healthy"


# ---------------------------------------------------------------------------
# Setup executor (#468)
# ---------------------------------------------------------------------------


def test_setup_returns_202_and_schedules(client, monkeypatch):
    called = {}
    monkeypatch.setattr(catalog_mod, "fetch_manifest", lambda aid, *a, **k: {"id": aid})
    monkeypatch.setattr(
        installer_mod,
        "run_setup",
        lambda manifests, **k: called.update(ids=sorted(manifests)),
    )
    resp = client.post("/api/agents/setup", json={"ids": ["a", "b"]}, headers=UI)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert called.get("ids") == ["a", "b"]


def test_setup_empty_ids_400(client):
    resp = client.post("/api/agents/setup", json={"ids": []}, headers=UI)
    assert resp.status_code == 400


def test_setup_requires_ui_header(client):
    resp = client.post("/api/agents/setup", json={"ids": ["a"]})
    assert resp.status_code == 403


def test_setup_status_polling(client, monkeypatch):
    monkeypatch.setattr(
        installer_mod,
        "get_setup_status",
        lambda *a, **k: {"status": "running", "steps": []},
    )
    resp = client.get("/api/agents/setup-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def test_setup_status_unknown_404(client, monkeypatch):
    monkeypatch.setattr(installer_mod, "get_setup_status", lambda *a, **k: None)
    resp = client.get("/api/agents/setup-status")
    assert resp.status_code == 404


def test_setup_status_not_swallowed_by_agents_route(client, monkeypatch):
    # Regression guard: /api/agents/setup-status must hit the hub router, not
    # the greedy GET /api/agents/{agent_id:path} in routers/agents.py.
    monkeypatch.setattr(
        installer_mod, "get_setup_status", lambda *a, **k: {"status": "completed"}
    )
    resp = client.get("/api/agents/setup-status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
