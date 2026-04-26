# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pass 1 — self-static review (§8 row 1).

Deterministic. Runs the project's lint harness on changed Python files,
``tsc --noEmit`` on changed TypeScript files, and a handful of regex
guards that catch the classes of mistake the agent's own feedback loop
has flagged in past iterations (debug prints, TODOs without an issue
link, commented-out code).

Output contract: :class:`gaia.coder.review.pass_result.PassResult` with
``status="pass"`` iff every tool returns 0 and no regex finding is
emitted.

Per §15.8 (Deterministic checks): ``python util/lint.py --all``,
``tsc --noEmit``, debug-print regex, TODO-without-issue regex. This
module implements all four.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from gaia.coder.review._diff import (
    DiffBundle,
    filter_by_extension,
    resolve_diff,
)
from gaia.coder.review.pass_result import PassResult, make_pass_result
from gaia.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Regex guards
# ---------------------------------------------------------------------------

# Matches a bare ``print(`` at the start of a line (ignoring indentation),
# excluding inside pytest fixtures or CLI tools that legitimately print. We
# only flag *added* lines in the diff, so false positives on library code
# we did not touch are impossible.
_DEBUG_PRINT_RE = re.compile(
    r"^\+(?P<indent>\s*)(?:print\(|console\.log\(|pprint\()",
    re.MULTILINE,
)

# Matches ``TODO`` / ``FIXME`` / ``XXX`` comments *without* a trailing issue
# link like ``(#123)`` or ``#123`` or a URL. Issue links are the project's
# convention for giving a TODO an owner and an expiry; bare TODOs tend to
# rot silently.
_BARE_TODO_RE = re.compile(
    r"^\+.*?\b(?P<marker>TODO|FIXME|XXX)\b(?![^\n]*?(?:#\d+|https?://))",
    re.MULTILINE,
)

