# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What the chat list needs from the backend (#5221): which chats wait on
the user, how each last turn ended, unread, pin, and eval/scheduled kind."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase, session_kind_for_title
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager
from gaia.ui.server import create_app
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


# ── Kind ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title,kind",
    [
        ("Eval: rag_quality_01", "eval"),
        ("Schedule: morning brief", "scheduled"),
        ("Plan my trip", "chat"),
        (None, "chat"),
        ("My Eval: notes", "chat"),
    ],
)
def test_kind_follows_the_runner_title_prefix(title, kind):
    assert session_kind_for_title(title) == kind
    db = ChatDatabase(":memory:")
    assert db.create_session(title=title)["kind"] == kind


def test_explicit_kind_wins_and_unknown_kind_is_refused():
    db = ChatDatabase(":memory:")
    assert db.create_session(title="Nightly", kind="scheduled")["kind"] == "scheduled"
    with pytest.raises(ValueError, match="Unknown session kind"):
        db.create_session(title="x", kind="robot")


def test_existing_eval_and_scheduled_chats_are_backfilled(tmp_path):
    path = tmp_path / "chat.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT, updated_at TEXT, model TEXT NOT NULL DEFAULT 'm',
            system_prompt TEXT);
        INSERT INTO sessions (id, title, model) VALUES
            ('e', 'Eval: one', 'm'), ('s', 'Schedule: two', 'm'), ('c', 'Mine', 'm');
        """)
    conn.commit()
    conn.close()

    db = ChatDatabase(str(path))
    kinds = {s["id"]: s["kind"] for s in db.list_sessions()}
    assert kinds == {"e": "eval", "s": "scheduled", "c": "chat"}
    db.close()


# ── Pin / unread / last run ──────────────────────────────────────────────


def test_pin_and_read_endpoints_leave_the_chats_place_alone(client):
    sid = client.post("/api/sessions", json={"title": "Mine"}).json()["id"]
    before = client.get(f"/api/sessions/{sid}").json()
    assert before["pinned"] is False and before["unread"] is False
    assert before["kind"] == "chat" and before["last_run_status"] is None

    pinned = client.put(f"/api/sessions/{sid}/pin", json={"pinned": True}).json()
    assert pinned["pinned"] is True
    assert pinned["updated_at"] == before["updated_at"]
    assert (
        client.put(f"/api/sessions/{sid}/pin", json={"pinned": False}).json()["pinned"]
        is False
    )

    db = client.app.state.db
    db.finish_session_run(sid, "failed")
    listed = {s["id"]: s for s in client.get("/api/sessions").json()["sessions"]}
    assert listed[sid]["unread"] is True
    assert listed[sid]["last_run_status"] == "failed"

    read = client.post(f"/api/sessions/{sid}/read").json()
    assert read["unread"] is False
    assert read["last_run_status"] == "failed"


def test_pin_and_read_404_for_a_missing_chat(client):
    assert (
        client.put("/api/sessions/nope/pin", json={"pinned": True}).status_code == 404
    )
    assert client.post("/api/sessions/nope/read").status_code == 404


def test_unknown_run_status_is_refused():
    db = ChatDatabase(":memory:")
    sid = db.create_session()["id"]
    with pytest.raises(ValueError, match="Unknown run status"):
        db.finish_session_run(sid, "maybe")


@pytest.mark.parametrize(
    "frames,status",
    [
        (['data: {"type":"done","message_id":1,"content":"hi"}\n\n'], "done"),
        (['data: {"type":"done","message_id":1,"cancelled":true}\n\n'], "stopped"),
        (['data: {"type":"error","content":"boom"}\n\n'], "failed"),
        (['data: {"type":"status","message":"x"}\n\n: ' + "x" * 40 + "\n\n"], "failed"),
        ([": keepalive xxx\n\n"], "failed"),
    ],
)
def test_run_outcome_is_read_off_the_emitted_events(frames, status):
    outcome = helpers._RunOutcome()
    for f in frames:
        outcome.observe(f)
    assert outcome.status() == status


def test_a_malformed_frame_fails_loudly():
    with pytest.raises(ValueError, match="malformed SSE frame"):
        helpers._RunOutcome().observe("data: {not json\n\n")


# ── Non-streamed turns ───────────────────────────────────────────────────


def _send_plain(client, sid):
    return client.post(
        "/api/chat/send", json={"session_id": sid, "message": "hi", "stream": False}
    )


def test_a_plain_api_turn_records_done_and_unread(client):
    sid = client.post("/api/sessions", json={"title": "Mine"}).json()["id"]
    with patch("gaia.ui.server._get_chat_response", return_value="ok"):
        assert _send_plain(client, sid).status_code == 200
    after = client.get(f"/api/sessions/{sid}").json()
    assert after["last_run_status"] == "done" and after["unread"] is True


def test_a_plain_api_turn_that_raises_records_failed(client):
    sid = client.post("/api/sessions", json={"title": "Mine"}).json()["id"]
    with patch(
        "gaia.ui.server._get_chat_response", side_effect=RuntimeError("model died")
    ):
        with pytest.raises(RuntimeError, match="model died"):
            _send_plain(client, sid)
    assert client.get(f"/api/sessions/{sid}").json()["last_run_status"] == "failed"


def test_a_plain_api_turn_refused_before_it_ran_records_nothing():
    from gaia.ui.turn_queue import TurnQueue

    app = create_app(db_path=":memory:")
    sid = app.state.db.create_session(title="Mine")["id"]
    queue = TurnQueue()

    async def _refused(awaitable, timeout):  # noqa: ARG001
        awaitable.close()
        raise asyncio.TimeoutError

    with (
        patch("gaia.ui.routers.chat.turn_queue", queue),
        patch("gaia.ui.routers.chat.asyncio.wait_for", _refused),
    ):
        assert _send_plain(TestClient(app), sid).status_code == 429
    row = app.state.db.get_session(sid)
    assert row["last_run_status"] is None and not row["unread"]


def test_a_db_error_recording_the_outcome_does_not_fail_the_answer(client):
    sid = client.post("/api/sessions", json={"title": "Mine"}).json()["id"]
    with (
        patch("gaia.ui.server._get_chat_response", return_value="ok"),
        patch.object(
            ChatDatabase,
            "finish_session_run",
            side_effect=sqlite3.OperationalError("database is locked"),
        ),
    ):
        resp = _send_plain(client, sid)
    assert resp.status_code == 200 and resp.json()["content"] == "ok"


# ── Waiting on the user ──────────────────────────────────────────────────


def test_handler_reports_what_the_turn_waits_on():
    h = SSEOutputHandler()
    assert h.waiting_on() is None
    h._confirm_id = "c1"
    assert h.waiting_on() == "permission"
    h._confirm_id = None
    h.set_relay_confirmation("r1")
    assert h.waiting_on() == "permission"
    h.set_relay_confirmation(None)
    h._user_input_events["q1"] = object()
    assert h.waiting_on() == "question"


@pytest.mark.asyncio
async def test_active_runs_name_the_chats_waiting_on_the_user():
    manager = RunManager()
    asking, working, starting = SSEOutputHandler(), SSEOutputHandler(), None
    asking._confirm_id = "c1"
    manager._runs = {
        "a": SimpleNamespace(handler=asking),
        "b": SimpleNamespace(handler=working),
        "c": SimpleNamespace(handler=starting),
    }
    with patch.object(chat, "run_manager", manager):
        active = await chat.list_active_runs()
    assert active["session_ids"] == ["a", "b", "c"]
    assert active["awaiting_user"] == {"a": "permission"}
    assert active["queued"] == []
