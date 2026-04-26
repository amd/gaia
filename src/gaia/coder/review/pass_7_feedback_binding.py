# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pass 7 — feedback-binding (§8 row 7).

Only runs for self-fix PRs, signalled by a ``Feedback-Id:`` trailer or a
``feedback_id`` token in the PR body. Has both a deterministic half
(differential pytest: the regression test must fail on ``coder`` and
pass on the fix branch) and an LLM half (``feedback_binding.md``), per
§15.8 P7.

If the PR body does not declare a feedback record this pass short-
circuits as ``status="skipped"`` and the gate honours that: Pass 7 is
required only for self-fix PRs per §8 row 7.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from gaia.coder.review._diff import DiffBundle, resolve_diff
from gaia.coder.review._llm import (
    LLMClientUnavailable,
    call_opus,
    load_prompt,
    parse_json_response,
    render_prompt,
)
from gaia.coder.review.pass_result import PassResult, make_pass_result
from gaia.logger import get_logger

logger = get_logger(__name__)

PROMPT_NAME: str = "feedback_binding.md"

#: ``Feedback-Id: <uuid>`` trailer or inline ``feedback_id=<uuid>`` token.
_FEEDBACK_ID_RE = re.compile(
    r"(?:Feedback-Id|feedback[_-]?id)\s*[:=]\s*(?P<id>[A-Za-z0-9_-]{4,})",
    re.IGNORECASE,
)

#: Matches a regression-test path declaration in the PR body. Either
#: ``regression_test: tests/foo/test_bar.py`` or the markdown-list form.
_REGRESSION_TEST_RE = re.compile(
    r"(?:^|\s)regression[_-]?test\s*[:=]\s*(?P<path>[\w./\-]+\.py)",
    re.IGNORECASE,
)


def extract_feedback_id(pr_body: str) -> Optional[str]:
    """Return the feedback-record UUID declared in ``pr_body``, if any."""
    if not pr_body:
        return None
    match = _FEEDBACK_ID_RE.search(pr_body)
    if match:
        return match.group("id")
    return None


def _extract_regression_test_path(pr_body: str) -> Optional[str]:
    if not pr_body:
        return None
    match = _REGRESSION_TEST_RE.search(pr_body)
    if match:
        return match.group("path")
    return None


# ---------------------------------------------------------------------------
# Differential pytest run
# ---------------------------------------------------------------------------


def _run_pytest_on_ref(
    test_path: str,
    ref: str,
    *,
    repo_root: Optional[Path],
) -> Tuple[str, str]:
    """Run the regression test on ``ref`` in a scratch worktree.

    Returns ``(outcome, tail)`` where outcome is one of ``"pass"``,
    ``"fail"``, or ``"error"`` (worktree/setup failure). We *do not*
    silently swallow worktree errors — ``"error"`` surfaces as a blocking
    finding so the EM knows the check was not actually performed.
    """
    if shutil.which("git") is None:
        return "error", "git not on PATH"
    if shutil.which("pytest") is None:
        return "error", "pytest not on PATH"

    import tempfile

    with tempfile.TemporaryDirectory(prefix="gaia-coder-pass7-") as scratch:
        add_cmd = ["git", "worktree", "add", "--detach", scratch, ref]
        add_proc = subprocess.run(  # pylint: disable=subprocess-run-check
            add_cmd,
            cwd=str(repo_root) if repo_root else None,
            capture_output=True,
            text=True,
        )
        if add_proc.returncode != 0:
            return (
                "error",
                f"worktree add failed: {add_proc.stderr.strip()!r}",
            )
        try:
            test_proc = subprocess.run(  # pylint: disable=subprocess-run-check
                ["pytest", "-x", "--tb=short", test_path],
                cwd=scratch,
                capture_output=True,
                text=True,
            )
            tail = "\n".join(
                (test_proc.stdout + test_proc.stderr).strip().splitlines()[-15:]
            )
            outcome = "pass" if test_proc.returncode == 0 else "fail"
            return outcome, tail
        finally:
            remove_proc = subprocess.run(  # pylint: disable=subprocess-run-check
                ["git", "worktree", "remove", "--force", scratch],
                cwd=str(repo_root) if repo_root else None,
                capture_output=True,
                text=True,
            )
            if remove_proc.returncode != 0:
                logger.warning(
                    "pass 7: failed to remove worktree %s: %s",
                    scratch,
                    remove_proc.stderr.strip(),
                )


