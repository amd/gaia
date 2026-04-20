# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.coder.self_fix.continuous_critique``."""

from __future__ import annotations

import json

from gaia.coder.self_fix.continuous_critique import (
    HIGH_CONFIDENCE_THRESHOLD,
    MIN_CRITIQUE_CONFIDENCE,
    critique_recent_output,
)


def _client_returning(payload: dict):
    def client(**_kwargs):
        return json.dumps(payload)

    return client


def test_continuous_critique_suppresses_low_confidence() -> None:
    """Findings with confidence < 60 must be dropped entirely."""
    payload = {
        "findings": [
            {
                "severity": "high",
                "citation": "GAIA.md:principle-3",
                "fix_direction": "do the right thing",
                "confidence": 45,
            },
            {
                "severity": "med",
                "citation": "mem:fp-123",
                "fix_direction": "avoid repeat pattern",
                "confidence": 72,
            },
        ],
        "most_impactful": {
            "severity": "high",
            "citation": "GAIA.md:principle-3",
            "fix_direction": "do the right thing",
            "confidence": 45,
        },
    }
    result = critique_recent_output(
        success_criterion="ship the fix",
        recent_output="some edit diff",
        memory_hits=[],
        client=_client_returning(payload),
    )
    # Only the 72-confidence finding survives.
    assert len(result.findings) == 1
    assert result.findings[0].confidence == 72
    # Most-impactful was below threshold → dropped too.
    assert result.most_impactful is None


def test_continuous_critique_preserves_high_confidence() -> None:
    """Findings ≥ HIGH_CONFIDENCE_THRESHOLD land in the high-confidence subset."""
    payload = {
        "findings": [
            {
                "severity": "high",
                "citation": "mem:fp-1",
                "fix_direction": "fix X",
                "confidence": 91,
            }
        ],
        "most_impactful": {
            "severity": "high",
            "citation": "mem:fp-1",
            "fix_direction": "fix X",
            "confidence": 91,
        },
    }
    result = critique_recent_output(
        success_criterion="s",
        recent_output="o",
        client=_client_returning(payload),
    )
    assert len(result.findings) == 1
    assert result.high_confidence_findings == result.findings
    assert result.findings[0].confidence >= HIGH_CONFIDENCE_THRESHOLD
    assert result.most_impactful is not None


def test_continuous_critique_empty_response() -> None:
    """Empty LLM output is legal (§7.2: empty list is valid)."""
    result = critique_recent_output(
        success_criterion="s",
        recent_output="o",
        client=lambda **_: "",
    )
    assert result.findings == ()
    assert result.most_impactful is None


def test_continuous_critique_threshold_constants() -> None:
    """Contract: 60 / 80 thresholds are the public numbers (§7.2)."""
    assert MIN_CRITIQUE_CONFIDENCE == 60
    assert HIGH_CONFIDENCE_THRESHOLD == 80
