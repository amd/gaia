# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run one task's agent through a harness, under conditions both harnesses share.

``gaia`` runs the flagship GaiaAgent in a child process (``gaia_child``);
``claude-code`` runs ``claude -p``. Both get, from one place:

- the same wall-clock cap, enforced by :func:`launch`; a run cut off keeps the
  record of what it did before the cap instead of reporting zero turns;
- the same environment (:func:`conditions_env`): no credentials, the project
  toolchain and the ``gh`` stand-in first on ``PATH``;
- the same model route: the gateway, which alone holds the upstream key
  (Claude Code on Anthropic's own models uses its own login instead);
- the same fence, when one is requested.

Scoring and judging happen afterwards, in the parent, identically for both.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import gaia
from gaia.eval.bench import sandbox, transcripts
from gaia.eval.bench.leaks import agent_env
from gaia.logger import get_logger

log = get_logger(__name__)

GAIA = "gaia"
CLAUDE_CODE = "claude-code"

#: What Claude Code sends as its key to the gateway, which drops it. Not a secret.
GATEWAY_PLACEHOLDER = "gaia-bench-gateway"
_ANTHROPIC_ALIASES = frozenset({"opus", "sonnet", "haiku", "opusplan"})


@dataclass(frozen=True)
class Conditions:
    """Everything one task's agent gets, whichever harness runs it."""

    time_limit_s: int
    #: Directories put first on ``PATH``: the gh stand-in, then the toolchain.
    path_prefix: Tuple[str, ...]
    #: Set in the agent's environment after credentials are removed.
    extra_env: Mapping[str, str]
    #: The model gateway's base URL (no path).
    gateway_url: str
    #: ``(fenced, read_write, read_only)`` when fenced; ``None`` otherwise.
    fence: Optional[Tuple[Tuple[Path, ...], Tuple[Path, ...], Tuple[Path, ...]]] = None
    full_access: bool = False


@dataclass
class AgentRun:
    """What the agent did, before anything is scored."""

    answer: str = ""
    transcript: Dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reported_cost_usd: Optional[float] = None
    error: str = ""
    error_kind: str = ""
    timed_out: bool = False
    wall_seconds: float = 0.0
    #: The tool record, for ``tests_verified`` on a GAIA run.
    conversation: List[Dict[str, Any]] = field(default_factory=list)


def toolchain_dir() -> str:
    """The interpreter's own bin directory: pytest and the project's tools."""
    return str(Path(sys.executable).parent)


def import_roots() -> List[str]:
    """Where this process imports GAIA and the flagship from, so the child runs the same code."""
    roots = [str(Path(gaia.__file__).resolve().parents[1])]
    for name in ("gaia_agent", "gaia_agent_chat"):
        spec = importlib.util.find_spec(name)
        if spec and spec.origin:
            roots.append(str(Path(spec.origin).resolve().parents[1]))
    return list(dict.fromkeys(roots))


def is_anthropic_model(model: str) -> bool:
    return model.startswith("claude-") or model in _ANTHROPIC_ALIASES


def conditions_env(conditions: Conditions, harness: str, model: str) -> Dict[str, str]:
    """The agent's environment. Both harnesses build it here, and only here."""
    extra = dict(conditions.extra_env)
    if harness == GAIA:
        extra["LEMONADE_BASE_URL"] = f"{conditions.gateway_url}/api/v1"
    elif not is_anthropic_model(model):
        extra.update(
            {
                "ANTHROPIC_BASE_URL": conditions.gateway_url,
                "ANTHROPIC_AUTH_TOKEN": GATEWAY_PLACEHOLDER,
                "ANTHROPIC_MODEL": model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                "ANTHROPIC_SMALL_FAST_MODEL": model,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            }
        )
    return agent_env(path_prefix=conditions.path_prefix, extra=extra)


def _fenced(cmd: Sequence[str], conditions: Conditions) -> List[str]:
    if conditions.fence is None:
        return list(cmd)
    fenced, read_write, read_only = conditions.fence
    return sandbox.wrap(cmd, fenced, read_write, read_only)


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def launch(
    cmd: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout_s: int,
    stdout_path: Path,
    stderr_path: Path,
) -> Tuple[Optional[int], bool]:
    """Run *cmd* to completion or the cap; return ``(exit code, timed out)``.

    Output goes to files, so a run killed at the cap keeps everything it wrote.
    The whole process tree is killed: a shell the agent started must not
    outlive the task.
    """
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            list(cmd),
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            **kwargs,
        )
        try:
            return proc.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.wait()
            return None, True


def _tail(path: Path, limit: int = 400) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:].strip()


# ---------------------------------------------------------------------------
# GAIA
# ---------------------------------------------------------------------------