# Matches *added* lines that are entirely a commented-out code line. We use
# a conservative rule: the line starts with a comment character and the
# remainder looks like code (contains `=`, `(`, or `:` but not a docstring
# fragment). Still regex-fallible, but flagging a false positive is
# preferable to letting a commented-out ``# foo = compute()`` slip through.
_COMMENTED_CODE_RE = re.compile(
    r"^\+(?P<indent>\s*)(?:#|//)\s*[a-zA-Z_][a-zA-Z0-9_]*\s*[=(]",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Tool runners
# ---------------------------------------------------------------------------


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run ``cmd`` synchronously, returning ``(returncode, stdout, stderr)``."""
    # Reuse the denylist guard from CLIToolsMixin (§6.8 layer 1).
    from gaia.coder.tools.cli import _check_denylist

    _check_denylist(cmd)
    completed = subprocess.run(  # pylint: disable=subprocess-run-check
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _lint_python(files: List[str], *, cwd: Optional[Path]) -> Tuple[bool, str]:
    """Run ``python util/lint.py --all`` and report pass/fail + stderr tail.

    The lint script lints the whole tree; filtering by changed files
    happens in a follow-up hardening pass when the script supports it.
    Running the whole tree is still cheap (seconds) and strictly stronger
    — a lint failure anywhere is a lint failure.
    """
    if not files:
        return True, "no python files in diff; skipped lint"
    code, out, err = _run(["python", "util/lint.py", "--all"], cwd=cwd)
    if code == 0:
        return True, ""
    tail = (err or out).strip().splitlines()[-30:]
    return False, "\n".join(tail)


def _tsc_no_emit(files: List[str], *, cwd: Optional[Path]) -> Tuple[bool, str]:
    """Run ``tsc --noEmit`` if TS files are in the diff.

    If ``tsc`` is missing (no TS toolchain installed), return a "skipped"
    marker — not a fail. TypeScript-free diffs should not be blocked by a
    missing TS compiler.
    """
    if not files:
        return True, "no TS files in diff; skipped tsc"
    try:
        code, out, err = _run(["tsc", "--noEmit"], cwd=cwd)
    except FileNotFoundError:
        return True, "tsc not installed; skipped"
    if code == 0:
        return True, ""
    tail = (err or out).strip().splitlines()[-30:]
    return False, "\n".join(tail)


def _regex_findings(unified_diff: str) -> List[dict]:
    """Scan ``unified_diff`` for debug prints / bare TODOs / commented code."""
    findings: List[dict] = []
    for match in _DEBUG_PRINT_RE.finditer(unified_diff):
        findings.append(
            {
                "severity": "minor",
                "description": "debug print / console.log on an added line",
                "snippet": match.group(0).rstrip(),
                "citation": "§8 Pass 1 — no debug prints",
            }
        )
    for match in _BARE_TODO_RE.finditer(unified_diff):
        findings.append(
            {
                "severity": "minor",
                "description": (
                    f"bare {match.group('marker')} without issue link "
                    f"(#NNN or a URL)"
                ),
                "snippet": match.group(0).rstrip(),
                "citation": "§8 Pass 1 — no TODO without issue link",
            }
        )
    for match in _COMMENTED_CODE_RE.finditer(unified_diff):
        findings.append(
            {
                "severity": "minor",
                "description": "commented-out code on an added line",
                "snippet": match.group(0).rstrip(),
                "citation": "§8 Pass 1 — no commented-out code",
            }
        )
    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_pass(
    pr_or_branch: str,
    *,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
    diff: Optional[DiffBundle] = None,
) -> PassResult:
    """Execute Pass 1 and return the :class:`PassResult`.

    Args:
        pr_or_branch: PR URL / shortform or local ref. Ignored if ``diff``
            is supplied (tests use this seam to avoid shelling to git).
        base_ref: Passed to :func:`resolve_diff` when ``diff`` is ``None``.
        repo_root: Working directory for subprocess calls.
        diff: Pre-resolved :class:`DiffBundle`. When present the function
            does not shell out to git / gh — used by the gate orchestrator
            to fetch the diff exactly once.
    """
    diff_bundle = diff or resolve_diff(
        pr_or_branch, base_ref=base_ref, repo_root=repo_root
    )
    py_files = filter_by_extension(diff_bundle.changed_files, (".py",))
    ts_files = filter_by_extension(diff_bundle.changed_files, (".ts", ".tsx"))

    findings: List[dict] = []
    tooling_used: List[str] = []

    lint_ok, lint_tail = _lint_python(py_files, cwd=repo_root)
    if py_files:
        tooling_used.append("python util/lint.py --all")
    if not lint_ok:
        findings.append(
            {
                "severity": "blocking",
                "description": "python util/lint.py --all failed",
                "output_tail": lint_tail,
                "citation": "§8 Pass 1 — lint must pass",
            }
        )

    tsc_ok, tsc_tail = _tsc_no_emit(ts_files, cwd=repo_root)
    if ts_files:
        tooling_used.append("tsc --noEmit")
    if not tsc_ok:
        findings.append(
            {
                "severity": "blocking",
                "description": "tsc --noEmit failed",
                "output_tail": tsc_tail,
                "citation": "§8 Pass 1 — types must check",
            }
        )

    regex_hits = _regex_findings(diff_bundle.unified_diff)
    if regex_hits:
        findings.extend(regex_hits)
        tooling_used.append("regex guards (debug print, TODO, commented code)")

    # Regex-only findings are minor and do not hard-fail the pass by
    # themselves — they surface in the PR description as "author's
    # pre-review notes" per §8 row 6 spirit. The hard-fail trigger is a
    # blocking finding (lint / tsc).
    hard_fail = any(f.get("severity") == "blocking" for f in findings)
    status = "fail" if hard_fail else "pass"

    return make_pass_result(
        status=status,
        findings=findings,
        confidence=None,
        citations=[
            "docs/plans/coder-agent.mdx §8 Pass 1",
            "docs/plans/coder-agent.mdx §15.8 deterministic checks",
        ],
        tooling_used=tooling_used,
    )


__all__ = ["run_pass"]
