# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run the same task through Claude Code, as the reference to measure against.

Every other arm varies the *model* inside GAIA's harness. This varies the
**harness**: identical task, identical workspace, identical verifier, but the
agent loop, system prompt and tool surface are Claude Code's. That makes it the
only arm that answers "how far is GAIA from the thing people actually use", and
it is why it belongs in every batch rather than being a one-off comparison.

What is held identical:

* the task — same starting files, same request, same verifier command;
* the isolation — its own sandbox, ``HOME`` redirected inside it, discarded
  afterwards;
* the freedom — GAIA arms run with confirmation-gated tools pre-approved, so
  Claude Code runs with permission checks bypassed. Anything less and it would
  be scored for refusing to run a test rather than for failing one. That
  asymmetry is real: on the first trial under ``acceptEdits`` it was denied
  ``Bash``, fixed the file blind and never ran the suite.

What necessarily differs, and must be read as part of the result rather than
controlled away: Claude Code brings its own prompt, its own tools and its own
loop. A difference in outcome is a difference between *agent systems*, not
between models.

``.claude.json`` is copied into the sandbox home because the credentials live
there; without it the redirected ``HOME`` leaves the CLI unauthenticated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

#: Emitted per-event so the tool sequence is recoverable. Plain ``json`` returns
#: only the final result, which would leave the behavioural half of every
#: comparison — which tools, in what order, how many failed — unmeasurable.
OUTPUT_FORMAT = "stream-json"


def cli() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError(
            "The `claude` CLI is not on PATH, so the reference arm cannot run. "
            "Install Claude Code (npm i -g @anthropic-ai/claude-code) or omit "
            "the claude-code arm from this batch."
        )
    return exe


def prepare_home(sandbox: Path) -> Path:
    """Sandbox home carrying just enough for the CLI to authenticate."""
    home = sandbox / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    for src in (
        Path.home() / ".claude.json",
        Path.home() / ".claude/.credentials.json",
    ):
        if src.exists():
            dest = home / src.relative_to(Path.home())
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    return home


def parse_stream(lines: List[str]) -> Dict[str, Any]:
    """Fold the event stream into the same shape the GAIA child returns."""
    tool_calls: List[Dict[str, Any]] = []
    denials: List[str] = []
    result: Dict[str, Any] = {}
    # tool_use_id -> index, so a later error result can mark the right call.
    index_of: Dict[str, int] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    index_of[block.get("id", "")] = len(tool_calls)
                    tool_calls.append(
                        {
                            "tool": block.get("name", "?"),
                            "arg_keys": sorted(block.get("input") or {}),
                            "failed": False,
                        }
                    )
        elif kind == "user":
            # Tool results come back as user-role blocks; is_error marks a call
            # the agent then had to recover from.
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    i = index_of.get(block.get("tool_use_id", ""))
                    if i is not None:
                        tool_calls[i]["failed"] = True
        elif kind == "result":
            result = event

    usage = result.get("usage") or {}
    denials = [
        d.get("tool_name", "?") for d in (result.get("permission_denials") or [])
    ]
    return {
        "ok": not result.get("is_error", False) and bool(result),
        "steps_taken": int(result.get("num_turns") or 0),
        # Cache reads and writes are real input the model processed and the
        # account is billed for. Reporting only ``input_tokens`` would make this
        # arm look an order of magnitude cheaper than it is.
        "input_tokens": int(
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        ),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "duration": (result.get("duration_ms") or 0) / 1000.0,
        "result": result.get("result") or "",
        "cost_usd": float(result.get("total_cost_usd") or 0.0),
        "tool_calls": tool_calls,
        "permission_denials": denials,
        "error_history": [],
        "conversation": [],
        "turn_metrics": {},
        "turns": [],
        "stop_reason": result.get("stop_reason"),
    }


def run(job: Dict[str, Any], sandbox: Path, timeout_s: int) -> Dict[str, Any]:
    """Drive the CLI once per turn, resuming the session for follow-ups."""
    workspace = Path(job["workspace"])
    home = prepare_home(sandbox)
    env = {
        **job.get("base_env", {}),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONIOENCODING": "utf-8",
    }

    merged: Dict[str, Any] = {}
    session: str = ""
    for text in [job["prompt"], *(job.get("follow_ups") or [])]:
        argv = [
            cli(),
            "-p",
            text,
            "--output-format",
            OUTPUT_FORMAT,
            "--verbose",
            # Matches the GAIA arms' pre-approved gated tools. Safe here: the
            # workspace is a throwaway sandbox.
            "--dangerously-skip-permissions",
            "--model",
            job["model"],
        ]
        if session:
            argv += ["--resume", session]
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        turn = parse_stream(proc.stdout.splitlines())
        if not turn["ok"] and not turn["steps_taken"]:
            turn["error"] = f"claude exited {proc.returncode}: {proc.stderr[-400:]}"
        # Session id lets a follow-up resume the same conversation, which is the
        # whole point of a multi-turn task.
        for line in proc.stdout.splitlines():
            if '"session_id"' in line:
                try:
                    session = json.loads(line).get("session_id") or session
                except json.JSONDecodeError:
                    pass

        if not merged:
            merged = turn
            merged["turns"] = [{"prompt": text[:200], "steps": turn["steps_taken"]}]
        else:
            for key in ("steps_taken", "input_tokens", "output_tokens"):
                merged[key] += turn[key]
            merged["duration"] += turn["duration"]
            merged["cost_usd"] += turn["cost_usd"]
            merged["tool_calls"].extend(turn["tool_calls"])
            merged["permission_denials"].extend(turn["permission_denials"])
            merged["result"] = turn["result"]
            merged["ok"] = merged["ok"] and turn["ok"]
            merged["turns"].append({"prompt": text[:200], "steps": turn["steps_taken"]})
    return merged
