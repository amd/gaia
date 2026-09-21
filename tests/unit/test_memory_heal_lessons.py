# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""When the agent works around a failure, memory keeps what fixed it.

Before, a failing call stored an error, the working call deleted it, and
nothing recorded the fix, so every session rediscovered the same project
quirk. Now a failure followed in the same turn by a success of the same
operation stores one lesson, built only from the two calls in the tool record.
It is scoped to the workspace, shown to later sessions there, confirmed when
the fix works again, and dropped when the fix itself fails.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.security import PathValidator


class _AgentBase:
    def process_query(self, user_input, **kwargs):
        return {"result": ""}

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        return self.next_result


class _Session(MemoryMixin, _AgentBase):
    """One agent session over an on-disk store, working in *workspace*."""

    _with_overflow_note = Agent._with_overflow_note
    _shrink_messages_for_overflow = Agent._shrink_messages_for_overflow

    def __init__(self, db_path: Path, workspace: Path):
        self._memory_store = MemoryStore(db_path=db_path)
        self._memory_context = "global"
        self._memory_session_id = "s"
        self.path_validator = PathValidator(allowed_paths=[str(workspace)])
        self.next_result: Any = None

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def turn(self, calls: List[tuple]) -> None:
        """A user turn that makes *calls*: (tool, args, result) triples."""
        self.process_query("fix the failing tests")
        for tool, args, result in calls:
            self.next_result = result
            self._execute_tool(tool, args)

    def lessons(self) -> List[Dict]:
        return self._memory_store.get_by_category("note", domain="lesson", limit=50)

    def close(self):
        self._memory_store.close()


SHELL = "run_shell_command"
FAIL = (
    SHELL,
    {"command": "pytest -q"},
    {
        "status": "error",
        "error": "test clock not configured. See docs/TESTING.md",
        "return_code": 1,
    },
)
READ_DOC = ("read_file", {"file_path": "docs/TESTING.md"}, {"status": "success"})
FIX = (SHELL, {"command": "env TOYBOX_CLOCK=frozen pytest -q"}, {"status": "success"})
FIX_FAILS = (
    SHELL,
    {"command": "env TOYBOX_CLOCK=frozen pytest -q"},
    {"status": "error", "error": "ModuleNotFoundError: toybox", "return_code": 1},
)
LESSON = (
    "run_shell_command: `pytest -q` failed (test clock not configured. See "
    "docs/TESTING.md). `env TOYBOX_CLOCK=frozen pytest -q` worked: added "
    "`env TOYBOX_CLOCK=frozen`."
)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "toybox"
    root.mkdir()
    return root


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "memory.db"


@pytest.fixture
def session(db_path, workspace):
    s = _Session(db_path, workspace)
    yield s
    s.close()


def _learn(db_path, workspace):
    first = _Session(db_path, workspace)
    first.turn([FAIL, READ_DOC, FIX])
    first.close()


class TestALessonIsLearned:
    def test_failure_then_fix_stores_one_lesson(self, session, workspace):
        session.turn([FAIL, READ_DOC, FIX])

        lessons = session.lessons()
        assert [
            (r["content"], r["category"], r["source"], r["context"]) for r in lessons
        ] == [(LESSON, "note", "tool_lesson", f"workspace:{workspace.resolve()}")]
        assert lessons[0]["confidence"] == pytest.approx(0.5)
        assert session._memory_store.get_by_category("error") == []

    @pytest.mark.parametrize(
        "calls",
        [
            [FAIL, (SHELL, {"command": "pytest -q"}, {"status": "success"})],
            [
                (
                    SHELL,
                    {"command": "pytest -q"},
                    {"status": "error", "error": "not allowed", "executed": False},
                ),
                FIX,
            ],
            [FAIL, (SHELL, {"command": "ls -la"}, {"status": "success"})],
        ],
        ids=["same-call-retried", "refused-first", "other-program"],
    )
    def test_no_lesson_without_a_real_fix(self, session, calls):
        session.turn(calls)

        assert session.lessons() == []

    def test_the_failure_and_fix_must_share_a_turn(self, session):
        session.turn([FAIL])
        session.turn([FIX])

        assert session.lessons() == []

    def test_a_path_fix_shows_the_changed_argument(self, session):
        session.turn(
            [
                (
                    "read_file",
                    {"file_path": "cfg.json", "encoding": "utf-8"},
                    {"status": "error", "error": "UnicodeDecodeError: byte 0xff"},
                ),
                (
                    "read_file",
                    {"file_path": "cfg.json", "encoding": "latin-1"},
                    {"status": "success"},
                ),
            ]
        )

        [lesson] = session.lessons()
        assert lesson["content"].endswith(
            "worked: changed encoding from `utf-8` to `latin-1`."
        )


