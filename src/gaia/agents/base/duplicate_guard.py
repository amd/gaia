# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Short-circuit a tool call the agent already made this turn, unchanged.

Measured with reasoning disabled, one model repeated the identical shell
command 101 times in a row, and its twin 101 times: 281 calls in 43 steps, no
edit, until the step budget ran out. Every call ran and every result was
re-sent. The guard is model-agnostic: the same call (same tool, same arguments
after JSON canonicalisation) already executed within the last
``duplicate_window`` steps does not run again. The model gets a structured
result naming the step that ran it and a handle to the archived output, and
nothing is lost.

A call runs anyway when its previous execution errored (a retry is
legitimate), when a file-changing tool touched a path the call references
since then, when the call observes the workspace at large (a shell command)
and any file changed since then, and for the tools that read the agent's own
state (``read_tool_output``, ``session_findings``, ``delegate_task``, ``sleep``,
``request_user_input``).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from gaia.agents.base.artifacts import ArtifactStore
from gaia.agents.base.verification import (
    NOT_EXECUTED,
    is_mutating_tool,
    paths_changed_by,
)

DUPLICATE_GUARD_ENV_VAR = "GAIA_DUPLICATE_GUARD"
DUPLICATE_WINDOW_ENV_VAR = "GAIA_DUPLICATE_WINDOW"
DUPLICATE_LIMIT_ENV_VAR = "GAIA_DUPLICATE_LIMIT"

DEFAULT_DUPLICATE_WINDOW = 6
DEFAULT_DUPLICATE_LIMIT = 3

#: Their result is the agent's own state or a fresh side effect, never a rerun.
ALWAYS_EXECUTE: FrozenSet[str] = frozenset(
    {
        "read_tool_output",
        "session_findings",
        "delegate_task",
        "sleep",
        "request_user_input",
    }
)

#: A shell command can observe any file, so any change since its last run may
#: change its output. Judged by name here and by a ``command`` argument too.
_WORKSPACE_TOOLS: FrozenSet[str] = frozenset(
    {"run_shell_command", "run_python", "execute_python_file"}
)


def _observes_workspace(tool_name: str, tool_args: Any) -> bool:
    return tool_name in _WORKSPACE_TOOLS or (
        isinstance(tool_args, dict) and "command" in tool_args
    )


_TRUE = ("1", "true", "on", "yes")
_FALSE = ("0", "false", "off", "no")


def duplicate_guard_from_env() -> Optional[bool]:
    """``GAIA_DUPLICATE_GUARD`` as a bool, or ``None`` when unset; malformed fails loudly."""
    raw = os.getenv(DUPLICATE_GUARD_ENV_VAR)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(
        f"{DUPLICATE_GUARD_ENV_VAR} must be one of 1, 0, true, false, on, off, "
        f"got {raw!r}"
    )


def _positive_int_from_env(var: str) -> Optional[int]:
    raw = os.getenv(var)
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except ValueError as e:
        raise ValueError(f"{var} must be a positive integer, got {raw!r}") from e
    if value < 1:
        raise ValueError(f"{var} must be a positive integer, got {value}")
    return value


def duplicate_window_from_env() -> Optional[int]:
    """``GAIA_DUPLICATE_WINDOW`` as steps, or ``None`` when unset; malformed fails loudly."""
    return _positive_int_from_env(DUPLICATE_WINDOW_ENV_VAR)


def duplicate_limit_from_env() -> Optional[int]:
    """``GAIA_DUPLICATE_LIMIT`` as a count, or ``None`` when unset; malformed fails loudly."""
    return _positive_int_from_env(DUPLICATE_LIMIT_ENV_VAR)


def validate_positive_int(name: str, value: Any) -> int:
    """*value* as a positive int, or a ``ValueError`` naming *name*."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}.")
    return value


def canonical_args(tool_args: Any) -> str:
    """The arguments as one deterministic string: key order does not matter."""
    try:
        return json.dumps(tool_args, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(tool_args)


def result_text(result: Any) -> str:
    """The archived form of a result: a string as is, anything else as JSON."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    except (TypeError, ValueError):
        return repr(result)


def _result_ok(result: Any) -> bool:
    """Did the call run and succeed? A failed, refused or unrun call may be retried."""
    if not isinstance(result, dict):
        return True
    if (
        result.get("executed") is False
        or result.get("ran") is False
        or result.get("status") == "denied"
    ):
        return False
    return not (
        result.get("status") == "error"
        or result.get("success") is False
        or result.get("has_errors") is True
        or result.get("return_code", 0) != 0
    )


_WORD_RE = re.compile(r"""[^\s"'`,;:=()\[\]{}<>|&]+""")


def _path_forms(path: str) -> List[str]:
    """*path* as given and, when absolute and under the working directory, relative."""
    forms = [path]
    if os.path.isabs(path):
        try:
            relative = os.path.relpath(path)
        except ValueError:
            relative = ""
        if relative and not relative.startswith(".."):
            forms.append(relative)
    return forms


