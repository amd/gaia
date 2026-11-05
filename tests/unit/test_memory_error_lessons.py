# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Stored tool errors must be worth replaying, and must not outlive the tool text.

Every auto-stored error is replayed into later system prompts. After a normal
session on v0.25.4-rc13 the store held rows like "run_shell_command: Unknown
error" (nothing to act on), "no such table: sales_top_product" (a table the
model made up once), and an NPU vision message whose advice was later fixed —
the stored copy kept steering turns after the fix shipped.

Two rules:

* errors with nothing to act on are not stored;
* each error is stamped with the GAIA version that saw it, and errors stamped
  by another version (or not stamped at all) are dropped when memory opens:
  the tool's wording may have changed, and a real error is re-learned the next
  time it happens.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.version import get_package_version
from tests.unit.test_memory_forget_sticks import _Host as _ToolHost
from tests.unit.test_memory_forget_sticks import _tool


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@pytest.fixture
def host(store):
    return _Host(store)


def _errors(store: MemoryStore):
    return store.get_by_category("error", context=None, limit=100)


class TestNothingToActOnIsNotStored:
    @pytest.mark.parametrize(
        "message",
        [
            "Unknown error",
            "unknown error.",
            "   ",
            "Error executing query: no such table: sales_top_product",
            "no such column: revenue_total",
        ],
    )
    def test_not_stored(self, host, store, message):
        host._auto_store_error("query_data", message, {"sql": "SELECT 1"})
        assert _errors(store) == []

    def test_an_actionable_error_is_still_stored(self, host, store):
        host._auto_store_error(
            "load_skill", "No skill named 'note-taker'.", {"name": "note-taker"}
        )
        assert [e["content"] for e in _errors(store)] == [
            "load_skill: No skill named 'note-taker'."
        ]

    def test_remember_refuses_an_unknown_error(self, store):
        _ToolHost(store, [])
        out = _tool("remember")(
            fact="run_shell_command: Unknown error", category="error"
        )
        assert out["status"] == "skipped"
        assert _errors(store) == []


class TestErrorsAreVersionStamped:
    def test_stored_error_carries_the_running_version(self, host, store):
        host._auto_store_error("load_skill", "No skill named 'x'.", {"name": "x"})
        (entry,) = _errors(store)
        assert entry["metadata"]["gaia_version"] == get_package_version()

    def test_errors_from_another_version_are_dropped(self, host, store):
        current = store.store(
            category="error",
            content="load_skill: No skill named 'x'.",
            source="error_auto",
            metadata={"gaia_version": get_package_version()},
        )
        store.store(
            category="error",
            content=(
                "answer_question_about_image: GAIA can't read images while it "
                "runs on the NPU, because the NPU model doesn't read images."
            ),
            source="error_auto",
            metadata={"gaia_version": "0.25.4rc12"},
        )
        store.store(
            category="error",
            content="run_shell_command: Command 'cmd' is not allowed",
            source="error_auto",
        )

        host._retire_errors_from_other_versions()

        assert [e["id"] for e in _errors(store)] == [current]

    def test_errors_the_user_wrote_are_kept(self, host, store):
        """Only tool-recorded rows expire; a row the user typed is theirs."""
        kept = store.store(
            category="error",
            content="The office printer jams on duplex jobs",
            source="user",
        )
        host._retire_errors_from_other_versions()
        assert [e["id"] for e in _errors(store)] == [kept]

    def test_errors_in_every_context_are_checked(self, host, store):
        store.store(
            category="error",
            content="load_skill: No skill named 'y'.",
            source="error_auto",
            context="work",
            metadata={"gaia_version": "0.0.1"},
        )
        host._retire_errors_from_other_versions()
        assert _errors(store) == []


def test_opening_memory_drops_errors_from_an_older_version(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
    db_path = tmp_path / "memory.db"
    seed = MemoryStore(db_path=db_path)
    seed.store(
        category="error",
        content="answer_question_about_image: switch the chat model to the GPU",
        source="error_auto",
        metadata={"gaia_version": "0.25.4rc12"},
    )
    seed.close()

    agent = MemoryMixin()
    with patch.object(MemoryMixin, "_start_memory_embedder", return_value=True):
        agent.init_memory(db_path=db_path)
    try:
        assert _errors(agent.memory_store) == []
    finally:
        agent.memory_store.close()
