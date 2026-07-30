# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The pre-publish security audit gate for skills (issue #2468).

A skill ships instructions that are injected into model context and, optionally,
code that is registered into an agent's tool registry. Both are untrusted input.
This package is the **analysis** half of the marketplace gate: it scans a skill
directory and emits an ``ALLOW`` / ``REVIEW`` / ``BLOCK`` verdict in the shared
:class:`~gaia.governance.schemas.GovernanceDecision` vocabulary.

The **enforcement** half lives in the hub Worker
(``workers/agent-hub/src/audit.ts``), which consumes the report this package
produces. Nothing here talks to the network or runs the skill's code — the gate
is entirely static, by design. Runtime sandboxing is Phase 2.

Usage::

    from gaia.skills.audit import audit_skill

    report = audit_skill("./my-skill/")
    print(report.verdict)               # ALLOW | REVIEW | BLOCK
    for finding in report.findings:
        print(finding.location, finding.severity, finding.message)

Or from the CLI::

    gaia skill audit ./my-skill/ --json
"""

from gaia.skills.audit.code import CodeAnalysis, DomainUse, analyze_code, analyze_source
from gaia.skills.audit.engine import audit_skill, audit_skill_object
from gaia.skills.audit.findings import (
    AUDIT_ENGINE,
    CATEGORIES,
    CATEGORY_DANGEROUS_CALL,
    CATEGORY_PERMISSION_TRUTH,
    CATEGORY_PROMPT_INJECTION,
    CATEGORY_SUPPLY_CHAIN,
    CATEGORY_TIER_CLAIM,
    SEVERITY_ORDER,
    AuditReport,
    Finding,
    Severity,
    auditable_files,
    content_digest,
    python_sources,
    worst_severity,
)
from gaia.skills.audit.instructions import analyze_instructions
from gaia.skills.audit.permission_truth import (
    check_permission_truth,
    observed_permission_strings,
    undeclared_domains,
)
from gaia.skills.audit.report import render_json, render_sarif, render_text
from gaia.skills.audit.supply import check_supply_chain
from gaia.skills.audit.verdict import (
    TIER_BLOCK_THRESHOLD,
    TIER_REVIEW_THRESHOLD,
    TIER_TRUST_ORDER,
    TIERS_REQUIRING_AUDIT,
    TIERS_REQUIRING_HUMAN_AUDIT,
    cleared_tiers,
    clears_tier,
    highest_cleared_tier,
    report_is_stale,
    severity_verdict,
    verdict_for_tier,
)

__all__ = [
    # Rendering
    "render_text",
    "render_json",
    "render_sarif",
    # Engine
    "audit_skill",
    "audit_skill_object",
    # Analyzers
    "analyze_code",
    "analyze_source",
    "analyze_instructions",
    "check_permission_truth",
    "check_supply_chain",
    "observed_permission_strings",
    "undeclared_domains",
    "CodeAnalysis",
    "DomainUse",
    # Verdict / tier gating
    "verdict_for_tier",
    "severity_verdict",
    "clears_tier",
    "cleared_tiers",
    "highest_cleared_tier",
    "report_is_stale",
    "TIER_BLOCK_THRESHOLD",
    "TIER_REVIEW_THRESHOLD",
    "TIER_TRUST_ORDER",
    "TIERS_REQUIRING_AUDIT",
    "TIERS_REQUIRING_HUMAN_AUDIT",
    "AuditReport",
    "Finding",
    "Severity",
    "AUDIT_ENGINE",
    "SEVERITY_ORDER",
    "CATEGORIES",
    "CATEGORY_DANGEROUS_CALL",
    "CATEGORY_PERMISSION_TRUTH",
    "CATEGORY_SUPPLY_CHAIN",
    "CATEGORY_PROMPT_INJECTION",
    "CATEGORY_TIER_CLAIM",
    "worst_severity",
    "content_digest",
    "auditable_files",
    "python_sources",
]
