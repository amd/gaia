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

What a check reported comes from the tool that ran it — a
:class:`~gaia.agents.base.checks.CheckResult` on its result. Reading the tool's
text output is the fallback, kept for results that carry none (hub agents,
MCP tools, anything written before the field existed).

The same log answers a second question: does what the answer claims about
tests (:mod:`gaia.agents.base.claims`) match what ran?
:func:`unsupported_test_claim` compares the two so the loop can ask for one
correction.

Pure and dependency-free on purpose: the agent loop, the Agent-UI SSE handler,
and hub agents all consume it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from gaia.agents.base.checks import (
    CheckResult,
    check_kind,
    command_check_label,
    declares_check,
    runner_summary,
)
from gaia.agents.base.claims import passing_test_claim

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


#: ``subtests?`` sits between the count and the outcome when pytest-subtests is
#: installed ("70 passed, 19 subtests passed in 1.32s"). Without it the whole
#: summary failed to match and a run that really did pass was called unverified.
_PYTEST_SUMMARY_RE = re.compile(
    r"(?m)^=*[ \t]*(?:\d+ (?:subtests? )?"
    r"(?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warning|warnings)"
    r"(?:, )?)+ in \d+(?:\.\d+)?s(?: \(.*\))?[ \t]*=*[ \t]*$"
)
_UNITTEST_SUMMARY_RE = re.compile(
    r"(?m)^Ran [1-9]\d* tests? in \d+(?:\.\d+)?s\s*\n\s*"
    r"(OK(?: \(.*\))?|FAILED \(.*\))[ \t]*$"
)


def _python_run_output(result: Dict[str, Any]) -> str:
    return "\n".join(
        value
        for key in ("stdout", "stderr")
        if isinstance((value := result.get(key)), str)
    )


def summary_reports_failure(tool_name: str, result: Any) -> bool:
    """True when a Python run's own test summary says something failed.

    A snippet that runs pytest and prints the result exits 0 whatever pytest
    reported, so its exit code cannot decide pass or fail. The last summary
    printed wins: a snippet may run the suite more than once.
    """
    if (tool_name or "").strip() not in ("execute_python_file", "run_python"):
        return False
    if not isinstance(result, dict):
        return False
    output = _python_run_output(result)
    summaries = list(_PYTEST_SUMMARY_RE.finditer(output))
    if summaries and re.search(
        r"\b[1-9]\d* (?:subtests? )?(?:failed|errors?)\b", summaries[-1].group(0)
    ):
        return True
    unittest = list(_UNITTEST_SUMMARY_RE.finditer(output))
    return bool(unittest) and unittest[-1].group(1).startswith("FAILED")


def verification_check_label(
    tool_name: str, tool_args: Any, result: Any = None
) -> Optional[str]:
    """Short label when this call is a verification check, else ``None``.

    ``pytest tests/unit -q`` → ``"pytest"``; ``read_file`` → ``None``. What
    the tool declared wins; everything below it is the fallback for results
    that declare nothing.
    """
    if declares_check(result):
        reported = CheckResult.from_result(result)
        return reported.label if reported else None
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
        found = runner_summary(output)
        return found[0] if found else None
    if name in _CHECK_TOOLS:
        return name
    if not isinstance(tool_args, dict):
        return None
    for key in _COMMAND_KEYS:
        command = tool_args.get(key)
        if isinstance(command, str) and command.strip():
            return command_check_label(command)
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


# ── answer-seam check: verify after the last change ──────────────────────

#: Prefix on the corrective message, so a transcript shows the check fired.
VERIFY_AFTER_CHANGE_TAG = "[check:verify-after-change]"

#: File-changing tools matched by exact name.
_MUTATING_TOOLS: FrozenSet[str] = frozenset(
    {
        "append_to_file",
        "apply_patch",
        "edit_file",
        "edit_python_file",
        "insert_lines",
        "multi_edit",
        "patch_file",
        "replace_function",
        "replace_in_file",
        "search_replace",
        "str_replace",
        "write_file",
        "write_python_file",
    }
)