def _references(args_text: str, path: str) -> bool:
    """Does a call with these arguments look at *path*?

    True when a word of the arguments is the path, ends in its basename, or is
    a directory above it: a call on a directory sees the files in it.
    """
    basename = os.path.basename(path)
    for word in _WORD_RE.findall(args_text):
        word = word.rstrip("/")
        if not word or word in (".", ".."):
            continue
        if word == path or word.endswith("/" + path):
            return True
        if basename and (word == basename or word.endswith("/" + basename)):
            return True
        if path.startswith(word + "/"):
            return True
    return False


class DuplicateCallGuard:
    """One agent's per-turn memory of executed calls and what they returned."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        window: int = DEFAULT_DUPLICATE_WINDOW,
        limit: int = DEFAULT_DUPLICATE_LIMIT,
        store: Optional[Callable[[], ArtifactStore]] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.window = validate_positive_int("duplicate_window", window)
        self.limit = validate_positive_int("duplicate_limit", limit)
        self._store = store
        self._step = 0
        self._sequence = 0
        # (tool, canonical args) -> record of the latest execution.
        self._executed: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # (path, sequence of the change) for every file changed this turn.
        self._changes: List[Tuple[str, int]] = []
        self._turn_short_circuits = 0
        self._step_short_circuits = 0
        self._step_loop_suspected = False

    # -- lifecycle ---------------------------------------------------------

    def begin_turn(self) -> None:
        self._step = 0
        self._sequence = 0
        self._executed.clear()
        self._changes.clear()
        self._turn_short_circuits = 0
        self.begin_step(0)

    def begin_step(self, step: int) -> None:
        self._step = step
        self._step_short_circuits = 0
        self._step_loop_suspected = False

    # -- the rule ----------------------------------------------------------

    def check(self, tool_name: str, tool_args: Any) -> Optional[Dict[str, Any]]:
        """The result to return instead of running the call, or ``None`` to run it."""
        if not self.enabled or tool_name in ALWAYS_EXECUTE:
            return None
        key = (tool_name, canonical_args(tool_args))
        record = self._executed.get(key)
        if record is None or not record["ok"]:
            return None
        if self._step - record["step"] > self.window:
            return None
        if self._changed_since(
            _observes_workspace(tool_name, tool_args), key[1], record["sequence"]
        ):
            return None
        return self._short_circuit(record)

    def record(self, tool_name: str, tool_args: Any, result: Any) -> None:
        """Note one executed call and, for a file-changing tool, what it changed."""
        if not self.enabled:
            return
        self._sequence += 1
        self._executed[(tool_name, canonical_args(tool_args))] = {
            "step": self._step,
            "sequence": self._sequence,
            "ok": _result_ok(result),
            "result": result,
            "artifact": None,
            "repeats": 0,
        }
        if _result_ok(result) and is_mutating_tool(tool_name):
            for path in paths_changed_by(tool_name, tool_args):
                for form in _path_forms(path):
                    self._changes.append((form, self._sequence))

    def _changed_since(self, any_file: bool, args_text: str, sequence: int) -> bool:
        recent = [path for path, seq in self._changes if seq > sequence]
        if not recent:
            return False
        if any_file:
            return True
        return any(_references(args_text, path) for path in recent)

    def _short_circuit(self, record: Dict[str, Any]) -> Dict[str, Any]:
        if record["artifact"] is None:
            record["artifact"] = self._archive(record["result"])
        record["repeats"] += 1
        self._turn_short_circuits += 1
        self._step_short_circuits += 1
        handle = record["artifact"]
        message = (
            f"Identical call already ran at step {record['step']} with the same "
            "result; nothing has changed since. Its full output is at "
            f"read_tool_output(artifact={handle}, entry=1). Take a different "
            "action."
        )
        if self._turn_short_circuits >= self.limit:
            self._step_loop_suspected = True
            message += (
                f" You have repeated calls {self._turn_short_circuits} times "
                "this turn; the loop guard will end the turn if it continues."
            )
        return {
            **NOT_EXECUTED,
            "status": "duplicate",
            "executed": False,
            "message": message,
            "artifact": handle,
            "previous_step": record["step"],
        }

    def _archive(self, result: Any) -> str:
        if self._store is None:
            raise RuntimeError(
                "DuplicateCallGuard has no artifact store to archive a repeated "
                "result in; construct it with store=."
            )
        store = self._store()
        text = result_text(result)
        handle = store.put(text)
        store.set_index(handle, [{"offset": 0, "length": len(text)}])
        return handle

    # -- what the loop reads -----------------------------------------------

    def repeats(self, tool_name: str, tool_args: Any) -> int:
        """How many times this call has been made this turn, executions included."""
        record = self._executed.get((tool_name, canonical_args(tool_args)))
        return 0 if record is None else 1 + record["repeats"]

    @property
    def turn_should_end(self) -> bool:
        """True once the turn has short-circuited ``2 * limit`` calls."""
        return self.enabled and self._turn_short_circuits >= 2 * self.limit

    def step_stats(self) -> Dict[str, Any]:
        """Fields for the current step's stats record; empty when nothing happened."""
        if not self._step_short_circuits:
            return {}
        stats: Dict[str, Any] = {
            "duplicates_short_circuited": self._step_short_circuits
        }
        if self._step_loop_suspected:
            stats["loop_suspected"] = True
        return stats
