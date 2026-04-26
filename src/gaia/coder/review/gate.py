# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The one-shot review gate for ``gh_pr_create`` (§8).

The gate runs the seven passes in order, short-circuiting on a hard-fail
of the deterministic passes (1, 2, 4) so the LLM-driven passes are never
paid for on a PR that already fails lint. Pass 7 only runs if the caller
declares the PR is a self-fix — otherwise it is marked ``skipped`` per
§8 row 7.

The single entry point :func:`run_all_passes` is exposed via
:meth:`gaia.coder.review.mixin.ReviewToolsMixin.review_diff_gate` as the
``review_diff_gate`` tool the coder agent invokes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, TypedDict

# Direct submodule imports avoid the circular-import hazard with
# ``gaia.coder.review.__init__`` (which itself imports from this module).
import gaia.coder.review.pass_1_static as pass_1_static
import gaia.coder.review.pass_2_functional as pass_2_functional
import gaia.coder.review.pass_3_architectural as pass_3_architectural
import gaia.coder.review.pass_4_security as pass_4_security
import gaia.coder.review.pass_5_prose as pass_5_prose
import gaia.coder.review.pass_6_adversarial as pass_6_adversarial
import gaia.coder.review.pass_7_feedback_binding as pass_7_feedback_binding
from gaia.coder.review._diff import resolve_diff
from gaia.coder.review.pass_result import PassResult, make_pass_result
from gaia.logger import get_logger

logger = get_logger(__name__)


#: Passes that short-circuit the gate on hard-fail — the deterministic,
#: cheap checks. A blocking finding here means we do not pay for LLM
#: passes (§8 uses "all applicable passes" but cost-sensibility in §6.6
#: argues for short-circuit on the cheap gates).
HARD_FAIL_PASSES: tuple = (1, 2, 4)


@dataclass
class GateResult:
    """Aggregated verdict from :func:`run_all_passes`.

    Attributes:
        overall: ``"pass"`` / ``"request-changes"`` / ``"block"``. ``block``
            is reserved for Pass 6 confidence < 60 (§7.6) or any
            hard-fail in Passes 1/2/4 — the PR must be rewritten, not
            just revised.
        pass_results: ordered list of :class:`PassResult` dicts. The list
            index is ``pass_number - 1``.
        confidence: The Pass-6 confidence score. ``None`` when Pass 6 was
            skipped (e.g. short-circuited).
        blockers: Flat list of blocking-severity descriptions. A
            convenience for rendering the PR body without re-walking
            every pass.
    """

    overall: str
    pass_results: List[PassResult] = field(default_factory=list)
    confidence: Optional[int] = None
    blockers: List[str] = field(default_factory=list)

    def as_dict(self) -> "GateResultDict":
        """Return a JSON-serialisable representation."""
        return {
            "overall": self.overall,
            "pass_results": list(self.pass_results),
            "confidence": self.confidence,
            "blockers": list(self.blockers),
        }


class GateResultDict(TypedDict):
    """TypedDict mirror of :class:`GateResult` for the ``@tool`` seam."""

    overall: str
    pass_results: List[PassResult]
    confidence: Optional[int]
    blockers: List[str]


def _skipped_result(reason: str) -> PassResult:
    """Build a ``skipped`` :class:`PassResult` with ``reason`` inline."""
    return make_pass_result(
        status="skipped",
        findings=[
            {
                "severity": "info",
                "description": reason,
                "citation": "§8 short-circuit",
            }
        ],
        citations=["docs/plans/coder-agent.mdx §8"],
        tooling_used=[],
    )


