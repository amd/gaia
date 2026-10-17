# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A memory the user forgot is not learned again from old conversations (#4730).

``forget`` deleted the row, but the conversation that stated the fact stayed in
the log, and consolidation later distilled it into a "new" memory. Forgetting
now records what was forgotten and when. Consolidation is told what the user
forgot after a window was said and drops any item that repeats it; extraction
does the same for a turn queued before the forget. Something the user says
*after* forgetting is the user telling GAIA again, and is learned. Runs
against a real ``MemoryStore``; only the model is stubbed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import List
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore

FACT = "User's favourite colour is teal"


def _iso(days_ago: float = 0) -> str:
    return (datetime.now().astimezone() - timedelta(days=days_ago)).isoformat()


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore, replies: List[str]):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s-now"
        self._auto_extract_enabled = True
        self._original_user_input = None
        self._faiss_index = None  # no embedder, so no vector index
        self._faiss_id_map = []
        self.prompts: List[str] = []
        replies_iter = iter(replies)

        def send_messages(messages, **kwargs):
            self.prompts.append(messages[0]["content"])
            return MagicMock(text=next(replies_iter), finish_reason="stop")

        self.chat = MagicMock()
        self.chat.send_messages.side_effect = send_messages
        self.register_memory_tools()

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        return []


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


def _contents(store: MemoryStore) -> List[str]:
    return [
        r[0]
        for r in store._conn.execute(
            "SELECT content FROM knowledge WHERE category != 'note'"
        )
    ]


def _add_op(content: str) -> str:
    return json.dumps(
        [
            {
                "op": "add",
                "category": "preference",
                "content": content,
                "grounded": "user",
            }
        ]
    )


def _old_session(store: MemoryStore, session_id: str, days_ago: float) -> None:
    for i in range(6):
        role = "user" if i % 2 == 0 else "assistant"
        text = (
            "My favourite colour is teal, please remember it"
            if role == "user"
            else "I'll remember that."
        )
        store.store_turn(session_id, role, text)
    with store._lock:
        store._conn.execute(
            "UPDATE conversations SET timestamp = ? WHERE session_id = ?",
            (_iso(days_ago), session_id),
        )
        store._conn.commit()


def _forget_fact(store: MemoryStore, days_ago: float = 0) -> None:
    store.forget(store.store(category="preference", content=FACT, source="tool"))
    if days_ago:
        with store._lock:
            store._conn.execute(
                "UPDATE forgotten SET forgotten_at = ?", (_iso(days_ago),)
            )
            store._conn.commit()


CONSOLIDATED = json.dumps(
    {
        "summary": "The user shared a colour preference.",
        "knowledge": [{"category": "preference", "content": FACT}],
    }
)


class TestStoreLevel:
    def test_forget_deletes_and_remembers_it_was_forgotten(self, store):
        kid = store.store(category="preference", content=FACT, source="llm_extract")

        assert store.forget(kid) is True

        assert store.get_item(kid) is None
        assert store.get_forgotten() == [FACT]
        assert store.is_forgotten(FACT)
        assert store.is_forgotten("The user's favorite color is teal")
        assert not store.is_forgotten("User lives in Calgary")

    def test_only_what_was_said_before_the_forget_is_covered(self, store):
        before = _iso(1)
        _forget_fact(store)
        after = _iso(-1)

        assert store.is_forgotten(FACT, said_before=before)
        assert not store.is_forgotten(FACT, said_before=after)
        assert store.get_forgotten(said_before=after) == []

    def test_plain_delete_is_not_a_forget(self, store):
        """Extraction deletes a memory newer information replaced — not a forget."""
        store.delete(store.store(category="preference", content=FACT))

        assert store.get_forgotten() == []

    def test_forget_of_a_missing_row_is_false(self, store):
        assert store.forget("no-such-id") is False
        assert store.get_forgotten() == []

    def test_clear_all_drops_the_forgotten_list(self, store):
        _forget_fact(store)

        store.clear_all()

        assert store.get_forgotten() == []

    def test_a_v5_database_migrates_to_v6(self, tmp_path):
        path = tmp_path / "old.db"
        MemoryStore(db_path=path).close()
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE forgotten")
        conn.execute("UPDATE schema_version SET version = 5")
        conn.commit()
        conn.close()

        db = MemoryStore(db_path=path)
        try:
            version = db._conn.execute("SELECT version FROM schema_version").fetchone()
            assert version[0] == 6
            db.forget(db.store(category="fact", content="User has a cat named Miso"))
            assert db.is_forgotten("User has a cat named Miso")
        finally:
            db.close()


class TestConsolidation:
    def test_an_old_conversation_does_not_bring_it_back(self, store):
        """The #4730 path: stated, forgotten later, then the session is distilled."""
        _old_session(store, "s-old", days_ago=30)
        _forget_fact(store, days_ago=10)
        host = _Host(store, [CONSOLIDATED])

        result = host.consolidate_old_sessions()

        assert result["windows"] == 1
        assert result["extracted_items"] == 0
        assert FACT not in _contents(store)
        # Told up front, so a reworded copy is skipped too.
        assert ("leave them out of the summary:\n- " + FACT) in host.prompts[0]

    def test_a_summary_that_repeats_it_is_not_stored(self, store):
        _old_session(store, "s-old", days_ago=30)
        _forget_fact(store, days_ago=10)
        reply = json.dumps(
            {
                "summary": "The user said their favourite colour is teal.",
                "knowledge": [],
            }
        )
        host = _Host(store, [reply])

        result = host.consolidate_old_sessions()

        assert result["windows"] == 1
        notes = store._conn.execute(
            "SELECT content FROM knowledge WHERE category = 'note'"
        ).fetchall()
        assert notes == []

    def test_a_conversation_after_the_forget_is_learned(self, store):
        """Saying it again after forgetting is telling GAIA again."""
        _forget_fact(store, days_ago=30)
        _old_session(store, "s-later", days_ago=20)
        host = _Host(store, [CONSOLIDATED])

        result = host.consolidate_old_sessions()

        assert result["extracted_items"] == 1
        assert FACT in _contents(store)
        assert "forget these" not in host.prompts[0]


