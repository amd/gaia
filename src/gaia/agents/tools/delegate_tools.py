# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""DelegateToolsMixin — hand a bounded subtask to a fresh child agent.

The child is a new instance of the same agent class with the same config, a
fresh conversation, its own step budget, and no delegate tool of its own. When
it finishes, its whole context is discarded: only a small result plus evidence
enters the parent's conversation, and the full transcript is kept as an
artifact the parent can page with ``read_tool_output``. The child's model cost
is added to the parent's totals so a saving is measured honestly.

Three modes (``delegate_mode`` / ``GAIA_DELEGATE``): ``off``; ``tool``, where
``delegate_task`` is one tool among the parent's usual set; and
``orchestrate``, where the parent is offered nothing but ``delegate_task`` and
``read_tool_output`` and every unit of work is a child.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base.artifacts import store_for
from gaia.agents.base.tool_output import elide_text
from gaia.agents.base.verification import (
    has_test_run_summary,
    is_check_execution,
    strip_verification_scope,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

DELEGATE_ENV_VAR = "GAIA_DELEGATE"
DELEGATE_MAX_STEPS_ENV_VAR = "GAIA_DELEGATE_MAX_STEPS"
DELEGATE_MAX_CHILDREN_ENV_VAR = "GAIA_DELEGATE_MAX_CHILDREN"
DEFAULT_DELEGATE_MAX_STEPS = 40
DEFAULT_DELEGATE_MAX_CHILDREN = 12

DELEGATE_MODES = ("off", "tool", "orchestrate")
#: ``GAIA_DELEGATE`` spellings of the ``tool`` and ``off`` modes.
_TOOL_MODE_ALIASES = frozenset({"1", "true", "yes", "on"})
_OFF_MODE_ALIASES = frozenset({"0", "false", "no", "off"})

#: What an orchestrating parent is offered, and all it may execute.
ORCHESTRATOR_TOOLS = ("delegate_task", "read_tool_output")

DELEGATE_KINDS = ("investigate", "implement", "verify")
DEFAULT_DELEGATE_KIND = "implement"

#: Serialized size the returned dict stays under: one read_tool_output page.
RESULT_BUDGET_CHARS = 8000
#: The child's answer is the deliverable; it is kept whole up to this size and
#: comes back as a chunk index of its own archive beyond it.
ANSWER_WHOLE_CHARS = 6500
COMMANDS_CAP = 12
COMMAND_CHARS = 200
FILES_CAP = 50
TEST_EVIDENCE_CHARS = 600

#: ``error_history`` types the agent loop records when the backend, not the
#: task, failed. A child ending on one of these did not produce a result.
PROVIDER_ERROR_TYPES = frozenset(
    {"llm_connection_error", "llm_streaming_error", "llm_error"}
)

#: The shell tool's own ``cd <workdir> && `` prefix says nothing about the task.
_CD_PREFIX_RE = re.compile(r"^\s*cd\s+\S+\s*&&\s*")

_SKIP_DIRS = frozenset({"node_modules", "__pycache__"})
_MTIME_SCAN_CAP = 200_000

#: Prompt guidance the flagship carries only while delegation is on, so the
#: prompt is byte-identical otherwise and the child never sees it.
DELEGATE_SYSTEM_PROMPT = """\
==== DELEGATION ====
Plan first. Then delegate each bounded piece of work as a subtask with \
delegate_task: investigation that ends in a short answer, and implementation of \
one component or change including its tests. The brief names the files, symbols \
and commands, and the exact acceptance check: which test command must pass. The \
worker starts with no memory of this conversation. Do not re-read files a worker \
has already changed unless its evidence shows a problem: trust files_changed and \
the test output in evidence. Keep for yourself only the plan, the review of each \
worker's evidence, and the final end-to-end verification."""

#: The parent's guidance in ``orchestrate`` mode, in place of the one above.
ORCHESTRATE_SYSTEM_PROMPT = """\
==== ORCHESTRATION ====
You are the orchestrator. You cannot read, search, run or edit anything \
yourself: your only tools are delegate_task and read_tool_output. Every unit of \
work — investigation, implementation, and the final verification — is a \
delegate_task call with a complete brief, since the worker has no memory of \
this conversation. Start with one kind="investigate" subtask that returns the \
facts you need to plan: the files and symbols involved, and how the tests run. \
Then one kind="implement" subtask per component, each including its tests. \
Finish with one kind="verify" subtask that runs the full relevant test command \
and reports the summary. Answer only from the workers' evidence, and report \
it as theirs: you never ran anything yourself, so say which worker ran what \
and quote its evidence.tests.command and summary — never "I ran"."""

_KIND_HINTS = {
    "investigate": "answer the question; do not change files.",
    "implement": "make the change and run its tests.",
    "verify": "run the checks and report their output; do not change files.",
}

_BRIEF_HEADER = (
    "You are a delegated worker. You have no memory of the conversation that "
    "produced this brief, so it contains everything you need. Complete exactly "
    "this brief and reply with the result in the requested format."
)


def delegate_env_override() -> Optional[str]:
    """``GAIA_DELEGATE`` as a mode, or ``None`` when unset; malformed fails loudly."""
    raw = os.getenv(DELEGATE_ENV_VAR)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _TOOL_MODE_ALIASES:
        return "tool"
    if value in _OFF_MODE_ALIASES:
        return "off"
    if value in DELEGATE_MODES:
        return value
    raise ValueError(
        f"{DELEGATE_ENV_VAR} must be one of {', '.join(DELEGATE_MODES)} "
        f"(or 1/0 for tool/off), got {raw!r}"
    )


def delegate_max_children_from_env() -> Optional[int]:
    """``GAIA_DELEGATE_MAX_CHILDREN`` parsed, or ``None``; malformed fails loudly."""
    raw = os.getenv(DELEGATE_MAX_CHILDREN_ENV_VAR)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(
            f"{DELEGATE_MAX_CHILDREN_ENV_VAR} must be an integer, got {raw!r}"
        ) from e
    if value < 1:
        raise ValueError(
            f"{DELEGATE_MAX_CHILDREN_ENV_VAR} must be at least 1, got {value}"
        )
    return value


def delegate_max_steps_from_env() -> Optional[int]:
    """``GAIA_DELEGATE_MAX_STEPS`` parsed, or ``None``; malformed fails loudly."""
    raw = os.getenv(DELEGATE_MAX_STEPS_ENV_VAR)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(
            f"{DELEGATE_MAX_STEPS_ENV_VAR} must be an integer, got {raw!r}"
        ) from e
    if value < 1:
        raise ValueError(
            f"{DELEGATE_MAX_STEPS_ENV_VAR} must be at least 1, got {value}"
        )
    return value


class DelegateToolsMixin:
    """Mixin providing ``delegate_task``.

    The host stores its dataclass config as ``self.config`` with the fields
    ``delegate_mode``, ``delegate_max_steps``, ``delegate_max_children``,
    ``delegate_depth``, ``max_steps``, ``silent_mode`` and ``output_handler``,
    and accepts ``type(self)(config=...)``. Override :meth:`_child_config` when
    a host's config is shaped differently.

    ``orchestrate`` mode needs the host's help at two points the mixin cannot
    reach from the back of the MRO: its per-turn tool selection must return
    :data:`ORCHESTRATOR_TOOLS`, and its tool execution must honour
    :meth:`_orchestrator_refusal`.
    """

    #: Running total of every child's model cost, for the parent's stats.
    delegated_tokens: Dict[str, int]

    def _resolve_delegate_mode(self) -> str:
        """Depth 1 is ``off``; then ``GAIA_DELEGATE`` wins over the config field."""
        config = getattr(self, "config", None)
        if getattr(config, "delegate_depth", 0) > 0:
            return "off"
        override = delegate_env_override()
        if override is not None:
            return override
        mode = getattr(config, "delegate_mode", "off")
        if mode not in DELEGATE_MODES:
            raise ValueError(
                f"delegate_mode must be one of {', '.join(DELEGATE_MODES)}, "
                f"got {mode!r}"
            )
        return mode

    def _resolve_delegate_enabled(self) -> bool:
        return self._resolve_delegate_mode() != "off"

    def _orchestrating(self) -> bool:
        return self._resolve_delegate_mode() == "orchestrate"

    def _orchestrator_refusal(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """The error an orchestrating parent gets for any tool it was not offered."""
        if not self._orchestrating() or tool_name in ORCHESTRATOR_TOOLS:
            return None
        from gaia.agents.base.verification import NOT_EXECUTED

        return {
            **NOT_EXECUTED,
            "status": "error",
            "error": (
                f"{tool_name} is not available to the orchestrator. Your only "
                f"tools are {' and '.join(ORCHESTRATOR_TOOLS)}: delegate this "
                "work to a worker with a complete brief."
            ),
        }

    def _delegate_max_steps(self) -> int:
        override = delegate_max_steps_from_env()
        if override is not None:
            return override
        return int(
            getattr(
                getattr(self, "config", None),
                "delegate_max_steps",
                DEFAULT_DELEGATE_MAX_STEPS,
            )
        )

    def _delegate_max_children(self) -> int:
        override = delegate_max_children_from_env()
        if override is not None:
            return override
        return int(
            getattr(
                getattr(self, "config", None),
                "delegate_max_children",
                DEFAULT_DELEGATE_MAX_CHILDREN,
            )
        )

    def get_delegate_system_prompt(self) -> str:
        """The mode's guidance, auto-collected by ``_get_mixin_prompts``."""
        mode = self._resolve_delegate_mode()
        if mode == "orchestrate":
            return ORCHESTRATE_SYSTEM_PROMPT
        if mode == "tool":
            return DELEGATE_SYSTEM_PROMPT
        return ""

    def register_delegate_tools(self) -> None:
        """Register ``delegate_task`` into the tool registry."""
        from gaia.agents.base.tools import tool

        self.delegated_tokens = {"input": 0, "output": 0, "cached": 0, "children": 0}

        # Above the default tool timeout: a child runs a whole agent loop.
        @tool(timeout=3600, display_label="Delegating")
        def delegate_task(
            goal: str,
            scope: str,
            done_when: str,
            return_format: str,
            kind: str = DEFAULT_DELEGATE_KIND,
        ) -> Dict[str, Any]:
            """Hand a bounded subtask to a fresh worker agent and get back only its result and evidence.

            Two kinds of subtask: investigation that ends in a short answer
            (where X is implemented, how Y works, which tests fail and why),
            and implementation of one component or change including its
            tests. The worker may edit files and run tests; it reads, edits
            and verifies itself and returns files_changed plus test evidence.
            Its exploration never enters this conversation, so it costs far
            less than doing the work here. Do not delegate a task whose full
            output you need verbatim, or one that depends on what has been
            said here.

            The worker starts with NO memory of this conversation. The brief
            must carry every path, symbol, command and criterion it needs,
            including the exact test command that must pass.

            The worker's run is the worker's, not yours: when reporting, say
            which worker ran what and quote its evidence.tests.command and
            summary; never present it as something you ran.

            Args:
                goal: What to find out or change, in one or two sentences.
                scope: Where to look or work: directories, files, symbols, commands.
                done_when: The concrete acceptance check, e.g. "pytest tests/unit/test_x.py passes".
                return_format: The shape of the answer wanted back, e.g. "file:line and a one-paragraph explanation".
                kind: "investigate" (answer a question, change nothing), "implement" (the default: change code and run its tests) or "verify" (run the checks and report).

            Returns:
                kind, result (the worker's answer, whole; a very long one comes back
                as shown parts plus a numbered index of its own archive, read
                with read_tool_output(artifact, entry=n)), evidence
                (files_changed with added/removed line counts, commands_run,
                tests: the last test command and its summary), steps,
                tool_calls, tokens, hit_step_limit, and transcript: the
                worker's full conversation log, for drilling into how it
                worked, never needed to use the answer.
            """
            return self._delegate_task(goal, scope, done_when, return_format, kind)

    # ── delegation ──────────────────────────────────────────────────────────

    def _delegate_task(
        self,
        goal: str,
        scope: str,
        done_when: str,
        return_format: str,
        kind: str = DEFAULT_DELEGATE_KIND,
    ) -> Dict[str, Any]:
        cap = self._delegate_max_children()
        spent = getattr(self, "delegated_tokens", {}).get("children", 0)
        if spent >= cap:
            return {
                "status": "error",
                "error": (
                    f"delegate_task budget exhausted: {spent} workers have already "
                    f"run (delegate_max_children={cap}). No more will start; "
                    "finish now with the evidence you already have."
                ),
            }
        if kind not in DELEGATE_KINDS:
            return {
                "status": "error",
                "error": (
                    f"delegate_task kind must be one of {', '.join(DELEGATE_KINDS)}, "
                    f"got {kind!r}."
                ),
            }
        fields = {
            "goal": goal,
            "scope": scope,
            "done_when": done_when,
            "return_format": return_format,
        }
        empty = [
            k for k, v in fields.items() if not isinstance(v, str) or not v.strip()
        ]
        if empty:
            return {
                "status": "error",
                "error": (
                    f"delegate_task needs every field as a non-empty string; "
                    f"missing: {', '.join(empty)}. The worker has no memory of "
                    "this conversation, so the brief must be complete."
                ),
            }
        brief = self._delegate_brief(fields, kind)
        workdir = self._delegate_workdir()
        try:
            before = self._workdir_snapshot(workdir)
            child = self._spawn_child()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("delegate_task could not start a worker: %s", e)
            return {
                "status": "error",
                "error": f"could not start a worker: {type(e).__name__}: {e}",
                "transcript": None,
            }
        try:
            outcome = child.process_query(brief)
            failure: Optional[str] = None
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("delegated worker raised: %s", e)
            outcome = {"result": "", "conversation": [], "error_history": []}
            failure = f"{type(e).__name__}: {e}"
        finally:
            self._close_child(child)
        if failure is None:
            failure = _provider_failure(outcome)
        evidence = self._delegate_evidence(child, workdir, before)
        tokens = _child_tokens(outcome)
        self._account_child(tokens, kind)
        handle = store_for(self).put(
            json.dumps(
                {
                    "kind": kind,
                    "brief": brief,
                    "status": outcome.get("status"),
                    "result": outcome.get("result", ""),
                    "conversation": outcome.get("conversation", []),
                    "error_history": outcome.get("error_history", []),
                    "tokens": tokens,
                },
                default=str,
                ensure_ascii=False,
            )
        )
        self._record_child_transcript(kind, fields, handle, outcome)
        result: Dict[str, Any] = {
            "status": "error" if failure else "success",
            "kind": kind,
            # The child's scope line restates what ``evidence`` carries.
            "result": strip_verification_scope(str(outcome.get("result") or "")),
            "evidence": evidence,
            "steps": int(outcome.get("steps_taken") or 0),
            "tool_calls": len(getattr(child, "_turn_tool_executions", None) or []),
            "tokens": tokens,
            "hit_step_limit": bool(outcome.get("max_steps_reached")),
            "transcript": handle,
        }
        if failure:
            result["error"] = failure
        return _bounded(result, store_for(self))

    def _delegate_brief(self, fields: Dict[str, str], kind: str) -> str:
        return "\n".join(
            [
                _BRIEF_HEADER,
                "",
                f"Kind: {kind} — {_KIND_HINTS[kind]}",
                f"Goal: {fields['goal'].strip()}",
                f"Scope: {fields['scope'].strip()}",
                f"Done when: {fields['done_when'].strip()}",
                f"Return format: {fields['return_format'].strip()}",
            ]
        )

    def _child_config(self):
        """The child's config: the parent's, minus delegation, plus its own budget."""
        config = getattr(self, "config", None)
        if config is None or not dataclasses.is_dataclass(config):
            raise TypeError(
                f"{type(self).__name__} keeps no dataclass config to build a "
                "worker from; override _child_config() to return one."
            )
        return dataclasses.replace(
            config,
            delegate_mode="off",
            delegate_depth=int(getattr(config, "delegate_depth", 0)) + 1,
            max_steps=self._delegate_max_steps(),
            silent_mode=True,
            output_handler=None,
        )

    def _spawn_child(self):
        """A fresh agent of this class, permissions copied from this console."""
        child = type(self)(config=self._child_config())
        child._enforce_delegate_toggle()  # pylint: disable=protected-access
        for name in ("auto_approve_gated_tools", "full_access"):
            if hasattr(self.console, name):
                setattr(child.console, name, getattr(self.console, name))
        return child

    def _enforce_delegate_toggle(self) -> None:
        """Drop a ``delegate_task`` another agent left in the process-global registry.

        ``@tool`` writes into one dict shared by every agent in the process, so
        a child (or any agent built after a delegating one) would otherwise
        snapshot the parent's tool as its own.
        """
        if self._resolve_delegate_enabled():
            return
        if self._instance_tools is None:
            self._snapshot_tools()
        self._instance_tools.pop("delegate_task", None)
        if hasattr(self, "_system_prompt_cache"):
            del self._system_prompt_cache

    @staticmethod
    def _close_child(child) -> None:
        close = getattr(child, "close", None)
        if callable(close):
            close()

    def _account_child(self, tokens: Dict[str, int], kind: str) -> None:
        """Fold the child's cost into this turn's stats and the running breakdown."""
        totals = getattr(self, "delegated_tokens", None)
        if totals is None:
            totals = self.delegated_tokens = {
                "input": 0,
                "output": 0,
                "cached": 0,
                "children": 0,
            }
        for key in ("input", "output", "cached"):
            totals[key] += tokens[key]
        totals["children"] += 1
        conversation = getattr(self, "_turn_conversation", None)
        if conversation is None:
            return
        conversation.append(
            {
                "role": "system",
                "content": {
                    "type": "stats",
                    "delegated": True,
                    "kind": kind,
                    "performance_stats": {
                        "input_tokens": tokens["input"],
                        "output_tokens": tokens["output"],
                        "cached_tokens": tokens["cached"],
                    },
                },
            }
        )

    def _record_child_transcript(
        self, kind: str, fields: Dict[str, str], handle: str, outcome: Dict[str, Any]
    ) -> None:
        """Keep the child's whole conversation in the parent's turn log, for audit.

        The turn log is what a transcript writer and the eval judge see; the
        model's messages are a separate list, so this never reaches the model.
        """
        conversation = getattr(self, "_turn_conversation", None)
        if conversation is None:
            return
        conversation.append(
            {
                "role": "system",
                "content": {
                    "type": "delegated_transcript",
                    "kind": kind,
                    "brief": dict(fields),
                    "handle": handle,
                    "steps": int(outcome.get("steps_taken") or 0),
                    "conversation": list(outcome.get("conversation") or []),
                },
            }
        )

    # ── evidence ────────────────────────────────────────────────────────────

    def _delegate_workdir(self) -> str:
        root = getattr(self, "_verification_project_root", lambda: None)()
        return str(root or os.getcwd())

    def _delegate_evidence(
        self, child, workdir: str, before: Tuple[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        executions = getattr(child, "_turn_tool_executions", None) or []
        commands = [
            _CD_PREFIX_RE.sub("", str(e.get("args", {}).get("command", "")))[
                :COMMAND_CHARS
            ]
            for e in executions
            if e.get("tool") == "run_shell_command"
        ]
        files = self._files_changed(workdir, before)
        return {
            "files_changed": files[:FILES_CAP],
            "files_changed_total": len(files),
            "commands_run": commands[:COMMANDS_CAP],
            "commands_total": len(commands),
            "tests": _test_evidence(executions),
        }

    def _workdir_snapshot(self, workdir: str) -> Tuple[str, Dict[str, Any]]:
        """``("git", status)`` in a repository, else ``("mtime", scan)``."""
        root = Path(workdir)
        if not root.is_dir():
            raise FileNotFoundError(
                f"delegate_task working directory {workdir} does not exist; "
                "set GAIA_PROJECT_ROOT or run from the project directory."
            )
        if (root / ".git").exists():
            return "git", _git_snapshot(root)
        return "mtime", _mtime_snapshot(root)

    def _files_changed(
        self, workdir: str, before: Tuple[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        kind, prior = before
        root = Path(workdir)
        # Both snapshots carry mtimes, so a path dirty before the child ran and
        # edited again still shows up.
        now = _git_snapshot(root) if kind == "git" else _mtime_snapshot(root)
        paths = sorted(p for p in set(prior) | set(now) if prior.get(p) != now.get(p))
        if kind != "git":
            return [{"path": p} for p in paths]
        counts = _git_numstat(root)
        return [
            {"path": p, **counts.get(p, _untracked_counts(root / p))} for p in paths
        ]


# ── module helpers ───────────────────────────────────────────────────────────


def _git_snapshot(root: Path) -> Dict[str, Tuple[str, Optional[int]]]:
    """path -> (porcelain status, mtime_ns) for every dirty or untracked path."""
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git status failed in {root} (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    snapshot: Dict[str, Tuple[str, Optional[int]]] = {}
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        try:
            mtime: Optional[int] = (root / path).stat().st_mtime_ns
        except OSError:
            mtime = None
        snapshot[path] = (status, mtime)
    return snapshot


def _git_numstat(root: Path) -> Dict[str, Dict[str, int]]:
    """path -> added/removed line counts against HEAD, for tracked changes."""
    proc = subprocess.run(
        ["git", "diff", "--numstat", "HEAD", "--"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git diff --numstat failed in {root} (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    counts: Dict[str, Dict[str, int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if " => " in path:
            path = path.split(" => ", 1)[1].rstrip("}")
        # "-" marks a binary file; report it as no countable lines.
        counts[path] = {
            "added": int(added) if added.isdigit() else 0,
            "removed": int(removed) if removed.isdigit() else 0,
        }
    return counts


def _untracked_counts(path: Path) -> Dict[str, int]:
    """A file git has never seen: every line is an addition."""
    try:
        with open(path, "rb") as stream:
            return {"added": sum(1 for _ in stream), "removed": 0}
    except OSError:
        return {"added": 0, "removed": 0}


def _test_evidence(executions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The worker's last test run: its command, outcome and summary lines."""
    checks = [e for e in executions if is_check_execution(e)]
    if not checks:
        return {"ran": 0}
    last = checks[-1]
    args = last.get("args") or {}
    command = args.get("command") or args.get("code") or last.get("check_target") or ""
    output = str(last.get("output") or "")
    summary = [
        line.strip() for line in output.splitlines() if has_test_run_summary(line)
    ]
    text = "\n".join(summary) if summary else output.strip()
    if len(text) > TEST_EVIDENCE_CHARS:
        text = text[-TEST_EVIDENCE_CHARS:]
    return {
        "ran": len(checks),
        "command": _CD_PREFIX_RE.sub("", str(command))[:COMMAND_CHARS],
        "check": last.get("check_label"),
        "failed": bool(last.get("failed")),
        "summary": text,
    }


def _mtime_snapshot(root: Path) -> Dict[str, Tuple[int, int]]:
    """path -> (mtime_ns, size) for every regular file outside hidden dirs."""
    snapshot: Dict[str, Tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS
        ]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            snapshot[str(full.relative_to(root))] = (st.st_mtime_ns, st.st_size)
            if len(snapshot) > _MTIME_SCAN_CAP:
                raise RuntimeError(
                    f"{root} holds more than {_MTIME_SCAN_CAP} files, too many "
                    "for a modification scan; delegate inside a git repository."
                )
    return snapshot


def _child_tokens(outcome: Dict[str, Any]) -> Dict[str, int]:
    from gaia.agents.base.agent import _sum_cached_tokens, _sum_conversation_tokens

    conversation = outcome.get("conversation") or []
    tokens_in, tokens_out = _sum_conversation_tokens(conversation)
    return {
        "input": tokens_in,
        "output": tokens_out,
        "cached": _sum_cached_tokens(conversation),
    }


def _provider_failure(outcome: Dict[str, Any]) -> Optional[str]:
    """The backend error a child ended on, or ``None`` when it produced a result."""
    if outcome.get("status") == "success":
        return None
    for entry in reversed(outcome.get("error_history") or []):
        if isinstance(entry, dict) and entry.get("type") in PROVIDER_ERROR_TYPES:
            return f"{entry['type']}: {entry.get('error', '')}"
    return None


def _serialize(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _bounded(result: Dict[str, Any], store) -> Dict[str, Any]:
    """Keep the dict under the budget without shortening the answer below its size.

    A short answer is returned whole. A long one is archived under its own
    handle and comes back as a chunk index of that archive; ``transcript`` is
    untouched and ``artifact`` never points at it.
    """
    from gaia.agents.base.chunk_index import condense_result

    answer = result["result"]
    if (
        len(answer) <= ANSWER_WHOLE_CHARS
        and len(_serialize(result)) <= RESULT_BUDGET_CHARS
    ):
        return result
    condensed = condense_result(
        "delegate_task", result, None, RESULT_BUDGET_CHARS, store, _serialize
    )
    if condensed is not None:
        return condensed
    # One structureless block: head and tail, the middle in its own archive.
    handle = store.put(answer)
    shell = dict(result)
    shell["result"] = {}
    room = RESULT_BUDGET_CHARS - len(_serialize(shell)) - 120
    excerpt = elide_text(answer, max(300, room))
    excerpt.update(
        {
            "artifact": handle,
            "continuation": "read_tool_output",
            "offset_unit": "characters",
        }
    )
    shell["result"] = excerpt
    return shell
