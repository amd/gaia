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

Only a read-only call is ever short-circuited: a read, search or listing
tool, or a shell command whose every pipeline segment is a read-only program
(``cat``, ``grep``, ``ls``, a read-only ``git`` subcommand, …) with no output
redirection and no in-place flag. Everything else — ``python``, ``pytest``, a
script, ``rm``, ``git commit`` — runs every time: a second ``python
tools/tick.py`` is a second side effect, not a repeat. And a read-only repeat
runs again unless the world is unchanged: at record time the guard fingerprints
what the call looked at (size and mtime of each file it names, the entries of
each directory, or the working directory's top level when it names nothing
that resolves) and a different fingerprint at check time means a fresh run.
A call also runs again when its previous execution errored (a retry is
legitimate) or a file-changing tool has touched a path it references, and
always for the tools that read the agent's own state (``read_tool_output``,
``session_findings``, ``delegate_task``, ``sleep``, ``request_user_input``).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat as stat_mod
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

#: Their result depends only on what is on disk; a repeat with nothing changed
#: is the same result.
READ_ONLY_TOOLS: FrozenSet[str] = frozenset(
    {
        "read_file",
        "search_file_content",
        "search_file",
        "search_code",
        "search_directory",
        "list_directory",
        "find_files",
        "browse_directory",
        "tree",
        "file_info",
        "get_file_info",
        "list_recent_files",
        "search_code_index",
        "get_index_status",
        "generate_diff",
    }
)

#: The one shell tool whose command line the classifier can read. ``run_python``
#: and ``execute_python_file`` run code, which is never read-only.
SHELL_TOOL = "run_shell_command"

#: Programs that only read. A segment starting with anything else is a run.
READ_ONLY_PROGRAMS: FrozenSet[str] = frozenset(
    {
        "cat",
        "sed",
        "head",
        "tail",
        "grep",
        "rg",
        "find",
        "ls",
        "wc",
        "tree",
        "stat",
        "file",
        "diff",
        "sort",
        "uniq",
        "cut",
        "awk",
        "echo",
        "printf",
        "git",
        "cd",
        "pwd",
    }
)
READ_ONLY_GIT_SUBCOMMANDS: FrozenSet[str] = frozenset(
    {"log", "show", "status", "diff", "branch", "rev-parse", "blame", "ls-files"}
)
#: Stderr redirections that write nothing; any other ``>`` is a write.
_HARMLESS_REDIRECTIONS = ("2>&1", "2>/dev/null", "2>nul")
#: Command substitution runs an arbitrary program the segment walk never sees.
_SUBSTITUTION = ("$(", "`")
#: Directory entries a fingerprint keeps; beyond that a change is still very
#: likely to move the count or the mtime.
_FINGERPRINT_ENTRIES = 2000
_CD_PREFIX_RE = re.compile(r"^\s*cd\s+(\S+)\s*&&\s*")
_PATH_ARG_KEYS = (
    "file_path",
    "path",
    "filepath",
    "filename",
    "file",
    "target_path",
    "directory",
    "dir",
    "root",
    "root_dir",
    "search_path",
)


def _git_subcommand(cmd_parts: list) -> "str | None":
    """The git subcommand after any global options (``git -C x log`` -> ``log``)."""
    index = 1
    while index < len(cmd_parts):
        token = cmd_parts[index]
        if not token.startswith("-"):
            return token
        if token in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            index += 2
            continue
        index += 1
    return None


def is_read_only_command(command: str) -> bool:
    """Does every part of *command* only read?

    Each pipeline segment's program must be in :data:`READ_ONLY_PROGRAMS`
    (``git`` only with a subcommand in :data:`READ_ONLY_GIT_SUBCOMMANDS`), with
    no output redirection, no command substitution, no ``sed -i`` / ``awk -i``
    and no ``find`` action that writes or executes. A line shlex rejects is not
    read-only: what it would do cannot be told.
    """
    # Lazy: the shell tool imports the base package, not the other way round.
    from gaia.agents.tools.shell_tools import (  # pylint: disable=import-outside-toplevel
        _ENV_ASSIGNMENT_RE,
        DANGEROUS_FIND_ACTIONS,
        _outside_double_quotes,
        _rewrites_in_place,
        _split_connectors,
        _split_pipeline,
    )

    if not isinstance(command, str) or not command.strip():
        return False
    syntax = _outside_double_quotes(command)
    for harmless in _HARMLESS_REDIRECTIONS:
        syntax = syntax.replace(harmless, " ")
    if ">" in syntax or any(mark in syntax for mark in _SUBSTITUTION):
        return False
    for pipeline, _connector in _split_connectors(command):
        try:
            parts = shlex.split(pipeline)
        except ValueError:
            return False
        for segment in _split_pipeline(parts):
            while segment and _ENV_ASSIGNMENT_RE.match(segment[0]):
                segment = segment[1:]
            if not segment:
                return False
            program = os.path.basename(segment[0]).lower()
            program = program[:-4] if program.endswith(".exe") else program
            if program not in READ_ONLY_PROGRAMS:
                return False
            if program == "git":
                subcommand = _git_subcommand(segment)
                if subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
                    return False
            elif program in ("sed", "awk") and _rewrites_in_place(program, segment):
                return False
            elif program == "find" and any(
                part in DANGEROUS_FIND_ACTIONS for part in segment[1:]
            ):
                return False
    return True


