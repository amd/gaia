# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A fact saved as sensitive is one memory, and forgetting it erases it.

On v0.26.0-rc18 "Remember that I am allergic to mango" ran ``remember`` with
``sensitive=true`` and an entity; seconds later the turn's extraction stored
the same fact again with no entity and not sensitive. Forgetting by the id the
model had just been given erased only the first row, so a new chat still said
"you're allergic to mango". Runs the real tools against a real ``MemoryStore``.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from typing import Any, Dict

import numpy as np
import pytest

faiss = pytest.importorskip("faiss")

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY

DIM = 256
MANGO = "User is allergic to mango"
SAID = "Remember that I am allergic to mango. It is private health info."


def _hashed(text: str) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    for word in re.findall(r"[a-z]+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


class _ToolBase:
    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        return _TOOL_REGISTRY[tool_name]["function"](**tool_args)


class _Host(MemoryMixin, _ToolBase):
    def __init__(self, store: MemoryStore):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._embedding_dim = DIM
        self._faiss_index = faiss.IndexFlatIP(DIM)
        self._faiss_id_map = []
        self._turn_forgot = False
        self._original_user_input = SAID

    def _embed_text(self, text):
        return _hashed(text)


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@contextmanager
def _tools(host: _Host):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        host.register_memory_tools()
        yield {name: entry["function"] for name, entry in _TOOL_REGISTRY.items()}
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _rows(store: MemoryStore):
    return store._conn.execute(
        "SELECT id, content, sensitive, entity FROM knowledge"
    ).fetchall()


def _extract(host: _Host, content: str, entity=None) -> None:
    host._execute_extraction_operations(
        [
            {
                "op": "add",
                "category": "fact",
                "content": content,
                "entity": entity,
                "grounded": "user",
            }
        ],
        [],
        context="global",
    )


@pytest.mark.parametrize("entity", ["user", "health", "user:self"])
@pytest.mark.parametrize("copy", [MANGO, MANGO + ".", "Allergic to mango"])
def test_extraction_after_a_sensitive_remember_keeps_one_sensitive_memory(
    store, entity, copy
):
    host = _Host(store)
    with _tools(host) as tools:
        saved = tools["remember"](fact=MANGO, sensitive="true", entity=entity)
        assert saved["status"] == "stored"

        _extract(host, copy)

    rows = _rows(store)
    assert len(rows) == 1, rows
    assert rows[0][2] == 1, "the copy downgraded the memory to not sensitive"
    assert rows[0][3] == entity


def test_forgetting_by_id_leaves_nothing_to_recall(store):
    host = _Host(store)
    with _tools(host) as tools:
        kid = tools["remember"](fact=MANGO, sensitive="true", entity="user")[
            "knowledge_id"
        ]
        _extract(host, MANGO)

        result = tools["forget"](knowledge_id=kid)
        assert result["status"] == "removed"

        recalled = tools["recall"](query="mango allergy")

    assert _rows(store) == []
    assert "mango" not in str(recalled).lower()


def test_forgetting_by_id_erases_a_copy_stored_apart(store):
    """A copy already stored under another context still goes with the id."""
    host = _Host(store)
    first = store.store(
        category="fact", content=MANGO, sensitive=True, entity="user", source="tool"
    )
    second = store.store(category="fact", content=MANGO + ".", context="personal")
    other = store.store(category="fact", content="User is allergic to green mango")
    for kid in (first, second, other):
        host._embed_and_index(kid, store.get_item(kid)["content"], "test memory")

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id=first)

    assert result["status"] == "removed"
    assert set(result["knowledge_ids"]) == {first, second}
    assert store.get_item(first) is None
    assert store.get_item(second) is None
    assert store.get_item(other) is not None


