# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A message with no text is refused before it reaches the model.

Stress testing sent "   \\n\\t " to /api/chat/send: it ran a full 27-second
turn and stored an empty exchange in the chat. The composer never sends one
(it trims, and an attachment adds text), so only a script or a bug can.
"""

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

UI = {"X-Gaia-UI": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    return TestClient(
        create_app(db_path=":memory:"), base_url="http://127.0.0.1", headers=UI
    )


@pytest.mark.parametrize(
    "message", ["", "   ", "\n\t \r\n"], ids=["empty", "spaces", "whitespace"]
)
def test_a_blank_message_is_refused_and_nothing_is_stored(client, message):
    sid = client.post("/api/sessions", json={}).json()["id"]
    r = client.post(
        "/api/chat/send", json={"session_id": sid, "message": message, "stream": True}
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "The message is empty."
    messages = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert messages == []
