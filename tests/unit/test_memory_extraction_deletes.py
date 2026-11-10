# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Background extraction never erases a memory the user didn't ask to remove.

In v0.25.4-rc11, "Forget that I'm allergic to shellfish" erased the shellfish
row through ``forget``, and the extraction job that ran after the turn then
hard-deleted "favourite tea is lapsang-31X": the model saw "forget" and
pointed a delete at another id. Nothing logged it, and the log called it
"1 memory stored". These run against a real ``MemoryStore``; only the model
is stubbed.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore


class _AgentBase:
    """Stands in for ``Agent``: runs the queued tool calls, then the post hook."""

    def process_query(self, user_input, **kwargs):
        for name, args, result in self.queued_calls:
            self.next_result = result
            self._execute_tool(name, args)
        self._after_process_query(user_input, self.answer)
        self.wait_for_memory_extraction(timeout=10)
        return {"result": self.answer}

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        if tool_name == "forget":
            self._memory_store.forget(tool_args["knowledge_id"])
        return self.next_result


class _Host(MemoryMixin, _AgentBase):
    def __init__(self, store: MemoryStore, extracted_ops: List[Dict]):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._auto_extract_enabled = True
        self.queued_calls: List[Tuple[str, Dict, Any]] = []
        self.answer = "Done."
        self._faiss_index = None
        self._faiss_id_map = {}
        self.chat = MagicMock()
        self.chat.send_messages.return_value = MagicMock(text=json.dumps(extracted_ops))

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        # What the extractor is shown: every live row, as on a small memory.
        rows = self._memory_store._conn.execute(
            "SELECT id, category, content FROM knowledge WHERE superseded_by IS NULL"
        ).fetchall()
        return [{"id": r[0], "category": r[1], "content": r[2]} for r in rows]


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@pytest.fixture
def memories(store):
    return {
        name: store.store(category="fact", content=content, context="global")
        for name, content in {
            "tea": "User's favourite tea is lapsang-31X",
            "shellfish": "User is allergic to shellfish",
            "cat": "User's cat is called Pixel-9R",
        }.items()
    }


def _delete(kid: str, reason: str = "user asked to forget it") -> Dict:
    return {"op": "delete", "knowledge_id": kid, "reason": reason, "grounded": "user"}


def _forgotten(store: MemoryStore) -> List[str]:
    return [r[0] for r in store._conn.execute("SELECT content FROM forgotten")]


class TestAForgetTurnErasesOnlyWhatWasForgotten:
    def test_extraction_after_forget_keeps_the_other_memories(self, store, memories):
        """The rc11 repro: forgetting shellfish also took the tea."""
        host = _Host(store, [_delete(memories["tea"])])
        host.queued_calls = [
            (
                "forget",
                {"knowledge_id": memories["shellfish"]},
                {"status": "removed"},
            )
        ]

        host.process_query("Forget that I'm allergic to shellfish.")

        assert store.get_item(memories["shellfish"]) is None
        assert store.get_item(memories["tea"]) is not None
        assert store.get_item(memories["cat"]) is not None

    def test_no_delete_runs_on_a_forget_turn_even_when_it_names_the_subject(
        self, store, memories
    ):
        host = _Host(store, [_delete(memories["cat"])])
        host.queued_calls = [
            (
                "forget",
                {"knowledge_id": memories["shellfish"]},
                {"status": "removed"},
            )
        ]

        host.process_query("Forget the shellfish thing, and my cat Pixel-9R too")

        assert store.get_item(memories["cat"]) is not None

    def test_a_failed_forget_does_not_block_the_delete(self, store, memories):
        """A made-up id erases nothing, so extraction's delete still applies."""
        host = _Host(store, [_delete(memories["shellfish"])])
        host.queued_calls = [
            (
                "forget",
                {"knowledge_id": "made-up-id"},
                {"status": "error", "message": "No memory has id 'made-up-id'."},
            )
        ]

        host.process_query("Forget that I'm allergic to shellfish.")

        assert store.get_item(memories["shellfish"]) is None
        assert store.get_item(memories["tea"]) is not None
        assert store.get_item(memories["cat"]) is not None


class TestADeleteMustBeOneTheTurnContradicts:
    def test_a_delete_for_an_id_the_model_was_not_shown_is_dropped(self, store):
        hidden = store.store(
            category="fact", content="User's cat is called Pixel-9R", context="global"
        )
        host = _Host(store, [_delete(hidden, "contradicted")])
        host._hybrid_search = lambda query, **kwargs: []

        host.process_query("I don't have a cat called Pixel-9R any more")

        assert store.get_item(hidden) is not None

    def test_a_delete_the_message_never_mentions_is_dropped(self, store, memories):
        host = _Host(store, [_delete(memories["tea"], "contradicted")])

        host.process_query("Actually I'm not allergic to anything at all")

        assert store.get_item(memories["tea"]) is not None

    def test_a_delete_sharing_one_common_word_is_dropped(self, store, memories):
        """A delete sharing only "favourite" with the message is not applied."""
        host = _Host(store, [_delete(memories["tea"], "contradicted")])

        host.process_query("My favourite coffee is a flat white")

        assert store.get_item(memories["tea"]) is not None

    def test_a_contradicted_memory_is_deleted_not_forgotten_and_logged(
        self, store, memories, caplog
    ):
        """Replaced, not forgotten: old chats about the cat stay searchable."""
        host = _Host(store, [_delete(memories["cat"], "the cat was rehomed")])

        with caplog.at_level(logging.INFO, logger="gaia.agents.base.memory"):
            host.process_query("I don't have a cat any more, Pixel-9R was rehomed")

        assert store.get_item(memories["cat"]) is None
        assert _forgotten(store) == []
        assert not store.mentions_forgotten("My cat is called Pixel-9R")
        assert memories["cat"] in caplog.text
        assert "0 stored, 0 updated, 1 deleted" in caplog.text


class TestTheFinishLineCountsEachKind:
    def test_stored_updated_and_deleted_are_reported_apart(
        self, store, memories, caplog
    ):
        host = _Host(
            store,
            [
                {
                    "op": "add",
                    "category": "fact",
                    "content": "User's dog is called Biscuit-7Q",
                    "grounded": "user",
                },
                {
                    "op": "update",
                    "knowledge_id": memories["tea"],
                    "category": "fact",
                    "content": "User's favourite tea is now sencha",
                    "grounded": "user",
                },
                _delete(memories["cat"], "the cat was rehomed"),
            ],
        )

        with caplog.at_level(logging.INFO, logger="gaia.agents.base.memory"):
            host.process_query(
                "My dog is called Biscuit-7Q, my favourite tea is now sencha, "
                "and my cat Pixel-9R was rehomed"
            )

        assert "1 stored, 1 updated, 1 deleted" in caplog.text
