# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``forget`` takes the user's words as well as an id.

On v0.25.4-rc13 every "Forget that I'm allergic to shellfish" began with
``forget(knowledge_id="allergic to shellfish")``. No row has that id, so the
user saw a failed step quoting text written for the model before the model
recalled the id and tried again. A description now erases the one memory it
clearly names; anything less erases nothing and hands back the candidates.
Runs against a real ``MemoryStore`` and FAISS index with a word-hashing
embedder, so the lookup is the same one ``recall`` runs.
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
from gaia.agents.base.tool_outcome import tool_result_failed
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.ui.sse_handler import _summarize_tool_result

DIM = 256
SHELLFISH = "User is allergic to shellfish"
PARTNER_SHELLFISH = "User's partner Sam is allergic to shellfish"
TEA = "User's favourite tea is sencha"
CAT = "User's cat is called Pixel"


def _hashed(text: str) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    for word in re.findall(r"[a-z]+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


class _ToolBase:
    """Stands in for ``Agent._execute_tool``: runs the registered tool."""

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
        self.vectors: Dict[str, np.ndarray] = {}

    def _embed_text(self, text):
        return self.vectors.get(text, _hashed(text))

    def save(self, content: str) -> str:
        # An entity of its own, so the store doesn't merge the two allergies.
        entity = "person:Sam" if "Sam" in content else None
        kid = self._memory_store.store(
            category="fact", content=content, context="global", entity=entity
        )
        assert self._embed_and_index(kid, content, "test memory")
        return kid


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


def test_a_description_with_one_match_erases_it(store):
    host = _Host(store)
    shellfish, tea, cat = host.save(SHELLFISH), host.save(TEA), host.save(CAT)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to shellfish")

    assert result["status"] == "removed"
    assert result["knowledge_id"] == shellfish
    assert result["erased"] == SHELLFISH
    assert not tool_result_failed(result)
    assert store.get_item(shellfish) is None
    assert store.get_item(tea) is not None
    assert store.get_item(cat) is not None
    assert shellfish not in host._faiss_id_map


def test_the_users_own_phrasing_finds_it(store):
    host = _Host(store)
    shellfish, tea = host.save(SHELLFISH), host.save(TEA)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="that I'm allergic to shellfish")

    assert result["status"] == "removed"
    assert store.get_item(shellfish) is None
    assert store.get_item(tea) is not None


def test_an_ambiguous_description_erases_nothing_and_lists_the_candidates(store):
    host = _Host(store)
    mine, partners, tea = (
        host.save(SHELLFISH),
        host.save(PARTNER_SHELLFISH),
        host.save(TEA),
    )

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to shellfish")

    assert result["status"] == "error"
    assert tool_result_failed(result)
    assert "Nothing was erased" in result["message"]
    ids = [c["knowledge_id"] for c in result["candidates"]]
    assert mine in ids and partners in ids
    assert all(set(c) == {"knowledge_id", "content"} for c in result["candidates"])
    for kid in (mine, partners, tea):
        assert store.get_item(kid) is not None


def test_a_valid_id_erases_it(store):
    host = _Host(store)
    shellfish, partners = host.save(SHELLFISH), host.save(PARTNER_SHELLFISH)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id=shellfish)

    assert result["status"] == "removed"
    assert result["knowledge_id"] == shellfish
    assert "erased" not in result
    assert store.get_item(shellfish) is None
    assert store.get_item(partners) is not None


def test_a_description_matching_nothing_erases_nothing(store):
    host = _Host(store)
    shellfish, tea = host.save(SHELLFISH), host.save(TEA)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="my dentist appointment")

    assert result["status"] == "error"
    assert tool_result_failed(result)
    assert "Nothing was erased" in result["message"]
    assert "candidates" not in result
    assert result["display_message"] == (
        "No saved memory matched, so nothing was erased"
    )
    assert store.get_item(shellfish) is not None
    assert store.get_item(tea) is not None


def _scoring(words: str, content: str, score: float) -> Dict[str, np.ndarray]:
    """An embedding for *words* whose cosine with *content* is exactly *score*."""
    target = _hashed(content)
    other = _hashed(words) - float(_hashed(words) @ target) * target
    other = other / np.linalg.norm(other)
    vec = score * target + float(np.sqrt(1 - score**2)) * other
    return {words: vec.astype(np.float32)}


