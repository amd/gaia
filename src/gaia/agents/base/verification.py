# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verification-scope statement appended to every emitted answer (#3376).

The agent loop used to report "done" in the same confident language whether it
ran the test suite or ran nothing at all. Every emitted answer now carries one
line saying which — derived from the turn's own tool-execution log, so it costs
no extra model call.

The line rides in the answer, which the surfaces persist and re-send as
conversation history, so it is HARD-CAPPED at ``VERIFICATION_SCOPE_MAX_CHARS``.
:func:`strip_verification_scope` removes it again for consumers that need the
answer text alone.

Pure and dependency-free on purpose: the agent loop, the Agent-UI SSE handler,
and hub agents all consume it.
"""

from __future__ import annotations

import re
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

VERIFICATION_SCOPE_PREFIX = "Verification: "
VERIFICATION_SCOPE_MAX_CHARS = 200

# Tools that ARE a check by name, whatever their arguments.
_CHECK_TOOLS: FrozenSet[str] = frozenset(
    {
        "build",
        "lint",
        "run_lint",
        "run_test_suite",
        "run_tests",
        "typecheck",
    }
)

# A shell-style call is a check when its command names a test / lint / build
# runner. Deliberately conservative: a miss reads "unverified" (honest and
# cautious), a false positive would claim a check that never ran.
_CHECK_COMMAND_RE = re.compile(
    r"\b("
    r"pytest|py\.test|tox|nox"
    r"|python\s+-m\s+(?:pytest|unittest)"
    r"|npm\s+(?:run\s+)?(?:test|lint|build|typecheck)"
    r"|yarn\s+(?:test|lint|build)"
    r"|pnpm\s+(?:run\s+)?(?:test|lint|build)"
    r"|go\s+(?:test|vet|build)"
    r"|cargo\s+(?:test|clippy|check|build)"
    r"|dotnet\s+(?:test|build)"
    r"|mvn\s+(?:test|verify)"
    r"|make\s+(?:test|check|lint|build)"
    r"|ctest|jest|vitest|mocha"
    r"|ruff|flake8|pylint|mypy|pyright|eslint|tsc|shellcheck"
    r"|util[/\\]lint\.py"
    r")\b",
    re.IGNORECASE,
)

# Argument keys that carry a shell command, in priority order.
_COMMAND_KEYS: Tuple[str, ...] = ("command", "cmd", "script")

#: Result key a tool sets to ``False`` to declare it refused the call before
#: running it. Set at the refusal itself — see ``NOT_EXECUTED`` below.
EXECUTED_KEY = "executed"

#: Spread into a tool's pre-execution refusal: ``{**NOT_EXECUTED, "status": …}``.
NOT_EXECUTED: Dict[str, Any] = {EXECUTED_KEY: False}

#: A declined confirmation never reaches the tool body, so there is nothing
#: there to declare it. The loop's own denial shape says it for them.
_DENIED_STATUS = "denied"

_SCOPE_LINE_RE = re.compile(
    r"\n{1,2}" + re.escape(VERIFICATION_SCOPE_PREFIX) + r"[^\n]*\s*\Z"
)


def verification_check_label(tool_name: str, tool_args: Any) -> Optional[str]:
    """Short label when this call is a verification check, else ``None``.

    ``pytest tests/unit -q`` → ``"pytest"``; ``read_file`` → ``None``.
    """
    name = (tool_name or "").strip()
    if name in _CHECK_TOOLS:
        return name
    if not isinstance(tool_args, dict):
        return None
    for key in _COMMAND_KEYS:
        command = tool_args.get(key)
        if isinstance(command, str) and command.strip():
            match = _CHECK_COMMAND_RE.search(command)
            return " ".join(match.group(0).split()).lower() if match else None
    return None


def check_was_executed(result: Any) -> bool:
    """False only when *result* says the call was stopped before it ran (#3677).

    A command the allowlist refused and a command that ran and failed are both
    ``{"status": "error"}``, so the footer called a rejected ``pytest`` a test
    that "ran and did not pass" — and a *declined* one, which is
    ``{"status": "denied"}``, a test that passed.

    The refusal has to say so: a tool that stops a call before running it
    spreads :data:`NOT_EXECUTED` into what it returns. Guessing from the shape
    of the result instead gets it wrong in the more damaging direction — a real
    failing test whose tool returned a bare error dict would be reported as
    never having run, which is the same false claim with the sign flipped.
    """
    if not isinstance(result, dict):
        return True
    declared = result.get(EXECUTED_KEY)
    if declared is not None:
        return bool(declared)
    return str(result.get("status", "")).lower() != _DENIED_STATUS


def _names(executions: List[Dict[str, Any]], limit: int = 3) -> str:
    """Deduped, order-preserving, count-capped label list."""
    labels: List[str] = []
    for execution in executions:
        label = execution.get("check_label")
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return "a check"
    shown = ", ".join(labels[:limit])
    extra = len(labels) - limit
    return f"{shown} +{extra} more" if extra > 0 else shown


def build_verification_scope(executions: List[Dict[str, Any]]) -> str:
    """One bounded line naming what ran, what passed, and what went unchecked.

    Three distinguishable states: ``verified`` (checks ran and every one
    passed), ``partially verified`` (checks ran, not all passed), and
    ``unverified`` (no check ran at all).

    A check the agent *requested* and never got to run — refused by the shell
    allowlist, declined by the user — is none of those three. It is named as
    not having run, and never counted as one that did (#3677).

    Each execution is ``{"tool": str, "check_label": str | None,
    "failed": bool, "ran": bool}`` — see ``Agent._note_verification_signal``.
    ``ran`` defaults to True for a record written before the field existed.
    """
    executions = list(executions or [])
    ran = [e for e in executions if e.get("ran", True)]
    checks = [e for e in ran if e.get("check_label")]
    # A refusal the agent recovered from is not an unrun check. Retrying a
    # refused command in an allowed form is the ordinary path, and listing the
    # first attempt alongside the one that succeeded read as
    # "pytest ran and passed. pytest did not run."
    reached = {e.get("check_label") for e in checks}
    blocked = [
        e
        for e in executions
        if e.get("check_label")
        and not e.get("ran", True)
        and e["check_label"] not in reached
    ]
    if not checks:
        if blocked:
            body = (
                f"unverified — {_names(blocked)} did not run (refused before "
                "execution), so nothing was checked."
            )
        elif not ran:
            body = "unverified — no tools ran, so nothing was checked."
        else:
            total = len(ran)
            plural = "" if total == 1 else "s"
            body = (
                f"unverified — {total} tool call{plural} ran, none of them a "
                "test, lint, or build."
            )
    else:
        passed = [e for e in checks if not e.get("failed")]
        failed = [e for e in checks if e.get("failed")]
        # A check left unrun keeps the claim below "verified", whatever the
        # ones that did run reported.
        unrun = f" {_names(blocked)} did not run." if blocked else ""
        if not failed:
            state = "partially verified" if blocked else "verified"
            body = f"{state} — {_names(passed)} ran and passed.{unrun}"
        elif not passed:
            tail = unrun or " Nothing else was checked."
            body = f"partially verified — {_names(failed)} ran and did not pass.{tail}"
        else:
            body = (
                f"partially verified — {_names(passed)} passed, "
                f"{_names(failed)} did not.{unrun}"
            )
    statement = VERIFICATION_SCOPE_PREFIX + body
    if len(statement) > VERIFICATION_SCOPE_MAX_CHARS:
        statement = statement[: VERIFICATION_SCOPE_MAX_CHARS - 1].rstrip() + "…"
    return statement


def strip_verification_scope(text: str) -> str:
    """Remove a trailing verification-scope line added by the agent loop."""
    if not isinstance(text, str) or VERIFICATION_SCOPE_PREFIX not in text:
        return text
    return _SCOPE_LINE_RE.sub("", text)
