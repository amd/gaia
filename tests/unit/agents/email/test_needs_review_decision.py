# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the shared ``needs_review_decision`` routing rule (#2582).

Extracted out of ``pre_scan_inbox_impl``'s inline bucketing so the attention-
view aggregator (#2582) and the pre-scan envelope can never silently diverge
on which triage results count as needs-review — a second hand-copied version
of this rule would not have picked up #2584's narrowing (URGENT/
NEEDS_RESPONSE never demote regardless of confidence) automatically.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.tools.read_tools import needs_review_decision  # noqa: E402


def _r(**overrides):
    base = {
        "category": "FYI",
        "confident": True,
        "is_spam": False,
        "is_phishing": False,
    }
    base.update(overrides)
    return base


class TestNeedsReviewDecision:
    def test_confident_fyi_is_not_needs_review(self):
        assert needs_review_decision(_r(category="FYI", confident=True)) is False

    def test_unconfident_fyi_is_needs_review(self):
        assert needs_review_decision(_r(category="FYI", confident=False)) is True

    def test_unconfident_personal_is_needs_review(self):
        assert needs_review_decision(_r(category="PERSONAL", confident=False)) is True

    def test_unconfident_promotional_is_needs_review(self):
        assert (
            needs_review_decision(_r(category="PROMOTIONAL", confident=False)) is True
        )

    def test_confident_promotional_is_not_needs_review(self):
        assert (
            needs_review_decision(_r(category="PROMOTIONAL", confident=True)) is False
        )

    def test_unconfident_urgent_never_demotes(self):
        # #2584: an unconfident guess must never make a message LESS visible
        # than a confident one would — URGENT keeps its own bucket.
        assert needs_review_decision(_r(category="URGENT", confident=False)) is False

    def test_unconfident_needs_response_never_demotes(self):
        assert (
            needs_review_decision(_r(category="NEEDS_RESPONSE", confident=False))
            is False
        )

    def test_spam_never_needs_review_even_if_unconfident(self):
        assert (
            needs_review_decision(_r(category="FYI", confident=False, is_spam=True))
            is False
        )

    def test_phishing_never_needs_review_even_if_unconfident(self):
        assert (
            needs_review_decision(_r(category="FYI", confident=False, is_phishing=True))
            is False
        )

    def test_missing_confident_key_defaults_to_true(self):
        r = {"category": "FYI", "is_spam": False, "is_phishing": False}
        assert needs_review_decision(r) is False