def test_meaning_alone_is_a_candidate_not_an_erase(store):
    """A paraphrase sharing no words is offered for the model to confirm by id."""
    result, ids, kept = _forget(
        store,
        [SHELLFISH, TEA],
        "my seafood allergy",
        vectors=_scoring("my seafood allergy", SHELLFISH, 0.9),
    )

    assert result["status"] == "error"
    assert tool_result_failed(result)
    assert "Nothing was erased" in result["message"]
    assert ids[0] in [c["knowledge_id"] for c in result["candidates"]]
    assert kept == ids


def test_a_sibling_memory_close_in_meaning_is_not_erased(store):
    """ "allergic to peanuts" scored 0.62 on the shellfish allergy and erased it."""
    result, ids, kept = _forget(
        store,
        [SHELLFISH, TEA],
        "allergic to peanuts",
        vectors=_scoring("allergic to peanuts", SHELLFISH, 0.62),
    )

    assert result["status"] == "error"
    assert ids[0] in [c["knowledge_id"] for c in result["candidates"]]
    assert kept == ids


@pytest.mark.parametrize(
    "words, content, score",
    [
        ("allergic to tree nuts", SHELLFISH, 0.95),
        ("allergic to shellfish and peanuts", SHELLFISH, 0.99),
        ("favourite tea is matcha", TEA, 0.9),
        ("cat is called Mochi", CAT, 0.9),
    ],
    ids=["other-allergen", "extra-word", "other-tea", "other-name"],
)
def test_a_near_miss_is_never_erased_however_close(store, words, content, score):
    """Most words shared and a near-perfect score: one word differs, so ask."""
    result, ids, kept = _forget(
        store,
        [SHELLFISH, TEA, CAT],
        words,
        vectors=_scoring(words, content, score),
    )

    assert result["status"] == "error"
    assert tool_result_failed(result)
    assert kept == ids


def test_the_exact_words_still_erase_beside_a_close_sibling(store):
    """Peanuts saved too: the words name it, so only it goes."""
    peanuts = "User is allergic to peanuts"
    host = _Host(store)
    shellfish, tea = host.save(SHELLFISH), host.save(TEA)
    # An entity of its own, so the store keeps both allergies.
    nuts = store.store(
        category="fact", content=peanuts, context="global", entity="user:peanuts"
    )
    assert host._embed_and_index(nuts, peanuts, "test memory")
    host.vectors.update(_scoring("allergic to peanuts", SHELLFISH, 0.62))

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to peanuts")

    assert result["status"] == "removed"
    assert result["erased"] == peanuts
    assert store.get_item(nuts) is None
    assert store.get_item(shellfish) is not None
    assert store.get_item(tea) is not None


def test_privileged_rows_are_never_matched_by_description(store):
    host = _Host(store)
    kid = store.store(
        category="system",
        content="User is allergic to shellfish",
        allow_privileged=True,
    )
    host._embed_and_index(kid, SHELLFISH, "test memory")

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to shellfish")

    assert result["status"] == "error"
    assert store.get_item(kid) is not None


def test_a_forget_by_description_counts_as_the_users_forget(store):
    """Extraction must not also delete on this turn (#6754)."""
    host = _Host(store)
    host.save(SHELLFISH)

    with _tools(host):
        result = host._execute_tool("forget", {"knowledge_id": "allergic to shellfish"})

    assert result["status"] == "removed"
    assert host._turn_forgot is True


def test_a_memory_from_another_context_is_never_matched(store):
    host = _Host(store)
    host._memory_context = "personal"
    kid = store.store(category="fact", content=SHELLFISH, context="work")
    host._embed_and_index(kid, SHELLFISH, "test memory")

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to shellfish")

    assert result["status"] == "error"
    assert "candidates" not in result
    assert store.get_item(kid) is not None


@pytest.mark.parametrize(
    "contents, shown_text",
    [
        (
            [SHELLFISH, PARTNER_SHELLFISH],
            "Nothing erased yet: checking which memory was meant",
        ),
        ([TEA], "No saved memory matched, so nothing was erased"),
    ],
    ids=["ambiguous", "none"],
)
def test_the_ui_step_never_quotes_the_model_instructions(store, contents, shown_text):
    host = _Host(store)
    for content in contents:
        host.save(content)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to shellfish")

    shown = _summarize_tool_result(result)
    assert shown == shown_text
    assert "erased" in shown and "error" not in shown
    assert "recall" not in shown
    assert "Don't tell the user" not in shown
    assert result["message"] not in shown


