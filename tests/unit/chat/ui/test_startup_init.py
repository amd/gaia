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


def test_status_init_tasks_has_no_error_field(client):
    """init_tasks in /api/system/status should never contain error strings."""
    resp = client.get("/api/system/status")
    data = resp.json()
    for task in data.get("init_tasks", []):
        # InitTaskInfo has only name + status — no error field
        assert "error" not in task


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


# ── Boot never seeds a local model for a cloud selection ──────────────────


def _run_boot_check(app):
    """Run the lifespan until the "Checking LLM server" job finishes."""
    import time

    from gaia.ui.dispatch import JobStatus

    with TestClient(app):
        queue = app.state.dispatch_queue
        job = next(j for j in queue.get_all_jobs() if j.name == "Checking LLM server")
        deadline = time.monotonic() + 10
        while job.status not in (JobStatus.DONE, JobStatus.FAILED):
            assert time.monotonic() < deadline, "boot check never finished"
            time.sleep(0.05)
        return job


@pytest.mark.parametrize(
    "selected", ["fireworks.deepseek-v4p1-flash", "Gemma-4-E4B-it-GGUF"]
)
def test_boot_check_never_loads_a_model(app, selected):
    """The boot reachability check loads nothing on an idle Lemonade, local or
    cloud selection: seeding there downloaded the model before setup ran."""
    from unittest.mock import MagicMock, patch

    from gaia.llm.lemonade_client import LemonadeStatus
    from gaia.llm.lemonade_manager import LemonadeManager
    from gaia.ui.dispatch import JobStatus

    app.state.db.set_setting("custom_model", selected)
    client = MagicMock()
    client.base_url = "http://localhost:13305/api/v1"
    client.get_model_max_context_window.return_value = None
    client.get_status.return_value = LemonadeStatus(
        running=True, context_size=0, loaded_models=[]
    )

    LemonadeManager.reset()
    try:
        with patch("gaia.llm.lemonade_manager.LemonadeClient", return_value=client):
            job = _run_boot_check(app)
    finally:
        LemonadeManager.reset()

    assert job.status == JobStatus.DONE
    client.load_model.assert_not_called()
    client.pull_model.assert_not_called()


def _run_boot_model_load(app, downloaded):
    """Run the "Loading AI model" boot job against an idle, mocked Lemonade."""
    import contextlib
    import time
    from unittest.mock import MagicMock, patch

    from gaia.ui.dispatch import JobStatus

    app.state.db.set_setting("custom_model", "Gemma-4-E4B-it-GGUF")
    lemonade = MagicMock()
    lemonade.get_model_info.return_value = {
        "id": "Gemma-4-E4B-it-GGUF",
        "downloaded": downloaded,
        "size_gb": 5.0,
    }
    idle = MagicMock(status_code=200)
    idle.json.return_value = {"all_models_loaded": []}

    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("httpx.get", return_value=idle),
        patch(
            "gaia.daemon.broker_client.model_lease",
            lambda *a, **k: contextlib.nullcontext(),
        ),
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=lemonade),
    ):
        with TestClient(app):
            queue = app.state.dispatch_queue
            job = next(j for j in queue.get_all_jobs() if j.name == "Loading AI model")
            deadline = time.monotonic() + 10
            while job.status not in (JobStatus.DONE, JobStatus.FAILED):
                assert time.monotonic() < deadline, "boot model load never finished"
                time.sleep(0.05)
    return job, lemonade


def test_boot_preload_skips_an_undownloaded_model(app, caplog):
    """`gaia init --skip-models` then opening the UI must not start a download."""
    from gaia.ui.dispatch import JobStatus

    with caplog.at_level(logging.INFO, logger="gaia.ui.server"):
        job, lemonade = _run_boot_model_load(app, downloaded=False)

    assert job.status == JobStatus.DONE
    lemonade.load_model.assert_not_called()
    lemonade.pull_model.assert_not_called()
    assert any("not downloaded" in r.getMessage() for r in caplog.records)


def test_boot_preload_loads_a_downloaded_model(app):
    from gaia.ui.dispatch import JobStatus

    job, lemonade = _run_boot_model_load(app, downloaded=True)

    assert job.status == JobStatus.DONE
    lemonade.load_model.assert_called_once()
    assert lemonade.load_model.call_args.args[0] == "Gemma-4-E4B-it-GGUF"
