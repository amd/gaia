# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A detail the user says to leave out of memory is never saved (#8798).

On v0.27.0-rc8, "Remember my phone is 555-1234 leave it out" made the model
call remember itself, and the number was stored. The model is told the rule,
remember and update_memory refuse such a detail, extraction drops it, and the
answer check catches "Stored" on that turn. Ordinary requests, including ones
with a negation in the fact ("remember that I don't like olives"), still save.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.memory import (
    MemoryMixin,
    asks_to_keep_in_memory,
    declined_details,
)
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY

ISSUE = "Remember my phone is 555-1234 leave it out"

DECLINES = [
    ISSUE,
    "Remember my phone is 555-1234 don't save that",
    "Remember my phone is 555-1234 – don’t save that",
    "Remember my phone is 555-1234, but don't keep it",
    "Remember my phone is 555-1234. Actually, leave it out.",
    "Don't remember my phone number, it's 555-1234",
    "remember my phone is 555-1234 please don't store it in memory",
    "My phone is 555-1234, no need to save it",
]

REQUESTS = [
    "Remember that I don't like olives",
    "Remember that I don't like olives.",
    "Remember I don't keep cash at home",
    "Remember that we leave it out of the recipe",
    "I told her to leave it out. Remember my name is Sam.",
    "Don't save the file yet, remember my name is Sam",
    "Leave it out. Remember I'm vegetarian.",
    "Remember my email is sam@example.com",
]


@pytest.mark.parametrize("text", DECLINES)
def test_a_decline_names_the_detail_and_is_not_a_request(text):
    (clause,) = declined_details(text)
    assert "555-1234" in clause or "phone" in clause
    assert not asks_to_keep_in_memory(text)


@pytest.mark.parametrize("text", REQUESTS)
def test_an_ordinary_request_declines_nothing(text):
    assert declined_details(text) == []
    assert asks_to_keep_in_memory(text)


@pytest.mark.parametrize(
    "text",
    [
        "I don't remember my password",
        "Remember my kids don't keep them",  # a statement, not an instruction
        "Remember to leave it out for the dog",
        'Reply to this email: "Please leave it out."',
    ],
)
def test_statements_and_quotes_are_not_declines(text):
    assert declined_details(text) == []


# ---------------------------------------------------------------------------
# The memory tools
# ---------------------------------------------------------------------------


class _Host(MemoryMixin):
    def __init__(self, store: MemoryStore):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._session_lessons = []
        self._faiss_index = None
        self._faiss_id_map = []

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def user_says(self, message: str) -> None:
        self._original_user_input = message
        self._turn_said_at = "turn-1"
        self._turn_tool_record = []


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@contextmanager
def _memory_tools(host: _Host):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        host.register_memory_tools()
        yield {name: entry["function"] for name, entry in _TOOL_REGISTRY.items()}
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _contents(store: MemoryStore) -> List[str]:
    return [row["content"] for row in store.get_all_knowledge(limit=100)["items"]]


@pytest.mark.parametrize("text", DECLINES)
def test_remember_refuses_the_declined_detail(store, text):
    host = _Host(store)
    host.user_says(text)
    with _memory_tools(host) as tools:
        result = tools["remember"](fact="Phone number is 555-1234")

    assert result["status"] == "skipped"
    assert result["stored"] == 0
    assert result["message"].startswith("Not stored: the user said to leave this out")
    assert _contents(store) == []


def test_remember_still_saves_a_fact_with_a_negation_in_it(store):
    host = _Host(store)
    host.user_says("Remember that I don't like olives")
    with _memory_tools(host) as tools:
        result = tools["remember"](fact="User doesn't like olives")

    assert result["status"] == "stored"
    assert _contents(store) == ["User doesn't like olives"]


def test_only_the_declined_clause_is_refused(store):
    host = _Host(store)
    host.user_says(
        "Remember my locker code is plum-walrus-63 and my phone is 555-1234 "
        "leave it out"
    )
    with _memory_tools(host) as tools:
        locker = tools["remember"](fact="Locker code is plum-walrus-63")
        phone = tools["remember"](fact="Phone number is 555-1234")

    assert locker["status"] == "stored"
    assert phone["status"] == "skipped"
    assert _contents(store) == ["Locker code is plum-walrus-63"]


@pytest.mark.parametrize(
    "request_text, kept, declined",
    [
        (
            "Remember my work phone is 555-1111, and my home phone is 555-2222, "
            "leave it out",
            "Work phone is 555-1111",
            "Home phone is 555-2222",
        ),
        (
            "Remember my sister's birthday is May 3 and my sister's address is "
            "4 Elm St, leave it out",
            "Sister's birthday is May 3",
            "Sister's address is 4 Elm St",
        ),
    ],
)
def test_a_kept_fact_sharing_a_word_with_the_declined_one_still_saves(
    store, request_text, kept, declined
):
    host = _Host(store)
    host.user_says(request_text)
    with _memory_tools(host) as tools:
        saved = tools["remember"](fact=kept)
        refused = tools["remember"](fact=declined)

    assert saved["status"] == "stored"
    assert refused["status"] == "skipped"
    assert _contents(store) == [kept]


