# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Continuous self-critique (§7.2) — single-turn Opus 4.7 call between tool ticks.

Runs after every state-changing tool call (``edit`` / ``write_file`` /
``run_cli_command``) and returns at most *one* actionable finding. The loop
driver routes findings by confidence:

* ``>= 80`` → surface inline, she addresses before next transition,
* ``60 - 79`` → log for review at ``self_review``,
* ``< 60`` → **discarded** (§7.2 low-confidence rule).

This module is deliberately cheap: one LLM call, bounded output, no memory
write side-effect. The caller persists findings elsewhere (audit log, trace
file).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Literal, Mapping, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

#: Confidence threshold below which findings are suppressed (§7.2).
MIN_CRITIQUE_CONFIDENCE: int = 60

#: High-confidence threshold — findings at/above this get surfaced inline.
HIGH_CONFIDENCE_THRESHOLD: int = 80


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


Severity = Literal["high", "med"]


@dataclass(frozen=True)
class Finding:
    """One critique finding."""

    severity: Severity
    citation: str
    fix_direction: str
    confidence: int


@dataclass(frozen=True)
class CritiqueResult:
    """Return value of :func:`critique_recent_output`."""

    findings: tuple[Finding, ...]
    most_impactful: Optional[Finding]
    raw_response: str = ""

    @property
    def high_confidence_findings(self) -> tuple[Finding, ...]:
        """Subset of :attr:`findings` at or above :data:`HIGH_CONFIDENCE_THRESHOLD`."""
        return tuple(
            f for f in self.findings if f.confidence >= HIGH_CONFIDENCE_THRESHOLD
        )


# ---------------------------------------------------------------------------
# LLM protocol (mockable)
# ---------------------------------------------------------------------------


class CritiqueClient(Protocol):
    """Callable that runs the critique prompt and returns raw JSON text."""

    def __call__(
        self,
        *,
        prompt: str,
        success_criterion: str,
        recent_output: str,
        kind: str,
    ) -> str: ...


CritiqueClientFn = Callable[..., str]


def _default_critique_client(**_kwargs: Any) -> str:  # pragma: no cover
    raise RuntimeError(
        "No CritiqueClient configured. Inject one via "
        "critique_recent_output(client=...) or wire the default Anthropic "
        "client in Phase 7."
    )


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_critique_prompt() -> str:
    path = _PROMPTS_DIR / "critique.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def _render(
    template: str,
    *,
    success_criterion: str,
    kind: str,
    content: str,
    gaia_md_principles: str,
    failure_pattern_hits: Sequence[Mapping[str, Any]],
) -> str:
    """Substitute slots used by ``prompts/critique.md`` (P2)."""
    rendered = template
    substitutions: dict[str, str] = {
        "plan_criterion": success_criterion,
        "edit|write_file|cli_output": kind,
        "content": content,
        "inject_verbatim": gaia_md_principles,
        "top_3": json.dumps(list(failure_pattern_hits or [])),
    }
    for key, value in substitutions.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def critique_recent_output(
    success_criterion: str,
    recent_output: str,
    memory_hits: Sequence[Mapping[str, Any]] = (),
    *,
    kind: str = "edit",
    gaia_md_principles: str = "",
    client: Optional[CritiqueClientFn] = None,
) -> CritiqueResult:
    """Run the P2 critique prompt once and return a filtered :class:`CritiqueResult`.

    Filtering rules (§7.2):
    * Any finding with ``confidence < 60`` is **dropped** entirely.
    * ``most_impactful`` is retained only if it also clears the 60 threshold.
    * ``raw_response`` preserves the original JSON for audit-log bookkeeping.

    ``kind`` maps to the P2 template slot ``{{edit|write_file|cli_output}}``.
    """
    client = client or _default_critique_client
    template = _load_critique_prompt()
    prompt = _render(
        template,
        success_criterion=success_criterion,
        kind=kind,
        content=recent_output,
        gaia_md_principles=gaia_md_principles,
        failure_pattern_hits=memory_hits,
    )
    raw = client(
        prompt=prompt,
        success_criterion=success_criterion,
        recent_output=recent_output,
        kind=kind,
    )
    return _parse_and_filter(raw)


def _parse_and_filter(raw: str) -> CritiqueResult:
    """Parse the LLM JSON output, apply the < 60 confidence filter."""
    if not isinstance(raw, str) or not raw.strip():
        # Empty response is legal per the prompt ("empty list is valid").
        return CritiqueResult(findings=(), most_impactful=None, raw_response=raw or "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"critique response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("critique response must be a JSON object")

    findings_raw = parsed.get("findings") or []
    if not isinstance(findings_raw, list):
        raise ValueError("critique 'findings' must be a list")

    kept: List[Finding] = []
    for entry in findings_raw:
        if not isinstance(entry, dict):
            continue
        confidence = entry.get("confidence")
        if not isinstance(confidence, int):
            continue
        if confidence < MIN_CRITIQUE_CONFIDENCE:
            continue
        severity = entry.get("severity")
        if severity not in ("high", "med"):
            continue
        citation = str(entry.get("citation") or "")
        fix_direction = str(entry.get("fix_direction") or "")
        kept.append(
            Finding(
                severity=severity,
                citation=citation,
                fix_direction=fix_direction,
                confidence=confidence,
            )
        )

    most_impactful_raw = parsed.get("most_impactful")
    most_impactful: Optional[Finding] = None
    if isinstance(most_impactful_raw, dict):
        conf = most_impactful_raw.get("confidence")
        if isinstance(conf, int) and conf >= MIN_CRITIQUE_CONFIDENCE:
            sev = most_impactful_raw.get("severity")
            if sev in ("high", "med"):
                most_impactful = Finding(
                    severity=sev,
                    citation=str(most_impactful_raw.get("citation") or ""),
                    fix_direction=str(most_impactful_raw.get("fix_direction") or ""),
                    confidence=conf,
                )

    return CritiqueResult(
        findings=tuple(kept),
        most_impactful=most_impactful,
        raw_response=raw,
    )


__all__ = [
    "CritiqueClient",
    "CritiqueResult",
    "Finding",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MIN_CRITIQUE_CONFIDENCE",
    "critique_recent_output",
]
