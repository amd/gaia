# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""DelegateToolsMixin — hand a bounded subtask to a fresh child agent.

The child is a new instance of the same agent class with the same config, a
fresh conversation, its own step budget, and no delegate tool of its own. When
it finishes, its whole context is discarded: only a small result plus evidence
enters the parent's conversation, and the full transcript is kept as an
artifact the parent can page with ``read_tool_output``. The child's model cost
is added to the parent's totals so a saving is measured honestly.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base.artifacts import store_for
from gaia.agents.base.tool_output import elide_text
from gaia.agents.base.verification import (
    is_check_execution,
    strip_verification_scope,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

DELEGATE_ENV_VAR = "GAIA_DELEGATE"
DELEGATE_MAX_STEPS_ENV_VAR = "GAIA_DELEGATE_MAX_STEPS"
DEFAULT_DELEGATE_MAX_STEPS = 40

#: Serialized size the returned dict stays under; the transcript keeps the rest.
RESULT_BUDGET_CHARS = 3800
COMMANDS_CAP = 20
COMMAND_CHARS = 200
FILES_CAP = 50
TESTS_CAP = 10

#: ``error_history`` types the agent loop records when the backend, not the
#: task, failed. A child ending on one of these did not produce a result.
PROVIDER_ERROR_TYPES = frozenset(
    {"llm_connection_error", "llm_streaming_error", "llm_error"}
)

_SKIP_DIRS = frozenset({"node_modules", "__pycache__"})
_MTIME_SCAN_CAP = 200_000

_BRIEF_HEADER = (
    "You are a delegated worker. You have no memory of the conversation that "
    "produced this brief, so it contains everything you need. Complete exactly "
    "this brief and reply with the result in the requested format."
)


def delegate_env_override() -> Optional[bool]:
    """``GAIA_DELEGATE`` parsed, or ``None`` when unset."""
    raw = os.getenv(DELEGATE_ENV_VAR)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


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
    ``delegate_enabled``, ``delegate_max_steps``, ``delegate_depth``,
    ``max_steps``, ``silent_mode`` and ``output_handler``, and accepts
    ``type(self)(config=...)``. Override :meth:`_child_config` when a host's
    config is shaped differently.
    """

    #: Running total of every child's model cost, for the parent's stats.
    delegated_tokens: Dict[str, int]

    def _resolve_delegate_enabled(self) -> bool:
        """Depth 1 is final; then ``GAIA_DELEGATE`` wins over the config field."""
        config = getattr(self, "config", None)
        if getattr(config, "delegate_depth", 0) > 0:
            return False
        override = delegate_env_override()
        if override is not None:
            return override
        return bool(getattr(config, "delegate_enabled", False))

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

    def register_delegate_tools(self) -> None:
        """Register ``delegate_task`` into the tool registry."""
        from gaia.agents.base.tools import tool

        self.delegated_tokens = {"input": 0, "output": 0, "cached": 0, "children": 0}

        # Above the default tool timeout: a child runs a whole agent loop.
        @tool(timeout=3600, display_label="Delegating")
        def delegate_task(
            goal: str, scope: str, done_when: str, return_format: str
        ) -> Dict[str, Any]:
            """Hand a bounded subtask to a fresh worker agent and get back only its short result.

            Use it for investigation that ends in a short answer — find where
            X is implemented, explain how Y works, run the tests and report
            the failures — and for a bounded edit when asked. The worker's
            exploration never enters this conversation, so it costs far less
            than exploring here. Do not delegate a task whose full output you
            need verbatim, or one that depends on what has been said here.

            The worker starts with NO memory of this conversation. The brief
            must carry every path, name, command and criterion it needs.

            Args:
                goal: What to find out or change, in one or two sentences.
                scope: Where to look or work: directories, files, symbols, commands.
                done_when: The concrete condition that means the task is finished.
                return_format: The shape of the answer wanted back, e.g. "file:line and a one-paragraph explanation".

            Returns:
                result, evidence (files_changed, commands_run, tests), steps,
                tool_calls, tokens, hit_step_limit, and a transcript handle
                for read_tool_output.
            """
            return self._delegate_task(goal, scope, done_when, return_format)

    # ── delegation ──────────────────────────────────────────────────────────

    def _delegate_task(
        self, goal: str, scope: str, done_when: str, return_format: str
    ) -> Dict[str, Any]:
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
        brief = self._delegate_brief(fields)
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
        self._account_child(tokens)
        handle = store_for(self).put(
            json.dumps(
                {
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
        result: Dict[str, Any] = {
            "status": "error" if failure else "success",
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
        return _bounded(result)

    def _delegate_brief(self, fields: Dict[str, str]) -> str:
        return "\n".join(
            [
                _BRIEF_HEADER,
                "",
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
            delegate_enabled=False,
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

    def _account_child(self, tokens: Dict[str, int]) -> None:
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
                    "performance_stats": {
                        "input_tokens": tokens["input"],
                        "output_tokens": tokens["output"],
                        "cached_tokens": tokens["cached"],
                    },
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
            str(e.get("args", {}).get("command", ""))[:COMMAND_CHARS]
            for e in executions
            if e.get("tool") == "run_shell_command"
        ]
        tests = [
            {
                "tool": e.get("tool"),
                "check": e.get("check_label"),
                "target": e.get("check_target"),
                "failed": bool(e.get("failed")),
            }
            for e in executions
            if is_check_execution(e)
        ]
        files = self._files_changed(workdir, before)
        return {
            "files_changed": files[:FILES_CAP],
            "files_changed_total": len(files),
            "commands_run": commands[:COMMANDS_CAP],
            "commands_total": len(commands),
            "tests": tests[:TESTS_CAP],
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
    ) -> List[str]:
        kind, prior = before
        root = Path(workdir)
        # Both snapshots carry mtimes, so a path dirty before the child ran and
        # edited again still shows up.
        now = _git_snapshot(root) if kind == "git" else _mtime_snapshot(root)
        return sorted(p for p in set(prior) | set(now) if prior.get(p) != now.get(p))


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


def _bounded(result: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the serialized result under the budget by eliding the answer only."""
    if len(json.dumps(result, ensure_ascii=False)) <= RESULT_BUDGET_CHARS:
        return result
    answer = result["result"]
    shell = dict(result)
    shell["result"] = {}
    room = RESULT_BUDGET_CHARS - len(json.dumps(shell, ensure_ascii=False)) - 80
    excerpt = elide_text(answer, max(300, room))
    excerpt["continuation"] = "read_tool_output"
    excerpt["artifact"] = result["transcript"]
    shell["result"] = excerpt
    return shell
