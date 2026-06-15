# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Endpoint tests for the background-run surface (#1580 follow-up).

Covers ``GET /api/chat/active`` (sidebar running-indicator source) and
``GET /api/chat/attach`` (revisit reconnect), plus the new overlap guard
that rejects a second turn while a run is still active in the registry.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.run_manager import run_manager
from gaia.ui.server import create_app


@pytest.fixture
def app():
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts and ends with an empty run registry."""
    run_manager._runs.clear()
    yield
    run_manager._runs.clear()


@pytest.fixture
def session_id(client):
    return client.post("/api/sessions", json={}).json()["id"]


class _FakeRun:
    """Stand-in registry entry — enough for is_running / active_sessions."""

    def __init__(self, session_id):
        self.session_id = session_id
        self.handler = None


def test_active_empty_by_default(client):
    resp = client.get("/api/chat/active")
    assert resp.status_code == 200
    assert resp.json() == {"session_ids": []}


def test_active_reports_running_sessions(client):
    run_manager._runs["s1"] = _FakeRun("s1")
    run_manager._runs["s2"] = _FakeRun("s2")

    resp = client.get("/api/chat/active")
    assert resp.status_code == 200
    assert set(resp.json()["session_ids"]) == {"s1", "s2"}


def test_attach_404_when_not_running(client):
    resp = client.get("/api/chat/attach", params={"session_id": "nope"})
    assert resp.status_code == 404


def test_attach_streams_existing_run(client):
    """Attach replays a run's buffered events then closes on DONE."""
    run_manager._runs["live"] = _FakeRun("live")

    async def fake_attach(session_id):
        assert session_id == "live"
        yield 'data: {"type": "chunk", "content": "hi"}\n\n'
        yield 'data: {"type": "done", "content": "hi"}\n\n'

    with patch("gaia.ui.server._attach_chat_stream", fake_attach):
        resp = client.get("/api/chat/attach", params={"session_id": "live"})
        assert resp.status_code == 200
        body = resp.text
    assert '"type": "chunk"' in body
    assert '"type": "done"' in body


def test_send_409_when_run_already_active(client, session_id):
    """A new turn is rejected while a background run is still registered."""
    run_manager._runs[session_id] = _FakeRun(session_id)

    resp = client.post(
        "/api/chat/send",
        json={"session_id": session_id, "message": "hi", "stream": False},
    )
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"]


def test_delete_session_cancels_active_run(client, session_id):
    """Deleting a session cancels its run so it can't persist to a dead session."""
    import threading

    fake = _FakeRun(session_id)
    fake.handler = type("H", (), {"cancelled": threading.Event()})()
    run_manager._runs[session_id] = fake

    resp = client.delete(f"/api/sessions/{session_id}")
    assert resp.status_code == 200
    assert fake.handler.cancelled.is_set()
