# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hermetic tests for the #2087 bulk-triage result-envelope condenser.

The bulk-triage agent-loop path re-reads its entire result envelope on the next
turn; for a large batch the verbatim verdict list overflows
``CONTEXT_TARGET_TOKENS``. ``condense_triage_result`` bounds that envelope while
keeping verdicts batch-independent and omissions explicit.

Covered:
- ``envelope_budget_tokens`` concrete value + reserve arithmetic
- no-op below budget (identity return, byte-for-byte unchanged)
- condense above budget: result fits the budget, exemplars kept in order,
  omission count is exact and explicit (no silent clip)
- ``grouped`` / ``usage`` / ``llm_classified_count`` preserved verbatim
- determinism (same input -> same condensed output)
- the AC-4 overflow-guard differential proof

All tests are pure Python — no chat, no Lemonade, no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.context_budget import (  # noqa: E402
    CONTEXT_TARGET_TOKENS,
    envelope_budget_tokens,
    estimate_tokens_json,
    _AGENT_LOOP_FIXED_TOKENS,
    _RESPONSE_RESERVE_TOKENS,
)
from gaia_agent_email.tools.triage_condense import (  # noqa: E402
    _estimate_envelope_tokens,
    condense_triage_result,
)


def _make_result(n: int) -> dict:
    """Build a triage result dict with ``n`` verbose per-message verdicts.

    Each verdict carries the same verbose fields the real path emits (subject /
    from / rationale) so the token math mirrors production, and ``grouped``
    holds the compact id-to-category map.
    """
    results = []
    groups: dict[str, list[str]] = {"URGENT": [], "FYI": []}
    for i in range(n):
        mid = f"msg-{i:05d}-abcdef"
        cat = "URGENT" if i % 2 == 0 else "FYI"
        results.append(
            {
                "id": mid,
                "thread_id": f"thr-{i:05d}",
                "subject": f"Subject line number {i} about quarterly planning",
                "from": f"Sender {i} <sender{i}@example.com>",
                "category": cat,
                "is_spam": False,
                "is_phishing": False,
                "confident": True,
                "rationale": (
                    f"Classified as {cat} because the body of message {i} "
                    "matched the heuristic signal for this category."
                ),
                "source": "heuristic",
            }
        )
        groups[cat].append(mid)
    return {
        "results": results,
        "grouped": {
            "groups": groups,
            "spam": [],
            "phishing": [],
            "total": n,
        },
        "usage": {"input_tokens": 1200 * n, "output_tokens": 40 * n},
        "llm_classified_count": n,
    }


class TestEnvelopeBudget:
    def test_concrete_value(self):
        assert envelope_budget_tokens() == 16384 - 9216 - 1024
        assert envelope_budget_tokens() == 6144

    def test_equals_target_minus_named_reserves(self):
        assert _AGENT_LOOP_FIXED_TOKENS == 9216
        assert _RESPONSE_RESERVE_TOKENS == 1024
        assert envelope_budget_tokens() == (
            CONTEXT_TARGET_TOKENS - _AGENT_LOOP_FIXED_TOKENS - _RESPONSE_RESERVE_TOKENS
        )

    def test_positive_and_below_target(self):
        budget = envelope_budget_tokens()
        assert isinstance(budget, int)
        assert 0 < budget < CONTEXT_TARGET_TOKENS


class TestJsonCalibrationPin:
    """Pins the #2087 CI failure: at limit 60 the post-tool turn 400'd at
    19,815 tokens vs 16,384 because the prose estimator (chars//4) said the
    ~11.4K-real-token envelope fit the budget, so the condenser no-op'd. The
    JSON-calibrated estimator must put that same envelope over budget."""

    def test_sixty_email_envelope_must_condense(self):
        result = _make_result(60)
        # A no-op here re-creates the real-hardware 400.
        assert _estimate_envelope_tokens(result) > envelope_budget_tokens()
        out = condense_triage_result(result)
        assert out is not result
        assert out["results_condensed"] is True
        assert _estimate_envelope_tokens(out) <= envelope_budget_tokens()

    def test_json_estimator_assumes_at_most_1_3_chars_per_token(self):
        # Hardware round 2: the hex-id-heavy grouped map tokenizes at
        # ~1.4 chars/token (a chars//2 estimate still overflowed by 672 at
        # limit 300). The estimator must assume <= 1.3 so it over-counts —
        # the safe direction for a gate.
        s = json.dumps(_make_result(60), default=str)
        assert estimate_tokens_json(s) * 13 >= len(s) * 10