class TestLaterSessionsSeeIt:
    def test_the_next_session_in_the_workspace_has_it_in_its_prompt(
        self, db_path, workspace
    ):
        _learn(db_path, workspace)

        second = _Session(db_path, workspace)
        try:
            prompt = second.get_memory_system_prompt()
        finally:
            second.close()

        today = datetime.now().astimezone().date().isoformat()
        assert (
            f"Lessons learned in this workspace:\n  - {LESSON} "
            f"(confidence: 0.50, learned {today})"
        ) in prompt

    def test_another_workspace_does_not_see_it(self, db_path, workspace, tmp_path):
        _learn(db_path, workspace)
        other = tmp_path / "other"
        other.mkdir()

        second = _Session(db_path, other)
        try:
            assert "TOYBOX_CLOCK" not in second.get_memory_system_prompt()
        finally:
            second.close()

    def test_the_rest_of_the_session_sees_it_in_the_turn_context(self, session):
        session.get_memory_system_prompt()
        session.turn([FAIL, FIX])

        ctx = session.get_memory_dynamic_context()

        assert f"Learned earlier this session:\n  - {LESSON}" in ctx


class TestConfirmAndRetire:
    def test_the_fix_working_again_raises_confidence_once_per_session(
        self, db_path, workspace
    ):
        _learn(db_path, workspace)

        second = _Session(db_path, workspace)
        try:
            second.turn([FIX, FIX])
            [lesson] = second.lessons()
        finally:
            second.close()

        assert lesson["confidence"] == pytest.approx(0.6)

    def test_the_fix_failing_drops_the_lesson(self, db_path, workspace):
        _learn(db_path, workspace)

        second = _Session(db_path, workspace)
        try:
            second.turn([FIX_FAILS])
            assert second.lessons() == []
        finally:
            second.close()

    def test_the_fix_being_refused_keeps_the_lesson(self, db_path, workspace):
        _learn(db_path, workspace)
        refused = (
            SHELL,
            FIX[1],
            {"status": "error", "error": "not allowed", "executed": False},
        )

        second = _Session(db_path, workspace)
        try:
            second.turn([refused])
            assert len(second.lessons()) == 1
        finally:
            second.close()


class TestOverflowRecoveryKeepsTheLesson:
    def _messages(self):
        return [
            {"role": "user", "content": "an earlier turn"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "fix the failing tests"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "clock not configured"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
            {"role": "tool", "tool_call_id": "2", "content": "5 passed"},
        ]

    def test_the_turn_query_carries_the_lesson_after_shrinking(self, session):
        session.turn([FAIL, FIX])

        shrunk = session._shrink_messages_for_overflow(self._messages())
        shrunk = session._shrink_messages_for_overflow(shrunk)

        assert "omitted" in shrunk[4]["content"][0]["text"]
        assert shrunk[2]["content"] == (
            "fix the failing tests\n\n[GAIA Memory] Learned this turn:\n"
            f"  - {LESSON}"
        )
        assert shrunk[0]["content"] == "an earlier turn"

    def test_nothing_is_added_without_a_lesson(self, session):
        session.turn([FIX])

        shrunk = session._shrink_messages_for_overflow(self._messages())

        assert shrunk[2]["content"] == "fix the failing tests"
