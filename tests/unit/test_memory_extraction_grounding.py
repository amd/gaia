# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Extraction must not launder the answer's claims into stored facts.

Post-turn extraction used to see only the user's text and the final answer, so
whatever the answer asserted became a "fact" replayed into later sessions. In a
benchmark sweep it stored "run_shell_command is limited to read-only
operations; rm is blocked" (false once permissions changed) and "parse_deleted
now accepts z" (the answer's own claim).

Extraction now sees this turn's tool record, and every op must say it is
grounded in the user's message or a tool result. Code enforces the part it can:
an op grounded in nothing else is dropped, and so is one citing a tool when no
tool succeeded this turn.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore


class _AgentBase:
    """Stands in for ``Agent``: runs the queued tool calls, then the post hook."""

    def process_query(self, user_input, **kwargs):
        for name, args, result in self.queued_calls:
            self.next_result = result
            self._execute_tool(name, args)
        answer = self.answer
        self._after_process_query(user_input, answer)
        return {"result": answer}

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        return self.next_result


class _Host(MemoryMixin, _AgentBase):
    def __init__(self, store: MemoryStore, extracted_ops: List[Dict]):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self._auto_extract_enabled = True
        self.queued_calls: List[Tuple[str, Dict, Any]] = []
        self.answer = ""
        self.prompts: List[str] = []

        def send_messages(messages, **kwargs):
            self.prompts.append(messages[0]["content"])
            return MagicMock(text=json.dumps(extracted_ops))

        self.chat = MagicMock()
        self.chat.send_messages.side_effect = send_messages

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _hybrid_search(self, query, **kwargs):
        return []


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


USER_TEXT = "Please fix the timestamp parser so it accepts a lowercase z suffix"


def _extracted(store: MemoryStore) -> List[Dict]:
    rows = store._conn.execute(
        "SELECT category, content FROM knowledge WHERE source = 'llm_extract' "
        "ORDER BY content"
    ).fetchall()
    return [{"category": c, "content": t} for c, t in rows]


class TestToolRecordReachesExtraction:
    def test_the_prompt_carries_each_call_and_its_outcome(self, store):
        host = _Host(store, [])
        host.queued_calls = [
            ("read_file", {"file_path": "toybox/dates.py"}, {"status": "success"}),
            (
                "run_shell_command",
                {"command": "rm -rf build"},
                {
                    "status": "error",
                    "error": "Command 'rm' is not in the allowed list",
                    "executed": False,
                },
            ),
            (
                "run_shell_command",
                {"command": "pytest -q"},
                {"status": "error", "error": "1 failed, 4 passed"},
            ),
        ]

        host.process_query(USER_TEXT)

        prompt = host.prompts[0]
        assert "Tool record (what the tools returned this turn):" in prompt
        assert '- read_file {"file_path": "toybox/dates.py"} -> ok:' in prompt
        assert (
            '- run_shell_command {"command": "rm -rf build"} -> refused, did not '
            "run: Command 'rm' is not in the allowed list"
        ) in prompt
        assert (
            '- run_shell_command {"command": "pytest -q"} -> failed: 1 failed, 4 passed'
        ) in prompt

    def test_a_new_turn_starts_with_an_empty_record(self, store):
        host = _Host(store, [])
        host.queued_calls = [("read_file", {"file_path": "a"}, {"status": "success"})]
        host.process_query(USER_TEXT)

        host.queued_calls = []
        host.process_query(USER_TEXT)

        assert "(no tools ran this turn)" in host.prompts[1]
        assert "read_file" not in host.prompts[1].split("Tool record")[1]


class TestOnlyGroundedKnowledgeIsStored:
    OPS = [
        {
            "op": "add",
            "category": "fact",
            "content": "parse_deleted now accepts a lowercase z",
            "grounded": "answer",
        },
        {
            "op": "add",
            "category": "fact",
            "content": "run_shell_command cannot run rm",
        },
        {
            "op": "add",
            "category": "preference",
            "content": "User wants regression tests with every parser fix",
            "grounded": "user",
        },
        {
            "op": "add",
            "category": "fact",
            "content": "toybox tests run with pytest from the repo root",
            "grounded": "tool",
        },
    ]

    def test_with_a_successful_tool_call(self, store):
        host = _Host(store, self.OPS)
        host.queued_calls = [
            (
                "run_shell_command",
                {"command": "pytest -q"},
                {"status": "success", "stdout": "5 passed"},
            )
        ]

        host.process_query(USER_TEXT)

        assert _extracted(store) == [
            {
                "category": "preference",
                "content": "User wants regression tests with every parser fix",
            },
            {
                "category": "fact",
                "content": "toybox tests run with pytest from the repo root",
            },
        ]

    def test_a_tool_claim_needs_a_tool_that_succeeded(self, store):
        host = _Host(store, self.OPS)
        host.queued_calls = [
            (
                "run_shell_command",
                {"command": "rm -rf build"},
                {"status": "error", "error": "not allowed", "executed": False},
            )
        ]

        host.process_query(USER_TEXT)

        assert _extracted(store) == [
            {
                "category": "preference",
                "content": "User wants regression tests with every parser fix",
            }
        ]

    def test_an_ungrounded_delete_leaves_the_memory_alone(self, store):
        kid = store.store(
            category="fact", content="The staging server is db-2", context="global"
        )
        host = _Host(
            store, [{"op": "delete", "knowledge_id": kid, "reason": "answer says so"}]
        )

        host.process_query(USER_TEXT)

        assert store.get_item(kid) is not None