@pytest.mark.parametrize(
    "content, entity",
    [("Is allergic to mango", "person:Sam"), ("Uses dark mode", "app:slack")],
)
@pytest.mark.parametrize("forget_first", [True, False])
def test_a_typed_tag_is_its_own_subject(store, content, entity, forget_first):
    """person:/app:/project: rows never merge with, or go with, the user's."""
    host = _Host(store)
    tagged = store.store(category="fact", content=content, entity=entity)
    mine = store.store(category="fact", content=content)

    assert tagged != mine

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id=tagged if forget_first else mine)

    assert result["status"] == "removed"
    assert "knowledge_ids" not in result
    kept = mine if forget_first else tagged
    assert store.get_item(kept) is not None


def test_a_user_tag_is_the_user(store):
    first = store.store(category="fact", content=MANGO, entity="user:self")
    assert store.store(category="fact", content=MANGO, entity="health") == first
    assert store.store(category="fact", content=MANGO) == first


def _plant_twin(store: MemoryStore, content: str, **kwargs) -> str:
    """A copy stored apart, as before this fix, then moved back beside the first."""
    kid = store.store(category="fact", content=content, context="apart", **kwargs)
    store._conn.execute("UPDATE knowledge SET context = 'global' WHERE id = ?", (kid,))
    store._conn.commit()
    return kid


def test_forgetting_by_words_erases_a_reworded_copy(store):
    """'User is allergic to mango' and 'Allergic to mango.' are one memory."""
    host = _Host(store)
    first = store.store(category="fact", content=MANGO, sensitive=True, entity="user")
    twin = _plant_twin(store, "Allergic to mango.")
    for kid in (first, twin):
        host._embed_and_index(kid, store.get_item(kid)["content"], "test memory")

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to mango")

    assert result["status"] == "removed", result
    assert set(result["knowledge_ids"]) == {first, twin}
    assert _rows(store) == []


def test_forgetting_by_words_keeps_someone_elses_copy(store):
    """Sam's allergy has the same words but is not the user's to erase."""
    host = _Host(store)
    mine = store.store(category="fact", content=MANGO)
    sams = store.store(
        category="fact", content="Allergic to mango", entity="person:Sam"
    )
    for kid in (mine, sams):
        host._embed_and_index(kid, store.get_item(kid)["content"], "test memory")

    with _tools(host) as tools:
        tools["forget"](knowledge_id="allergic to mango")

    assert store.get_item(sams) is not None


@pytest.mark.parametrize("sensitive_one", ["first", "twin"])
def test_a_copy_of_a_sensitive_fact_is_forgotten_as_sensitive(store, sensitive_one):
    """No copy may leave the fact's words behind in the forgotten list."""
    host = _Host(store)
    first = store.store(
        category="fact", content=MANGO, sensitive=sensitive_one == "first"
    )
    twin = _plant_twin(store, "Allergic to mango.", sensitive=sensitive_one == "twin")
    assert first != twin

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id=first)

    assert set(result["knowledge_ids"]) == {first, twin}
    kept = store._conn.execute("SELECT content, sensitive FROM forgotten").fetchall()
    assert len(kept) == 2
    assert all(sensitive == 1 and "mango" not in content for content, sensitive in kept)
    assert store.is_forgotten("I am allergic to mango")


def test_forgetting_an_unflagged_fact_keeps_its_words(store):
    """Only a sensitive group is reduced to stems; plain facts stay readable."""
    host = _Host(store)
    first = store.store(category="fact", content="User likes green tea")
    with _tools(host) as tools:
        tools["forget"](knowledge_id=first)

    kept = store._conn.execute("SELECT content, sensitive FROM forgotten").fetchall()
    assert kept == [("User likes green tea", 0)]


# The phrasings round 2 of the rc18 test saw: (said to remember, tool's entity,
# what extraction stored, extraction's entity).
MANGO_PAIR = ("I am allergic to mango.", None, "User is allergic to mango.", None)
BLOOD_PAIR = (
    "My blood type is O negative.",
    "person:user",
    "User's blood type is O negative (sensitive medical info).",
    "user",
)


