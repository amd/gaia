# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ``stream=false`` send that the sidecar agent can't serve stores nothing.

It used to save the question first, so the chat showed it as a message that
never got an answer.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.security import AGENT_UI_CLIENT, CLIENT_HEADER_NAME
from gaia.ui.server import create_app

UI = {CLIENT_HEADER_NAME: AGENT_UI_CLIENT}

REFUSAL = (
    "The GAIA agent requires streaming (stream=true); "
    "non-streaming GAIA chat is not supported."
)


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


def _gaia_chat(client, title=None):
    # Straight to the database: creating it through the API needs the
    # flagship registered, which not every test job installs.
    return client.app.state.db.create_session(title=title, agent_type="gaia")["id"]


@pytest.fixture
def served_by_its_sidecar():
    with patch("gaia.ui._chat_helpers._should_relay_to_sidecar", return_value=True):
        yield


def test_a_refused_unstreamed_send_saves_no_message(client, served_by_its_sidecar):
    sid = _gaia_chat(client, title="New Task")
    with patch("gaia.ui.server._get_chat_response") as reply:
        r = client.post(
            "/api/chat/send",
            json={"session_id": sid, "message": "hello there", "stream": False},
            headers=UI,
        )
    assert r.status_code == 400
    assert r.json()["detail"] == REFUSAL
    reply.assert_not_called()
    assert client.get(f"/api/sessions/{sid}/messages").json()["messages"] == []
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["title"] == "New Task"
    assert session["message_count"] == 0
    assert client.app.state.db.turns_in_progress() == []


def test_the_refusal_leaves_the_chat_free_for_a_streamed_send(
    client, served_by_its_sidecar
):
    sid = _gaia_chat(client)
    client.post(
        "/api/chat/send",
        json={"session_id": sid, "message": "a", "stream": False},
        headers=UI,
    )

    async def _no_reply(db, session, request):
        return
        yield  # pragma: no cover - an async generator that yields nothing

    with patch("gaia.ui.server._stream_chat_response", _no_reply):
        r = client.post(
            "/api/chat/send", json={"session_id": sid, "message": "b"}, headers=UI
        )
    assert r.status_code == 200
    contents = [
        m["content"]
        for m in client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    ]
    assert contents == ["b"]


def test_an_in_process_agent_still_answers_unstreamed(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    with patch("gaia.ui.server._get_chat_response", return_value="hi back"):
        r = client.post(
            "/api/chat/send",
            json={"session_id": sid, "message": "hi", "stream": False},
        )
    assert r.status_code == 200
    contents = [
        m["content"]
        for m in client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    ]
    assert contents == ["hi", "hi back"]