# --- Adversarial: a forget by words must never erase the wrong memory ------


def _forget(store, contents, words, *, vectors=None):
    """Save *contents*, forget by *words*; return the result and the survivors."""
    host = _Host(store)
    ids = [host.save(content) for content in contents]
    host.vectors.update(vectors or {})
    with _tools(host) as tools:
        result = tools["forget"](knowledge_id=words)
    kept = [kid for kid in ids if store.get_item(kid) is not None]
    return result, ids, kept


@pytest.mark.parametrize("words", ["", "   ", "x", "that", "forget that thing", "my"])
def test_words_that_name_nothing_erase_nothing(store, words):
    result, ids, kept = _forget(store, [SHELLFISH, TEA, CAT], words)

    assert result["status"] == "error"
    assert tool_result_failed(result)
    assert kept == ids


def test_words_naming_nothing_erase_nothing_even_on_a_high_score(store):
    """Filler alone names no memory, whatever the embedder makes of it."""
    result, ids, kept = _forget(
        store, [SHELLFISH], "that thing", vectors={"that thing": _hashed(SHELLFISH)}
    )

    assert result["status"] == "error"
    assert kept == ids


@pytest.mark.parametrize("words", ["fish", "shell", "allerg", "shellfis"])
def test_part_of_a_word_is_not_a_match(store, words):
    result, ids, kept = _forget(store, [SHELLFISH, TEA], words)

    assert result["status"] == "error"
    assert kept == ids


@pytest.mark.parametrize(
    "words",
    ["ALLERGIC TO SHELLFISH", "Allergic, to shellfish!", "shellfish allergic"],
)
def test_case_punctuation_and_order_still_find_the_one_memory(store, words):
    result, ids, kept = _forget(store, [SHELLFISH, TEA, CAT], words)

    assert result["status"] == "removed"
    assert kept == ids[1:]


def test_a_fact_and_a_preference_both_matching_ask(store):
    host = _Host(store)
    fact = host.save("User drinks coffee every morning")
    pref = store.store(
        category="preference", content="User prefers coffee black", context="global"
    )
    host._embed_and_index(pref, "User prefers coffee black", "test memory")

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="coffee")

    assert result["status"] == "error"
    assert {c["knowledge_id"] for c in result["candidates"]} >= {fact, pref}
    assert store.get_item(fact) is not None
    assert store.get_item(pref) is not None


def test_many_memories_sharing_the_words_ask(store):
    places = [
        "Paris airport",
        "Berlin office",
        "Tokyo station",
        "Lisbon harbour",
        "Austin campus",
        "Toronto mall",
        "Oslo library",
        "Cairo hotel",
        "Seoul gym",
        "Lima market",
        "Dublin pub",
        "Quito plaza",
    ]
    contents = [f"User orders a coffee latte at the {p}" for p in places]
    result, ids, kept = _forget(store, contents, "coffee latte")

    assert len(set(ids)) == len(places)
    assert result["status"] == "error"
    assert kept == ids


def test_someone_elses_memory_is_not_erased_for_the_users_words(store):
    """Only Sam's allergy is saved; "my" allergy must not erase Sam's."""
    result, ids, kept = _forget(
        store, [PARTNER_SHELLFISH, TEA], "that I'm allergic to shellfish"
    )

    assert result["status"] == "error"
    assert "candidates" in result
    assert kept == ids


def test_a_relative_without_a_name_is_not_the_user(store):
    result, ids, kept = _forget(
        store,
        ["User's sister is allergic to shellfish", TEA],
        "allergic to shellfish",
    )

    assert result["status"] == "error"
    assert kept == ids


def test_naming_the_other_person_erases_their_memory(store):
    result, ids, kept = _forget(store, [PARTNER_SHELLFISH, TEA], "Sam shellfish")

    assert result["status"] == "removed"
    assert kept == [ids[1]]


def test_naming_the_other_person_never_erases_the_users_own(store):
    result, ids, kept = _forget(
        store, [SHELLFISH, PARTNER_SHELLFISH], "that Sam is allergic to shellfish"
    )

    assert ids[0] in kept
    if result["status"] == "removed":
        assert kept == [ids[0]]


