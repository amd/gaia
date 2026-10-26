# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Sidebar search finds chats by message text, private chats included."""

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


def _chat(client, title, *messages, private=False):
    body = {"title": title, "private": private}
    sid = client.post("/api/sessions", json=body).json()["id"]
    for role, content in messages:
        client.app.state.db.add_message(sid, role, content)
    return sid


def _search(client, q):
    resp = client.get("/api/sessions/search", params={"q": q})
    assert resp.status_code == 200
    return resp.json()["session_ids"]


def test_finds_chat_by_answer_text_case_insensitively(client):
    hit = _chat(client, "Sky colour", ("assistant", "Rayleigh scattering bends blue."))
    _chat(client, "Other", ("user", "Plan a trip"))
    assert _search(client, "rayleigh") == [hit]


def test_matches_user_messages_too(client):
    hit = _chat(client, "Trip", ("user", "Plan a trip to Lisbon"))
    assert _search(client, "lisbon") == [hit]


def test_private_chats_are_searchable(client):
    hit = _chat(client, "Secret", ("user", "needle"), private=True)
    assert _search(client, "needle") == [hit]


def test_ignores_autonomous_and_system_rows(client):
    _chat(client, "Auto", ("autonomous", "needle"), ("system", "needle"))
    assert _search(client, "needle") == []


def test_wildcards_are_literal(client):
    hit = _chat(client, "Pct", ("user", "100% sure"))
    _chat(client, "Plain", ("user", "100 sure"))
    assert _search(client, "100%") == [hit]
    assert _search(client, "_") == []


def test_each_chat_listed_once_and_deleted_chats_dropped(client):
    sid = _chat(client, "Twice", ("user", "needle"), ("assistant", "needle again"))
    assert _search(client, "needle") == [sid]
    client.delete(f"/api/sessions/{sid}")
    assert _search(client, "needle") == []


def test_empty_query_is_rejected(client):
    assert client.get("/api/sessions/search", params={"q": ""}).status_code == 422
