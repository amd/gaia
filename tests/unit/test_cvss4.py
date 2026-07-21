# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/cvss4.py.

Anchors the scorer against values confirmed in the FIRST v4 calculator so a
future cvss-library bump that drifts from FIRST fails loudly here.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import cvss4  # noqa: E402

# vector -> (base_score, severity), each verified against
# https://www.first.org/cvss/calculator/4.0
ANCHORS = {
    # The hub tar-slip triage vector — confirmed 6.0 in the calculator.
    "CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:P/VC:N/VI:H/VA:N/SC:N/SI:N/SA:N": (6.0, "Medium"),
    # All-max.
    "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H": (
        10.0,
        "Critical",
    ),
    # No impact.
    "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N": (0.0, "None"),
    # Single high-integrity, network, no UI — the vector the AI triage mis-scored
    # as 7.3; the real value is 8.7 (this is why we don't trust LLM CVSS numbers).
    "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:H/VA:N/SC:N/SI:N/SA:N": (8.7, "High"),
}


@pytest.mark.parametrize("vector,expected", ANCHORS.items())
def test_score_matches_first_calculator(vector, expected):
    result = cvss4.score(vector)
    assert (result["base_score"], result["severity"]) == expected


def test_invalid_vector_raises():
    with pytest.raises(ValueError, match="Invalid CVSS 4.0 vector"):
        cvss4.score("CVSS:4.0/not-a-vector")


def test_cli_outputs_json(capsys):
    rc = cvss4.main(["CVSS:4.0/AV:N/AC:L/AT:P/PR:N/UI:P/VC:N/VI:H/VA:N/SC:N/SI:N/SA:N"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"base_score": 6.0' in out and '"severity": "Medium"' in out