def test_the_opposite_statement_is_not_a_match(store):
    """ "I don't like coffee" must not erase "User likes coffee", however close."""
    likes = "User likes coffee"
    result, ids, kept = _forget(
        store,
        [likes, TEA],
        "that I don't like coffee",
        vectors={"that I don't like coffee": _hashed(likes)},
    )

    assert result["status"] == "error"
    assert kept == ids


def test_a_negated_memory_is_not_erased_by_the_plain_words(store):
    result, ids, kept = _forget(
        store, ["User does not eat shellfish", TEA], "eat shellfish"
    )

    assert result["status"] == "error"
    assert kept == ids


def test_an_id_of_a_privileged_row_is_still_refused(store):
    host = _Host(store)
    kid = store.store(category="system", content=SHELLFISH, allow_privileged=True)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id=kid)

    assert result["status"] == "error"
    assert store.get_item(kid) is not None


MANGO = "Allergic to mango"


def _save_copy(host: _Host, content: str, *, context: str) -> str:
    """A second row with the same words, kept apart from the first by context."""
    kid = host._memory_store.store(category="fact", content=content, context=context)
    assert host._embed_and_index(kid, content, "test memory")
    return kid


def test_an_invented_id_is_read_as_the_users_words(store):
    """rc16: forget(knowledge_id="allergy:mango") left the allergy stored."""
    host = _Host(store)
    mango, tea = host.save(MANGO), host.save(TEA)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergy:mango")

    assert result["status"] == "removed"
    assert result["knowledge_id"] == mango
    assert store.get_item(mango) is None
    assert store.get_item(tea) is not None


@pytest.mark.parametrize(
    "words", ["allergy:mango", "allergic to mango", "mango allergies"]
)
def test_copies_of_one_memory_are_all_erased(store, words):
    """rc16 stored "Allergic to mango" twice; forgetting it must take both."""
    host = _Host(store)
    first = host.save(MANGO)
    second = _save_copy(host, MANGO, context="personal")
    tea = host.save(TEA)

    with _tools(host) as tools:
        result = host._execute_tool("forget", {"knowledge_id": words})

    assert result["status"] == "removed"
    assert set(result["knowledge_ids"]) == {first, second}
    assert result["erased"] == MANGO
    assert "2" in result["message"]
    assert store.get_item(first) is None
    assert store.get_item(second) is None
    assert store.get_item(tea) is not None
    assert host._turn_forgot is True


def test_a_stem_alone_still_names_no_memory(store):
    """allergy meets allergic, but a cut-off word is not a word."""
    result, ids, kept = _forget(store, [MANGO, TEA], "allerg mang")

    assert result["status"] == "error"
    assert kept == ids


def test_an_ambiguous_result_lists_ids_and_contents_in_the_message(store):
    """A small model reads the message, so the candidates are spelled out there."""
    host = _Host(store)
    mine, partners = host.save(SHELLFISH), host.save(PARTNER_SHELLFISH)

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="allergic to shellfish")

    assert result["status"] == "error"
    for kid, content in ((mine, SHELLFISH), (partners, PARTNER_SHELLFISH)):
        assert kid in result["message"]
        assert content in result["message"]
    assert "forget(knowledge_id=" in result["message"]
    assert store.get_item(mine) is not None
    assert store.get_item(partners) is not None


@pytest.mark.parametrize(
    "words, content",
    [
        ("cookies", "User likes to cook pasta"),
        ("party", "User is part of the chess club"),
        ("history", "User studies historic buildings"),
        ("academy", "User is an academic"),
        ("economy", "User is an economic advisor"),
        ("technology", "User likes technological gadgets"),
        ("historic", "User studies history"),
    ],
)
def test_a_word_is_kin_only_to_a_word_that_also_lost_an_ending(store, words, content):
    """ "cookies" cut to "cook" is not the verb "cook"."""
    result, ids, kept = _forget(store, [content, TEA], words)

    assert result["status"] == "error"
    assert kept == ids


@pytest.mark.parametrize(
    "words, content",
    [
        ("diabetic", "User has diabetes"),
        ("diabetes", "User is diabetic"),
        ("asthma", "User is asthmatic"),
        ("pharmacies", "User works at a pharmacy"),
    ],
)
def test_a_listed_adjective_or_a_plural_names_its_noun(store, words, content):
    result, ids, kept = _forget(store, [content, TEA], words)

    assert result["status"] == "removed"
    assert kept == ids[1:]
