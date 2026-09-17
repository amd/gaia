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


#: Phrasings that claim a file was produced, as opposed to merely mentioning it.
#: Kept narrow on purpose: "see config.py" or "config.py defines X" must not
#: trip this, or every answer that names a file gets a warning nobody reads.
_WROTE_PATTERNS = (
    r"\b(?:wrote|written|saved|created|generated|produced)\b[^.\n]{0,60}?"
    r"[`'\"]([\w./\-]+\.[A-Za-z0-9]{1,8})[`'\"]",
    r"[`'\"]([\w./\-]+\.[A-Za-z0-9]{1,8})[`'\"][^.\n]{0,40}?"
    r"\b(?:was|is|has been)\s+(?:written|saved|created|generated)\b",
)


def claimed_written_files(answer: str) -> List[str]:
    """Files the answer says it produced, in the order claimed.

    Reads the agent's own words rather than its tool log, because that is where
    the failure lives: an agent can write a script, narrate running it, and stop
    without ever executing it. The tool log looks clean; the claim is false.
    """
    import re

    if not answer:
        return []
    seen, found = set(), []
    for pattern in _WROTE_PATTERNS:
        for match in re.finditer(pattern, answer, re.IGNORECASE):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                found.append(name)
    return found


def unwritten_claims(answer: str, workspace: Optional[str] = None) -> List[str]:
    """Of the files the answer claims to have written, those that do not exist.

    An empty file counts as missing: "generated orders.json" followed by a
    zero-byte file is the same broken promise as no file at all.
    """
    import os

    missing = []
    for name in claimed_written_files(answer):
        path = os.path.join(workspace, name) if workspace else name
        try:
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                missing.append(name)
        except OSError:
            # Unreadable is not the same as absent, and guessing either way
            # would either cry wolf or hide a real miss. Say nothing.
            continue
    return missing


#: A request that names where its output must go. The agent can compute the
#: right answer and simply not write it — observed on a task that asked for a
#: number "in answer.txt", where the agent replied "400" and created nothing.
#: The phantom-write check cannot catch that: there is no false claim to
#: contradict, only a silent omission.
_REQUESTED_OUTPUT = (
    r"\b(?:write|save|put|output|store|record)\b[^.\n]{0,80}?"
    r"\b(?:to|in|into|as)\s+[`'\"]?([\w./\-]+\.[A-Za-z0-9]{1,8})[`'\"]?",
    r"\b(?:create|produce|generate)\b[^.\n]{0,40}?"
    r"[`'\"]([\w./\-]+\.[A-Za-z0-9]{1,8})[`'\"]",
)


def requested_output_files(request: str) -> List[str]:
    """Files the *user's request* says the answer must be written to."""
    import re

    if not request:
        return []
    seen, found = set(), []
    for pattern in _REQUESTED_OUTPUT:
        for match in re.finditer(pattern, request, re.IGNORECASE):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                found.append(name)
    return found


def missing_requested_outputs(
    request: str, workspace: Optional[str] = None
) -> List[str]:
    """Of the files the request asked for, those that were never produced."""
    import os

    missing = []
    for name in requested_output_files(request):
        path = os.path.join(workspace, name) if workspace else name
        try:
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                missing.append(name)
        except OSError:
            continue
    return missing