def test_the_prompt_forbids_session_narration_and_refusals():
    from gaia.agents.base.memory import _EXTRACTION_PROMPT

    assert "where the user is working, the current" in _EXTRACTION_PROMPT
    assert "Never store a refused call or a permission limit" in _EXTRACTION_PROMPT


class TestGroundingGoesDarkLoudly:
    """Grounding fails closed, so a model that drops the field stores nothing.

    That is indistinguishable from "nothing worth storing" unless it says so.
    """

    UNLABELLED = [
        {"op": "add", "category": "fact", "content": "alpha"},
        {"op": "add", "category": "fact", "content": "beta", "grounded": "vibes"},
    ]

    def test_every_write_dropped_for_grounding_warns(self, store, caplog):
        host = _Host(store, self.UNLABELLED)

        with caplog.at_level("WARNING", logger="gaia.agents.base.memory"):
            host.process_query(USER_TEXT)

        assert _extracted(store) == []
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("extraction stored nothing" in m for m in warnings), warnings
        assert any("all 2 proposed writes" in m for m in warnings), warnings

    def test_a_turn_with_nothing_to_store_stays_quiet(self, store, caplog):
        host = _Host(store, [{"op": "noop"}])

        with caplog.at_level("WARNING", logger="gaia.agents.base.memory"):
            host.process_query(USER_TEXT)

        assert not any(
            "extraction stored nothing" in r.getMessage() for r in caplog.records
        )

    def test_one_good_write_among_unlabelled_ones_stays_quiet(self, store, caplog):
        host = _Host(
            store,
            self.UNLABELLED
            + [
                {
                    "op": "add",
                    "category": "preference",
                    "content": "User prefers tabs",
                    "grounded": "user",
                }
            ],
        )

        with caplog.at_level("WARNING", logger="gaia.agents.base.memory"):
            host.process_query(USER_TEXT)

        assert _extracted(store) == [
            {"category": "preference", "content": "User prefers tabs"}
        ]
        assert not any(
            "extraction stored nothing" in r.getMessage() for r in caplog.records
        )


class TestToolRecordStaysBounded:
    def test_older_calls_are_dropped_and_counted(self, store):
        from gaia.agents.base.memory import EXTRACTION_TOOL_RECORD_MAX_CALLS as CAP

        host = _Host(store, [])
        extra = 5
        host.queued_calls = [
            ("read_file", {"file_path": f"f{i}.py"}, {"status": "success"})
            for i in range(CAP + extra)
        ]

        host.process_query(USER_TEXT)

        record = host.prompts[0].split("Tool record")[1]
        assert f"({extra} earlier calls omitted)" in record
        assert record.count("- read_file ") == CAP
        # The oldest are the ones dropped; the newest survive.
        assert "f0.py" not in record
        assert f"f{CAP + extra - 1}.py" in record

    def test_a_huge_result_is_truncated_without_splitting_all_of_it(self, store):
        from gaia.agents.base.memory import (
            EXTRACTION_TOOL_RECORD_DETAIL_CHARS as DETAIL,
        )

        host = _Host(store, [])
        huge = "word " * 400_000  # ~2 MB
        host.queued_calls = [("read_file", {"file_path": "big.py"}, huge)]

        host.process_query(USER_TEXT)

        entry = host._turn_tool_record[0]
        assert len(entry["detail"]) <= DETAIL
        assert entry["detail"].startswith("word word")


def test_bookkeeping_failure_never_replaces_the_tool_result(store, monkeypatch):
    """A crash in the turn record must not turn a good call into a failure."""
    host = _Host(store, [])
    host.queued_calls = [("read_file", {"file_path": "a.py"}, {"status": "success"})]
    monkeypatch.setattr(
        _Host,
        "_record_tool_call",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bookkeeping exploded")),
    )

    host.process_query(USER_TEXT)  # must not raise
