# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pass 3 — self-architectural review (§8 row 3).

LLM-driven. Claude Opus 4.7 reads the diff with her ``ARCHITECTURE.md`` +
``PROJECT_MAP.md`` + the PR body in a **fresh context** and returns a
structured verdict on layering, circular imports, public-API breaks,
silent fallbacks, drive-by changes, and the docs mandate.

Per §15.8 P5: "invoked via ``architecture-reviewer`` subagent". In
production this dispatches through the Claude Agent SDK's
``architecture-reviewer`` subagent (``.claude/agents/architecture-reviewer.md``).
In v1 we call the Anthropic SDK directly behind the :func:`call_opus`
seam — the prompt text is identical either way. When a real
``claude_agent_sdk`` is available, :func:`_via_subagent` switches over
transparently.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from gaia.coder.review._diff import DiffBundle, resolve_diff
from gaia.coder.review._llm import (
    DEFAULT_MAX_TOKENS,
    LLMClientUnavailable,
    call_opus,
    load_prompt,
    parse_json_response,
    render_prompt,
)
from gaia.coder.review.pass_result import PassResult, make_pass_result

logger = logging.getLogger(__name__)

#: Prompt file (relative to ``src/gaia/coder/prompts/``).
PROMPT_NAME: str = "architectural.md"


def _read_document(path: Path) -> str:
    """Read a living architecture document, returning ``""`` if missing.

    Missing is not fatal — the §8 doc-mandate check still fires via the
    PR-body scan, and the LLM is trained to flag an empty
    ``<architecture_md>`` slot rather than hallucinating content. This is
    an intentional exception to the fail-loudly rule: a doc-not-yet-written
    day-one run should not hard-block the first PR she ever opens.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            "pass 3: document missing at %s; sending empty slot to Opus.",
            path,
        )
        return ""


def _via_subagent(prompt: str) -> Optional[str]:
    """Return the subagent's response, or ``None`` if the SDK is absent.

    When the ``claude_agent_sdk`` package is installed we dispatch to the
    ``architecture-reviewer`` agent so the review runs in a dedicated
    context window. Otherwise we return ``None`` and let the caller fall
    back to :func:`call_opus`.

    Today the SDK is not a hard dep of GAIA; this wrapper is a shim so the
    wiring is explicit for when we add it.
    """
    try:
        import claude_agent_sdk  # type: ignore
    except ImportError:
        return None
    dispatch = getattr(claude_agent_sdk, "dispatch_subagent", None)
    if dispatch is None:  # pragma: no cover - SDK shape changing
        return None
    return dispatch("architecture-reviewer", prompt)  # pragma: no cover


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_pass(
    pr_or_branch: str,
    *,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
    diff: Optional[DiffBundle] = None,
    architecture_md: Optional[Path] = None,
    project_map_md: Optional[Path] = None,
) -> PassResult:
    """Execute Pass 3 and return the :class:`PassResult`.

    Args:
        architecture_md: Override the ``ARCHITECTURE.md`` path. Defaults
            to :data:`gaia.coder.base.ARCHITECTURE_MD_PATH`.
        project_map_md: Override the ``PROJECT_MAP.md`` path. Defaults
            to :data:`gaia.coder.base.PROJECT_MAP_MD_PATH`.
    """
    # Late import so the review package remains importable before the
    # CoderAgent base has been fully wired up.
    from gaia.coder.base import ARCHITECTURE_MD_PATH, PROJECT_MAP_MD_PATH

    diff_bundle = diff or resolve_diff(
        pr_or_branch, base_ref=base_ref, repo_root=repo_root
    )

    architecture_text = _read_document(architecture_md or ARCHITECTURE_MD_PATH)
    project_map_text = _read_document(project_map_md or PROJECT_MAP_MD_PATH)

    template = load_prompt(PROMPT_NAME)
    rendered = render_prompt(
        template,
        {
            "inject_HER_ARCHITECTURE_md": architecture_text,
            "inject_PROJECT_MAP_md": project_map_text,
            "unified_diff": diff_bundle.unified_diff,
            "pr_description": diff_bundle.pr_body or "(local branch; no PR body)",
        },
    )

    tooling_used: List[str] = []
    try:
        subagent_response = _via_subagent(rendered)
        if subagent_response is not None:
            raw = subagent_response
            tooling_used.append("claude-agent-sdk → architecture-reviewer subagent")
        else:
            raw = call_opus(rendered, max_tokens=DEFAULT_MAX_TOKENS)
            tooling_used.append("anthropic SDK → Opus 4.7 (direct)")
    except LLMClientUnavailable as exc:
        return make_pass_result(
            status="fail",
            findings=[
                {
                    "severity": "blocking",
                    "description": ("architectural pass could not run: " + str(exc)),
                    "citation": "§8 Pass 3",
                }
            ],
            citations=["docs/plans/coder-agent.mdx §8 Pass 3"],
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
            citations=["docs/plans/coder-agent.mdx §8 Pass 3"],
            tooling_used=tooling_used,
        )

    overall = str(payload.get("overall", "request-changes")).lower()
    blockers = payload.get("blockers", []) or []
    rule_verdicts = payload.get("rules", []) or []

    findings: List[dict] = []
    for blocker in blockers:
        findings.append(
            {
                "severity": "blocking",
                "description": str(blocker),
                "citation": "§8 Pass 3 blocker",
            }
        )
    for rule in rule_verdicts:
        if str(rule.get("verdict", "")).lower() == "fail":
            findings.append(
                {
                    "severity": "significant",
                    "description": (
                        f"architectural rule failed: "
                        f"{rule.get('rule', '<unnamed>')} — "
                        f"{rule.get('citation', '<no citation>')}"
                    ),
                    "citation": "§8 Pass 3 rule",
                }
            )

    status = "pass" if overall == "pass" and not blockers else "fail"

    return make_pass_result(
        status=status,
        findings=findings,
        confidence=None,
        citations=[
            "docs/plans/coder-agent.mdx §8 Pass 3",
            "docs/plans/coder-agent.mdx §15.8 P5",
        ],
        tooling_used=tooling_used,
    )


__all__ = ["run_pass"]
