# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Thin wrapper around the Claude Code CLI, for the harvest pipeline's two
optional LLM steps.

``scan``/``report``/``context``/``savings`` stay deterministic, offline and
dependency-free — that property is why their numbers are trustworthy. This
module is deliberately the only place in ``harvest`` that runs a model, so the
boundary is one import away from obvious.

Every failure here is terminal and named. There is no fallback model, no retry
that swallows its last error, and no partial output: a step either produces a
validated artifact or tells the caller exactly what to fix, including how to do
the work by hand.

Privacy: the prompts built on top of this carry material derived from your own
transcripts — session goals, tool names, aggregate tables. It goes to the same
Claude Code install you already use, and nowhere else, but it does leave the
machine. The deterministic steps never do.
"""

import json
import os
import re
import shutil
import subprocess
from typing import Optional

# Sonnet is enough for labelling and summarising tables, and keeps a
# whole-corpus run cheap. Override per invocation with --model.
DEFAULT_MODEL = "sonnet"

_MANUAL = (
    "Both LLM steps are optional: the deterministic reports work without them. "
    "To do this step by hand instead, follow "
    ".claude/skills/analyzing-claude-sessions/SKILL.md."
)


def resolve_claude(binary: Optional[str] = None) -> str:
    """Absolute path to the Claude Code CLI, or an actionable error."""

    candidate = binary or os.environ.get("GAIA_CLAUDE_BIN") or "claude"
    found = shutil.which(candidate)
    if not found:
        raise SystemExit(
            f"Claude Code CLI not found (looked for {candidate!r} on PATH). "
            "Install it from https://claude.com/claude-code, or point "
            "GAIA_CLAUDE_BIN at the binary. " + _MANUAL
        )
    return found


def run_claude(
    prompt: str,
    *,
    binary: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    timeout: int = 900,
) -> str:
    """Run one non-interactive Claude Code turn and return its text.

    Tools are disabled: these steps pass everything the model needs in the
    prompt, and a tool-enabled run could read files the caller never offered.
    """

    exe = resolve_claude(binary)
    argv = [
        exe,
        "-p",
        "--output-format",
        "text",
        # No filesystem or shell access — the prompt is the whole input.
        "--allowed-tools",
        "",
        "--model",
        model,
    ]
    try:
        proc = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise SystemExit(
            f"Claude Code did not answer within {timeout}s. Re-run with a "
            "smaller --batch, or raise --timeout. " + _MANUAL
        ) from e

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:800] or "no output"
        raise SystemExit(
            f"Claude Code exited {proc.returncode}. If this is an auth error, "
            f"run `{exe}` once interactively to sign in.\n{detail}\n" + _MANUAL
        )

    out = proc.stdout.strip()
    if not out:
        raise SystemExit("Claude Code returned an empty response. " + _MANUAL)
    return out


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str, *, what: str = "response"):
    """Parse a JSON payload out of a model reply, failing loudly.

    Models wrap JSON in prose or a fence often enough that stripping both is
    worth doing — but a reply with no parseable JSON is an error, never an
    empty result standing in for one.
    """

    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    candidates.append(text)
    # A bare object/array embedded in prose, as a last resort.
    brace = re.search(r"[\{\[].*[\}\]]", text, re.S)
    if brace:
        candidates.append(brace.group(0))

    for blob in candidates:
        blob = blob.strip()
        if not blob:
            continue
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            continue
    raise SystemExit(
        f"Could not parse JSON from the model's {what}. First 500 characters:\n"
        f"{text[:500]}\n" + _MANUAL
    )