#: Name prefixes of file-changing tools. These also match tools that change no
#: file (``create_event``, ``update_memory``), so a prefix-only match must name
#: a path in its arguments to count as a change.
_MUTATING_PREFIXES: Tuple[str, ...] = (
    "write_",
    "edit_",
    "create_",
    "delete_",
    "update_",
)

_PATH_ARG_KEYS: Tuple[str, ...] = (
    "file_path",
    "path",
    "filepath",
    "filename",
    "file",
    "target_path",
    "output_path",
    "dest",
    "destination",
)

#: Writing a report, a data file, or an image cannot break a test suite.
_NON_CODE_SUFFIXES: FrozenSet[str] = frozenset(
    {
        ".csv",
        ".docx",
        ".gif",
        ".jpeg",
        ".jpg",
        ".log",
        ".markdown",
        ".md",
        ".pdf",
        ".png",
        ".rst",
        ".svg",
        ".tsv",
        ".txt",
        ".xlsx",
    }
)

#: Tools whose *output* can show a test run, whatever the command looked like.
_TEST_OUTPUT_TOOLS: FrozenSet[str] = frozenset(
    {"execute_python_file", "run_python", "run_shell_command"}
)

_PYTEST_OUTCOME = (
    r"\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed"
    r"|warnings?|rerun)"
)
# A line made only of pytest outcome counts, optionally timed and ruled —
# ``3 passed``, ``1 failed, 2 passed in 0.12s``, ``=== 2 passed in 1s ===``.
# Machine-produced, so a script printing "3 failed attempts" does not match.
_TEST_SUMMARY_RE = re.compile(
    r"(?im)^[ \t=]*(?:"
    rf"(?:{_PYTEST_OUTCOME}(?:,[ \t]*|[ \t]+))*{_PYTEST_OUTCOME}"
    r"|no tests ran)"
    r"(?:[ \t]+in[ \t]+\d+(?:\.\d+)?m?s(?:[ \t]*\([^)\n]*\))?)?[ \t=]*$"
    r"|^Ran \d+ tests? in \d+(?:\.\d+)?s[ \t]*$"
)

#: Directory names never searched for test files.
_SKIP_DIRS: FrozenSet[str] = frozenset(
    {"node_modules", "site-packages", "__pycache__", "venv", "env", "build", "dist"}
)
_TEST_FILE_SCAN_LIMIT = 5000


