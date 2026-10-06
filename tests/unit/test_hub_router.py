# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Agent Hub HTTP router (gaia.ui.routers.hub)."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gaia.agents.registry import AgentRegistry
from gaia.daemon.sidecars.errors import StopFailedError
from gaia.hub import catalog as catalog_mod
from gaia.hub import installer as installer_mod
from gaia.hub.catalog import UnifiedCatalog
from gaia.hub.installer import NotInstalledError
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
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


@pytest.fixture
def client_no_ui_header(app):
    """Client that opts out of ``tests/conftest.py``'s ``X-Gaia-UI`` default.

    Used by the tests that assert the CSRF guard refuses an unheadered
    request -- they would otherwise pass for the wrong reason.
    """
    client = TestClient(app)
    client.headers.pop("x-gaia-ui", None)
    return client


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
        called["trusted"] = kwargs.get("trusted")

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
    assert called.get("trusted") is True


def test_install_non_verified_python_refused_403(client, monkeypatch):
    # A non-verified PYTHON (community) agent without the trust opt-in is refused
    # synchronously at the router — the gate is not native-only.
    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {
            "id": "demo",
            "language": "python",
            "security_tier": "community",
        },
    )
    resp = client.post("/api/agents/install", json={"id": "demo"}, headers=UI)
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


def test_install_requires_ui_header(client_no_ui_header):
    resp = client_no_ui_header.post("/api/agents/install", json={"id": "demo"})
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


def test_uninstall_not_installed_404(client, monkeypatch):
    def boom(*a, **k):
        raise NotInstalledError("not installed")

    monkeypatch.setattr(installer_mod, "uninstall", boom)
    resp = client.delete("/api/agents/demo", headers=UI)
    assert resp.status_code == 404


def test_uninstall_requires_ui_header(client_no_ui_header):
    resp = client_no_ui_header.delete("/api/agents/demo")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Platform-selection binary installs (#2084)
# ---------------------------------------------------------------------------


def test_install_error_surfaces_via_install_status(client, monkeypatch):
    # Real InstallError from the platform-selection path (no artifact on this
    # host's platform) must flow through the existing progress-tracking
    # machinery unmodified — this drives the REAL installer.install(), not a
    # mock, so it also proves the new error path wires into _set_progress.
    bad_artifact = {
        "filename": "email-agent-nonexistent-plat",
        "path": "agents/email/0.1.0/email-agent-nonexistent-plat",
        "size_bytes": 4,
        "sha256": "0" * 64,
        "content_type": "application/octet-stream",
    }
    manifest = {
        "id": "email",
        "language": "python",
        "security_tier": "verified",  # isolate this test from the trust gate
        "latest_version": "0.1.0",
        "requirements": {"platforms": []},
        "versions": {
            "0.1.0": {
                "version": "0.1.0",
                "artifact": bad_artifact,
                "artifacts": [bad_artifact],
            }
        },
    }
    monkeypatch.setattr(catalog_mod, "fetch_manifest", lambda *a, **k: manifest)

    resp = client.post("/api/agents/install", json={"id": "email"}, headers=UI)
    assert resp.status_code == 202

    status_resp = client.get("/api/agents/email/install-status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "failed"
    assert body["error"]
    assert "email" in body["error"]


class _StopSidecarRecorder:
    """Fake ``daemon_client.stop_sidecar`` — records every call, optionally
    raising a scripted error (e.g. ``StopFailedError``)."""

    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def __call__(self, agent_id):
        self.calls.append(agent_id)
        if self._error is not None:
            raise self._error


def test_email_install_shuts_down_running_sidecar_first(client, monkeypatch):
    recorder = _StopSidecarRecorder()
    monkeypatch.setattr(daemon_client_module, "stop_sidecar", recorder)
    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {
            "id": "email",
            "language": "python",
            "security_tier": "verified",
        },
    )
    monkeypatch.setattr(installer_mod, "install", lambda agent_id, **k: None)
    resp = client.post("/api/agents/install", json={"id": "email"}, headers=UI)
    assert resp.status_code == 202
    assert recorder.calls == ["email"]


def test_email_uninstall_shuts_down_running_sidecar_first(client, monkeypatch):
    recorder = _StopSidecarRecorder()
    monkeypatch.setattr(daemon_client_module, "stop_sidecar", recorder)
    monkeypatch.setattr(installer_mod, "uninstall", lambda *a, **k: None)
    resp = client.delete("/api/agents/email", headers=UI)
    assert resp.status_code == 200
    assert recorder.calls == ["email"]


def test_email_install_proceeds_when_stop_sidecar_noops(client, monkeypatch):
    # No daemon / no running sidecar is a genuine no-op INSIDE stop_sidecar
    # itself now (attach-only, per #2142 T3) — the router just calls it and
    # lets it return normally; install still proceeds.
    recorder = _StopSidecarRecorder()
    monkeypatch.setattr(daemon_client_module, "stop_sidecar", recorder)
    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {
            "id": "email",
            "language": "python",
            "security_tier": "verified",
        },
    )
    called = {}
    monkeypatch.setattr(
        installer_mod, "install", lambda agent_id, **k: called.update(id=agent_id)
    )
    resp = client.post("/api/agents/install", json={"id": "email"}, headers=UI)
    assert resp.status_code == 202
    assert called.get("id") == "email"


def test_email_install_aborts_when_stop_fails(client, monkeypatch):
    # D-4b: a sidecar that survives a tree-kill must abort the install rather
    # than mutate a directory a still-live process holds open.
    recorder = _StopSidecarRecorder(
        error=StopFailedError("pid 4242 survived a tree-kill")
    )
    monkeypatch.setattr(daemon_client_module, "stop_sidecar", recorder)
    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {
            "id": "email",
            "language": "python",
            "security_tier": "verified",
        },
    )
    called = {}
    monkeypatch.setattr(
        installer_mod, "install", lambda agent_id, **k: called.update(id=agent_id)
    )
    resp = client.post("/api/agents/install", json={"id": "email"}, headers=UI)
    assert resp.status_code == 500
    assert "4242" in resp.json()["detail"]
    assert called == {}


def test_non_email_install_does_not_shutdown_sidecar(client, monkeypatch):
    recorder = _StopSidecarRecorder()
    monkeypatch.setattr(daemon_client_module, "stop_sidecar", recorder)
    monkeypatch.setattr(
        catalog_mod,
        "fetch_manifest",
        lambda *a, **k: {
            "id": "demo",
            "language": "python",
            "security_tier": "verified",
        },
    )
    monkeypatch.setattr(installer_mod, "install", lambda agent_id, **k: None)
    resp = client.post("/api/agents/install", json={"id": "demo"}, headers=UI)
    assert resp.status_code == 202
    assert recorder.calls == []
