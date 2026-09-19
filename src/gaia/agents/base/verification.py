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

import json
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

#: Leading blockquote markers, headings, list bullets and emphasis runs, so a
#: model's ``> **Verification:** …`` or ``## Verification: …`` is recognised as
#: the same line.
#:
#: The TUI's no-op-only strip (tui/internal/ui/chat/verification.go,
#: verificationScopeRE) is a hand-kept copy of this pattern, narrowed to the
#: "unverified" case only — update both if this changes.
_SCOPE_MARKUP_RE = re.compile(
    r"^[ \t]*(?:>[ \t]*)*(?:\#{1,6}[ \t]+)?(?:(?:[-*+]|\d{1,3}[.)])[ \t]+)?[*_~`]*[ \t]*"
)

#: An opening or closing code fence — three or more backticks or tildes,
#: indented or not. Lines between a matching pair are never touched.
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

#: A line is a scope statement only when it carries the WHOLE generated shape:
#: the prefix, then one of the three states, then the em dash that introduces
#: the body. Matching the bare prefix deleted a user's own "Verification: run
#: pytest before tagging" out of a checklist the model wrote.
_SCOPE_BODY_RE = re.compile(
    re.escape(VERIFICATION_SCOPE_PREFIX.strip())
    + r"\s*[*_~`]*\s*(?:un|partially )?verified\s*[—–-]"
)


def _is_scope_line(line: str) -> bool:
    """True when *line* is a generated verification statement, Markdown and all."""
    return bool(_SCOPE_BODY_RE.match(_SCOPE_MARKUP_RE.sub("", line)))


def verification_check_label(
    tool_name: str, tool_args: Any, result: Any = None
) -> Optional[str]:
    """Short label when this call is a verification check, else ``None``.

    ``pytest tests/unit -q`` → ``"pytest"``; ``read_file`` → ``None``.
    """
    name = (tool_name or "").strip()
    if name in ("execute_python_file", "run_python") and isinstance(result, dict):
        return_code = result.get("return_code")
        if (
            not check_was_executed(result)
            or not isinstance(return_code, int)
            or isinstance(return_code, bool)
        ):
            return None
        output = "\n".join(
            value
            for key in ("stdout", "stderr")
            if isinstance((value := result.get(key)), str)
        )
        summary = re.search(
            r"(?m)^=*[ \t]*(?:\d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warning|warnings)"
            r"(?:, )?)+ in \d+(?:\.\d+)?s(?: \(.*\))?[ \t]*=*[ \t]*$",
            output,
        )
        if summary and re.search(
            r"\b[1-9]\d* (?:passed|failed|error|errors|xfailed|xpassed)\b",
            summary.group(0),
        ):
            return "pytest"
        if re.search(
            r"(?m)^Ran [1-9]\d* tests? in \d+(?:\.\d+)?s\s*\n\s*"
            r"(?:OK(?: \(.*\))?|FAILED \(.*\))[ \t]*$",
            output,
        ):
            return "unittest"
        return None
    if name in _CHECK_TOOLS:
        return name
    if not isinstance(tool_args, dict):
        return None
    for key in _COMMAND_KEYS:
        command = tool_args.get(key)
        if isinstance(command, str) and command.strip():
            match = _CHECK_COMMAND_RE.search(command)
            if not match:
                return None
            label = " ".join(match.group(0).split()).lower()
            return {
                "python -m pytest": "pytest",
                "py.test": "pytest",
                "python -m unittest": "unittest",
            }.get(label, label)
    return None


def verification_check_target(tool_name: str, tool_args: Any) -> str:
    """What a check ran against, so only reruns of the same command group.

    ``pytest tests/`` and ``pytest tests/test_cart.py`` share the label
    ``"pytest"`` but are different checks: the narrow one passing says nothing
    about the suite that failed.
    """
    if isinstance(tool_args, dict):
        for key in _COMMAND_KEYS:
            command = tool_args.get(key)
            if isinstance(command, str) and command.strip():
                return " ".join(command.split())
        return f"{tool_name} {json.dumps(tool_args, sort_keys=True, default=str)}"
    return f"{tool_name} {tool_args!r}"


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


def _mixed(passed: List[Dict[str, Any]], failed: List[Dict[str, Any]]) -> str:
    """What passed and what did not, naming no check on both sides.

    Two runs of one runner on different commands share a label, so listing them
    by label read "pytest passed, pytest did not" — a line that contradicts
    itself. A label on both sides is counted instead.
    """
    split = {e["check_label"] for e in passed} & {e["check_label"] for e in failed}
    parts = []
    only_passed = [e for e in passed if e["check_label"] not in split]
    if only_passed:
        parts.append(f"{_names(only_passed)} passed")
    for label in dict.fromkeys(e["check_label"] for e in (*passed, *failed)):
        if label in split:
            bad = sum(1 for e in failed if e["check_label"] == label)
            total = bad + sum(1 for e in passed if e["check_label"] == label)
            parts.append(f"{bad} of {total} {label} runs did not pass")
    only_failed = [e for e in failed if e["check_label"] not in split]
    if only_failed:
        parts.append(f"{_names(only_failed)} did not")
    return ", ".join(parts)


