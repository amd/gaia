# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/findings_to_sarif.py.

Verifies the SARIF shape GitHub code scanning needs: CVSS-derived
security-severity, stable partialFingerprints, and a valid 2.1.0 skeleton.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import findings_to_sarif as f2s  # noqa: E402

TARSLIP_FINDING = {
    "path": "src/gaia/hub/installer.py",
    "line": 382,
    "symbol": "_install_cpp_artifact",
    "cwe": "CWE-22",
    "title": "Unvalidated tarfile.extractall enables path traversal",
    "why": "A crafted archive member escapes the install dir.",
    "evidence": "installer.py:382 tf.extractall(install_dir) with no member check",
    "remediation": "Validate every member resolves inside install_dir.",
    "confidence": "high",
    "cvss_vector": "CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:P/VC:N/VI:H/VA:N/SC:N/SI:N/SA:N",
    "dedup_key": "security:src/gaia/hub/installer.py:_install_cpp_artifact",
}


def test_sarif_skeleton_is_valid():
    sarif = f2s.to_sarif([TARSLIP_FINDING])
    assert sarif["version"] == "2.1.0"
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "gaia-claude-security-audit"
    assert len(run["results"]) == 1


def test_security_severity_from_cvss():
    sarif = f2s.to_sarif([TARSLIP_FINDING])
    rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
    # 6.0 -> GitHub maps 4.0-6.9 to medium; the value must be the real CVSS score.
    assert rule["properties"]["security-severity"] == "6.0"


def test_stable_fingerprint_is_dedup_key_not_line():
    sarif = f2s.to_sarif([TARSLIP_FINDING])
    result = sarif["runs"][0]["results"][0]
    fp = result["partialFingerprints"][f2s.FINGERPRINT_KEY]
    assert fp == TARSLIP_FINDING["dedup_key"]
    # Same finding on a different line keeps the same fingerprint (won't re-fire).
    moved = dict(TARSLIP_FINDING, line=999)
    moved_fp = f2s.to_sarif([moved])["runs"][0]["results"][0]["partialFingerprints"][
        f2s.FINGERPRINT_KEY
    ]
    assert moved_fp == fp


def test_location_carries_path_and_line():
    result = f2s.to_sarif([TARSLIP_FINDING])["runs"][0]["results"][0]
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/gaia/hub/installer.py"
    assert loc["region"]["startLine"] == 382


def test_high_severity_maps_to_error_level():
    crit = dict(
        TARSLIP_FINDING,
        cvss_vector="CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H",
    )
    assert f2s.to_sarif([crit])["runs"][0]["results"][0]["level"] == "error"


def test_missing_required_field_raises():
    bad = dict(TARSLIP_FINDING)
    del bad["cvss_vector"]
    with pytest.raises(ValueError, match="missing required field"):
        f2s.to_sarif([bad])


def test_load_findings_reads_multiple(tmp_path):
    (tmp_path / "findings-a.json").write_text(
        '{"findings": [%s]}' % _json(TARSLIP_FINDING), encoding="utf-8"
    )
    (tmp_path / "findings-b.json").write_text('{"findings": []}', encoding="utf-8")
    loaded = f2s.load_findings([str(tmp_path / "findings-*.json")])
    assert len(loaded) == 1


def _json(obj):
    import json

    return json.dumps(obj)


def _open_alert(finding=TARSLIP_FINDING, number=7):
    """An open alert shaped like the code-scanning alerts API returns it."""
    result = f2s.to_sarif([finding])["runs"][0]["results"][0]
    return {
        "number": number,
        "state": "open",
        "rule": {
            "id": finding["dedup_key"],
            "name": "CWE22",
            "severity": "error",
            "description": finding["title"],
            "tags": ["security", "CWE-22"],
        },
        "most_recent_instance": {
            "category": "claude-security",
            "message": {"text": result["message"]["text"]},
            "location": {"path": finding["path"], "start_line": finding["line"]},
        },
    }


def _repo_with(tmp_path, rel_path):
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return tmp_path


