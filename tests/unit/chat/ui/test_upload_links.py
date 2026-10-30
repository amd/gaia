# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Composer attachment links reach the agent as file paths it can open."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app
from gaia.ui.utils import resolve_upload_links, uploads_dir


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    up = uploads_dir()
    up.mkdir(parents=True)
    (up / "abc.pdf").write_bytes(b"%PDF-1.4")
    (up / "shot.png").write_bytes(b"\x89PNG")
    return up.resolve()


def test_document_and_image_links_become_paths(home):
    msg = (
        "Summarize it\n\n[report.pdf](/api/files/uploads/abc.pdf)\n"
        "![shot.png](/api/files/uploads/shot.png)"
    )
    assert resolve_upload_links(msg) == (
        f"Summarize it\n\n[report.pdf]({home / 'abc.pdf'})\n"
        f"![shot.png]({home / 'shot.png'})"
    )


@pytest.mark.parametrize(
    "link",
    [
        "[x](/api/files/uploads/missing.pdf)",
        "[x](/api/files/uploads/..%2F..%2Fsecret.txt)",
        "[x](/api/files/uploads/../config.json)",
        "[x](/api/files/uploads/..)",
    ],
)
def test_links_to_nothing_in_uploads_are_left_alone(home, link):
    (home.parent / "config.json").write_text("{}")
    assert resolve_upload_links(f"see {link}") == f"see {link}"


def test_message_without_links_is_untouched(home):
    assert resolve_upload_links("plain text") == "plain text"


@pytest.fixture
def client(home):
    with TestClient(create_app(db_path=":memory:")) as c:
        yield c


def _session(client):
    return client.post("/api/sessions", json={}).json()["id"]


def test_non_streaming_agent_gets_path_and_transcript_keeps_link(client, home):
    sid = _session(client)
    msg = "What is in it?\n\n[report.pdf](/api/files/uploads/abc.pdf)"
    with patch("gaia.ui.server._get_chat_response") as chat:
        chat.return_value = "ok"
        resp = client.post(
            "/api/chat/send", json={"session_id": sid, "message": msg, "stream": False}
        )
    assert resp.status_code == 200
    sent = chat.call_args.args[2]
    assert sent.message == f"What is in it?\n\n[report.pdf]({home / 'abc.pdf'})"
    stored = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert stored[0]["content"] == msg


def test_streaming_agent_gets_path(client, home):
    sid = _session(client)
    msg = "[report.pdf](/api/files/uploads/abc.pdf)"
    with patch("gaia.ui.server._stream_chat_response") as stream:

        async def fake(*args, **kwargs):
            yield 'data: {"type": "done", "content": "ok"}\n\n'

        stream.return_value = fake()
        resp = client.post(
            "/api/chat/send", json={"session_id": sid, "message": msg, "stream": True}
        )
        assert resp.status_code == 200
    assert stream.call_args.args[2].message == f"[report.pdf]({home / 'abc.pdf'})"
    stored = client.get(f"/api/sessions/{sid}/messages").json()["messages"]
    assert stored[0]["content"] == msg
