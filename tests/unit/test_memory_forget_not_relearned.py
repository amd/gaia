# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A forget turn never saves what it asked to forget, and one sentence is one memory.

On v0.25.4-rc16 "Allergic to mango" was stored twice: as a fact after the
"Remember…" turn, then as a preference by the extraction that ran after
"Forget that I am allergic to mango." Dedupe merged only within a category,
so the second copy stood, and forgetting one would have left the other.
These run against a real ``MemoryStore``; only the model is stubbed.
"""

from __future__ import annotations

import json
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore

MANGO = "Allergic to mango"


class _AgentBase:
    def process_query(self, user_input, **kwargs):
        self._after_process_query(user_input, "Done.")
        self.wait_for_memory_extraction(timeout=10)
        return {"result": "Done."}

    def _execute_tool(self, tool_name, tool_args):
        return None


class _Host(MemoryMixin, _AgentBase):
    def __init__(self, store: MemoryStore, extracted_ops: List[Dict]):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._auto_extract_enabled = True
        self._faiss_index = None
        self._faiss_id_map = {}
        self.chat = MagicMock()
        self.chat.send_messages.return_value = MagicMock(text=json.dumps(extracted_ops))

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        rows = self._memory_store._conn.execute(
            "SELECT id, category, content FROM knowledge WHERE superseded_by IS NULL"
        ).fetchall()
        return [{"id": r[0], "category": r[1], "content": r[2]} for r in rows]


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


def _add(content: str, category: str = "preference") -> Dict:
    return {"op": "add", "category": category, "content": content, "grounded": "user"}


def _contents(store: MemoryStore) -> List[str]:
    return sorted(
        r[0]
        for r in store._conn.execute(
            "SELECT content FROM knowledge WHERE category != 'note'"
        )
    )


class TestOneSentenceIsOneMemory:
    @pytest.mark.parametrize(
        "again", [MANGO, "allergic to mango.", "ALLERGIC  to Mango!"]
    )
    def test_the_same_words_in_another_category_are_the_same_memory(self, store, again):
        fact = store.store(category="fact", content=MANGO, context="global")

        assert (
            store.store(category="preference", content=again, context="global") == fact
        )
        assert store.get_item(fact)["category"] == "fact"
        assert len(_contents(store)) == 1

    @pytest.mark.parametrize("category", ["skill", "reminder", "note"])
    def test_other_categories_keep_their_own_meaning(self, store, category):
        fact = store.store(category="fact", content=MANGO, context="global")

        assert store.store(category=category, content=MANGO, context="global") != fact

    def test_different_words_stay_separate(self, store):
        fact = store.store(category="fact", content=MANGO, context="global")
        pref = store.store(
            category="preference", content="Likes mango sorbet", context="global"
        )

        assert pref != fact

    def test_another_context_or_entity_stays_separate(self, store):
        mine = store.store(category="fact", content=MANGO, context="global")
        work = store.store(category="preference", content=MANGO, context="work")
        sams = store.store(
            category="preference", content=MANGO, context="global", entity="person:sam"
        )

        assert len({mine, work, sams}) == 3

    def test_a_privileged_row_never_absorbs_a_memory(self, store):
        system = store.store(
            category="system", content=MANGO, context="global", allow_privileged=True
        )

        assert store.store(category="fact", content=MANGO, context="global") != system


class TestAForgetTurnDoesNotRelearnIt:
    def test_the_rc16_sequence_leaves_one_copy_to_forget(self, store):
        """The forget failed, and extraction stored the allergy again."""
        fact = store.store(category="fact", content=MANGO, context="global")
        host = _Host(store, [_add(MANGO)])

        host.process_query("Forget that I am allergic to mango.")

        assert _contents(store) == [MANGO]
        assert store.get_item(fact) is not None

    def test_a_reworded_copy_of_the_forgotten_thing_is_not_added(self, store):
        host = _Host(store, [_add("User is allergic to mangoes", "fact")])

        host.process_query("Please forget my mango allergy")

        assert _contents(store) == []

    @pytest.mark.parametrize(
        "message",
        [
            "Never mind, forget my mango allergy.",
            "Don't worry about it, just forget that I am allergic to mango.",
            "Okay, forget that I am allergic to mango.",
        ],
    )
    def test_a_discourse_aside_does_not_negate_the_forget(self, store, message):
        """The aside's negation negates the aside, not the request (#6914).

        On main the fact was saved again on this turn, because "never" in
        "Never mind," read as negating the forget request itself.
        """
        host = _Host(store, [_add(MANGO, "fact")])

        host.process_query(message)

        assert _contents(store) == []

    def test_a_lone_negation_word_before_a_comma_still_keeps_the_fact(self, store):
        """ "Never, ever forget" repeats the negation as its own clause."""
        host = _Host(store, [_add(MANGO, "fact")])

        host.process_query("Never, ever forget that I am allergic to mango.")

        assert _contents(store) == [MANGO]

    def test_what_else_the_user_says_on_that_turn_is_still_learned(self, store):
        host = _Host(store, [_add(MANGO, "fact"), _add("Likes kiwi")])

        host.process_query(
            "Forget that I am allergic to mango, and remember that I like kiwi."
        )

        assert _contents(store) == ["Likes kiwi"]

    @pytest.mark.parametrize(
        "message",
        [
            "Don't forget that I am allergic to mango.",
            "Remember that I'm allergic to mango, never forget it.",
            "Do not forget: I am allergic to mango.",
        ],
    )
    def test_dont_forget_is_a_request_to_remember(self, store, message):
        host = _Host(store, [_add(MANGO, "fact")])

        host.process_query(message)

        assert _contents(store) == [MANGO]

    @pytest.mark.parametrize(
        "message",
        [
            "Please don't ever forget that I'm vegetarian",
            "I don't want you to forget that I'm vegetarian",
            "Never, ever forget I'm vegetarian",
            "Don't ever, ever forget that I'm vegetarian",
            "Please don't, under any circumstances, forget that I'm vegetarian",
            "Make sure you never forget I am vegetarian",
            "Did you forget I'm vegetarian?",
            "You forgot I'm vegetarian. Don’t forget again: I'm vegetarian",
        ],
    )
    def test_asking_to_keep_a_fact_saves_it(self, store, message):
        host = _Host(store, [_add("User is vegetarian", "fact")])

        host.process_query(message)

        assert _contents(store) == ["User is vegetarian"]

    @pytest.mark.parametrize(
        "message",
        [
            "remove the peanut butter cookies from the list since I'm allergic "
            "to peanuts",
            "Remove the peanut butter cookies because I'm allergic to peanuts",
            "Delete the peanut cookies as I'm allergic to peanuts",
        ],
    )
    def test_the_reason_for_a_removal_is_still_saved(self, store, message):
        host = _Host(store, [_add("User is allergic to peanuts", "fact")])

        host.process_query(message)

        assert _contents(store) == ["User is allergic to peanuts"]

    @pytest.mark.parametrize(
        "message",
        [
            "Can you forget that I'm vegetarian?",
            "Could you please forget I am vegetarian?",
            "I'm not sure why you kept it, but forget that I'm vegetarian.",
        ],
    )
    def test_a_polite_or_hedged_forget_is_still_a_forget(self, store, message):
        host = _Host(store, [_add("User is vegetarian", "fact")])

        host.process_query(message)

        assert _contents(store) == []