class TestNoOpBelowBudget:
    def test_small_batch_returns_identical_object(self):
        result = _make_result(5)
        out = condense_triage_result(result)
        # Below budget -> the SAME object, byte-for-byte unchanged.
        assert out is result
        assert "results_condensed" not in out
        assert len(out["results"]) == 5

    def test_no_op_when_it_exactly_fits(self):
        result = _make_result(3)
        assert _estimate_envelope_tokens(result) <= envelope_budget_tokens()
        assert condense_triage_result(result) is result


class TestCondenseAboveBudget:
    def test_large_batch_is_condensed_and_fits(self):
        result = _make_result(300)
        assert _estimate_envelope_tokens(result) > envelope_budget_tokens()

        out = condense_triage_result(result)

        assert out is not result
        assert out["results_condensed"] is True
        # AC-1 in miniature: the condensed envelope fits the budget.
        assert _estimate_envelope_tokens(out) <= envelope_budget_tokens()

    def test_omission_count_is_exact_and_explicit(self):
        result = _make_result(300)
        out = condense_triage_result(result)

        kept = len(out["results"])
        omitted = out["results_omitted"]
        # No silent clip: kept + omitted accounts for every verdict.
        assert kept + omitted == 300
        assert omitted > 0
        assert out["results_total"] == 300
        assert f"omitted {omitted}" in out["note"]
        assert "grouped" in out["note"]

    def test_exemplars_are_the_leading_verdicts_in_order(self):
        result = _make_result(300)
        out = condense_triage_result(result)
        kept = out["results"]
        assert kept == result["results"][: len(kept)]

    def test_grouped_and_usage_preserved_verbatim(self):
        result = _make_result(300)
        out = condense_triage_result(result)
        # grouped carries the full id-to-category map -> nothing truly lost.
        assert out["grouped"] == result["grouped"]
        assert out["grouped"]["total"] == 300
        # #1891: usage accounting must survive condensing unchanged.
        assert out["usage"] == result["usage"]
        assert out["llm_classified_count"] == 300

    def test_deterministic(self):
        r1, r2 = _make_result(300), _make_result(300)
        out1 = condense_triage_result(r1)
        out2 = condense_triage_result(r2)
        assert json.dumps(out1, sort_keys=True, default=str) == json.dumps(
            out2, sort_keys=True, default=str
        )

    def test_tiny_budget_omits_all_but_stays_explicit(self):
        # Even a budget too small for any exemplar must not silently truncate:
        # zero kept, all omitted, marker present, grouped intact.
        result = _make_result(50)
        out = condense_triage_result(result, budget_tokens=1)
        assert out["results"] == []
        assert out["results_omitted"] == 50
        assert "omitted 50" in out["note"]
        assert out["grouped"]["total"] == 50


class TestOverflowGuardDifferential:
    """AC-4: the same fake-overflow differential proof shape as #1889's fold.

    A batch that provably overflows the budget verbatim must, after condensing,
    provably fit it — and the per-message verdicts kept must be byte-identical
    to the unchunked run (verdicts are batch-independent).
    """

    def test_overflow_before_fit_after(self):
        result = _make_result(200)
        budget = envelope_budget_tokens()

        # Before: verbatim envelope overflows.
        assert estimate_tokens_json(json.dumps(result, default=str)) > budget

        out = condense_triage_result(result)

        # After: condensed envelope fits.
        assert estimate_tokens_json(json.dumps(out, default=str)) <= budget

        # Differential: every kept verdict is identical to the source verdict at
        # the same index — condensing never mutates a verdict, only drops the
        # tail.
        for i, kept in enumerate(out["results"]):
            assert kept == result["results"][i]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
