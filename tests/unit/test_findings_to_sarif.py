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
