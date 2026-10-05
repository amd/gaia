# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep a turn on the request once it has been answered, and stop failing loops.

Two stop conditions the identical-call loop guard cannot see, because in both
the model varies its arguments on every call:

* **Work after the answer.** Once the turn has produced an answer, a tool call
  that touches nothing the request touched — no file it named, read, searched
  or changed, no scratchpad table it built, and no test run — is new work the
  user did not ask for. It is
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
#: Conversation entry type marking where a check sent the answer back for more work.
REOPENED_MARKER = "reopened"

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
#: Arguments naming a scratchpad table — state a turn builds up without a path.
_TABLE_ARG_KEYS = ("table_name",)
_SQL_ARG_KEYS = ("sql",)
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


#: ``cd <dir> &&`` / ``cd <dir>;`` at the start of a command line.
_CD_PREFIX = re.compile(
    r"""^\s*cd\s+(?:/d\s+)?(?:"[^"]*"|'[^']*'|\S+)\s*(?:&&|;)\s*""", re.I
)


def _streak_key(tool: str, args: Dict[str, Any]) -> str:
    """What a failure streak counts: the tool, or for a shell line the program it runs.

    A shell tool dispatches many programs; `python` refused, then `ls` denied,
    then `pip` refused is three walls, not one tool failing over and over.
    """
    command = args.get("command")
    if not isinstance(command, str):
        return tool
    text = command
    while (match := _CD_PREFIX.match(text)) is not None:
        text = text[match.end() :]
    words = text.split(None, 1)
    if not words:
        return tool
    program = re.split(r"[\\/]", words[0].strip("\"'"))[-1].lower()
    return f"{tool} ({program.removesuffix('.exe')})"


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
        #: The failure streak that ended the turn: a tool, or a tool and program.
        self.end_key: Optional[str] = None
        self.drift_blocked = 0
        self.failures: Dict[str, int] = {}
        self.last_error: Dict[str, str] = {}
        self.corrected: Set[str] = set()
        self.paths: Set[str] = set()
        self.tables: Set[str] = set()

    def begin_turn(self, query: str, root: str) -> None:
        self.root = root
        self.answered = False
        self.turn_should_end = False
        self.end_reason = None
        self.end_key = None
        self.drift_blocked = 0
        self.failures = {}
        self.last_error = {}
        self.corrected = set()
        self.paths = {_norm(p, root) for p in mentioned_paths(query or "")}
        self.tables = set()

    def mark_answered(self) -> None:
        """The turn has produced an answer; later calls must serve it."""
        self.answered = True

    def reopen(self) -> None:
        """A check sent the answer back: the work it asks for is the request's."""
        self.answered = False
        self.drift_blocked = 0

    def widen(self, text: str) -> None:
        """Paths a check names (a missing deliverable) become part of the request."""
        self.paths |= {_norm(p, self.root) for p in mentioned_paths(text or "")}

    def _call_paths(self, args: Dict[str, Any]) -> Set[str]:
        return {
            _norm(value, self.root)
            for key in _PATH_ARG_KEYS
            if isinstance((value := args.get(key)), str)
            and value.strip()
            and "\x00" not in value
        }

    @staticmethod
    def _call_tables(args: Dict[str, Any]) -> Set[str]:
        return {
            value.strip().lower()
            for key in _TABLE_ARG_KEYS
            if isinstance((value := args.get(key)), str) and value.strip()
        }

    def _touches_known_table(self, args: Dict[str, Any]) -> bool:
        if self._call_tables(args) & self.tables:
            return True
        for key in _SQL_ARG_KEYS:
            text = args.get(key)
            if isinstance(text, str) and any(
                # Not \b: the scratchpad's own prefix (scratch_<name>) is joined
                # with an underscore, which \b treats as part of the word; that
                # prefix is the only one allowed.
                re.search(
                    rf"(?<![a-z0-9_])(?:scratch_)?{re.escape(table)}(?![a-z0-9_])",
                    text,
                    re.I,
                )
                for table in self.tables
            ):
                return True
        return False

    def related(self, tool: str, args: Dict[str, Any]) -> bool:
        if tool in _ALWAYS_RELATED or verification_check_label(tool, args):
            return True
        if self._call_paths(args) & self.paths:
            return True
        # A table this turn already built is the request's own data.
        if self._touches_known_table(args):
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
        key = _streak_key(tool, args)
        count = self.failures.get(key, 0)
        if self.failure_limit and count >= self.failure_limit:
            if key in self.corrected:
                self.turn_should_end = True
                self.end_reason = "failures"
                self.end_key = key
            self.corrected.add(key)
            return {
                **NOT_EXECUTED,
                "status": "error",
                "error": FAILURE_STREAK_CORRECTION.format(
                    tool=key,
                    count=count,
                    error=self.last_error.get(key, "the tool returned an error"),
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
        key = _streak_key(tool, args)
        if _is_tool_failure(result):
            self.failures[key] = self.failures.get(key, 0) + 1
            self.last_error[key] = _error_brief(result)
            return
        self.failures.pop(key, None)
        self.corrected.discard(key)
        if is_mutating_tool(tool):
            # The world changed, so a retry of anything is a new attempt.
            self.failures.clear()
            self.corrected.clear()
        if not self.answered or self.related(tool, args):
            self.paths |= self._call_paths(args)
            self.tables |= self._call_tables(args)
