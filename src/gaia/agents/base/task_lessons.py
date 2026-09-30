# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What this task has taught the agent about its environment, kept in view.

A refusal or a tool failure is visible once, in the result of the call that
hit it, and then scrolls up the conversation. A model probing for a way
forward walks into the same wall again several steps later. Each failure is
recorded here as a lesson — what failed and why, and what later worked for the
same program — and the whole list rides on every later failed result, so the
constraints learned so far are in front of the model exactly when it is
exploring.

The lessons come from the harness's own records of what ran, never from the
content a tool returned, so a fetched page cannot write one. They last for the
turn.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from gaia.agents.base.turn_scope import _error_brief, _is_tool_failure, _streak_key

#: Most lessons carried; the oldest drop first.
MAX_LESSONS = 8
#: Length of the call shown with a lesson.
_CALL_CHARS = 120


def _call_text(tool: str, args: Dict[str, Any]) -> str:
    command = args.get("command")
    text = command if isinstance(command, str) else json.dumps(args, default=str)
    text = " ".join(text.split())
    return text if len(text) <= _CALL_CHARS else text[: _CALL_CHARS - 3] + "..."


class TaskLessons:
    """Per-turn lessons, keyed like the turn guard's failure streaks."""

    def __init__(self) -> None:
        self._lessons: Dict[str, Dict[str, Any]] = {}

    def begin_turn(self) -> None:
        self._lessons = {}

    def record(self, tool: str, args: Any, result: Any) -> Optional[List[str]]:
        """Note what the call taught; return the lessons when it failed."""
        args = args if isinstance(args, dict) else {}
        key = _streak_key(tool, args)
        if _is_tool_failure(result):
            lesson = self._lessons.pop(key, None) or {"failures": 0, "worked": None}
            lesson["failures"] += 1
            lesson["error"] = _error_brief(result)
            lesson["failed_call"] = _call_text(tool, args)
            self._lessons[key] = lesson
            while len(self._lessons) > MAX_LESSONS:
                self._lessons.pop(next(iter(self._lessons)))
            # A first failure's own result already says everything.
            if lesson["failures"] > 1 or len(self._lessons) > 1:
                return self.lines()
            return None
        if key in self._lessons:
            self._lessons[key]["worked"] = _call_text(tool, args)
        return None

    def lines(self) -> List[str]:
        out = []
        for key, lesson in self._lessons.items():
            times = f" ({lesson['failures']}x)" if lesson["failures"] > 1 else ""
            line = f"{key}{times}: `{lesson['failed_call']}` failed: {lesson['error']}"
            if lesson["worked"]:
                line += f" — later worked: `{lesson['worked']}`"
            out.append(line)
        return out


LESSONS_NOTE = (
    "Failures so far in this task. Don't repeat a form that already failed; "
    "use what worked, or a different approach."
)


def attach(result: Any, lines: Optional[List[str]]) -> Any:
    """The failed *result* with the task's lessons on it, when there are any."""
    if not lines or not isinstance(result, dict):
        return result
    return {**result, "task_lessons": {"note": LESSONS_NOTE, "lessons": lines}}
