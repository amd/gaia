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

from gaia.agents.base.memory import FORGET_SURE_SIMILARITY, MemoryMixin
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


def test_a_sure_similarity_alone_is_a_match(store):
    """A paraphrase shares no words but scores clear of the bar on meaning."""
    host = _Host(store)
    shellfish, tea = host.save(SHELLFISH), host.save(TEA)
    near = 0.9 * _hashed(SHELLFISH) + 0.1 * _hashed("seafood")
    host.vectors["my seafood allergy"] = near / np.linalg.norm(near)
    assert float(host.vectors["my seafood allergy"] @ _hashed(SHELLFISH)) >= (
        FORGET_SURE_SIMILARITY
    )

    with _tools(host) as tools:
        result = tools["forget"](knowledge_id="my seafood allergy")

    assert result["status"] == "removed"
    assert store.get_item(shellfish) is None
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
