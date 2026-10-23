# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A chat started private on the new-chat screen is private from its first
turn: the flag is set when the session is created, not toggled afterwards."""

import pytest
from fastapi.testclient import TestClient

from gaia.ui._chat_helpers import _memory_mode, _memory_off
from gaia.ui.memory_settings import memory_enabled
from gaia.ui.server import create_app


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


def test_session_created_private_is_private_before_any_turn(client):
    resp = client.post("/api/sessions", json={"title": "New Task", "private": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["private"] is True

    db = client.app.state.db
    session = db.get_session(body["id"])
    # Memory is on, yet the first turn must neither save nor recall anything.
    assert memory_enabled(db) is True
    assert _memory_off(session, db) is True
    assert _memory_mode(session, db) == "private"
    assert client.get(f"/api/sessions/{body['id']}").json()["private"] is True


def test_session_created_without_the_flag_remembers(client):
    body = client.post("/api/sessions", json={"title": "New Task"}).json()
    assert body["private"] is False

    db = client.app.state.db
    assert _memory_mode(db.get_session(body["id"]), db) == "on"