def test_two_declines_of_one_detail_keep_the_other_facts(store):
    host = _Host(store)
    host.user_says(
        "Remember my home phone is 555-2222, leave it out, don't save it, "
        "and my work phone is 555-1111"
    )
    with _memory_tools(host) as tools:
        saved = tools["remember"](fact="Work phone is 555-1111")
        refused = tools["remember"](fact="Home phone is 555-2222")

    assert saved["status"] == "stored"
    assert refused["status"] == "skipped"


@pytest.mark.parametrize(
    "request_text",
    [
        "Remember my phone, and my phone is 555-1234, leave it out",
        # Every word repeats in a kept clause: nothing tells them apart, so refuse.
        "Remember my phone is 555-1234, and my phone is 555-1234, leave it out",
    ],
)
@pytest.mark.parametrize(
    "fact", ["Phone is 555-1234", "My phone number: 555-1234", "555-1234"]
)
def test_a_declined_detail_repeated_in_a_kept_clause_is_still_refused(
    store, request_text, fact
):
    host = _Host(store)
    host.user_says(request_text)
    with _memory_tools(host) as tools:
        result = tools["remember"](fact=fact)

    assert result["status"] == "skipped"
    assert _contents(store) == []


def test_update_memory_refuses_the_declined_detail(store):
    host = _Host(store)
    host.user_says("Remember that my locker code is plum-walrus-63.")
    with _memory_tools(host) as tools:
        saved = tools["remember"](fact="Locker code is plum-walrus-63")
        host.user_says(ISSUE)
        result = tools["update_memory"](
            knowledge_id=saved["knowledge_id"],
            content="Locker code is plum-walrus-63; phone is 555-1234",
        )

    assert result["status"] == "skipped"
    assert _contents(store) == ["Locker code is plum-walrus-63"]


def test_the_remember_tool_states_the_rule(store):
    with _memory_tools(_Host(store)) as tools:
        description = " ".join(tools["remember"].__doc__.split())
    assert "Never store a detail the user says to leave out" in description
    assert "tell them you left it out" in description


def test_the_memory_prompt_states_the_rule(store):
    prompt = " ".join(_Host(store)._build_stable_memory_prompt().split())
    assert "A detail the user declines is never saved, even inside a request" in prompt
    assert "say you left it out" in prompt


# ---------------------------------------------------------------------------
# Background extraction
# ---------------------------------------------------------------------------


class _AgentBase:
    """Stands in for ``Agent``: answers, then runs the post-turn hook."""

    def process_query(self, user_input, **kwargs):
        self._after_process_query(user_input, "Okay.")
        self.wait_for_memory_extraction(timeout=10)
        return {"result": "Okay."}


class _ExtractingHost(MemoryMixin, _AgentBase):
    def __init__(self, store: MemoryStore, ops: List[Dict[str, Any]]):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._auto_extract_enabled = True
        self.chat = MagicMock()
        self.chat.send_messages.return_value = MagicMock(text=json.dumps(ops))

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        return []


def _op(content: str) -> Dict[str, Any]:
    return {"op": "add", "category": "fact", "content": content, "grounded": "user"}


def test_extraction_drops_the_declined_detail_and_keeps_the_rest(store):
    host = _ExtractingHost(
        store,
        [_op("Phone number is 555-1234"), _op("Locker code is plum-walrus-63")],
    )
    host.process_query(
        "Remember my locker code is plum-walrus-63 and my phone is 555-1234 "
        "leave it out"
    )

    rows = store._conn.execute(
        "SELECT content FROM knowledge WHERE source = 'llm_extract'"
    ).fetchall()
    assert [r[0] for r in rows] == ["Locker code is plum-walrus-63"]


# ---------------------------------------------------------------------------
# The answer check
# ---------------------------------------------------------------------------


class _Base:
    def _execute_tool_timed(self, tool_name, tool_args):
        return self.next_result

    def answer_problems(self, answer):
        return []

    def answer_repair_prompt(self, problems):
        return ""


class _CheckedHost(MemoryMixin, _Base):
    pass


def _checked(request: str, results=()) -> _CheckedHost:
    host = object.__new__(_CheckedHost)
    host._turn_tool_executions = []
    host._tools_registry = dict.fromkeys(("remember", "recall"))
    host._original_user_input = request
    host._incognito = False
    host.console = SimpleNamespace(background_mode=False)
    for result in results:
        host.next_result = result
        host._execute_tool_timed("remember", {"fact": "x"})
        host._turn_tool_executions.append({"tool": "remember"})
    return host


REFUSED = {"status": "skipped", "message": "Not stored: the user said to leave this"}


@pytest.mark.parametrize("results", [(), (REFUSED,)])
def test_stored_over_a_decline_is_flagged_and_repaired(results):
    host = _checked(ISSUE, results)

    # The rc8 reply, verbatim.
    problems = host.answer_problems("Stored. Won't repeat it back.")

    assert problems == ["This was not saved to memory: you asked to leave it out."]
    repair = host.answer_repair_prompt(problems)
    assert "Say plainly that you left it out" in repair
    assert "Do not call remember" in repair


def test_an_honest_answer_about_a_decline_passes():
    host = _checked(ISSUE, (REFUSED,))

    assert host.answer_problems("Left it out — your phone number was not saved.") == []


def test_a_decline_with_no_save_is_not_nudged_to_save():
    host = _checked(ISSUE)

    assert host._unsaved_memory_request(set()) is None
    assert host.answer_problems("Okay, I left your phone number out.") == []
