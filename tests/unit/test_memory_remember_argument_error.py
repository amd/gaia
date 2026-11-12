# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A remember call that fails on an argument must not read as "nothing saved".

On v0.26.0-rc18 ``remember(category="banana")`` returned "Invalid category",
the answer said "nothing was saved", and seconds later the turn's extraction
stored the fact, as the user had asked. The error now says what is true: this
call stored nothing, and the fact may still be learned from the chat.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import EXTRACTABLE_CATEGORIES, MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY

FACT = "My favourite fruit stand is on Elm Street."
MAY_STILL = "may still learn"


class _ToolBase:
    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        return _TOOL_REGISTRY[tool_name]["function"](**tool_args)


class _Host(MemoryMixin, _ToolBase):
    def __init__(self, store: MemoryStore):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._original_user_input = f"Use your remember tool to store this: {FACT}"

    def _embed_and_index(self, knowledge_id, content, what):
        pass


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@contextmanager
def _remember(host: _Host):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        host.register_memory_tools()
        yield _TOOL_REGISTRY["remember"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _contents(store: MemoryStore):
    return [r[0] for r in store._conn.execute("SELECT content FROM knowledge")]


@pytest.mark.parametrize(
    "bad", [{"category": "banana"}, {"category": "reminder", "due_at": "next week"}]
)
def test_an_argument_error_does_not_claim_the_fact_is_lost(store, bad):
    host = _Host(store)
    with _remember(host) as remember:
        result = remember(fact=FACT, **bad)

    assert result["status"] == "error"
    assert _contents(store) == []
    assert "This call stored nothing" in result["message"]
    assert MAY_STILL in result["message"]
    # The note must not talk the model out of fixing the argument and retrying.
    assert "Call remember again" in result["message"]


def test_a_corrected_retry_still_saves(store):
    with _remember(_Host(store)) as remember:
        remember(fact=FACT, category="banana")
        result = remember(fact=FACT, category="fact")

    assert result["status"] != "error"
    assert len(_contents(store)) == 1


def test_the_category_error_still_lists_the_valid_categories(store):
    with _remember(_Host(store)) as remember:
        message = remember(fact=FACT, category="banana")["message"]

    assert str(sorted(EXTRACTABLE_CATEGORIES)) in message


def test_extraction_still_keeps_what_the_user_asked_to_remember(store):
    """The failed call must not stop the turn's extraction: they asked to keep it."""
    host = _Host(store)
    with _remember(host) as remember:
        remember(fact=FACT, category="banana")
        host._execute_extraction_operations(
            [
                {
                    "op": "add",
                    "category": "preference",
                    "content": "User's favourite fruit stand is on Elm Street.",
                    "grounded": "user",
                }
            ],
            [],
            context="global",
        )

    assert _contents(store) == ["User's favourite fruit stand is on Elm Street."]


def test_a_refused_credential_makes_no_such_promise(store):
    """Extraction refuses a credential too, so there "nothing saved" is true."""
    with _remember(_Host(store)) as remember:
        result = remember(fact="My API key is sk-ant-api03-abcdefghijklmnopqrstuvwx")

    assert result["status"] == "skipped"
    assert MAY_STILL not in result["message"]
