# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Memory extractions from several chats, released at the same instant.

Each Agent UI chat has its own agent and its own connection to the one
memory.db, and a long model load releases every chat's queued extraction
together. They must neither raise, deadlock, nor store a fact twice.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import List
from unittest.mock import MagicMock

import pytest

import gaia.agents.base.memory as memory_mod
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore

CHATS = 6
SHARED_FACT = "User's favourite colour is teal"


class _Chat(MemoryMixin):
    """One Agent UI session: its own store on the shared DB, a mocked model."""

    def __init__(self, store: MemoryStore, chat):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = f"s{id(self)}"
        self._auto_extract_enabled = True
        self._original_user_input = None
        self.chat = chat

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        return []


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:59999")
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setattr(memory_mod, "EXTRACTION_IDLE_S", 0.0)


def _release_together(facts: List[str], db_path, caplog) -> List[str]:
    """Run one extraction per fact, all answering at one barrier; return rows."""
    barrier = threading.Barrier(len(facts), timeout=10)
    chats = []
    for fact in facts:
        ops = [
            {"op": "add", "category": "preference", "content": fact, "grounded": "user"}
        ]

        def send_messages(messages, cancel=None, _ops=ops, **kwargs):
            barrier.wait()
            return MagicMock(text=json.dumps(_ops), finish_reason="stop")

        llm = MagicMock()
        llm.send_messages.side_effect = send_messages
        chats.append(_Chat(MemoryStore(db_path=db_path), llm))
    try:
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base.memory"):
            for chat, fact in zip(chats, facts):
                chat._after_process_query(f"Please remember: {fact}", "Noted.")
            for chat in chats:
                assert chat.wait_for_memory_extraction(timeout=20), "deadlocked"
        failures = [r for r in caplog.records if "extraction" in r.getMessage()]
        assert not failures, [r.getMessage() for r in failures]
        assert not barrier.broken, "an extraction never reached the model"
        return [
            r[0]
            for r in chats[0]
            ._memory_store._conn.execute(
                "SELECT content FROM knowledge WHERE source = 'llm_extract'"
            )
            .fetchall()
        ]
    finally:
        for chat in chats:
            chat._memory_store.close()


def test_distinct_facts_from_every_chat_all_land(tmp_path, caplog):
    facts = [f"User's project number {i} is called Falcon{i}" for i in range(CHATS)]

    stored = _release_together(facts, tmp_path / "memory.db", caplog)

    assert sorted(stored) == sorted(facts)


def test_the_same_fact_from_every_chat_is_stored_once(tmp_path, caplog):
    stored = _release_together([SHARED_FACT] * CHATS, tmp_path / "memory.db", caplog)

    assert stored == [SHARED_FACT]
