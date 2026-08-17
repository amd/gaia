# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Install-time security tiers: the trust ladder and the permission ceiling it sets.

The three tiers and their ceilings are fixed by ``docs/plans/skill-format.mdx``
(*Security tiers*); this module is that table turned into enforcement for
``gaia skill install`` (#2467 scope C).

===============  ======================  ===========================  ===================================
Tier             Signing                 At install                   Ceiling
===============  ======================  ===========================  ===================================
``verified``     AMD-signed, audited     auto-grant, no prompt        every declared permission
``community``    publisher-signed        dangerous grants prompt      below verified
``experimental`` none                    explicit ``--allow-experimental``  most restrictive
===============  ======================  ===========================  ===================================

Two independent checks run at install, and both must pass:

1. **Attestation ceiling** — the tier a skill *claims* cannot exceed the tier its
   signature *earns* (see :mod:`gaia.skills.signing`). An unsigned skill can
   never install as ``verified``; :func:`effective_tier` collapses the claim down
   to the attested tier rather than trusting the front matter.
2. **Permission ceiling** — every declared permission must sit at or below the
   effective tier's ceiling (:func:`enforce_tier_ceiling`).

**What the ceiling can and cannot mean in v1.** Phase 1 (#888) bridges
connector-backed domains only; a skill declaring a local capability
(``filesystem``/``shell``/``database``/``desktop``/``env``) is refused outright by
:func:`gaia.skills.permissions.refuse_unbridged_permissions` because the sandbox
that would contain it does not exist yet. So the ceilings below discriminate
between *bridged* permissions, and :data:`DANGEROUS_GRANTS` keeps the spec's
authoritative dangerous set even though three of its entries are unreachable
today — a skill declaring them never gets as far as the prompt. Do not read a
``verified`` stamp as "a runtime sandbox is protecting you"; it is not.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from gaia.skills.errors import (
    FORMAT_DOCS_URL,
    SkillPermissionError,
    SkillValidationError,
)
from gaia.skills.format import SECURITY_TIERS
from gaia.skills.permissions import Permission

#: Tiers ordered least- to most-trusted. Index == rung on the promotion ladder.
TIER_ORDER: tuple[str, ...] = ("experimental", "community", "verified")

#: The tier a skill lands on when nothing attests to it.
LOWEST_TIER = TIER_ORDER[0]

#: ``<domain>:<level>`` grants the spec calls dangerous: allowed at ``verified``
#: (pre-approved by the AMD audit), prompted at ``community``, refused below.
#:
#: ``shell:execute`` / ``desktop:control`` / ``database:write`` are the spec's
#: original three. They are local-capability domains, so in v1 they are refused
#: before any tier check — kept here so the set stays the single authoritative
#: list when the Phase 2 sandbox makes them reachable.
DANGEROUS_GRANTS: frozenset[str] = frozenset(
    {
        "shell:execute",
        "desktop:control",
        "database:write",
        # Bridged and reachable today: unrestricted outbound egress. A skill that
        # can POST anywhere can exfiltrate whatever the model puts in front of it.
        "network:write",
    }
)

#: Per-tier permission ceiling as ``{tier: allowed <domain>:<level> pairs}``.
#: ``None`` means "no restriction" — every permission the skill declares is
#: within the ceiling (``verified`` is pre-approved by audit).
#:
#: ``<domain>:none`` is always allowed: it asks for nothing.
_CEILINGS: dict[str, frozenset[str] | None] = {
    "verified": None,
    # Everything the bridge supports; dangerous grants still prompt.
    "community": frozenset({"network:read", "network:write", "mcp:connect"}),
    # Read-only egress only. An unattested skill may not open an MCP connection
    # or write to the network without the user promoting it first.
    "experimental": frozenset({"network:read"}),
}


def tier_rank(tier: str) -> int:
    """Rung of *tier* on the trust ladder (higher == more trusted).

    Raises:
        SkillValidationError: for a tier outside :data:`TIER_ORDER`.
    """
    try:
        return TIER_ORDER.index(tier)
    except ValueError as exc:
        raise SkillValidationError(
            f"Unknown security tier {tier!r}. Valid tiers: "
            f"{', '.join(sorted(SECURITY_TIERS))}. See {FORMAT_DOCS_URL}#security-tiers"
        ) from exc


def effective_tier(claimed: str, attested: str) -> str:
    """The tier a skill actually installs at: the lower of claim and attestation.

    A skill's front matter is publisher-authored input, so it is a *request*, not
    a fact. The attested tier comes from verifying its signature against the
    trust store. Taking the minimum is what makes "no unsigned skill installs as
    ``verified``" true by construction rather than by a separate check that could
    be forgotten.
    """
    return claimed if tier_rank(claimed) <= tier_rank(attested) else attested


def dangerous_grants(permissions: Iterable[Permission]) -> list[Permission]:
    """The subset of *permissions* the spec classifies as dangerous."""
    return [
        p
        for p in permissions
        if not p.grants_nothing and f"{p.domain}:{p.level}" in DANGEROUS_GRANTS
    ]


def enforce_tier_ceiling(
    permissions: Sequence[Permission], *, tier: str, skill_name: str
) -> None:
    """Refuse a skill whose declared permissions exceed its tier's ceiling.

    Args:
        permissions: The skill's parsed permissions.
        tier: The **effective** tier (post-:func:`effective_tier`), not the claim.
        skill_name: Named in the error.

    Raises:
        SkillPermissionError: if any permission sits above the ceiling. Nothing is
            installed — the ceiling is checked before anything is written.
        SkillValidationError: for an unknown *tier*.
    """
    allowed = _CEILINGS[_validated_tier(tier)]
    if allowed is None:
        return

    over = [
        p
        for p in permissions
        if not p.grants_nothing and f"{p.domain}:{p.level}" not in allowed
    ]
    if not over:
        return

    declared = ", ".join(str(p) for p in over)
    higher = _lowest_tier_allowing(over)
    remedy = (
        f"Publish or promote it to '{higher}' first"
        if higher
        else "Drop those permissions"
    )
    raise SkillPermissionError(
        f"Skill '{skill_name}' declares permission(s) above the '{tier}' ceiling: "
        f"{declared}. A '{tier}' skill may declare only: "
        f"{', '.join(sorted(allowed)) or '(nothing)'}. {remedy} — a skill cannot "
        f"install above its tier's ceiling. See {FORMAT_DOCS_URL}#security-tiers"
    )


def _lowest_tier_allowing(permissions: Sequence[Permission]) -> str | None:
    """The least-trusted tier whose ceiling covers every permission in *permissions*."""
    for tier in TIER_ORDER:
        allowed = _CEILINGS[tier]
        if allowed is None or all(
            f"{p.domain}:{p.level}" in allowed for p in permissions
        ):
            return tier
    return None


def _validated_tier(tier: str) -> str:
    tier_rank(tier)  # raises SkillValidationError on an unknown tier
    return tier
