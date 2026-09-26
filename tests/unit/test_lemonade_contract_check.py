# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The candidate smoke check has to FAIL on the breaks it exists to catch.

``util/check_lemonade_contract.py`` runs against a Lemonade *candidate* build a
week before it ships. A checker that only ever passes would be worse than none:
it would launder an upcoming break into a green job.

Both regressions modelled here are real. v2026.39.1 changed the version format
and GAIA's gates went quiet instead of loud; 9.1.4 moved ``ctx_size`` out of the
top level of ``/health``.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

from check_lemonade_contract import (  # noqa: E402
    REQUIRED_HEALTH_KEYS,
    check_health_shape,
    check_loaded_model_shape,
    check_version_gate,
)

HEALTHY = {"version": "2026.39.1", "all_models_loaded": [], "status": "ok"}


# -- the health payload shape ------------------------------------------------


def test_a_healthy_payload_produces_no_findings():
    assert check_health_shape(HEALTHY) == []


@pytest.mark.parametrize("dropped", REQUIRED_HEALTH_KEYS)
def test_dropping_any_required_key_is_reported(dropped):
    payload = {k: v for k, v in HEALTHY.items() if k != dropped}
    findings = check_health_shape(payload)
    assert len(findings) == 1
    assert dropped in findings[0]


# -- the version gate --------------------------------------------------------


@pytest.mark.parametrize(
    "version",
    [
        "2026.39.1",  # a CalVer release
        "2026.39.0~12.abc1234",  # a CalVer development build
        "11.9.0",  # the last semver release
    ],
)
def test_versions_gaia_can_gate_on_pass(version):
    assert check_version_gate(version) == []


def test_an_unparseable_version_is_a_failure_not_a_shrug():
    """The v2026.39.1 regression: the gate stopped checking and said nothing.

    Everywhere else in GAIA an unparseable version is deliberately non-blocking.
    Here it must be loud — catching exactly that is the job.
    """
    findings = check_version_gate("not-a-version")
    assert len(findings) == 1
    assert "cannot parse" in findings[0]


def test_a_missing_version_is_a_failure():
    findings = check_version_gate(None)
    assert len(findings) == 1
    assert "no version" in findings[0]


def test_a_version_below_the_floor_is_reported():
    findings = check_version_gate("9.1.4")
    assert len(findings) == 1
    assert "floor" in findings[0]


@pytest.mark.parametrize("version", ["not-a-version", "9.1.4"])
def test_findings_quote_the_version_so_the_filed_issue_is_actionable(version):
    """The failure lands in an issue nobody was watching for — it must say what it saw."""
    findings = check_version_gate(version)
    assert findings, f"expected a finding for {version!r}"
    assert version in findings[0]


# -- the nested shape that actually moved in 9.1.4 --------------------------
# The top-level keys were never the regression. ctx_size moved OUT of the top
# level into all_models_loaded[].recipe_options, so that is the shape worth
# checking — and a freshly started server has no model loaded, which is why the
# check reports whether it ran instead of silently passing.

LOADED = {
    "all_models_loaded": [
        {"model_name": "Gemma-4-E4B-it-GGUF", "recipe_options": {"ctx_size": 65536}}
    ]
}


def test_an_intact_loaded_model_passes_and_counts_as_checked():
    findings, checked = check_loaded_model_shape(LOADED)
    assert findings == []
    assert checked is True


def test_no_model_loaded_is_reported_as_unchecked_not_as_a_pass():
    """The distinction this whole script exists to preserve."""
    for payload in ({"all_models_loaded": []}, {}):
        findings, checked = check_loaded_model_shape(payload)
        assert findings == []
        assert checked is False


def test_ctx_size_moving_back_out_of_recipe_options_is_caught():
    """A 9.1.4-style rename — the exact regression cited as motivation."""
    payload = {
        "all_models_loaded": [
            {"model_name": "m", "ctx_size": 65536}  # hoisted back to the top
        ]
    }
    findings, checked = check_loaded_model_shape(payload)
    assert checked is True
    assert len(findings) == 1
    assert "recipe_options" in findings[0]


def test_ctx_size_renamed_inside_recipe_options_is_caught():
    payload = {"all_models_loaded": [{"recipe_options": {"context_size": 65536}}]}
    findings, checked = check_loaded_model_shape(payload)
    assert checked is True
    assert len(findings) == 1
    assert "ctx_size" in findings[0]
