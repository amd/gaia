# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access

"""Persistent evidence and real chat route coverage without model inference."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base.history import transcript_turns
from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.server import create_app


def _trace(query="read fact"):
    return [
        {"role": "user", "content": query},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"fact.txt"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "read_file",
            "content": [{"type": "text", "text": "violet-otter-92"}],
        },
        {"role": "assistant", "content": "I read the fact."},
    ]


def test_database_restart_and_delete_preserve_then_remove_evidence(tmp_path):
    path = str(tmp_path / "history.db")
    db = ChatDatabase(path)
    session = db.create_session()["id"]
    db.add_message(session, "user", "read fact")
    msg_id = db.add_message(
        session, "assistant", "I read the fact.", model_messages=_trace()
    )
    db.close()
    db = ChatDatabase(path)
    assert transcript_turns(db.get_context_messages(session)) == [_trace()]
    assert "model_messages" not in db.get_messages(session)[1]
    db.delete_message(session, msg_id)
    assert transcript_turns(db.get_context_messages(session)) == []
    db.close()


def test_old_database_migration_is_additive(tmp_path):
    path = str(tmp_path / "legacy.db")
    db = ChatDatabase(path)
    session = db.create_session()["id"]
    db.add_message(session, "user", "old question")
    db.add_message(session, "assistant", "old answer")
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE messages DROP COLUMN model_messages")
    db = ChatDatabase(path)
    assert transcript_turns(db.get_context_messages(session)) == [
        [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]
    ]
    db.close()


def test_invalid_persisted_history_fails_loudly():
    db = ChatDatabase(":memory:")
    session = db.create_session()["id"]
    db.add_message(session, "assistant", "answer")
    db._conn.execute("UPDATE messages SET model_messages = 'broken-json'")
    with pytest.raises(ValueError, match="Cannot restore model history"):
        db.get_context_messages(session)
    db.close()


def test_upsert_replaces_evidence_atomically():
    db = ChatDatabase(":memory:")
    session = db.create_session()["id"]
    msg_id = db.add_message(session, "assistant", "old", model_messages=_trace())
    new_trace = _trace("updated")
    db.upsert_message(session, msg_id, "assistant", "new", model_messages=new_trace)
    assert len(db.get_context_messages(session)) == 1
    assert db.get_context_messages(session)[0]["model_messages"] == new_trace
    db.close()


def test_deleting_user_removes_replay_copy_from_assistant():
    db = ChatDatabase(":memory:")
    session = db.create_session()["id"]
    user_id = db.add_message(session, "user", "read fact")
    db.add_message(session, "assistant", "answer", model_messages=_trace())
    db.delete_message(session, user_id)
    assert db.get_context_messages(session)[0]["model_messages"] is None
    assert transcript_turns(db.get_context_messages(session)) == []
    db.close()


def test_legacy_role_migration_preserves_ids_indexes_and_sequence(tmp_path):
    path = str(tmp_path / "legacy-roles.db")
    db = ChatDatabase(":memory:")
    session = db.create_session()["id"]
    original_id = db.add_message(session, "user", "old question")
    removed_id = db.add_message(session, "assistant", "deleted")
    db.delete_message(session, removed_id)
    legacy = "\n".join(db._conn.iterdump()).replace(
        "'system', 'autonomous'", "'system'"
    )
    db.close()
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy)
    migrated = ChatDatabase(path)
    assert migrated.get_messages(session)[0]["id"] == original_id
    tick_id = migrated.add_message(
        session, "autonomous", "tick", model_messages=_trace()
    )
    assert tick_id > removed_id
    assert migrated._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrated._conn.execute("PRAGMA index_list(messages)").fetchall()
    assert migrated.count_messages(session) == 1
    assert len(migrated.get_messages(session)) == 1
    assert migrated.get_session(session)["message_count"] == 1
    assert transcript_turns(migrated.get_context_messages(session)) == [_trace()]
    migrated.delete_session(session)
    assert migrated.get_context_messages(session) == []
    migrated.close()


def test_role_migration_failure_rolls_back_ddl_and_can_retry():
    db = ChatDatabase(":memory:")
    schema = db._conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'messages'"
    ).fetchone()[0]
    db._conn.executescript(
        "DROP TABLE messages;" + schema.replace("'system', 'autonomous'", "'system'")
    )
    session = db.create_session()["id"]
    db.add_message(session, "user", "preserve me")

    def deny_drop(action, table, *_args):
        if action == sqlite3.SQLITE_DROP_TABLE and table == "messages":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db._conn.set_authorizer(deny_drop)
    with pytest.raises(sqlite3.DatabaseError):
        db._migrate_autonomous_role()
    db._conn.set_authorizer(None)
    assert (
        db._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'messages_history_migration'"
        ).fetchone()
        is None
    )
    assert db.get_messages(session)[0]["content"] == "preserve me"
    db._migrate_autonomous_role()
    db.add_message(session, "autonomous", "tick", model_messages=_trace())
    assert transcript_turns(db.get_context_messages(session)) == [_trace()]
    db.close()


@pytest.mark.parametrize("fails", [False, True])
def test_autonomous_execution_restores_and_persists_real_database(monkeypatch, fails):
    from gaia.ui.agent_loop import AgentLoop

    db = ChatDatabase(":memory:")
    session = db.create_session()
    db.add_message(session["id"], "user", "read fact")
    db.add_message(session["id"], "assistant", "answer", model_messages=_trace())
    captured = []

    class TickAgent:
        conversation_history = []
        device = "npu"
        system_prompt = "test"

        def _register_tools(self):
            pass

        def process_query(self, query):
            captured.append(self.conversation_history)
            if fails:
                raise RuntimeError("injected tick error")
            return {"result": "done", "model_messages": _trace(query)}

    monkeypatch.setattr(helpers, "_get_cached_agent", lambda *args: TickAgent())
    loop = AgentLoop()
    loop._db = db
    monkeypatch.setattr(loop, "_get_actionable_goals", lambda: [])
    asyncio.run(loop._execute_tick(session["id"], session, []))
    assert captured == [_trace()]
    rows = db.get_context_messages(session["id"])
    assert rows[-1]["role"] == "autonomous"
    if fails:
        assert rows[-1]["model_messages"] is None
    else:
        assert rows[-1]["model_messages"] == _trace(rows[-1]["content"])
    assert len(db.get_messages(session["id"])) == 2
    db.close()


@pytest.mark.parametrize("device,expected", [("npu", 16384), ("gpu", 32768)])
def test_budget_follows_device_and_reserves_overhead(monkeypatch, device, expected):
    db = ChatDatabase(":memory:")
    session = db.create_session()["id"]
    agent = SimpleNamespace(
        device=device,
        system_prompt="small",
        chat=SimpleNamespace(config=SimpleNamespace(max_tokens=8192)),
    )
    selection = MagicMock(return_value=[])
    monkeypatch.setattr("gaia.agents.base.history.select_history", selection)
    helpers._restore_model_history(agent, db, session, "query")
    assert selection.call_args.args[1] == expected
    agent.system_prompt = "very large system prompt"
    monkeypatch.setattr("gaia.agents.base.turn_metrics.count_tokens", lambda _: 20000)
    helpers._restore_model_history(agent, db, session, "query")
    assert selection.call_args.args[1] == min(
        expected, (32768 if device == "npu" else 65536) - 30240
    )
    db.close()


@pytest.mark.parametrize(
    "stream,blocked", [(False, False), (True, False), (True, True)]
)
def test_http_turn_persists_evidence_and_restores_it_to_fresh_agent(
    monkeypatch, stream, blocked
):
    app = create_app(db_path=":memory:")
    db = app.state.db
    session = db.create_session(agent_type="history-test")["id"]
    captured = []
    answer = (
        "Blocked: write_file is restricted by policy."
        if blocked
        else "I read the fact."
    )

    def trace(query="read fact"):
        messages = _trace(query)
        messages[-1]["content"] = answer
        return messages

    class FakeAgent:
        console = None
        model_id = "test-model"
        device = "gpu"
        system_prompt = "test"
        indexed_files = set()
        conversation_history = []

        def process_query(self, query):
            captured.append(self.conversation_history)
            if blocked:
                self.console.print_policy_alert(
                    "write_file", "BLOCK", "test policy", [], "1"
                )
            return {"result": answer, "model_messages": trace(query)}

    registry = MagicMock()
    registry.get.return_value = True
    registry.resolve_model.return_value = None
    registry.create_agent.side_effect = lambda *args, **kwargs: FakeAgent()
    monkeypatch.setattr(helpers, "_agent_registry", registry)
    monkeypatch.setattr(helpers, "_get_cached_agent", lambda *args: None)
    monkeypatch.setattr(helpers, "_store_agent", lambda *args: None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *args: None)
    monkeypatch.setattr(helpers, "_maybe_update_session_title", AsyncMock())
    monkeypatch.setattr("gaia.ui.routers.chat._notify_loop", lambda *args: None)
    stats = MagicMock(status_code=200)
    stats.json.return_value = {}
    monkeypatch.setattr("httpx.AsyncClient.get", AsyncMock(return_value=stats))
    client = TestClient(app)
    for query in ("read fact", "follow up"):
        response = client.post(
            "/api/chat/send",
            json={"session_id": session, "message": query, "stream": stream},
        )
        assert response.status_code == 200, response.text
        assert answer in response.text
    assert captured == [[], trace()]
    restored = transcript_turns(db.get_context_messages(session))
    assert restored == [trace(), trace("follow up")]
    assert "violet-otter-92" in json.dumps(restored)
    db.close()