def _extract_as(host: _Host, content: str, entity, category: str) -> None:
    host._execute_extraction_operations(
        [
            {
                "op": "add",
                "category": category,
                "content": content,
                "entity": entity,
                "grounded": "user",
            }
        ],
        [],
        context="global",
    )


@pytest.mark.parametrize("pair", [MANGO_PAIR, BLOOD_PAIR])
@pytest.mark.parametrize("extracted_as", ["fact", "preference", "note"])
@pytest.mark.parametrize("extraction_first", [False, True])
def test_a_reworded_extraction_leaves_one_sensitive_row(
    store, pair, extracted_as, extraction_first
):
    said, tool_entity, extracted, extract_entity = pair
    host = _Host(store)
    with _tools(host) as tools:
        if extraction_first:
            _extract_as(host, extracted, extract_entity, extracted_as)
        saved = tools["remember"](fact=said, sensitive="true", entity=tool_entity or "")
        assert saved["status"] == "stored"
        if not extraction_first:
            _extract_as(host, extracted, extract_entity, extracted_as)

    rows = _rows(store)
    assert len(rows) == 1, rows
    assert rows[0][2] == 1, "a copy of the sensitive fact is stored unflagged"


def test_a_sensitive_fact_is_found_among_many_memories(store):
    """The copy check must not depend on a ranked search that can miss the row."""
    for n in range(60):
        store.store(category="fact", content=f"User is allergic to pollen type {n}")
    host = _Host(store)
    with _tools(host) as tools:
        tools["remember"](fact=MANGO_PAIR[0], sensitive="true")
        _extract_as(host, MANGO_PAIR[2], None, "preference")

    mango = [r for r in _rows(store) if "mango" in r[1]]
    assert len(mango) == 1 and mango[0][2] == 1, mango


def test_a_copy_in_another_context_is_stored_sensitive(store):
    """Each context keeps its own row, but none of them unflagged."""
    store.store(category="fact", content=MANGO_PAIR[0], sensitive=True, source="tool")
    store.store(
        category="fact",
        content=MANGO_PAIR[2],
        context="work",
        source="llm_extract",
    )
    assert [r[2] for r in _rows(store)] == [1, 1]


def test_saving_as_sensitive_flags_copies_in_other_contexts(store):
    store.store(category="fact", content=MANGO_PAIR[2], context="work")
    store.store(category="note", content=MANGO_PAIR[2])
    store.store(category="fact", content=MANGO_PAIR[0], sensitive=True, source="tool")

    rows = store._conn.execute(
        "SELECT context, sensitive FROM knowledge ORDER BY context"
    ).fetchall()
    assert rows == [("global", 1), ("work", 1)]


def test_a_different_fact_is_not_folded_into_a_sensitive_one(store):
    store.store(category="fact", content=MANGO_PAIR[0], sensitive=True)
    store.store(category="fact", content="User is not allergic to mango.")
    store.store(category="fact", content="User is allergic to kiwi.")
    store.store(category="fact", content="Allergic to mango", entity="person:Sam")

    flags = {content: flag for _, content, flag, _ in _rows(store)}
    assert flags == {
        MANGO_PAIR[0]: 1,
        # Its own row, but it restates the sensitive one, so it is flagged too.
        "User is not allergic to mango.": 1,
        "User is allergic to kiwi.": 0,
        "Allergic to mango": 0,
    }


@pytest.mark.parametrize("extraction_first", [False, True])
def test_a_loose_rewording_is_stored_sensitive_and_forgotten_with_it(
    store, extraction_first
):
    """Too reworded to merge, so two rows: neither unflagged, both forgotten."""
    reworded = "User has a mango allergy."
    host = _Host(store)
    with _tools(host) as tools:
        if extraction_first:
            _extract_as(host, reworded, None, "fact")
        tools["remember"](fact=MANGO_PAIR[0], sensitive="true")
        if not extraction_first:
            _extract_as(host, reworded, None, "fact")

    rows = _rows(store)
    assert [r[2] for r in rows] == [1] * len(rows), rows

    visible = next(r[0] for r in rows if r[1] == reworded)
    store.forget_with_copies(visible)
    assert _rows(store) == []