def run_gaia(
    *,
    prompt: str,
    model: str,
    max_steps: int,
    workdir: Path,
    memory_db: Path,
    harness_dir: Path,
    conditions: Conditions,
) -> AgentRun:
    """The flagship in a child process, killed at the cap."""
    outcome_path = harness_dir / "gaia-outcome.json"
    progress_path = harness_dir / "gaia-progress.jsonl"
    spec_path = harness_dir / "gaia-spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "prompt": prompt,
                "model": model,
                "max_steps": max_steps,
                "workdir": str(workdir),
                "memory_db": str(memory_db),
                "full_access": conditions.full_access,
                "outcome": str(outcome_path),
                "progress": str(progress_path),
            }
        ),
        encoding="utf-8",
    )
    env = conditions_env(conditions, GAIA, model)
    env["GAIA_MEMORY_DB"] = str(memory_db)
    env["PYTHONPATH"] = os.pathsep.join(
        [*import_roots(), *filter(None, [env.get("PYTHONPATH")])]
    )
    cmd = _fenced(
        [sys.executable, "-m", "gaia.eval.bench.gaia_child", str(spec_path)], conditions
    )
    started = time.time()
    code, timed_out = launch(
        cmd,
        env=env,
        cwd=workdir,
        timeout_s=conditions.time_limit_s,
        stdout_path=harness_dir / "gaia-stdout.log",
        stderr_path=harness_dir / "gaia-stderr.log",
    )
    run = AgentRun(wall_seconds=round(time.time() - started, 1), timed_out=timed_out)
    if timed_out or not outcome_path.is_file():
        conversation = _progress(progress_path)
        run.conversation = conversation
        run.steps = _progress_steps(progress_path)
        run.tool_calls = sum(1 for e in conversation if e.get("role") == "tool")
        run.transcript = {"prompt": prompt, "answer": "", "conversation": conversation}
        run.error_kind = "failed"
        run.error = (
            f"timed out after {conditions.time_limit_s}s"
            if timed_out
            else f"the agent process exited {code} without a result: "
            f"{_tail(harness_dir / 'gaia-stderr.log')}"
        )
        return run
    data = json.loads(outcome_path.read_text(encoding="utf-8"))
    outcome = data.get("outcome") or {}
    conversation = outcome.get("conversation") or []
    run.answer = str(outcome.get("result") or "")
    run.conversation = conversation
    run.steps = int(outcome.get("steps_taken") or 0)
    run.tool_calls = sum(1 for m in conversation if m.get("role") == "tool")
    run.input_tokens = int(outcome.get("input_tokens") or 0)
    run.output_tokens = int(outcome.get("output_tokens") or 0)
    run.cached_tokens = int(data.get("cached_tokens") or 0)
    run.error, run.error_kind = data.get("error") or "", data.get("error_kind") or ""
    run.transcript = {
        "prompt": prompt,
        "answer": run.answer,
        "conversation": conversation,
    }
    return run


def _progress_lines(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            lines.append(json.loads(line))
        except ValueError:
            continue  # the line the kill cut short
    return lines


def _progress(path: Path) -> List[Dict[str, Any]]:
    return [e for e in _progress_lines(path) if e.get("role") == "tool"]


def _progress_steps(path: Path) -> int:
    return sum(1 for e in _progress_lines(path) if e.get("event") == "llm_call")


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def claude_code_command(prompt: str, model: str) -> List[str]:
    claude = _which("claude")
    return [
        claude,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
    ]


def _which(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(
            f"The claude-code harness needs the Claude Code CLI ({name}) on PATH. "
            "Install it with `npm install -g @anthropic-ai/claude-code`."
        )
    return found


def parse_stream(text: str) -> List[Dict[str, Any]]:
    """Every complete JSON event; a line cut off by the cap is skipped."""
    events = []
    for line in text.splitlines():
        if line.strip().startswith("{"):
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def run_claude_code(
    *,
    prompt: str,
    model: str,
    workdir: Path,
    harness_dir: Path,
    conditions: Conditions,
) -> AgentRun:
    cmd = _fenced(claude_code_command(prompt, model), conditions)
    env = conditions_env(conditions, CLAUDE_CODE, model)
    stdout_path = harness_dir / "claude-stdout.jsonl"
    started = time.time()
    code, timed_out = launch(
        cmd,
        env=env,
        cwd=workdir,
        timeout_s=conditions.time_limit_s,
        stdout_path=stdout_path,
        stderr_path=harness_dir / "claude-stderr.log",
    )
    run = AgentRun(wall_seconds=round(time.time() - started, 1), timed_out=timed_out)
    events = parse_stream(stdout_path.read_text(encoding="utf-8", errors="replace"))
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    run.answer = str(result.get("result") or "")
    run.transcript = {
        "prompt": prompt,
        "answer": run.answer,
        "events": events,
        "result": {k: v for k, v in result.items() if k != "result"},
    }
    stats = transcripts.cc_stats(run.transcript)
    run.steps = int(result.get("num_turns") or stats["turns"] or 0)
    run.tool_calls = int(stats["tool_calls"] or 0)
    usage = result.get("usage") or {}
    cached = int(usage.get("cache_read_input_tokens") or 0)
    run.cached_tokens = cached
    run.input_tokens = (
        int(usage.get("input_tokens") or 0)
        + cached
        + int(usage.get("cache_creation_input_tokens") or 0)
    )
    run.output_tokens = int(usage.get("output_tokens") or 0)
    run.reported_cost_usd = result.get("total_cost_usd")
    if timed_out:
        run.error, run.error_kind = (
            f"timed out after {conditions.time_limit_s}s",
            "failed",
        )
    elif result.get("is_error"):
        run.error = f"claude error: {str(result.get('result'))[:300]}"
        run.error_kind = "failed"
    elif not result:
        run.error = (
            f"claude exited {code} without a result: "
            f"{_tail(harness_dir / 'claude-stderr.log')}"
        )
        run.error_kind = "failed"
    return run
