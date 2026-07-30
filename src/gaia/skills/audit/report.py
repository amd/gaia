# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Rendering an audit report for its three audiences.

- :func:`render_text` — the author at a terminal, who needs ``file:line`` and
  the fix.
- :func:`render_json` — the publish path, which needs the exact wire payload the
  hub Worker parses.
- :func:`render_sarif` — GitHub code scanning, which is the **private** channel
  the repo's security-disclosure policy requires: findings land in the
  access-controlled Security tab rather than a public PR comment.

Every renderer withholds :attr:`Finding.snippet` unless a caller opts in, so the
offending source text never reaches an artifact by default.
"""

from __future__ import annotations

import json

from gaia.skills.audit.findings import (
    AUDIT_ENGINE,
    SEVERITY_ORDER,
    AuditReport,
    Severity,
)

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/"
    "sarif-schema-2.1.0.json"
)

#: GAIA severity -> SARIF level.
_SARIF_LEVEL: dict[Severity, str] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

_VERDICT_ICON = {"ALLOW": "✅", "REVIEW": "⏸️", "BLOCK": "❌"}


def _severity_rank(severity: Severity) -> int:
    return SEVERITY_ORDER.index(severity)


def _sorted_findings(report: AuditReport):
    """Worst first, then by file and line, so the report reads top-down."""
    return sorted(
        report.findings,
        key=lambda f: (-_severity_rank(f.severity), f.file, f.line, f.rule_id),
    )


def render_text(report: AuditReport, *, include_snippets: bool = False) -> str:
    """Render a human-readable report for the skill's author."""
    lines: list[str] = []
    icon = _VERDICT_ICON.get(report.verdict, "")
    lines.append(
        f"{report.verdict} {icon}  {report.skill} {report.version or ''}".rstrip()
    )
    lines.append(f"  {report.reason}")
    lines.append("")
    lines.append(f"  claimed tier : {report.security_tier}")
    lines.append(f"  cleared tiers: {', '.join(report.cleared_tiers) or 'none'}")
    lines.append(f"  engine       : {report.engine}")
    lines.append(f"  audited at   : {report.audited_at}")
    if report.content_digest:
        lines.append(f"  content      : {report.content_digest}")

    counts = report.counts_by_severity()
    if counts:
        summary = ", ".join(f"{count} {severity}" for severity, count in counts.items())
        lines.append(f"  findings     : {summary}")
    lines.append("")

    if not report.findings:
        lines.append("  No findings.")
        lines.append("")
        return "\n".join(lines)

    for finding in _sorted_findings(report):
        lines.append(f"  [{finding.severity}] {finding.rule_id}  {finding.location}")
        lines.append(f"      {finding.message}")
        if finding.remediation:
            lines.append(f"      fix: {finding.remediation}")
        if include_snippets and finding.snippet:
            lines.append(f"      code: {finding.snippet}")
        lines.append("")

    if not include_snippets and any(f.snippet for f in report.findings):
        lines.append(
            "  (Offending source text withheld. Re-run with --show-snippets to "
            "see it locally; it is deliberately kept out of shared artifacts.)"
        )
        lines.append("")

    return "\n".join(lines)


def render_json(report: AuditReport, *, include_snippets: bool = False) -> str:
    """Render the wire payload the hub Worker's ``audit`` form part carries."""
    return json.dumps(
        report.to_dict(include_snippets=include_snippets), indent=2, sort_keys=False
    )


def render_sarif(
    report: AuditReport,
    *,
    include_snippets: bool = False,
    path_prefix: str = "",
) -> str:
    """Render SARIF 2.1.0 for upload to GitHub code scanning.

    Args:
        report: The audit report.
        include_snippets: Include the offending source text (off by default).
        path_prefix: Repository-relative directory of the skill, so the results
            point at real paths when the skill is nested in a checkout.
    """
    rule_ids: list[str] = []
    rules: list[dict] = []
    for finding in report.findings:
        if finding.rule_id in rule_ids:
            continue
        rule_ids.append(finding.rule_id)
        rules.append(
            {
                "id": finding.rule_id,
                "name": finding.rule_id,
                "shortDescription": {"text": finding.message},
                "fullDescription": {
                    "text": f"{finding.message} {finding.remediation}".strip()
                },
                "defaultConfiguration": {
                    "level": _SARIF_LEVEL.get(finding.severity, "warning")
                },
                "properties": {
                    "category": finding.category,
                    "problem.severity": finding.severity,
                    "tags": ["security", "skill-audit", finding.category],
                },
            }
        )

    results: list[dict] = []
    for finding in _sorted_findings(report):
        text = finding.message
        if finding.remediation:
            text = f"{text} {finding.remediation}"
        if include_snippets and finding.snippet:
            text = f"{text} [{finding.snippet}]"
        results.append(
            {
                "ruleId": finding.rule_id,
                "ruleIndex": rule_ids.index(finding.rule_id),
                "level": _SARIF_LEVEL.get(finding.severity, "warning"),
                "message": {"text": text},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": _uri(finding.file, path_prefix)
                            },
                            # SARIF regions are 1-indexed; a manifest-level
                            # finding carries no line of its own.
                            "region": {"startLine": max(1, finding.line)},
                        }
                    }
                ],
            }
        )

    sarif = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "gaia-skill-audit",
                        "version": AUDIT_ENGINE.split("/", 1)[1],
                        "informationUri": "https://github.com/amd/gaia/issues/2468",
                        "rules": rules,
                    }
                },
                "results": results,
                "properties": {
                    "skill": report.skill,
                    "version": report.version,
                    "verdict": report.verdict,
                    "security_tier": report.security_tier,
                    "cleared_tiers": list(report.cleared_tiers),
                },
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def _uri(file: str, prefix: str) -> str:
    if not file:
        return prefix or "SKILL.md"
    if not prefix:
        return file
    return f"{prefix.rstrip('/')}/{file}"
