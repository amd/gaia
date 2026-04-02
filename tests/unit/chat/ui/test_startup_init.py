# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for boot-time initialization and system status init_state."""

import logging

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

logger = logging.getLogger(__name__)


@pytest.fixture
def app():
    """Create FastAPI app with in-memory database."""
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    """Create test client that triggers lifespan (startup/shutdown)."""
    with TestClient(app) as c:
        yield c


# ── App Wiring ────────────────────────────────────────────────────────────


def test_create_app_has_dispatch_queue(app):
    """Lifespan wires up a DispatchQueue on app.state."""
    from gaia.ui.dispatch import DispatchQueue

    # Must enter TestClient context to trigger lifespan
    with TestClient(app):
        queue = getattr(app.state, "dispatch_queue", None)
        assert queue is not None
        assert isinstance(queue, DispatchQueue)


# ── /api/system/status init_state ─────────────────────────────────────────


def test_system_status_includes_init_state(client):
    """GET /api/system/status returns init_state field."""
    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "init_state" in data
    assert data["init_state"] in ("initializing", "ready", "degraded")


def test_system_status_includes_init_tasks(client):
    """GET /api/system/status returns init_tasks list."""
    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "init_tasks" in data
    assert isinstance(data["init_tasks"], list)


def test_system_status_defaults_without_queue():
    """SystemStatus defaults to init_state='ready' when constructed directly."""
    from gaia.ui.models import SystemStatus

    status = SystemStatus()
    assert status.init_state == "ready"
    assert status.init_tasks == []


# ── /api/system/tasks ─────────────────────────────────────────────────────


def test_tasks_endpoint_returns_list(client):
    """GET /api/system/tasks returns a list of tasks."""
    resp = client.get("/api/system/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert isinstance(data["tasks"], list)


def test_tasks_endpoint_returns_visible_only(app):
    """GET /api/system/tasks only returns visible=True jobs.

    The lifespan dispatches 3 visible startup tasks.  We verify the endpoint
    returns only those, not any internal (non-visible) jobs.
    """
    with TestClient(app) as c:
        queue = app.state.dispatch_queue
        # All startup tasks are visible — add a hidden job directly to the dict
        from gaia.ui.dispatch import Job, JobStatus

        hidden = Job(name="hidden job", visible=False, status=JobStatus.DONE)
        queue._jobs[hidden.id] = hidden

        resp = c.get("/api/system/tasks")
        data = resp.json()

        hidden_names = [t["name"] for t in data["tasks"] if t["name"] == "hidden job"]
        assert len(hidden_names) == 0
        # But visible startup tasks should be present
        assert len(data["tasks"]) >= 3


def test_tasks_endpoint_sanitizes_errors(client):
    """GET /api/system/tasks does not expose raw exception strings."""
    resp = client.get("/api/system/tasks")
    data = resp.json()
    for task in data["tasks"]:
        # error field should always be None (sanitized)
        assert task.get("error") is None


# ── Startup Tasks ─────────────────────────────────────────────────────────


def test_startup_dispatches_visible_tasks(app):
    """The lifespan dispatches at least 3 visible startup tasks."""
    with TestClient(app):
        queue = app.state.dispatch_queue
        visible = queue.get_visible_jobs()
        assert len(visible) >= 3

        names = {j.name for j in visible}
        assert "Checking LLM server" in names
        assert "Loading ML libraries" in names
        assert "Loading AI model" in names