class TestExtraction:
    def test_a_turn_queued_before_the_forget_does_not_store_it(self, store):
        host = _Host(store, [_add_op(FACT)])
        # A turn in flight holds the queued job, so the forget lands first.
        host._note_user_turn_started()
        host._after_process_query(
            "Please remember that my favourite colour is teal", "Noted."
        )
        _forget_fact(store)
        host._note_user_turn_finished()

        assert host.wait_for_memory_extraction(timeout=10)

        assert _contents(store) == []

    def test_saying_it_again_after_forgetting_stores_it(self, store):
        _forget_fact(store)
        host = _Host(store, [_add_op(FACT)])

        host._after_process_query(
            "Actually, please remember my favourite colour is teal after all", "Noted."
        )
        assert host.wait_for_memory_extraction(timeout=10)

        assert _contents(store) == [FACT]

    def test_the_forget_tool_records_it(self, store):
        _Host(store, [])
        kid = _tool("remember")(fact=FACT, category="preference")["knowledge_id"]

        assert _tool("forget")(kid)["status"] == "removed"

        assert store.get_forgotten() == [FACT]
        # remember still works afterwards: the user is asking again.
        assert _tool("remember")(fact=FACT, category="preference")["status"] == "stored"


class TestMatching:
    @pytest.mark.parametrize(
        "forgotten, new, same",
        [
            (FACT, "The user's favorite color is teal", True),
            ("The user lives in Calgary.", "User lives in Calgary", True),
            # Sharing a word or two is not the same memory.
            ("User prefers dark mode", "User prefers dark roast coffee", False),
            ("User has a dog", "User's dog Max is allergic to chicken", False),
            ("User's wife works at AMD", "User works at AMD in Markham", False),
        ],
    )
    def test_same_memory(self, store, forgotten, new, same):
        store.forget(store.store(category="fact", content=forgotten))

        assert store.is_forgotten(new) is same

    def test_a_summary_must_contain_the_fact_to_repeat_it(self, store):
        store.forget(store.store(category="fact", content="The user lives in Calgary."))

        assert store.mentions_forgotten(
            "The user asked for lunch spots and said they live in Calgary."
        )
        assert not store.mentions_forgotten(
            "The user asked for lunch spots near their downtown Calgary office."
        )

    def test_teaching_it_again_on_purpose_unforgets_it(self, store):
        _forget_fact(store)

        store.store(category="preference", content=FACT, source="llm_extract")
        assert store.get_forgotten() == [FACT]

        store.store(category="preference", content=FACT, source="user")
        assert store.get_forgotten() == []

    def test_a_sensitive_memory_keeps_only_its_stems(self, store):
        secret = "User's bank PIN hint is the dog's birthday"
        store.forget(store.store(category="fact", content=secret, sensitive=True))

        kept = store._conn.execute("SELECT content FROM forgotten").fetchone()[0]
        assert "PIN" not in kept and "birthday" not in kept
        assert store.get_forgotten() == []  # never put in a prompt
        assert store.is_forgotten(secret)


class TestForgetInTheSameChat:
    def test_consolidation_honours_a_forget_later_in_the_window(self, store):
        """'I live in Paris' ... a few turns on ... 'forget where I live'."""
        for i in range(6):
            role = "user" if i % 2 == 0 else "assistant"
            store.store_turn("s-paris", role, f"turn {i}: my favourite colour is teal")
        rows = store._conn.execute(
            "SELECT id FROM conversations WHERE session_id = 's-paris' ORDER BY id"
        ).fetchall()
        with store._lock:
            for n, (row_id,) in enumerate(rows):
                store._conn.execute(
                    "UPDATE conversations SET timestamp = ? WHERE id = ?",
                    (_iso(30 - n * 0.01), row_id),
                )
            store._conn.commit()
        # Forgotten between the window's first and last turns.
        _forget_fact(store, days_ago=29.975)
        host = _Host(store, [CONSOLIDATED])

        host.consolidate_old_sessions()

        assert FACT not in _contents(store)

    def test_extraction_honours_a_forget_made_during_the_same_turn(self, store):
        host = _Host(store, [_add_op(FACT)])
        host._turn_said_at = _iso(1 / 86400)  # the turn began a second ago
        _forget_fact(store)  # ...and its forget tool ran before it ended

        host._after_process_query(
            "Please forget my favourite colour is teal, thanks", "Forgotten."
        )
        assert host.wait_for_memory_extraction(timeout=10)

        assert _contents(store) == []


def test_about_user_listing_leaves_out_gaias_own_records(store):
    store.store(category="fact", content="User lives in Calgary")
    store.store(
        category="note",
        content="read_file needs an absolute path",
        source="tool_lesson",
    )
    store.store(
        category="note",
        content="Session about lunch spots",
        source="consolidation",
        domain="session:abcd1234",
    )
    store.store(category="error", content="read_file failed: not found")

    rows = store.get_all_knowledge(about_user=True, limit=200)["items"]

    assert [r["content"] for r in rows] == ["User lives in Calgary"]