def test_carry_forward_keeps_alert_the_run_did_not_report(tmp_path):
    repo = _repo_with(tmp_path, TARSLIP_FINDING["path"])
    sarif = f2s.to_sarif([])
    assert f2s.carry_forward(sarif, [_open_alert()], repo) == 1

    run = sarif["runs"][0]
    (result,) = run["results"]
    assert result["ruleId"] == TARSLIP_FINDING["dedup_key"]
    assert result["partialFingerprints"] == {
        f2s.FINGERPRINT_KEY: TARSLIP_FINDING["dedup_key"]
    }
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == TARSLIP_FINDING["path"]
    assert loc["region"]["startLine"] == 382
    (rule,) = run["tool"]["driver"]["rules"]
    expected = f2s.to_sarif([TARSLIP_FINDING])["runs"][0]["tool"]["driver"]["rules"][0]
    assert (
        rule["properties"]["security-severity"]
        == expected["properties"]["security-severity"]
    )


def test_carry_forward_skips_alert_the_run_reported_again(tmp_path):
    repo = _repo_with(tmp_path, TARSLIP_FINDING["path"])
    sarif = f2s.to_sarif([TARSLIP_FINDING])
    assert f2s.carry_forward(sarif, [_open_alert()], repo) == 0
    assert len(sarif["runs"][0]["results"]) == 1
    assert len(sarif["runs"][0]["tool"]["driver"]["rules"]) == 1


def test_carry_forward_lets_alert_close_when_file_is_gone(tmp_path):
    sarif = f2s.to_sarif([])
    assert f2s.carry_forward(sarif, [_open_alert()], tmp_path) == 0
    assert sarif["runs"][0]["results"] == []


def test_carry_forward_refuses_alert_without_cvss_score(tmp_path):
    repo = _repo_with(tmp_path, TARSLIP_FINDING["path"])
    alert = _open_alert()
    alert["most_recent_instance"]["message"]["text"] = "no score here"
    with pytest.raises(ValueError, match="no 'CVSS 4.0: <score>'"):
        f2s.carry_forward(f2s.to_sarif([]), [alert], repo)


def test_load_alerts_reads_compact_and_pretty_json(tmp_path):
    import json

    alerts_file = tmp_path / "open-alerts.jsonl"
    alerts_file.write_text(
        json.dumps(_open_alert(number=1))
        + "\n"
        + json.dumps(_open_alert(number=2), indent=2)
        + "\n",
        encoding="utf-8",
    )
    assert [a["number"] for a in f2s.load_alerts(str(alerts_file))] == [1, 2]
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert f2s.load_alerts(str(tmp_path / "empty.jsonl")) == []


def test_main_carries_forward_into_written_sarif(tmp_path):
    import json

    repo = _repo_with(tmp_path / "repo", TARSLIP_FINDING["path"])
    (tmp_path / "findings-a.json").write_text('{"findings": []}', encoding="utf-8")
    alerts_file = tmp_path / "open-alerts.jsonl"
    alerts_file.write_text(json.dumps(_open_alert()) + "\n", encoding="utf-8")
    out = tmp_path / "out.sarif"

    rc = f2s.main(
        [
            str(tmp_path / "findings-*.json"),
            "-o",
            str(out),
            "--carry-forward",
            str(alerts_file),
            "--repo-root",
            str(repo),
        ]
    )
    assert rc == 0
    results = json.loads(out.read_text(encoding="utf-8"))["runs"][0]["results"]
    assert [r["ruleId"] for r in results] == [TARSLIP_FINDING["dedup_key"]]


def test_carry_forward_keeps_both_alerts_two_lenses_filed_under_one_key(tmp_path):
    repo = _repo_with(tmp_path, TARSLIP_FINDING["path"])
    first, second = _open_alert(number=1), _open_alert(number=2)
    second["most_recent_instance"]["message"]["text"] = (
        "A second lens's wording. " + first["most_recent_instance"]["message"]["text"]
    )
    sarif = f2s.to_sarif([])
    assert f2s.carry_forward(sarif, [first, second], repo) == 2

    run = sarif["runs"][0]
    assert [r["message"]["text"] for r in run["results"]] == [
        first["most_recent_instance"]["message"]["text"],
        second["most_recent_instance"]["message"]["text"],
    ]
    assert len(run["tool"]["driver"]["rules"]) == 1


def test_carry_forward_skips_every_alert_whose_key_the_run_reported(tmp_path):
    repo = _repo_with(tmp_path, TARSLIP_FINDING["path"])
    sarif = f2s.to_sarif([TARSLIP_FINDING])
    alerts = [_open_alert(number=1), _open_alert(number=2)]
    assert f2s.carry_forward(sarif, alerts, repo) == 0
    assert len(sarif["runs"][0]["results"]) == 1
