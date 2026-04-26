# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pass 5 — self-prose review (§8 row 5).

Two-stage:

1. **Regex / structural linter** (cheap, deterministic). Checks:

   * PR title is conventional-commits shape
     (``<type>(<scope>)?: <subject>``).
   * PR body contains a ``## Summary`` / ``## Test plan`` section or
     equivalent bullets.
   * No ``Co-Authored-By: Claude`` trailer / ``Generated with Claude
     Code`` attribution.
   * Banned phrase list ("Certainly!", "As an AI", …).

2. **Persona linter** (LLM). Renders ``prompts/persona_linter.md`` with
   the GAIA.md persona section as a cacheable prefix and the PR body as
   the candidate text. Returns the verdict and violations.

The LLM step only runs if stage 1 passed — a body that already fails
conventional-commits + deterministic checks does not need an Opus call
to tell the author it's broken.
"""

from __future__ import annotations

import re
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

PROMPT_NAME: str = "persona_linter.md"

# Conventional-commits shape: ``type(scope)?: subject``. Allowed types match
# the project's ``CLAUDE.md`` convention.
_CONV_COMMIT_RE = re.compile(
    r"^(?P<type>feat|fix|docs|chore|refactor|test|perf|ci|build|style|revert)"
    r"(?:\(.+?\))?(!)?:\s.+",
)

# Claude-attribution trailer variants. The repo explicitly forbids every
# permutation per CLAUDE.md.
_CLAUDE_ATTRIBUTION_RE = re.compile(
    r"(?i)(?:"
    r"Co-Authored-By:\s*Claude"
    r"|Generated with\s+\[?Claude Code\]?"
    r"|Authored by\s+Claude"
    r"|Written by\s+Claude"
    r"|As an AI"
    r")",
)

# Banned persona phrases caught by regex before we ask Opus.
_BANNED_PHRASES = (
    "Certainly!",
    "Absolutely!",
    "I'd be happy to help",
    "Great question!",
    "As an AI",
    "Generated with",
)

# Simple check for the "why" on bullets: every bullet line that starts with
# ``- `` or ``* `` must contain either a ``—`` / `` - `` separator OR end
# in a clause-terminating sentence (punctuation). We implement the heuristic
# as: "bullet must be at least 10 words OR contain a dash separator". Short
# pure-noun bullets trip it.
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+)$", re.MULTILINE)


def _stage1_regex(pr_title: str, pr_body: str) -> Tuple[List[dict], List[str]]:
    """Run the cheap deterministic checks.

    Returns ``(findings, citations_added)``.
    """
    findings: List[dict] = []
    citations: List[str] = [
        "docs/plans/coder-agent.mdx §8 Pass 5 — deterministic",
        "CLAUDE.md PR description rules",
    ]

    if not pr_title or not _CONV_COMMIT_RE.match(pr_title.strip()):
        findings.append(
            {
                "severity": "blocking",
                "description": (
                    "PR title is not conventional-commits shape "
                    "(expected 'feat(scope): …' / 'fix(scope): …' / etc.)"
                ),
                "title_seen": pr_title,
                "citation": "CLAUDE.md title convention",
            }
        )

    if pr_body:
        lower = pr_body.lower()
        if "summary" not in lower:
            findings.append(
                {
                    "severity": "significant",
                    "description": "PR body has no Summary section",
                    "citation": "CLAUDE.md PR description",
                }
            )
        if "test plan" not in lower and "test-plan" not in lower:
            findings.append(
                {
                    "severity": "significant",
                    "description": "PR body has no Test plan section",
                    "citation": "CLAUDE.md PR description",
                }
            )
        if _CLAUDE_ATTRIBUTION_RE.search(pr_body):
            findings.append(
                {
                    "severity": "blocking",
                    "description": (
                        "PR body contains forbidden Claude attribution / "
                        "'Generated with Claude Code' / 'Co-Authored-By: Claude' "
                        "/ 'As an AI' trailer"
                    ),
                    "citation": "CLAUDE.md no-attribution rule",
                }
            )
        for phrase in _BANNED_PHRASES:
            if phrase.lower() in lower:
                findings.append(
                    {
                        "severity": "significant",
                        "description": f"PR body contains banned phrase: {phrase!r}",
                        "citation": "§8 Pass 5 persona",
                    }
                )
        # Bullets-too-short heuristic (only informational)
        short_bullets = [
            match.group("text").strip()
            for match in _BULLET_RE.finditer(pr_body)
            if len(match.group("text").split()) < 4
            and "—" not in match.group("text")
            and " - " not in match.group("text")
        ]
        if len(short_bullets) >= 3:
            findings.append(
                {
                    "severity": "minor",
                    "description": (
                        "several bullets lack a 'why' clause; each non-trivial "
                        "bullet should explain why the change matters"
                    ),
                    "examples": short_bullets[:3],
                    "citation": "CLAUDE.md PR description — why over what",
                }
            )

    return findings, citations


def _stage2_persona(
    pr_body: str, *, gaia_md_path: Optional[Path]
) -> Tuple[List[dict], List[str]]:
    """Run the persona linter (LLM).

    Returns ``(findings, tooling_used_additions)``.
    """
    # Late import so the module can be imported without ``gaia.coder.base``.
    from gaia.coder.base import GAIA_MD_PATH

    persona_section: str = ""
    candidate_path = gaia_md_path or GAIA_MD_PATH
    try:
        persona_section = candidate_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            "pass 5: %s missing; persona linter gets empty slot.",
            candidate_path,
        )

    template = load_prompt(PROMPT_NAME)
    rendered = render_prompt(
        template,
        {
            "inject_verbatim": persona_section,
            "candidate_text": pr_body,
        },
    )

    tooling: List[str] = ["anthropic SDK → Opus 4.7 (persona linter)"]
    try:
        raw = call_opus(rendered)
    except LLMClientUnavailable as exc:
        return (
            [
                {
                    "severity": "info",
                    "description": (
                        "persona linter LLM unavailable: " + str(exc) + " — "
                        "deterministic checks still applied."
                    ),
                    "status": "skipped",
                    "citation": "§15.9 fail-loudly",
                }
            ],
            tooling,
        )

    try:
        payload = parse_json_response(raw)
    except ValueError as exc:
        return (
            [
                {
                    "severity": "blocking",
                    "description": f"persona linter JSON parse failed: {exc}",
                    "raw_head": raw[:500],
                    "citation": "§15.9 fail-loudly",
                }
            ],
            tooling,
        )

    findings: List[dict] = []
    for violation in payload.get("violations", []) or []:
        findings.append(
            {
                "severity": "significant",
                "description": (
                    f"persona violation: {violation.get('pattern', '?')} — "
                    f"{violation.get('phrase', '?')!r}"
                ),
                "suggested_rewrite": violation.get("rewrite", ""),
                "citation": "§8 Pass 5 persona linter",
            }
        )
    if str(payload.get("verdict", "")).lower() == "request-changes":
        findings.append(
            {
                "severity": "blocking",
                "description": (
                    "persona linter overall verdict: request-changes — "
                    + str(payload.get("reasoning", "")).strip()
                ),
                "citation": "§8 Pass 5 persona linter",
            }
        )
    return findings, tooling


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_pass(
    pr_or_branch: str,
    *,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
    diff: Optional[DiffBundle] = None,
    gaia_md_path: Optional[Path] = None,
    skip_llm: bool = False,
) -> PassResult:
    """Execute Pass 5 and return the :class:`PassResult`.

    Args:
        skip_llm: Stop after stage 1. Used by the gate orchestrator when
            stage 1 already failed hard — no point paying the Opus call.
    """
    diff_bundle = diff or resolve_diff(
        pr_or_branch, base_ref=base_ref, repo_root=repo_root
    )

    pr_title = diff_bundle.pr_title or ""
    pr_body = diff_bundle.pr_body or ""

    findings, citations = _stage1_regex(pr_title, pr_body)
    tooling_used: List[str] = ["regex + conventional-commits matcher"]

    hard_fail_stage1 = any(f.get("severity") == "blocking" for f in findings)

    if not skip_llm and not hard_fail_stage1 and pr_body:
        stage2_findings, stage2_tooling = _stage2_persona(
            pr_body, gaia_md_path=gaia_md_path
        )
        findings.extend(stage2_findings)
        tooling_used.extend(stage2_tooling)

    # Branch-without-PR body has no prose to review; do not hard-fail in
    # that case — the gate calls this pass knowing the context.
    if not pr_body and not pr_title:
        findings.append(
            {
                "severity": "info",
                "description": (
                    "no PR title or body provided (local branch, no PR yet) "
                    "— Pass 5 deferred until PR is opened"
                ),
                "status": "deferred",
                "citation": "§8 Pass 5",
            }
        )
        return make_pass_result(
            status="pass",
            findings=findings,
            confidence=None,
            citations=citations,
            tooling_used=tooling_used,
        )

    hard_fail = any(f.get("severity") == "blocking" for f in findings)
    return make_pass_result(
        status="fail" if hard_fail else "pass",
        findings=findings,
        confidence=None,
        citations=citations + ["docs/plans/coder-agent.mdx §15.8 P4"],
        tooling_used=tooling_used,
    )


__all__ = ["run_pass"]