def _differential_pytest(
    test_path: str,
    *,
    fix_ref: str,
    base_ref: str,
    repo_root: Optional[Path],
) -> Tuple[dict, str, str]:
    """Run pytest on ``base_ref`` then on ``fix_ref``.

    Returns ``(finding_or_none, base_tail, fix_tail)``.

    The contract is: the regression test must FAIL on ``base_ref`` and
    PASS on ``fix_ref``. Any other combination is a blocking finding.
    """
    base_outcome, base_tail = _run_pytest_on_ref(
        test_path, base_ref, repo_root=repo_root
    )
    fix_outcome, fix_tail = _run_pytest_on_ref(test_path, fix_ref, repo_root=repo_root)
    if base_outcome == "error" or fix_outcome == "error":
        return (
            {
                "severity": "blocking",
                "description": (
                    "differential pytest could not run: "
                    f"base={base_outcome!r}, fix={fix_outcome!r}. "
                    f"base_tail: {base_tail[:200]} fix_tail: {fix_tail[:200]}"
                ),
                "citation": "§8 Pass 7 — differential pytest",
            },
            base_tail,
            fix_tail,
        )
    if base_outcome == "fail" and fix_outcome == "pass":
        return {}, base_tail, fix_tail
    return (
        {
            "severity": "blocking",
            "description": (
                f"regression test {test_path} should FAIL on {base_ref!r} "
                f"and PASS on {fix_ref!r}; got base={base_outcome}, "
                f"fix={fix_outcome}"
            ),
            "base_outcome": base_outcome,
            "fix_outcome": fix_outcome,
            "citation": "§8 Pass 7 — differential pytest",
        },
        base_tail,
        fix_tail,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_pass(
    pr_or_branch: str,
    *,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
    diff: Optional[DiffBundle] = None,
    feedback_record: Optional[dict] = None,
    skip_differential_pytest: bool = False,
) -> PassResult:
    """Execute Pass 7 and return the :class:`PassResult`.

    Args:
        feedback_record: Optional pre-fetched feedback record (dict with
            at least ``id``, ``body``, ``fix_class``, ``candidate_files``,
            ``success_criterion``). When absent we build a minimal one
            from the PR body — enough to run the LLM half but not the
            root-cause-file check.
        skip_differential_pytest: Short-circuit the pytest half. Used by
            tests to isolate the LLM half without shelling out to git /
            pytest.
    """
    diff_bundle = diff or resolve_diff(
        pr_or_branch, base_ref=base_ref, repo_root=repo_root
    )

    feedback_id = (feedback_record or {}).get("id") or extract_feedback_id(
        diff_bundle.pr_body
    )
    if not feedback_id:
        return make_pass_result(
            status="skipped",
            findings=[
                {
                    "severity": "info",
                    "description": (
                        "no Feedback-Id trailer in PR body; Pass 7 is a "
                        "self-fix-PR-only pass per §8 row 7"
                    ),
                    "citation": "§8 Pass 7",
                }
            ],
            citations=["docs/plans/coder-agent.mdx §8 Pass 7"],
            tooling_used=[],
        )

    findings: List[dict] = []
    tooling_used: List[str] = []

    # --- deterministic half: differential pytest ---
    if not skip_differential_pytest:
        test_path = (feedback_record or {}).get(
            "regression_test_path"
        ) or _extract_regression_test_path(diff_bundle.pr_body)
        if test_path:
            tooling_used.append("pytest (differential: coder vs fix-branch)")
            diff_finding, _base_tail, _fix_tail = _differential_pytest(
                test_path,
                fix_ref=pr_or_branch,
                base_ref=base_ref,
                repo_root=repo_root,
            )
            if diff_finding:
                findings.append(diff_finding)
        else:
            findings.append(
                {
                    "severity": "blocking",
                    "description": (
                        "self-fix PR has no regression_test_path declared; "
                        "§7.4 step 5 requires a regression test"
                    ),
                    "citation": "§8 Pass 7 — regression test required",
                }
            )

    # --- LLM half ---
    fb = feedback_record or {}
    template = load_prompt(PROMPT_NAME)
    rendered = render_prompt(
        template,
        {
            "fb_id": str(feedback_id),
            "verbatim_em_wording": str(fb.get("body", "<unknown>")),
            "class": str(fb.get("fix_class", "<unknown>")),
            "triaged_paths": "\n".join(fb.get("candidate_files", []) or []),
            "from_plan_stage_3": str(fb.get("success_criterion", "<unknown>")),
            "title": diff_bundle.pr_title,
            "body": diff_bundle.pr_body,
            "unified_diff": diff_bundle.unified_diff,
            "test_path": str(
                (feedback_record or {}).get("regression_test_path")
                or _extract_regression_test_path(diff_bundle.pr_body)
                or "<not declared>"
            ),
            "pytest_result_on_coder": "see differential_pytest results",
            "pytest_result_on_fix_branch": "see differential_pytest results",
        },
    )

    try:
        raw = call_opus(rendered)
        tooling_used.append("anthropic SDK → Opus 4.7 (feedback binding)")
    except LLMClientUnavailable as exc:
        findings.append(
            {
                "severity": "blocking",
                "description": ("feedback-binding LLM unavailable: " + str(exc)),
                "citation": "§15.9 fail-loudly",
            }
        )
        return make_pass_result(
            status="fail",
            findings=findings,
            citations=["docs/plans/coder-agent.mdx §8 Pass 7"],
            tooling_used=tooling_used,
        )

    try:
        payload = parse_json_response(raw)
    except ValueError as exc:
        findings.append(
            {
                "severity": "blocking",
                "description": f"Opus response was not valid JSON: {exc}",
                "raw_head": raw[:500],
                "citation": "§15.9 fail-loudly",
            }
        )
        return make_pass_result(
            status="fail",
            findings=findings,
            citations=["docs/plans/coder-agent.mdx §8 Pass 7"],
            tooling_used=tooling_used,
        )

    for blocker in payload.get("blockers", []) or []:
        findings.append(
            {
                "severity": "blocking",
                "description": f"feedback-binding blocker: {blocker}",
                "citation": "§8 Pass 7",
            }
        )
    for check in payload.get("checks", []) or []:
        if str(check.get("verdict", "")).lower() == "fail":
            findings.append(
                {
                    "severity": "significant",
                    "description": (
                        f"feedback-binding check failed: "
                        f"{check.get('name', '?')} — "
                        f"{check.get('evidence', '')}"
                    ),
                    "citation": "§8 Pass 7",
                }
            )

    hard_fail = any(f.get("severity") == "blocking" for f in findings)
    overall = str(payload.get("overall", "request-changes")).lower()

    status = "fail" if hard_fail or overall == "request-changes" else "pass"

    return make_pass_result(
        status=status,
        findings=findings,
        confidence=None,
        citations=[
            "docs/plans/coder-agent.mdx §8 Pass 7",
            "docs/plans/coder-agent.mdx §15.8 P7",
        ],
        tooling_used=tooling_used,
    )


__all__ = ["extract_feedback_id", "run_pass"]