def is_read_only_call(tool_name: str, tool_args: Any) -> bool:
    """May this call be answered from its last result, given nothing changed?"""
    if tool_name in READ_ONLY_TOOLS:
        return True
    if tool_name == SHELL_TOOL and isinstance(tool_args, dict):
        return is_read_only_command(tool_args.get("command"))
    return False


def _call_cwd(tool_name: str, tool_args: Any) -> str:
    cwd = os.getcwd()
    if not isinstance(tool_args, dict):
        return cwd
    given = tool_args.get("working_directory")
    if isinstance(given, str) and given.strip():
        cwd = os.path.join(cwd, os.path.expanduser(given))
    if tool_name == SHELL_TOOL and isinstance(tool_args.get("command"), str):
        match = _CD_PREFIX_RE.match(tool_args["command"])
        if match:
            cwd = os.path.join(cwd, os.path.expanduser(match.group(1)))
    return cwd


def _candidate_paths(tool_name: str, tool_args: Any) -> List[str]:
    """Tokens of the call that may name a file or directory, as given."""
    if not isinstance(tool_args, dict):
        return []
    if tool_name == SHELL_TOOL:
        command = tool_args.get("command")
        if not isinstance(command, str):
            return []
        command = _CD_PREFIX_RE.sub("", command)
        return [w for w in _WORD_RE.findall(command) if not w.startswith("-")]
    return [
        tool_args[key] for key in _PATH_ARG_KEYS if isinstance(tool_args.get(key), str)
    ]


def _entry_state(directory: str) -> Tuple[Any, ...]:
    entries = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    entries.append((entry.name, None, None))
                    continue
                entries.append((entry.name, st.st_size, st.st_mtime_ns))
    except OSError:
        return ()
    entries.sort()
    return tuple(entries[:_FINGERPRINT_ENTRIES]) + ((len(entries),),)


def _state_of(path: str) -> Optional[Tuple[Any, ...]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    if stat_mod.S_ISDIR(st.st_mode):
        return ("dir", st.st_mtime_ns, _entry_state(path))
    return ("file", st.st_size, st.st_mtime_ns)


def fingerprint(tool_name: str, tool_args: Any) -> Dict[str, Tuple[Any, ...]]:
    """What the call looks at, as it is on disk right now.

    Every path-like token that resolves is a file's ``(size, mtime)`` or a
    directory's mtime and entries. When nothing resolves — a pattern-only grep,
    a listing of the current directory — the working directory's top level
    stands in, so a change anywhere visible from there still invalidates.
    """
    cwd = _call_cwd(tool_name, tool_args)
    states: Dict[str, Tuple[Any, ...]] = {}
    for token in _candidate_paths(tool_name, tool_args):
        full = os.path.normpath(os.path.join(cwd, os.path.expanduser(token)))
        if full in states:
            continue
        state = _state_of(full)
        if state is not None:
            states[full] = state
    if not states:
        state = _state_of(cwd)
        states[os.path.normpath(cwd)] = state if state is not None else ("missing",)
    return states


def _changed_paths(
    before: Dict[str, Tuple[Any, ...]], after: Dict[str, Tuple[Any, ...]]
) -> List[str]:
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
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
        on_invalidate: Optional[Callable[[List[str]], None]] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.window = validate_positive_int("duplicate_window", window)
        self.limit = validate_positive_int("duplicate_limit", limit)
        self._store = store
        # Told the paths whose on-disk state no longer matches a recorded
        # result, so the session ledger can mark what it knows of them stale.
        self._on_invalidate = on_invalidate
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
        if not is_read_only_call(tool_name, tool_args):
            return None
        key = (tool_name, canonical_args(tool_args))
        record = self._executed.get(key)
        if record is None or not record["ok"]:
            return None
        if self._step - record["step"] > self.window:
            return None
        if self._changed_since(key[1], record["sequence"]):
            return None
        changed = _changed_paths(
            record["fingerprint"], fingerprint(tool_name, tool_args)
        )
        if changed:
            self._invalidate(changed)
            return None
        return self._short_circuit(record)

    def record(self, tool_name: str, tool_args: Any, result: Any) -> None:
        """Note one executed call and, for a file-changing tool, what it changed."""
        if not self.enabled:
            return
        self._sequence += 1
        ok = _result_ok(result)
        read_only = is_read_only_call(tool_name, tool_args)
        self._executed[(tool_name, canonical_args(tool_args))] = {
            "step": self._step,
            "sequence": self._sequence,
            "ok": ok,
            "result": result,
            "artifact": None,
            "repeats": 0,
            "fingerprint": (
                fingerprint(tool_name, tool_args) if ok and read_only else {}
            ),
        }
        if ok and is_mutating_tool(tool_name):
            changed = paths_changed_by(tool_name, tool_args)
            for path in changed:
                for form in _path_forms(path):
                    self._changes.append((form, self._sequence))
            if changed:
                self._invalidate([os.path.abspath(p) for p in changed])

    def _invalidate(self, paths: List[str]) -> None:
        if self._on_invalidate is not None and paths:
            self._on_invalidate(paths)

    def _changed_since(self, args_text: str, sequence: int) -> bool:
        recent = [path for path, seq in self._changes if seq > sequence]
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
