# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access

"""History limits follow the context budget rather than pair/character clamps."""

from types import SimpleNamespace

import pytest

from gaia.ui._chat_helpers import _restore_model_history
from gaia.ui.database import ChatDatabase


@pytest.fixture
def history_db():
    db = ChatDatabase(":memory:")
    yield db
    db.close()


def _agent(device="gpu"):
    return SimpleNamespace(device=device, system_prompt="Test prompt")


def test_all_short_turns_survive_beyond_transcript_page(history_db):
    session = history_db.create_session()["id"]
    for index in range(60):
        history_db.add_message(session, "user", f"USER_{index}")
        history_db.add_message(session, "assistant", f"ASST_{index}")
    history_db.add_message(session, "user", "current unanswered turn")
    agent = _agent()
    _restore_model_history(agent, history_db, session, "current unanswered turn")
    assert len(agent.conversation_history) == 120
    assert agent.conversation_history[0]["content"] == "USER_0"
    assert agent.conversation_history[-1]["content"] == "ASST_59"


def test_long_messages_are_preserved_within_budget(history_db):
    session = history_db.create_session()["id"]
    text = "Long text " * 500
    history_db.add_message(session, "user", text)
    history_db.add_message(session, "assistant", text)
    agent = _agent("npu")
    _restore_model_history(agent, history_db, session, "followup")
    assert [message["content"] for message in agent.conversation_history] == [
        text,
        text,
    ]


def test_empty_session_clears_cached_agent_history(history_db):
    session = history_db.create_session()["id"]
    agent = _agent()
    agent.conversation_history = [{"role": "user", "content": "stale cached text"}]
    _restore_model_history(agent, history_db, session, "hello")
    assert agent.conversation_history == []


def test_equal_timestamps_keep_database_insertion_order(history_db):
    session = history_db.create_session()["id"]
    for role, content in [("user", "first"), ("assistant", "answer"), ("user", "next")]:
        history_db.add_message(session, role, content)
    history_db._conn.execute("UPDATE messages SET created_at = '2026-09-11T00:00:00Z'")
    agent = _agent()
    _restore_model_history(agent, history_db, session, "next")
    assert agent.conversation_history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
    ]
