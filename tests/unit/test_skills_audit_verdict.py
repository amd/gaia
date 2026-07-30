# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tier-scaled verdict gating (issue #2468).

The rigor of the gate scales with the tier a skill claims, and a skill can never
be stamped a tier whose gate it did not clear.
"""

from __future__ import annotations

import pytest

from gaia.skills.audit import (
    TIERS_REQUIRING_HUMAN_AUDIT,
    Finding,
    cleared_tiers,
    clears_tier,
    highest_cleared_tier,
    severity_verdict,
    verdict_for_tier,
)


def _f(severity: str, *, rule_id: str = "code.shell.subprocess") -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        category="dangerous-call",
        message="m",
        file="tools.py",
        line=1,
        remediation="r",
    )


# ----------------------------------------------------------------------
# The pure severity gate, per tier
# ----------------------------------------------------------------------


def test_clean_skill_passes_the_severity_gate_at_every_tier():
    for tier in ("experimental", "community", "verified"):
        assert severity_verdict((), tier) == "ALLOW"


@pytest.mark.parametrize("tier", ["experimental", "community", "verified"])
def test_critical_findings_block_at_every_tier(tier):
    """There is no tier at which shipping known-malicious code is acceptable."""
    assert severity_verdict((_f("critical"),), tier) == "BLOCK"


def test_experimental_is_advisory_below_critical():
    """The advisory tier surfaces findings without gating on them."""
    assert severity_verdict((_f("high"),), "experimental") == "ALLOW"
    assert severity_verdict((_f("medium"),), "experimental") == "ALLOW"


def test_community_forces_review_on_dangerous_findings():
    assert severity_verdict((_f("high"),), "community") == "REVIEW"


def test_community_allows_low_severity_findings():
    assert severity_verdict((_f("medium"),), "community") == "ALLOW"
    assert severity_verdict((_f("low"),), "community") == "ALLOW"


def test_verified_is_the_strictest_tier():
    assert severity_verdict((_f("high"),), "verified") == "BLOCK"
    assert severity_verdict((_f("medium"),), "verified") == "REVIEW"


def test_unknown_tier_is_rejected_not_defaulted():
    with pytest.raises(ValueError, match="tier"):
        severity_verdict((), "platinum")


# ----------------------------------------------------------------------
# The human-audit hook: the robot cannot stamp 'verified'
# ----------------------------------------------------------------------


def test_verified_requires_a_human_audit():
    assert "verified" in TIERS_REQUIRING_HUMAN_AUDIT
    assert "community" not in TIERS_REQUIRING_HUMAN_AUDIT
    assert "experimental" not in TIERS_REQUIRING_HUMAN_AUDIT


def test_clean_skill_claiming_verified_is_held_for_review_not_allowed():
    """The automated gate is a prerequisite for 'verified', never a grant of it."""
    verdict, reason = verdict_for_tier((), "verified")
    assert verdict == "REVIEW"
    assert "human" in reason.lower() or "audit" in reason.lower()


def test_clean_skill_claiming_community_is_allowed_outright():
    verdict, _ = verdict_for_tier((), "community")
    assert verdict == "ALLOW"


def test_clean_skill_claiming_experimental_is_allowed_outright():
    verdict, _ = verdict_for_tier((), "experimental")
    assert verdict == "ALLOW"


def test_human_audit_hook_never_downgrades_a_block():
    """A blocked verified skill stays BLOCK — the hook must not soften it."""
    verdict, _ = verdict_for_tier((_f("critical"),), "verified")
    assert verdict == "BLOCK"


def test_verdict_reason_names_the_worst_severity_and_the_tier():
    _, reason = verdict_for_tier((_f("critical"),), "community")
    assert "critical" in reason
    assert "community" in reason


def test_clean_reason_says_so():
    _, reason = verdict_for_tier((), "community")
    assert "no" in reason.lower()


# ----------------------------------------------------------------------
# clears_tier / cleared_tiers — the enforceable tier claim
# ----------------------------------------------------------------------


def test_clean_skill_clears_experimental_and_community():
    assert clears_tier((), "experimental")
    assert clears_tier((), "community")


def test_clean_skill_does_not_clear_verified_automatically():
    assert not clears_tier((), "verified")


def test_high_severity_finding_clears_only_experimental():
    findings = (_f("high"),)
    assert clears_tier(findings, "experimental")
    assert not clears_tier(findings, "community")
    assert not clears_tier(findings, "verified")


def test_critical_finding_clears_nothing():
    findings = (_f("critical"),)
    for tier in ("experimental", "community", "verified"):
        assert not clears_tier(findings, tier)


def test_cleared_tiers_is_ordered_least_to_most_trusted():
    assert cleared_tiers(()) == ("experimental", "community")


def test_cleared_tiers_is_empty_for_a_critical_finding():
    assert cleared_tiers((_f("critical"),)) == ()


def test_cleared_tiers_narrows_as_severity_rises():
    assert cleared_tiers((_f("high"),)) == ("experimental",)
    assert cleared_tiers((_f("medium"),)) == ("experimental", "community")


def test_highest_cleared_tier_prefers_the_most_trusted():
    assert highest_cleared_tier(()) == "community"
    assert highest_cleared_tier((_f("high"),)) == "experimental"


def test_highest_cleared_tier_is_none_when_nothing_clears():
    assert highest_cleared_tier((_f("critical"),)) is None


def test_a_tier_is_never_cleared_unless_its_own_gate_passed():
    """The invariant behind 'cannot be stamped a tier it did not clear'."""
    for findings in [(), (_f("low"),), (_f("medium"),), (_f("high"),), (_f("critical"),)]:
        for tier in ("experimental", "community", "verified"):
            assert clears_tier(findings, tier) == (tier in cleared_tiers(findings))