def run_all_passes(
    pr_or_branch: str,
    *,
    is_self_fix: bool = False,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
    success_criterion: Optional[str] = None,
    feedback_record: Optional[dict] = None,
) -> GateResult:
    """Run all applicable passes and return the :class:`GateResult`.

    Args:
        pr_or_branch: PR URL / shortform or local branch name.
        is_self_fix: When ``True``, Pass 7 is run. When ``False`` (the
            default), Pass 7 is ``skipped`` even if a ``Feedback-Id``
            trailer is present — the caller is the source of truth for
            "this PR claims to be a self-fix" per §8 row 7.
        base_ref: The base ref for diff resolution. Defaults to ``coder``
            because ``gaia-coder``'s PRs always target ``coder`` per
            §5.1 and §7.4.
        repo_root: Working directory for subprocess calls.
        success_criterion: The Stage-3 plan success criterion (§5.1).
            Piped into Pass 6's confidence-rubric prompt.
        feedback_record: Optional dict of the triaged feedback row.
            Piped into Pass 7 only.
    """
    # Resolve the diff exactly once; share across passes.
    diff_bundle = resolve_diff(pr_or_branch, base_ref=base_ref, repo_root=repo_root)

    results: List[PassResult] = []
    blockers: List[str] = []

    def _collect_blockers(result: PassResult) -> None:
        for finding in result.get("findings", []):
            if finding.get("severity") == "blocking":
                blockers.append(str(finding.get("description", "")))

    # Pass 1
    r1 = pass_1_static.run_pass(
        pr_or_branch,
        base_ref=base_ref,
        repo_root=repo_root,
        diff=diff_bundle,
    )
    results.append(r1)
    _collect_blockers(r1)
    short_circuit = r1["status"] == "fail"

    # Pass 2
    if not short_circuit:
        r2 = pass_2_functional.run_pass(
            pr_or_branch,
            base_ref=base_ref,
            repo_root=repo_root,
            diff=diff_bundle,
        )
        short_circuit = r2["status"] == "fail"
    else:
        r2 = _skipped_result("Pass 1 failed; skipping Pass 2 to save time")
    results.append(r2)
    _collect_blockers(r2)

    # Pass 3 (architectural, LLM)
    if not short_circuit:
        r3 = pass_3_architectural.run_pass(
            pr_or_branch,
            base_ref=base_ref,
            repo_root=repo_root,
            diff=diff_bundle,
        )
    else:
        r3 = _skipped_result(
            "Earlier deterministic pass failed; Pass 3 would be wasted Opus tokens"
        )
    results.append(r3)
    _collect_blockers(r3)

    # Pass 4 (security, deterministic)
    if not short_circuit:
        r4 = pass_4_security.run_pass(
            pr_or_branch,
            base_ref=base_ref,
            repo_root=repo_root,
            diff=diff_bundle,
        )
    else:
        r4 = _skipped_result("Earlier pass failed; Pass 4 would re-run on stale state")
    results.append(r4)
    _collect_blockers(r4)
    if r4["status"] == "fail":
        short_circuit = True

    # Pass 5 (prose, regex + persona)
    if not short_circuit:
        r5 = pass_5_prose.run_pass(
            pr_or_branch,
            base_ref=base_ref,
            repo_root=repo_root,
            diff=diff_bundle,
        )
    else:
        r5 = _skipped_result("Earlier hard-fail; Pass 5 persona LLM skipped")
    results.append(r5)
    _collect_blockers(r5)

    # Pass 6 (adversarial, LLM, always runs unless short-circuited)
    if not short_circuit:
        r6 = pass_6_adversarial.run_pass(
            pr_or_branch,
            base_ref=base_ref,
            repo_root=repo_root,
            diff=diff_bundle,
            success_criterion=success_criterion,
        )
    else:
        r6 = _skipped_result("Earlier hard-fail; Pass 6 adversarial review skipped")
    results.append(r6)
    _collect_blockers(r6)

    # Pass 7 (feedback binding, only for self-fix PRs)
    if is_self_fix and not short_circuit:
        r7 = pass_7_feedback_binding.run_pass(
            pr_or_branch,
            base_ref=base_ref,
            repo_root=repo_root,
            diff=diff_bundle,
            feedback_record=feedback_record,
        )
    elif is_self_fix and short_circuit:
        r7 = _skipped_result(
            "Earlier hard-fail; Pass 7 feedback-binding skipped for self-fix"
        )
    else:
        r7 = _skipped_result(
            "is_self_fix=False; Pass 7 only runs on self-fix PRs per §8 row 7"
        )
    results.append(r7)
    _collect_blockers(r7)

    # Decide overall verdict.
    confidence = r6.get("confidence")
    from gaia.coder.review.pass_6_adversarial import HARD_FAIL_CONFIDENCE

    if short_circuit:
        overall = "block"
    elif confidence is not None and confidence < HARD_FAIL_CONFIDENCE:
        overall = "block"
    elif any(r["status"] == "fail" for r in results):
        overall = "request-changes"
    else:
        overall = "pass"

    return GateResult(
        overall=overall,
        pass_results=results,
        confidence=confidence,
        blockers=blockers,
    )


__all__ = [
    "GateResult",
    "GateResultDict",
    "HARD_FAIL_PASSES",
    "run_all_passes",
]