def observed_text(value: Any) -> str:
    """Every string, number, and key inside *value*, one per line.

    Flattened rather than ``json.dumps``-ed: JSON escapes newlines and quotes,
    so a value read from a file would no longer match itself.
    """
    parts: List[str] = []

    def walk(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for key, sub in item.items():
                parts.append(str(key))
                walk(sub)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for sub in item:
                walk(sub)
        else:
            parts.append(str(item))

    walk(value)
    return "\n".join(parts)


#: A runner's summary line comes last; keep only the tail of long outputs.
_CHECK_OUTPUT_TAIL = 20_000


def check_output(tool_name: str, result: Any) -> str:
    """The part of *result* a test-summary check reads; empty for other tools."""
    if tool_name not in _TEST_OUTPUT_TOOLS:
        return ""
    return observed_text(result)[-_CHECK_OUTPUT_TAIL:]


def has_test_run_summary(output: str) -> bool:
    """True when *output* carries a pytest or unittest summary line."""
    return bool(output) and bool(_TEST_SUMMARY_RE.search(output))


def is_mutating_tool(tool_name: str) -> bool:
    """True when *tool_name* is a file write/edit tool, judged by name."""
    name = (tool_name or "").strip()
    return name in _MUTATING_TOOLS or name.startswith(_MUTATING_PREFIXES)


def _changed_paths(execution: Dict[str, Any]) -> Optional[List[str]]:
    """Paths an execution changed; ``None`` when it changed no project file."""
    name = (execution.get("tool") or "").strip()
    if not is_mutating_tool(name):
        return None
    if not execution.get("ran", True) or execution.get("failed"):
        return None
    args = execution.get("args")
    args = args if isinstance(args, dict) else {}
    paths = [
        value
        for key in _PATH_ARG_KEYS
        if isinstance((value := args.get(key)), str) and value.strip()
    ]
    if not paths:
        return [] if name in _MUTATING_TOOLS else None
    return paths


def _is_project_change(execution: Dict[str, Any], project_root: str) -> bool:
    import os

    paths = _changed_paths(execution)
    if paths is None:
        return False
    if not paths:
        return True
    root = os.path.realpath(project_root)
    for path in paths:
        if os.path.splitext(path)[1].lower() in _NON_CODE_SUFFIXES:
            continue
        if os.path.isabs(path):
            real = os.path.realpath(path)
            if real != root and not real.startswith(root.rstrip(os.sep) + os.sep):
                continue
        return True
    return False


def is_check_execution(execution: Dict[str, Any]) -> bool:
    """True when *execution* ran a test / lint / build check.

    A labelled check counts, and so does a Python or shell run whose output
    holds a test-runner summary — the model can start pytest many ways, but
    the summary line is written by the runner, not the model.
    """
    if not execution.get("ran", True):
        return False
    if execution.get("check_label"):
        return True
    return execution.get("tool") in _TEST_OUTPUT_TOOLS and has_test_run_summary(
        execution.get("output") or ""
    )


def unverified_change(
    executions: List[Dict[str, Any]], project_root: Optional[str]
) -> Optional[str]:
    """Name of the last project change no check ran after, else ``None``.

    ``None`` too when there is no project root: with no project there is no
    suite to run.
    """
    if not project_root:
        return None
    executions = list(executions or [])
    last = None
    for index, execution in enumerate(executions):
        if _is_project_change(execution, project_root):
            last = index
    if last is None:
        return None
    if any(is_check_execution(e) for e in executions[last + 1 :]):
        return None
    changed = executions[last]
    paths = _changed_paths(changed) or []
    return paths[0] if paths else changed.get("tool") or "a file"


def project_has_tests(project_root: Optional[str]) -> bool:
    """True when *project_root* has a test suite pytest could run.

    Any of: a ``tests/`` or ``test/`` directory, a pytest config
    (``pytest.ini``, ``[tool.pytest…]`` in ``pyproject.toml``, ``[tool:pytest]``
    in ``setup.cfg``, ``[pytest]`` in ``tox.ini``), or a ``test_*.py`` /
    ``*_test.py`` file within the first few thousand entries of the tree.
    """
    import os

    from gaia.logger import get_logger

    if not project_root or not os.path.isdir(project_root):
        return False
    if any(os.path.isdir(os.path.join(project_root, d)) for d in ("tests", "test")):
        return True
    if os.path.isfile(os.path.join(project_root, "pytest.ini")):
        return True
    for name, marker in (
        ("pyproject.toml", "[tool.pytest"),
        ("setup.cfg", "[tool:pytest]"),
        ("tox.ini", "[pytest]"),
    ):
        config = os.path.join(project_root, name)
        if not os.path.isfile(config):
            continue
        try:
            with open(config, encoding="utf-8", errors="replace") as fh:
                if marker in fh.read():
                    return True
        except OSError as e:
            get_logger(__name__).debug("could not read %s: %s", config, e)
    seen = 0
    for _dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS
        ]
        for filename in filenames:
            if filename.endswith(".py") and (
                filename.startswith("test_") or filename.endswith("_test.py")
            ):
                return True
        seen += len(filenames) + len(dirnames)
        if seen > _TEST_FILE_SCAN_LIMIT:
            return False
    return False


def verify_after_change_correction(changed: str) -> str:
    """The corrective message for an unverified change to *changed*."""
    return (
        f"{VERIFY_AFTER_CHANGE_TAG} You changed files ({changed}) after the last "
        "test run, so the change is untested. Run the project's tests now "
        "(pytest, or run_python) and report what they printed. If they can't be "
        "run, say plainly that the change is unverified."
    )


