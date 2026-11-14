# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A bad category is refused out loud; a bad date still keeps the fact.

``remember(category="banana")`` stores nothing and says which categories it
takes, so the model retries with a real one instead of a guess being filed
for it. A bad due date is only a missing detail: the fact is stored without
it, and the result opens with "Saved" and names what was ignored.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import EXTRACTABLE_CATEGORIES, MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY

FACT = "My favourite fruit stand is on Elm Street."
# Words that read to the user as "it won't be remembered".
NOT_KEPT = ("nothing", "failed", "not stored", "not saved", "invalid")


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
        "SELECT content, category, due_at FROM knowledge"
    ).fetchall()


def test_an_unknown_category_is_refused_with_the_valid_ones(store):
    with _tools(_Host(store)) as tools:
        result = tools["remember"](fact=FACT, category="banana")

    assert result["status"] == "error"
    assert result["stored"] == 0
    assert _rows(store) == []
    message = result["message"]
    assert "'banana' is not a category" in message
    assert ", ".join(sorted(EXTRACTABLE_CATEGORIES)) in message
    assert "Call remember again" in message


@pytest.mark.parametrize("category", ["Fact", " fact", "facts", "reminders", "None"])
def test_a_near_miss_category_is_refused_too(store, category):
    with _tools(_Host(store)) as tools:
        result = tools["remember"](fact=FACT, category=category)

    assert result["status"] == "error"
    assert _rows(store) == []


def test_an_empty_category_is_the_default(store):
    with _tools(_Host(store)) as tools:
        result = tools["remember"](fact=FACT, category="")

    assert result["status"] == "stored"
    assert _rows(store) == [(FACT, "fact", None)]


def test_the_retry_the_error_asks_for_is_stored(store):
    with _tools(_Host(store)) as tools:
        tools["remember"](fact=FACT, category="banana")
        result = tools["remember"](fact=FACT, category="fact")

    assert result["status"] == "stored"
    assert _rows(store) == [(FACT, "fact", None)]


def test_a_bad_due_date_saves_the_reminder_without_one(store):
    with _tools(_Host(store)) as tools:
        result = tools["remember"](
            fact=FACT, category="reminder", due_at="next blursday"
        )

    assert result["status"] == "stored"
    assert result["ignored"] == {"due_at": "next blursday"}
    assert _rows(store) == [(FACT, "reminder", None)]
    message = result["message"]
    assert message.startswith("Saved.")
    assert "'next blursday'" in message
    # The model is told how to add the date, with the id to add it to.
    assert f"update_memory(knowledge_id='{result['knowledge_id']}'" in message
    assert not any(word in message.lower() for word in NOT_KEPT), message


def test_a_bad_category_is_refused_before_the_date_is_read(store):
    with _tools(_Host(store)) as tools:
        result = tools["remember"](fact=FACT, category="banana", due_at="soon")

    assert result["status"] == "error"
    assert "'banana'" in result["message"]
    assert _rows(store) == []


def test_the_date_can_be_added_as_the_result_says(store):
    with _tools(_Host(store)) as tools:
        saved = tools["remember"](fact=FACT, category="reminder", due_at="soon")
        updated = tools["update_memory"](
            knowledge_id=saved["knowledge_id"], due_at="2026-11-20T09:00"
        )

    assert updated["status"] == "updated"
    assert _rows(store)[0][2].startswith("2026-11-20T09:00")


@pytest.mark.parametrize("category", ["profile", "system", "permission"])
def test_a_privileged_category_is_refused(store, category):
    """A chat can't write these rows, which lead every prompt, and nothing is
    filed under another category in their place."""
    with _tools(_Host(store)) as tools:
        result = tools["remember"](fact=FACT, category=category)

    assert result["status"] == "error"
    assert _rows(store) == []
    message = result["message"]
    assert f"a chat can't write the '{category}' category" in message
    assert "is not a category" not in message
    assert ", ".join(sorted(EXTRACTABLE_CATEGORIES)) in message
    assert category not in ", ".join(sorted(EXTRACTABLE_CATEGORIES))


def test_valid_arguments_report_nothing_ignored(store):
    with _tools(_Host(store)) as tools:
        result = tools["remember"](fact=FACT, category="reminder", due_at="2026-11-20")

    assert result["status"] == "stored"
    assert "ignored" not in result
    assert result["message"].startswith("Remembered:")


def test_extraction_after_the_save_leaves_one_memory(store):
    """The turn's extraction restates the fact; it must merge, not double it."""
    host = _Host(store)
    with _tools(host) as tools:
        tools["remember"](fact=FACT, category="fact")
        host._execute_extraction_operations(
            [
                {
                    "op": "add",
                    "category": "fact",
                    "content": "User's favourite fruit stand is on Elm Street.",
                    "grounded": "user",
                }
            ],
            [],
            context="global",
        )

    assert len(_rows(store)) == 1


def test_an_empty_fact_is_still_an_error(store):
    """There is nothing to save here, whatever the label."""
    with _tools(_Host(store)) as tools:
        result = tools["remember"](fact="  ", category="banana")

    assert result["status"] == "error"
    assert _rows(store) == []


def test_a_refused_credential_is_still_refused(store):
    with _tools(_Host(store)) as tools:
        result = tools["remember"](
            fact="My API key is sk-ant-api03-abcdefghijklmnopqrstuvwx",
            category="fact",
        )

    assert result["status"] == "skipped"
    assert _rows(store) == []


def test_update_memory_still_rejects_a_bad_argument(store):
    """Nothing new is at stake in an edit, so a bad value changes nothing."""
    with _tools(_Host(store)) as tools:
        saved = tools["remember"](fact=FACT)
        result = tools["update_memory"](
            knowledge_id=saved["knowledge_id"], category="banana"
        )

    assert result["status"] == "error"
    assert _rows(store) == [(FACT, "fact", None)]
