# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A memory saved under a context label must reach later sessions.

The ``remember`` tool offered ``context: work/personal``, and bootstrap writes
``work`` rows. The prompt builder read only ``context IN (active, 'global')``,
and the active context is ``global`` unless an agent sets one, so a default
session read global rows only. Anything filed under ``work`` or ``personal``
was stored and never shown again. In a learning benchmark the model saved two
standing rules with ``context='work'``, and the next three sessions never saw
them.

Now a default (``global``) session reads every context, an agent that set a
context keeps its scoping, and the model can't pick a label at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY


class _Session(MemoryMixin):
    """One agent session over the on-disk store."""

    def __init__(self, db_path: Path, context: str = "global"):
        self._memory_store = MemoryStore(db_path=db_path)
        self._memory_context = context
        self._memory_session_id = "s"

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def close(self):
        self._memory_store.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "memory.db"


RULE = "Always open a draft PR before asking for review"


def _save_as_the_model_did_on_main(db_path: Path, context: str = "work") -> None:
    """``remember(category='preference', context='work')`` on main wrote this."""
    first = _Session(db_path)
    first._memory_store.store(
        category="preference",
        content=RULE,
        domain="work",
        context=context,
        source="tool",
        confidence=0.7,
    )
    first.close()


class TestADefaultSessionSeesEveryContext:
    def test_a_work_labelled_preference_reaches_the_next_session(self, db_path):
        _save_as_the_model_did_on_main(db_path)

        second = _Session(db_path)
        try:
            prompt = second.get_memory_system_prompt()
        finally:
            second.close()

        assert f"Preferences:\n  - {RULE}" in prompt

    def test_a_personal_reminder_reaches_the_turn(self, db_path):
        first = _Session(db_path)
        due = (datetime.now().astimezone() + timedelta(days=1)).isoformat()
        first._memory_store.store(
            category="reminder",
            content="Dentist appointment",
            context="personal",
            due_at=due,
        )
        first.close()

        second = _Session(db_path)
        try:
            ctx = second.get_memory_dynamic_context()
        finally:
            second.close()

        assert "] Dentist appointment" in ctx

    def test_sensitive_rows_stay_out(self, db_path):
        first = _Session(db_path)
        first._memory_store.store(
            category="fact",
            content="Private medical detail",
            context="personal",
            sensitive=True,
        )
        first.close()

        second = _Session(db_path)
        try:
            assert "Private medical detail" not in second.get_memory_system_prompt()
        finally:
            second.close()


class TestAScopedAgentKeepsItsScope:
    def test_email_sees_its_own_and_global_rows_only(self, db_path):
        _save_as_the_model_did_on_main(db_path, context="work")
        seed = _Session(db_path)
        seed._memory_store.store(
            category="preference", content="Summaries in bullets", context="global"
        )
        seed._memory_store.store(
            category="preference", content="Archive newsletters", context="email"
        )
        seed.close()

        email = _Session(db_path, context="email")
        try:
            prompt = email.get_memory_system_prompt()
        finally:
            email.close()

        assert "Archive newsletters" in prompt
        assert "Summaries in bullets" in prompt
        assert RULE not in prompt


class TestTheModelCannotPickALabel:
    @pytest.fixture
    def tools(self, db_path):
        session = _Session(db_path, context="email")
        saved = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            session.register_memory_tools()
            yield session, {
                name: _TOOL_REGISTRY[name]["function"]
                for name in ("remember", "update_memory")
            }
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)
            session.close()

    def test_remember_stores_under_the_active_context(self, tools):
        session, fn = tools

        fn["remember"](fact=RULE, category="preference")

        rows = session._memory_store.get_by_category("preference")
        assert [(r["content"], r["context"]) for r in rows] == [(RULE, "email")]
        assert RULE in session.get_memory_system_prompt()

    def test_neither_tool_accepts_a_context(self, tools):
        _, fn = tools

        with pytest.raises(TypeError):
            fn["remember"](fact=RULE, context="work")
        with pytest.raises(TypeError):
            fn["update_memory"](knowledge_id="x", context="work")