# ---------------------------------------------------------------------------
# What the answer claims, and whether the record backs it
# ---------------------------------------------------------------------------

#: Tools that rewrite a file, so a check older than one of these is stale.
#: Names only — a shell command that edits in place (``sed -i``, ``git apply``)
#: is not counted, because a miss here reads "claim supported" and the cost of
#: a false correction on an honest answer is higher than a missed stale one.
_FILE_MUTATION_TOOLS: FrozenSet[str] = frozenset(
    {
        "edit_file",
        "edit_python_file",
        "replace_function",
        "update_gaia_md",
        "write_file",
        "write_markdown_file",
        "write_python_file",
    }
)


#: Why the record does not back a pass claim, phrased for the correction.
NO_TEST_RUN = "no test run is recorded for this turn"


TEST_RUN_FAILED = "the test run recorded for this turn did not pass"


TEST_RUN_STALE = (
    "the test run recorded for this turn finished before the last file change"
)


def is_file_mutation(tool_name: str) -> bool:
    """True when this tool call rewrote a file."""
    return (tool_name or "").strip() in _FILE_MUTATION_TOOLS


def verification_record(
    tool_name: str, tool_args: Any, result: Any, *, errored: bool
) -> Dict[str, Any]:
    """One tool call as :func:`build_verification_scope` reads it.

    A result whose tool declared its check is taken at its word — label,
    target, kind and outcome, or "not a check" — straight from the tool that
    ran it. Only a result that declares nothing is classified from its
    arguments and output, and judged by *errored* (the caller's own "did this
    call fail" predicate).
    """
    declared = declares_check(result)
    reported = CheckResult.from_result(result)
    if reported is not None:
        label: Optional[str] = reported.label
        target: Optional[str] = reported.target
        kind: Optional[str] = reported.kind
        failed = not reported.passed
    elif declared:
        label = target = kind = None
        failed = errored
    else:
        label = verification_check_label(tool_name, tool_args, result)
        target = verification_check_target(tool_name, tool_args) if label else None
        kind = check_kind(label) if label else None
        failed = errored
    return {
        "tool": tool_name,
        "check_label": label,
        "check_target": target,
        "check_kind": kind,
        "failed": failed,
        "ran": check_was_executed(result),
        "mutated": is_file_mutation(tool_name),
        "declared": declared,
    }


def unsupported_test_claim(
    answer: str, executions: List[Dict[str, Any]]
) -> Optional[Tuple[str, str]]:
    """``(claim, why)`` when *answer* reports a test result the record denies.

    ``None`` when the answer makes no test claim, or when a test check ran,
    passed, and — if this turn rewrote a file — ran after the last rewrite. A
    check that passed and was then invalidated by an edit is the same false
    claim as one that never ran.

    Records come from :func:`verification_record`, in execution order. Only a
    ``kind == "test"`` check is evidence — a clean lint says nothing about a
    pass count. A repeated check counts by its latest run, matching the footer
    (#3989).
    """
    claim = passing_test_claim(answer)
    if not claim:
        return None
    last_mutation = -1
    latest: Dict[Tuple[str, Any], Tuple[int, Dict[str, Any]]] = {}
    for index, execution in enumerate(executions or []):
        if not execution.get("ran", True):
            continue
        if execution.get("mutated") and not execution.get("failed"):
            last_mutation = index
        label = execution.get("check_label")
        if not label:
            continue
        if (execution.get("check_kind") or check_kind(label)) == "test":
            latest[(label, execution.get("check_target"))] = (index, execution)
    if not latest:
        return (claim, NO_TEST_RUN)
    passed = [(index, e) for index, e in latest.values() if not e.get("failed")]
    if not passed:
        return (claim, TEST_RUN_FAILED)
    if all(index < last_mutation for index, _ in passed):
        return (claim, TEST_RUN_STALE)
    return None
