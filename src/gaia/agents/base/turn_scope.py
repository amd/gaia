# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep a turn on the request once it has been answered, and stop failing loops.

Two stop conditions the identical-call loop guard cannot see, because in both
the model varies its arguments on every call:

* **Work after the answer.** Once the turn has produced an answer, a tool call
  that touches nothing the request touched — no file it named, read, searched
  or changed, and no test run — is new work the user did not ask for. It is
  not run; the model is told to finish and offer the work instead. A second
  such call ends the turn with a closing answer.
* **The same tool failing with different arguments.** A tool the framework
  rejected ``limit`` times in a row, with no file changed in between, is not
  run again: the model is told to change approach. Calling it once more ends
  the turn. A program that ran and exited non-zero is not a tool failure —
  that is how a failing test reports — so only the tool's own refusals count.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Set

from gaia.agents.base.completion import mentioned_paths
from gaia.agents.base.verification import (
    NOT_EXECUTED,
    is_mutating_tool,
    verification_check_label,
)

#: Conversation entry type marking where the turn first produced an answer.
ANSWERED_MARKER = "answered"

#: A second off-request call after the answer ends the turn.
DEFAULT_DRIFT_LIMIT = 2

#: Their result is the agent's own state, a question to the user, or a
#: skill that unlocks the check the loop just asked for.
_ALWAYS_RELATED = frozenset(
    {
        "read_tool_output",
        "request_user_input",
        "session_findings",
        "load_skill",
        "list_skills",
        "skill_status",
    }
)
#: Tools whose arguments hold code or a command line rather than a path.
_COMMAND_ARG_KEYS = ("command", "cmd", "script", "code")
_PATH_ARG_KEYS = (
    "file_path",
    "path",
    "directory_path",
    "filepath",
    "filename",
    "file",
    "target_path",
    "directory",
    "dir",
    "root",
    "root_dir",
    "search_path",
    "destination",
    "output_path",
)
#: Reviewing the change just made is part of the request.
_REVIEW_COMMAND_RE = re.compile(r"^\s*git\s+(?:--no-pager\s+)?(?:diff|status)\b")

POST_ANSWER_CORRECTION = (
    "You already answered this request. `{tool}` {target}is outside what the "
    "request touched, so it was not run. Don't start new work without asking: "
    "give your final answer now, and if more work seems worthwhile, offer it "
    "as a question the user can say yes to."
)
POST_ANSWER_CLOSING_PROMPT = (
    "You already answered the request, then started work it did not ask for. "
    "Stop calling tools. Give your final answer to the original request from "
    "the results above; mention any further work only as an offer."
)
FAILURE_STREAK_CORRECTION = (
    "`{tool}` has failed {count} times in a row with different arguments "
    "(last error: {error}). It was not run again. Do not call it again — try "
    "a different approach, or give your final answer with what is missing."
)


def _norm(path: str, root: str) -> str:
    joined = path if os.path.isabs(path) else os.path.join(root, path)
    return os.path.normcase(os.path.normpath(joined))


def _relative(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:  # another drive on Windows: there is no relative form
        return path


def _slashed(text: str) -> str:
    """Forward slashes, and case-folded where the filesystem ignores case."""
    text = text.replace("\\", "/")
    return text.lower() if os.name == "nt" else text


def _error_brief(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("error", "message", "stderr"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                brief = " ".join(value.split())
                return brief if len(brief) <= 200 else brief[:197] + "..."
    return "the tool returned an error"


def _is_tool_failure(result: Any) -> bool:
    """The tool refused or broke; a program exiting non-zero does not count."""
    if (
        not isinstance(result, dict)
        or "return_code" in result
        or result.get("rate_limited") is True
    ):
        return False
    return result.get("status") in ("error", "denied") or result.get("success") is False


class TurnScopeGuard:
    """Per-turn scope of the request, and per-tool failure streaks."""

    def __init__(self, failure_limit: int, drift_limit: int = DEFAULT_DRIFT_LIMIT):
        self.failure_limit = failure_limit
        self.drift_limit = drift_limit
        self.root = os.getcwd()
        self.answered = False
        self.turn_should_end = False
        self.end_reason: Optional[str] = None
        self.drift_blocked = 0
        self.failures: Dict[str, int] = {}
        self.last_error: Dict[str, str] = {}
        self.corrected: Set[str] = set()
        self.paths: Set[str] = set()

    def begin_turn(self, query: str, root: str) -> None:
        self.root = root
        self.answered = False
        self.turn_should_end = False
        self.end_reason = None
        self.drift_blocked = 0
        self.failures = {}
        self.last_error = {}
        self.corrected = set()
        self.paths = {_norm(p, root) for p in mentioned_paths(query or "")}

    def mark_answered(self) -> None:
        """The turn has produced an answer; later calls must serve it."""
        self.answered = True

    def _call_paths(self, args: Dict[str, Any]) -> Set[str]:
        return {
            _norm(value, self.root)
            for key in _PATH_ARG_KEYS
            if isinstance((value := args.get(key)), str)
            and value.strip()
            and "\x00" not in value
        }

    def related(self, tool: str, args: Dict[str, Any]) -> bool:
        if tool in _ALWAYS_RELATED or verification_check_label(tool, args):
            return True
        if self._call_paths(args) & self.paths:
            return True
        for key in _COMMAND_ARG_KEYS:
            text = args.get(key)
            if not isinstance(text, str):
                continue
            if _REVIEW_COMMAND_RE.match(text) or re.search(
                r"\b(?:pytest|unittest)\b", text
            ):
                return True
            normalized = _slashed(text)
            for path in self.paths:
                rel = _slashed(_relative(path, self.root))
                if not rel.startswith("..") and rel in normalized:
                    return True
        return False

    def check(self, tool: str, args: Any) -> Optional[Dict[str, Any]]:
        """A not-executed result when the call must not run, else ``None``."""
        args = args if isinstance(args, dict) else {}
        count = self.failures.get(tool, 0)
        if self.failure_limit and count >= self.failure_limit:
            if tool in self.corrected:
                self.turn_should_end = True
                self.end_reason = "failures"
            self.corrected.add(tool)
            return {
                **NOT_EXECUTED,
                "status": "error",
                "error": FAILURE_STREAK_CORRECTION.format(
                    tool=tool,
                    count=count,
                    error=self.last_error.get(tool, "the tool returned an error"),
                ),
            }
        if not self.answered or self.related(tool, args):
            return None
        self.drift_blocked += 1
        if self.drift_blocked >= self.drift_limit:
            self.turn_should_end = True
            self.end_reason = "drift"
        paths = sorted(self._call_paths(args))
        target = f"on `{_relative(paths[0], self.root)}` " if paths else ""
        return {
            **NOT_EXECUTED,
            "status": "error",
            "error": POST_ANSWER_CORRECTION.format(tool=tool, target=target),
        }

    def record(self, tool: str, args: Any, result: Any) -> None:
        """Note a call that ran: widen the scope, and count or clear failures."""
        args = args if isinstance(args, dict) else {}
        if _is_tool_failure(result):
            self.failures[tool] = self.failures.get(tool, 0) + 1
            self.last_error[tool] = _error_brief(result)
            return
        self.failures.pop(tool, None)
        self.corrected.discard(tool)
        if is_mutating_tool(tool):
            # The world changed, so a retry of anything is a new attempt.
            self.failures.clear()
            self.corrected.clear()
        if not self.answered or self.related(tool, args):
            self.paths |= self._call_paths(args)