def build_verification_scope(executions: List[Dict[str, Any]]) -> str:
    """One bounded line naming what ran, what passed, and what went unchecked.

    Three distinguishable states: ``verified`` (checks ran and every one
    passed), ``partially verified`` (checks ran, not all passed), and
    ``unverified`` (no check ran at all).

    A check that ran more than once counts once, by its most recent run: a
    test that failed and then passed after a fix is verified, and one that
    passed and then failed after an edit is not (#3989). A check is one
    runner on one command, so a narrower rerun that passes does not hide a
    wider run that failed.

    A check the agent *requested* and never got to run — refused by the shell
    allowlist, declined by the user — is none of those three. It is named as
    not having run, and never counted as one that did (#3677).

    Each execution is ``{"tool": str, "check_label": str | None,
    "check_target": str | None, "failed": bool, "ran": bool}`` — see
    ``Agent._note_verification_signal``. ``ran`` defaults to True for a record
    written before the field existed; a record without ``check_target`` groups
    by its label alone.
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
        # A check that ran more than once is judged by its latest run, so a
        # fix that made it pass and an edit that made it fail stay distinct.
        latest = {(e["check_label"], e.get("check_target")): e for e in checks}
        passed = [e for e in latest.values() if not e.get("failed")]
        failed = [e for e in latest.values() if e.get("failed")]
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
            body = f"partially verified — {_mixed(passed, failed)}.{unrun}"
    statement = VERIFICATION_SCOPE_PREFIX + body
    if len(statement) > VERIFICATION_SCOPE_MAX_CHARS:
        statement = statement[: VERIFICATION_SCOPE_MAX_CHARS - 1].rstrip() + "…"
    return statement


def split_verification_scope(text: str) -> Tuple[str, str]:
    """Split *text* into ``(body, scope_line)``, removing EVERY scope line.

    The line rides in the answer, and the answer is re-sent as conversation
    history — so a model that reads it can write one of its own, anywhere in
    its reply, in whatever Markdown it likes. Taking only a trailing one left
    the echo in place and the appended line beside it, and the user saw the
    same verification paragraph twice (#3675).

    ``scope_line`` is the LAST one found, stripped of its markup, or ``""``.

    Only the statement lines go, plus the blank line each one was separated by.
    Everything else is left byte-for-byte: this runs on every Agent-UI answer,
    and an earlier version that normalised blank runs silently reflowed the
    inside of every fenced code block it passed through.

    Code blocks are skipped entirely. An answer that *quotes* a footer — a
    transcript, an explanation of the feature, this repo's own source — has to
    come back with the quote intact, or the deletion lands in the middle of a
    fence and leaves an empty pair of backticks.

    A statement sharing a line with prose is left alone on purpose: the models
    emit it on its own line, and matching mid-line risks eating real prose.
    """
    if not isinstance(text, str) or VERIFICATION_SCOPE_PREFIX.strip() not in text:
        return (text if isinstance(text, str) else "", "")
    kept: List[str] = []
    found = ""
    fence = ""
    removed = False
    # split("\n"), not splitlines(): the email agent compares a stripped answer
    # against the original for identity, and splitlines() also breaks on \x0b,
    # \x0c and U+2028 and would rewrite CRLF as LF.
    for line in text.split("\n"):
        marker = _FENCE_RE.match(line)
        if marker:
            token = marker.group(1)[:3]
            if not fence:
                fence = token
            elif token == fence:
                fence = ""
            kept.append(line)
            continue
        if not fence and _is_scope_line(line):
            bare = _SCOPE_MARKUP_RE.sub("", line).strip()
            body = bare[len(VERIFICATION_SCOPE_PREFIX.strip()) :].strip(" *_~`")
            found = VERIFICATION_SCOPE_PREFIX + body if body else ""
            # The blank line that set this statement apart goes with it.
            if kept and not kept[-1].strip():
                kept.pop()
            removed = True
            continue
        kept.append(line)
    if not removed:
        return text, found
    return "\n".join(kept).rstrip(), found


def strip_verification_scope(text: str) -> str:
    """Remove every verification-scope line, wherever it sits in *text*."""
    return split_verification_scope(text)[0]
