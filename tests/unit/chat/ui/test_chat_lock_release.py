# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn that fails before it starts must not leave its chat locked."""

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "chat.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("stream", [True, False])
def test_failed_link_resolution_does_not_lock_the_chat(client, monkeypatch, stream):
    sid = client.post("/api/sessions", json={}).json()["id"]

    def boom(_message):
        raise PermissionError("uploads folder is unreadable")

    monkeypatch.setattr("gaia.ui.routers.chat.resolve_upload_links", boom)

    body = {"session_id": sid, "message": "hello", "stream": stream}
    first = client.post("/api/chat/send", json=body)
    assert first.status_code == 500

    second = client.post("/api/chat/send", json=body)
    assert second.status_code != 409


def test_failed_yield_to_background_work_does_not_lock_the_chat(client, monkeypatch):
    sid = client.post("/api/sessions", json={}).json()["id"]
    monkeypatch.setattr("gaia.ui.routers.chat.turn_takes_the_line", lambda *a: True)

    def boom(_self):
        raise RuntimeError("background tick would not stop")

    monkeypatch.setattr("gaia.ui.agent_loop.AgentLoop.yield_to_user", boom)

    body = {"session_id": sid, "message": "hello", "stream": False}
    assert client.post("/api/chat/send", json=body).status_code == 500
    assert client.post("/api/chat/send", json=body).status_code != 409
