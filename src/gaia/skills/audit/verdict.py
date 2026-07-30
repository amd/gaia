# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tier-scaled verdict gating.

Two rules turn findings into an ``ALLOW`` / ``REVIEW`` / ``BLOCK`` verdict:

1. **A severity gate that tightens with the tier.** ``experimental`` is
   advisory — findings are surfaced but do not gate — with one exception:
   ``critical`` blocks at *every* tier, because there is no tier at which
   shipping known-malicious code is acceptable. ``community`` requires the scan
   to pass and sends dangerous (``high``) findings to review. ``verified`` is
   strictest: ``high`` blocks and ``medium`` reviews.

2. **A human-audit hook for ``verified``.** The automated gate is a
   *prerequisite* for the top tier, never a grant of it (issue #2468 delivers
   the gate, not the AMD-Verified audit policy). So a clean skill claiming
   ``verified`` earns ``REVIEW`` — "cleared the robot, awaiting the human" —
   and ``verified`` never appears in :func:`cleared_tiers`. That is what stops a
   publisher self-stamping the most trusted tier.

Together these make the tier claim *enforceable* rather than merely recorded:
the report says which tiers the findings actually clear, so the hub Worker can
reject a skill claiming more.
"""

from __future__ import annotations

from typing import Optional, Sequence

from gaia.governance.schemas import DecisionType
from gaia.skills.audit.findings import (
    SEVERITY_ORDER,
    Finding,
    Severity,
    worst_severity,
)
from gaia.skills.format import SECURITY_TIERS

#: Tiers ordered least → most trusted (``SECURITY_TIERS`` is most-trusted-first).
TIER_TRUST_ORDER: tuple[str, ...] = tuple(reversed(SECURITY_TIERS))

#: Severity at or above which the verdict is ``BLOCK``, per claimed tier.
TIER_BLOCK_THRESHOLD: dict[str, Severity] = {
    "experimental": "critical",
    "community": "critical",
    "verified": "high",
}

#: Severity at or above which the verdict is ``REVIEW``, per claimed tier.
#: ``None`` means nothing short of the block threshold gates this tier.
TIER_REVIEW_THRESHOLD: dict[str, Optional[Severity]] = {
    "experimental": None,
    "community": "high",
    "verified": "medium",
}

#: Tiers the hub refuses to publish without an audit report (mirrors
#: ``TIERS_REQUIRING_AUDIT`` in ``workers/agent-hub/src/audit.ts``).
TIERS_REQUIRING_AUDIT = frozenset({"community", "verified"})

#: Tiers the automated gate cannot grant on its own — a human/AMD audit signs
#: off on top of a passing scan.
TIERS_REQUIRING_HUMAN_AUDIT = frozenset({"verified"})


def _rank(severity: Severity) -> int:
    return SEVERITY_ORDER.index(severity)


def _validate_tier(tier: str) -> None:
    if tier not in TIER_BLOCK_THRESHOLD:
        raise ValueError(
            f"Unknown security tier {tier!r}: the audit gate has no threshold for it. "
            f"Valid tiers: {', '.join(TIER_TRUST_ORDER)}."
        )


def severity_verdict(findings: Sequence[Finding], tier: str) -> DecisionType:
    """Apply only the severity gate for ``tier`` (no human-audit hook).

    Raises:
        ValueError: if ``tier`` is not a known security tier. An unknown tier is
            a manifest defect, never silently treated as the safe default.
    """
    _validate_tier(tier)
    worst = worst_severity(findings)
    if worst is None:
        return "ALLOW"

    if _rank(worst) >= _rank(TIER_BLOCK_THRESHOLD[tier]):
        return "BLOCK"

    review_threshold = TIER_REVIEW_THRESHOLD[tier]
    if review_threshold is not None and _rank(worst) >= _rank(review_threshold):
        return "REVIEW"

    return "ALLOW"


def verdict_for_tier(
    findings: Sequence[Finding], tier: str
) -> tuple[DecisionType, str]:
    """Return the verdict for ``tier`` plus the reason to show the author."""
    verdict = severity_verdict(findings, tier)
    worst = worst_severity(findings)
    counted = len(findings)

    if verdict == "BLOCK":
        return verdict, (
            f"Blocked at the '{tier}' tier: {counted} finding(s), worst severity "
            f"{worst}. Fix the findings below and re-run the audit."
        )

    if verdict == "REVIEW":
        return verdict, (
            f"Held for review at the '{tier}' tier: {counted} finding(s), worst "
            f"severity {worst}. A maintainer must sign off before this skill can "
            "be published."
        )

    # Severity gate passed. The top tier still needs its human audit.
    if tier in TIERS_REQUIRING_HUMAN_AUDIT:
        return "REVIEW", (
            f"Cleared the automated gate with {counted} finding(s), but the "
            f"'{tier}' tier also requires a human audit sign-off, which this "
            "engine does not grant. Publish as 'community' or request the "
            f"'{tier}' audit."
        )

    if counted:
        return verdict, (
            f"Allowed at the '{tier}' tier: {counted} advisory finding(s), worst "
            f"severity {worst}, none above this tier's threshold."
        )
    return verdict, f"Allowed at the '{tier}' tier: no findings."


def clears_tier(findings: Sequence[Finding], tier: str) -> bool:
    """True when ``findings`` clear ``tier``'s gate outright.

    ``verified`` is never cleared by the automated gate alone — see the module
    docstring.
    """
    _validate_tier(tier)
    if tier in TIERS_REQUIRING_HUMAN_AUDIT:
        return False
    return severity_verdict(findings, tier) == "ALLOW"


def cleared_tiers(findings: Sequence[Finding]) -> tuple[str, ...]:
    """Every tier ``findings`` clear, ordered least → most trusted."""
    return tuple(tier for tier in TIER_TRUST_ORDER if clears_tier(findings, tier))


def highest_cleared_tier(findings: Sequence[Finding]) -> Optional[str]:
    """The most trusted tier ``findings`` clear, or ``None`` if none do."""
    cleared = cleared_tiers(findings)
    return cleared[-1] if cleared else None


def report_is_stale(report, *, skill: str, version: Optional[str], digest: str) -> bool:
    """True when ``report`` does not describe the artifact being published.

    A new version re-earns its verdict: the report is stale if it names a
    different skill, a different version, or different bytes. This is the check
    that makes "re-audit on every version bump" enforceable rather than a
    convention — the hub Worker mirrors it.
    """
    return (
        report.skill != skill
        or report.version != version
        or report.content_digest != digest
    )
