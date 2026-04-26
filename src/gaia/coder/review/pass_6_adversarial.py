# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pass 6 — adversarial self-review (§8 row 6).

LLM-driven, **fresh context** (no prior-turn history). Opus 4.7 gets the
diff cold, tries to find three distinct things wrong, and emits the
``confidence_score`` (0-100) the §7.6 auto-merge gate reads.

Per §15.8 P6: *"If you cannot find three, say so explicitly — do not
pad."* The prompt template enforces that; this module only maps the
parsed JSON onto :class:`PassResult`.

The confidence-score rubric (from §15.8 P6 / §7.6):

* ``90-100`` — does exactly the criterion, nothing more/less. Auto-merge
  eligible once the EM has graduated the fix-class.
* ``75-89``  — achieves with scope creep OR minor gaps. Opens the PR but
  does not auto-merge.
* ``60-74``  — partial / mismatch. Do not auto-merge; flag for rewrite.
* ``<60``    — does not achieve OR unrelated. Blocks the PR entirely.

This module does not implement the merge policy itself (that lives in
the §7.6 merge orchestrator) — it only surfaces the score.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

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

PROMPT_NAME: str = "adversarial.md"

#: Auto-merge threshold per §7.6 — "only at ≥ 90%".
AUTO_MERGE_CONFIDENCE_THRESHOLD: int = 90

#: Lower bound; below this the PR is blocked entirely per §7.6.
HARD_FAIL_CONFIDENCE: int = 60


def _via_subagent(prompt: str) -> Optional[str]:
    """Route via the ``code-reviewer`` subagent when the SDK is installed.

    Per §7.6 the adversarial pass for self-code PRs must run as a fresh
    Opus 4.7 session. Using the SDK's ``code-reviewer`` gives us that
    isolation plus a stable subagent identity. Absent the SDK we fall
    back to a direct call with no conversation history — still fresh
    context.
    """
    try:
        import claude_agent_sdk  # type: ignore
    except ImportError:
        return None
    dispatch = getattr(claude_agent_sdk, "dispatch_subagent", None)
    if dispatch is None:  # pragma: no cover
        return None
    return dispatch("code-reviewer", prompt)  # pragma: no cover


def run_pass(
    pr_or_branch: str,
    *,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
    diff: Optional[DiffBundle] = None,
    success_criterion: Optional[str] = None,
) -> PassResult:
    """Execute Pass 6.

    Args:
        success_criterion: The plan-stage-3 success criterion (§5.1 Stage 3).
            When ``None`` and no PR body is available we pass a single
            placeholder line — the prompt still asks for a confidence score
            but the model has less to compare against. The gate
            orchestrator is expected to pipe through the actual criterion
            once Phase 5 lands the loop-runner.
    """
    diff_bundle = diff or resolve_diff(
        pr_or_branch, base_ref=base_ref, repo_root=repo_root
    )

    criterion = (
        success_criterion
        or diff_bundle.pr_body
        or "(success criterion unavailable — judge diff on its own merits)"
    )

    template = load_prompt(PROMPT_NAME)
    rendered = render_prompt(
        template,
        {
            "title": diff_bundle.pr_title or "(local branch; no PR title)",
            "body": diff_bundle.pr_body or "(local branch; no PR body)",
            "unified_diff": diff_bundle.unified_diff,
            "criterion": criterion,
        },
    )

    tooling_used: List[str] = []
    try:
        subagent_response = _via_subagent(rendered)
        if subagent_response is not None:
            raw = subagent_response
            tooling_used.append(
                "claude-agent-sdk → code-reviewer subagent (fresh context)"
            )
        else:
            raw = call_opus(rendered)
            tooling_used.append("anthropic SDK → Opus 4.7 (fresh context, no history)")
    except LLMClientUnavailable as exc:
        return make_pass_result(
            status="fail",
            findings=[
                {
                    "severity": "blocking",
                    "description": ("adversarial pass could not run: " + str(exc)),
                    "citation": "§8 Pass 6",
                }
            ],
            citations=["docs/plans/coder-agent.mdx §8 Pass 6"],
            tooling_used=tooling_used,
        )

    try:
        payload = parse_json_response(raw)
    except ValueError as exc:
        return make_pass_result(
            status="fail",
            findings=[
                {
                    "severity": "blocking",
                    "description": f"Opus response was not valid JSON: {exc}",
                    "raw_head": raw[:500],
                    "citation": "§15.9 fail-loudly",
                }
            ],
            citations=["docs/plans/coder-agent.mdx §8 Pass 6"],
            tooling_used=tooling_used,
        )

    # Parse confidence — may arrive as int or string
    confidence_raw = payload.get("confidence_score")
    try:
        confidence = int(confidence_raw) if confidence_raw is not None else None
    except (TypeError, ValueError):
        confidence = None

    findings: List[dict] = []
    for item in payload.get("findings", []) or []:
        severity = str(item.get("severity", "minor")).lower()
        if severity not in {"blocking", "significant", "minor"}:
            severity = "minor"
        findings.append(
            {
                "severity": severity,
                "description": str(item.get("description", "")),
                "file_line": item.get("file_line", ""),
                "fix": item.get("fix", ""),
                "citation": "§8 Pass 6",
            }
        )

    if "rubric_reasoning" in payload:
        findings.append(
            {
                "severity": "info",
                "description": str(payload.get("rubric_reasoning", "")),
                "kind": "rubric_reasoning",
                "citation": "§15.8 P6 rubric",
            }
        )

    # Status:
    # - If confidence < HARD_FAIL_CONFIDENCE (60) → fail (PR blocked)
    # - Else if any finding is "blocking" → fail
    # - Else pass (auto-merge eligibility is a separate property the gate
    #   computes from ``confidence`` vs. AUTO_MERGE_CONFIDENCE_THRESHOLD)
    if confidence is not None and confidence < HARD_FAIL_CONFIDENCE:
        status = "fail"
        findings.append(
            {
                "severity": "blocking",
                "description": (
                    f"adversarial confidence {confidence} < "
                    f"{HARD_FAIL_CONFIDENCE}; §7.6 requires rewrite before "
                    "opening a PR"
                ),
                "citation": "§7.6 confidence gate",
            }
        )
    else:
        status = (
            "fail" if any(f.get("severity") == "blocking" for f in findings) else "pass"
        )

    return make_pass_result(
        status=status,
        findings=findings,
        confidence=confidence,
        citations=[
            "docs/plans/coder-agent.mdx §8 Pass 6",
            "docs/plans/coder-agent.mdx §7.6 confidence gate",
            "docs/plans/coder-agent.mdx §15.8 P6",
        ],
        tooling_used=tooling_used,
    )


__all__ = [
    "AUTO_MERGE_CONFIDENCE_THRESHOLD",
    "HARD_FAIL_CONFIDENCE",
    "run_pass",
]
