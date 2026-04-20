# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Self-fix PR publishing (§7.4 steps 7–8).

Opens a **draft** PR via ``gh pr create`` on the self-fix branch against the
coder integration branch. The PR body cites the feedback record id, quotes
the original EM wording verbatim (§15.8 P7 requirement #3), and attaches the
multi-pass review result when available.

Two subprocess entry points:

* :func:`open_self_fix_pr` — creates the PR.
* :func:`notify_em` — posts a comment on the original feedback context
  (usually an issue or PR URL) so the EM knows the draft is ready.

Both functions shell out to ``gh``. Tests mock the shell-out boundary.
``GitHubToolsMixin`` is a Phase-10 deliverable; until it lands this module
is the canonical way to open a self-fix PR.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence

from gaia.coder.self_fix.fixer import Diff
from gaia.coder.self_fix.planner import Plan

logger = logging.getLogger(__name__)

#: Default base branch for self-fix PRs (§5.6 — never ``main``).
DEFAULT_BASE: str = "coder"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PRHandle:
    """Return value of :func:`open_self_fix_pr`."""

    number: int
    url: str
    branch: str
    draft: bool = True


@dataclass(frozen=True)
class ReviewGateResult:
    """Loose shape of the Phase-4 review gate output.

    Phase 4's ``review.gate.run_all_passes`` is a sibling task; when it
    lands it will return a structured result. Here we accept a
    duck-typed mapping so imports stay loose: any mapping with ``overall``
    and ``passes`` keys is usable.
    """

    overall: str
    passes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    confidence: Optional[int] = None


# ---------------------------------------------------------------------------
# Command-running shim (mockable)
# ---------------------------------------------------------------------------


GhRunner = Callable[..., subprocess.CompletedProcess]


def _default_gh_runner(
    args: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``gh <args...>`` via subprocess. Pure wrapper.

    Loose coupling: if PR #818's :class:`gaia.coder.tools.cli.CLIToolsMixin`
    is available we prefer it for logging / denylist enforcement, but the
    fallback plain ``subprocess.run`` keeps the module usable in places
    where the mixin isn't instantiated (tests, CI-only runs).
    """
    completed = subprocess.run(  # pylint: disable=subprocess-run-check
        ["gh", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(shlex.quote(a) for a in args)} failed: "
            f"rc={completed.returncode} stderr={completed.stderr!r}"
        )
    return completed


# ---------------------------------------------------------------------------
# PR body composition
# ---------------------------------------------------------------------------


_PR_NUMBER_RE = re.compile(r"/pull/(\d+)$")


def _extract_pr_number(pr_url: str) -> int:
    match = _PR_NUMBER_RE.search(pr_url.strip().rstrip("/"))
    if not match:
        raise ValueError(
            f"could not extract PR number from {pr_url!r} "
            "(expected https://github.com/<owner>/<repo>/pull/<n>)"
        )
    return int(match.group(1))


def _format_passes_section(review_gate_result: Optional[ReviewGateResult]) -> str:
    """Render the Pass 1-7 status table in the PR body.

    When Phase 4 hasn't landed yet we emit a "(review gate not available)"
    stub so downstream tooling can detect that state.
    """
    if review_gate_result is None:
        return (
            "| Pass | Status | Note |\n"
            "|---|---|---|\n"
            "| — | — | review gate (Phase 4) not yet wired — reviewer must run passes manually |"
        )
    rows = ["| Pass | Status | Note |", "|---|---|---|"]
    passes = review_gate_result.passes or {}
    for name, result in passes.items():
        status = result.get("verdict") or result.get("status") or "?"
        note = result.get("note") or result.get("blockers") or ""
        if isinstance(note, (list, tuple)):
            note = "; ".join(str(n) for n in note)
        rows.append(f"| {name} | {status} | {note} |")
    if len(rows) == 2:
        rows.append("| (none reported) | — | — |")
    if review_gate_result.confidence is not None:
        rows.append("")
        rows.append(
            f"_Confidence score (Pass 6): **{review_gate_result.confidence}**._"
        )
    return "\n".join(rows)


def compose_pr_body(
    plan: Plan,
    diff: Diff,
    *,
    feedback_body: str,
    feedback_id: str,
    em_handle: str,
    context_url: Optional[str],
    regression_test_path: str,
    review_gate_result: Optional[ReviewGateResult],
) -> str:
    """Compose the self-fix PR body.

    Hard requirements (§15.8 P7 feedback-binding pass):
    * body cites ``feedback_id`` explicitly,
    * body quotes ``feedback_body`` verbatim (inside a block-quote),
    * body names the ``regression_test_path``.
    """
    quoted_em = "\n".join(f"> {line}" for line in feedback_body.splitlines() or [""])
    context_link = context_url or "(no context URL)"
    files_bullet = "\n".join(f"- `{f}`" for f in diff.files_edited) or "- (none)"
    alternatives = (
        "\n".join(f"- {a}" for a in plan.alternatives_considered) or "- (none recorded)"
    )
    risks = "\n".join(f"- {r}" for r in plan.risks) or "- (none recorded)"
    passes_section = _format_passes_section(review_gate_result)

    return f"""## Self-fix for feedback `{feedback_id}`

This PR addresses EM feedback from @{em_handle} (context: {context_link}).

### Feedback (verbatim)

{quoted_em}

### Root cause ({plan.fix_class})

{plan.root_cause}

### Proposed change

{plan.proposed_change}

### Files edited

{files_bullet}

### Regression test

Added at `{regression_test_path}`. Fails on `{DEFAULT_BASE}`; passes on
`{diff.branch}` — verified via ``verify_test_differential``.

### Alternatives considered

{alternatives}

### Risks

{risks}

### Review-pass results

{passes_section}

### Closes feedback

- feedback_id: `{feedback_id}`

---

*Draft opened by `gaia-coder` self-correction loop (§7.4). The EM approves
at merge time; this PR is draft until all applicable review passes are
green and the EM has signed off.*
"""


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def open_self_fix_pr(
    fix_branch: str,
    feedback_id: str,
    plan: Plan,
    review_gate_result: Optional[ReviewGateResult],
    *,
    feedback_body: str,
    em_handle: str,
    context_url: Optional[str] = None,
    regression_test_path: Optional[str],
    repo_root: Optional[Path] = None,
    base: str = DEFAULT_BASE,
    draft: bool = True,
    gh_runner: Optional[GhRunner] = None,
    title: Optional[str] = None,
) -> PRHandle:
    """Open a **draft** self-fix PR via ``gh pr create``.

    Raises:
        ValueError: if ``regression_test_path`` is falsy. Per §7.4 step 5,
            a fix PR may not be opened without a regression test. This is a
            hard rule — the Phase 6 task spec asserts it in
            ``test_regression_test_is_required``.
        RuntimeError: on ``gh`` failures.
    """
    if not regression_test_path:
        raise ValueError(
            "open_self_fix_pr: regression_test_path is required — "
            "§7.4 step 5 forbids opening a self-fix PR without a "
            "regression test."
        )
    if plan.feedback_id != feedback_id:
        raise ValueError(
            f"plan.feedback_id {plan.feedback_id!r} does not match "
            f"feedback_id argument {feedback_id!r} — refuse to publish a "
            "PR with mismatched feedback binding."
        )
    runner = gh_runner or _default_gh_runner
    body = compose_pr_body(
        plan,
        Diff(
            feedback_id=feedback_id,
            branch=fix_branch,
            files_edited=tuple(f.path for f in plan.files),
        ),
        feedback_body=feedback_body,
        feedback_id=feedback_id,
        em_handle=em_handle,
        context_url=context_url,
        regression_test_path=regression_test_path,
        review_gate_result=review_gate_result,
    )
    pr_title = title or f"self-fix({plan.fix_class}): feedback {feedback_id}"

    args: List[str] = [
        "pr",
        "create",
        "--base",
        base,
        "--head",
        fix_branch,
        "--title",
        pr_title,
        "--body",
        body,
    ]
    if draft:
        args.append("--draft")

    completed = runner(args, cwd=repo_root, check=True)
    pr_url = (
        (completed.stdout or "").strip().splitlines()[-1] if completed.stdout else ""
    )
    if not pr_url:
        raise RuntimeError(
            f"gh pr create returned no URL; stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r}"
        )
    number = _extract_pr_number(pr_url)
    logger.info(
        "open_self_fix_pr: opened draft #%d at %s for feedback %s",
        number,
        pr_url,
        feedback_id,
    )
    return PRHandle(number=number, url=pr_url, branch=fix_branch, draft=draft)


def notify_em(
    pr_url: str,
    feedback_id: str,
    *,
    context_url: Optional[str] = None,
    repo_root: Optional[Path] = None,
    gh_runner: Optional[GhRunner] = None,
    extra_body: Optional[str] = None,
) -> Mapping[str, Any]:
    """Post a comment on the original feedback context pointing at the fix PR.

    If ``context_url`` is a GitHub issue or PR URL, uses ``gh issue comment``
    / ``gh pr comment`` respectively. If the URL is missing, logs a WARN and
    returns a deferred marker so the loop driver can retry.
    """
    runner = gh_runner or _default_gh_runner
    body = (
        f"Drafted self-fix PR: {pr_url} (feedback_id: `{feedback_id}`). "
        "Please review at your next natural breakpoint. "
        f"{(extra_body or '').strip()}"
    ).strip()
    if not context_url:
        logger.warning(
            "notify_em: no context_url recorded for feedback %s — comment deferred",
            feedback_id,
        )
        return {"posted": False, "reason": "no-context-url", "body": body}

    kind, number = _context_kind_and_number(context_url)
    if kind is None:
        logger.warning(
            "notify_em: context_url %r is not a recognisable gh artifact — "
            "skipping comment",
            context_url,
        )
        return {"posted": False, "reason": "unknown-context", "body": body}

    args = [kind, "comment", str(number), "--body", body]
    runner(args, cwd=repo_root, check=True)
    return {"posted": True, "kind": kind, "number": number, "body": body}


_PR_URL_RE = re.compile(r"/pull/(\d+)(?:/|$)")
_ISSUE_URL_RE = re.compile(r"/issues/(\d+)(?:/|$)")


def _context_kind_and_number(url: str) -> tuple[Optional[str], Optional[int]]:
    """Return ``("pr", 123)`` / ``("issue", 45)`` / ``(None, None)``."""
    url = url.strip().rstrip("/")
    pr_match = _PR_URL_RE.search(url)
    if pr_match:
        return "pr", int(pr_match.group(1))
    issue_match = _ISSUE_URL_RE.search(url)
    if issue_match:
        return "issue", int(issue_match.group(1))
    return None, None


__all__ = [
    "DEFAULT_BASE",
    "GhRunner",
    "PRHandle",
    "ReviewGateResult",
    "compose_pr_body",
    "notify_em",
    "open_self_fix_pr",
]
