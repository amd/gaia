# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Audit orchestration — runs every analyzer and produces the verdict.

The pipeline is deliberately dull: parse, scan, diff, gate. Nothing here
executes the skill's code, imports its modules, or touches the network, so
auditing a hostile skill is safe. That is why the gate is static and why the
runtime sandbox is a separate phase.

Analyzer order matters only for readability; the verdict depends on the union of
their findings, gated by the tier the skill claims.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from gaia.logger import get_logger
from gaia.skills.audit.code import analyze_code
from gaia.skills.audit.findings import (
    CATEGORY_TIER_CLAIM,
    AuditReport,
    Finding,
    content_digest,
    manifest_digest,
    python_sources,
)
from gaia.skills.audit.instructions import analyze_instructions
from gaia.skills.audit.permission_truth import check_permission_truth
from gaia.skills.audit.supply import check_supply_chain
from gaia.skills.audit.verdict import (
    TIERS_REQUIRING_HUMAN_AUDIT,
    cleared_tiers,
    verdict_for_tier,
)
from gaia.skills.format import SKILL_FILENAME, Skill, parse_skill_file

log = get_logger(__name__)

#: Source label for findings in the frontmatter's description field.
DESCRIPTION_SOURCE = f"{SKILL_FILENAME} (description)"


def audit_skill(directory: Path | str) -> AuditReport:
    """Audit a skill directory and return its report.

    Args:
        directory: The skill directory (the one containing ``SKILL.md``).

    Returns:
        An :class:`~gaia.skills.audit.findings.AuditReport` whose verdict is
        ``ALLOW``, ``REVIEW``, or ``BLOCK``.

    Raises:
        SkillValidationError: if the directory holds no parseable ``SKILL.md``.
            A skill that does not parse is not silently passed — there is
            nothing to audit and nothing to publish.
    """
    directory = Path(directory)
    # check_directory_name=False: the audit runs on unpacked bundles and CI
    # checkouts where the folder name is not the author's choice.
    skill = parse_skill_file(directory, check_directory_name=False)
    return audit_skill_object(skill, directory=directory)


def audit_skill_object(skill: Skill, *, directory: Optional[Path] = None) -> AuditReport:
    """Audit an already-parsed :class:`Skill`.

    Args:
        skill: The parsed skill.
        directory: Its directory. Defaults to ``skill.directory``; when neither
            is available only the instruction analyzers run (there are no files
            to scan).
    """
    directory = Path(directory) if directory is not None else skill.directory

    findings: list[Finding] = []

    # --- instruction analysis (every skill) -----------------------------
    # Both the body and the description reach the model: the description is
    # resident from discovery onward, the body once the skill triggers.
    findings.extend(analyze_instructions(skill.body))
    findings.extend(
        analyze_instructions(skill.description, filename=DESCRIPTION_SOURCE)
    )

    # --- code analysis (tool skills) ------------------------------------
    if directory is not None and directory.is_dir():
        analysis = analyze_code(directory)
        findings.extend(analysis.findings)
        findings.extend(check_permission_truth(skill.parsed_permissions(), analysis))
        findings.extend(
            check_supply_chain(
                skill.gaia.requirements.dependencies,
                skill.gaia.requirements.node_dependencies,
                analysis,
                local_modules=_local_modules(directory),
            )
        )
        digest = content_digest(directory)
        manifest = manifest_digest(
            (directory / SKILL_FILENAME).read_text(encoding="utf-8")
        )
    else:
        log.debug(
            "Auditing skill '%s' without a directory: instruction analyzers only.",
            skill.name,
        )
        findings.extend(
            check_supply_chain(
                skill.gaia.requirements.dependencies,
                skill.gaia.requirements.node_dependencies,
                _empty_analysis(),
            )
        )
        digest = ""
        manifest = manifest_digest(skill.to_markdown())

    tier = skill.security_tier
    verdict, reason = verdict_for_tier(findings, tier)
    cleared = cleared_tiers(findings)

    # Explain the tier outcome in the findings list. These are advisory by
    # construction ('info' gates nothing at any tier), so appending them after
    # the gate cannot change the verdict they describe.
    findings.extend(_tier_claim_findings(tier, cleared))

    return AuditReport(
        skill=skill.name,
        version=skill.version,
        security_tier=tier,
        verdict=verdict,
        reason=reason,
        findings=tuple(findings),
        cleared_tiers=cleared,
        content_digest=digest,
        manifest_digest=manifest,
    )


def _tier_claim_findings(tier: str, cleared: tuple[str, ...]) -> list[Finding]:
    """Advisory findings explaining why a claimed tier was or was not cleared."""
    if tier in cleared:
        return []

    if tier in TIERS_REQUIRING_HUMAN_AUDIT:
        return [
            Finding(
                rule_id="tier.human_audit_required",
                severity="info",
                category=CATEGORY_TIER_CLAIM,
                message=(
                    f"Claims the '{tier}' tier, which an automated scan cannot "
                    "grant on its own."
                ),
                file=SKILL_FILENAME,
                line=0,
                remediation=(
                    f"Publish as 'community' — the automated gate can clear that "
                    f"— or request the '{tier}' audit. This engine reports "
                    "whether the scan passed; it does not sign off on the top "
                    "tier."
                ),
            )
        ]

    highest = cleared[-1] if cleared else "none"
    return [
        Finding(
            rule_id="tier.not_cleared",
            severity="info",
            category=CATEGORY_TIER_CLAIM,
            message=(
                f"Claims the '{tier}' tier but the findings only clear "
                f"'{highest}'."
            ),
            file=SKILL_FILENAME,
            line=0,
            remediation=(
                f"Fix the findings above to earn '{tier}', or set "
                f"metadata.gaia.security_tier to '{highest}'. The publish path "
                "compares the claimed tier against the tiers this report says "
                "were cleared, so an unearned claim is refused rather than "
                "recorded."
            ),
        )
    ]


def _local_modules(directory: Path) -> list[str]:
    """Module names provided by Python files inside the skill itself."""
    modules: list[str] = []
    for path in python_sources(directory):
        relative = path.relative_to(directory)
        modules.append(relative.parts[0].removesuffix(".py"))
    return modules


def _empty_analysis():
    from gaia.skills.audit.code import CodeAnalysis

    return CodeAnalysis()