@pytest.mark.parametrize("entity", ["person:user", "Person:User", "person:me"])
def test_a_tag_naming_the_user_is_the_user(store, entity):
    first = store.store(category="fact", content=MANGO, entity=entity)
    assert store.store(category="fact", content=MANGO) == first
    assert store.store(category="fact", content=MANGO, entity="user") == first


@pytest.mark.parametrize("pair", [MANGO_PAIR, BLOOD_PAIR])
@pytest.mark.parametrize("forget_visible", [True, False])
def test_forgetting_either_copy_of_an_rc18_pair_erases_both(
    store, pair, forget_visible
):
    """Pairs a build before this fix left behind still go together."""
    said, tool_entity, extracted, extract_entity = pair
    host = _Host(store)
    hidden = store.store(
        category="fact", content=said, sensitive=True, entity=tool_entity
    )
    visible = _plant_twin(store, extracted, entity=extract_entity)
    store._conn.execute("UPDATE knowledge SET sensitive = 0 WHERE id = ?", (visible,))
    store._conn.commit()
    assert len(_rows(store)) == 2

    erased = store.forget_with_copies(visible if forget_visible else hidden)

    assert set(erased) == {hidden, visible}
    assert _rows(store) == []
    kept = store._conn.execute("SELECT content, sensitive FROM forgotten").fetchall()
    assert all(flag == 1 for _, flag in kept), kept
    with _tools(host) as tools:
        recalled = tools["recall"](query=said)
    assert "mango" not in str(recalled).lower()
    assert "negative" not in str(recalled).lower()


def test_folding_keeps_the_fuller_wording_of_a_dropped_copy(store):
    """A sensitive save must not erase detail an older copy had."""
    store.store(category="fact", content="User is allergic to mango")
    store.store(
        category="note",
        content="User is allergic to mango since childhood, carries an EpiPen",
    )
    store.store(category="fact", content=MANGO_PAIR[0], sensitive=True, source="tool")

    rows = _rows(store)
    assert len(rows) == 1, rows
    assert "EpiPen" in rows[0][1] and rows[0][2] == 1


def test_forgetting_a_sensitive_fact_keeps_its_negation(store):
    """ "Is not allergic" is the user's correction, not a copy to forget."""
    sensitive = store.store(
        category="fact", content=MANGO_PAIR[0], sensitive=True, source="tool"
    )
    store.store(category="fact", content="User is not allergic to mango.")

    erased = store.forget_with_copies(sensitive)

    assert erased == [sensitive]
    assert [r[1] for r in _rows(store)] == ["User is not allergic to mango."]


@pytest.mark.parametrize(
    "said, unrelated",
    [
        ("I have diabetes", "User is writing a work report on diabetes research"),
        ("I take insulin", "User reads about the history of insulin at work"),
    ],
)
@pytest.mark.parametrize("sensitive_first", [True, False])
def test_one_shared_word_does_not_flag_an_unrelated_memory(
    store, said, unrelated, sensitive_first
):
    if sensitive_first:
        store.store(category="fact", content=said, sensitive=True, source="tool")
        store.store(category="note", content=unrelated)
    else:
        store.store(category="note", content=unrelated)
        store.store(category="fact", content=said, sensitive=True, source="tool")

    flags = {content: flag for _, content, flag, _ in _rows(store)}
    assert flags[unrelated] == 0, flags
    assert flags[said] == 1


@pytest.mark.parametrize(
    "reworded", ["User has diabetes (health)", "User has diabetes, diagnosed 2019"]
)
def test_a_one_word_sensitive_fact_still_flags_its_rewording(store, reworded):
    store.store(category="fact", content="I have diabetes", sensitive=True)
    store.store(category="note", content=reworded, context="work")

    assert [r[2] for r in _rows(store)] == [1, 1]
