# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Precision gate for phishing detection.

Runs ``_looks_phishing_full`` (which evaluates subject + sender + body
signals) against the synthetic phishing/ham fixture and enforces a hard
precision gate of >= 90%.

This is a LOCAL, DETERMINISTIC test — no LLM, no Lemonade.

Fixture: tests/fixtures/email/phishing_fixture.json
  - 30 phishing examples (clear credential-harvesting / account-takeover attempts)
  - 42 ham examples (legit "verify your email", password-reset, shipping,
    calendar invites, onboarding) — the precision trap: many look phishy
    on the surface

Gate semantics:
  precision = TP / (TP + FP)
  recall    = TP / (TP + FN)
  FP-rate   = FP / (FP + TN)

Precision is primary: a false positive (flagging a legit onboarding email
as phishing) destroys user trust. Recall is secondary: the plan calls for
a conservative detector that is sure when it fires.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make src importable when invoked directly.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from gaia.agents.email.tools.triage_heuristics import detect_phishing  # noqa: E402
from gaia.eval.quality_metrics import Confusion  # noqa: E402

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "tests/fixtures/email/phishing_fixture.json"
)

PRECISION_GATE = 0.90  # hard gate — test fails if precision < 90%


@pytest.fixture(scope="module")
def fixture_records():
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        records = json.load(f)
    assert records, "phishing_fixture.json must not be empty"
    return records


@pytest.fixture(scope="module")
def confusion(fixture_records):
    """Run the detector over the fixture and return the Confusion counts."""
    c = Confusion()
    for rec in fixture_records:
        predicted = detect_phishing(
            subject=rec["subject"],
            sender=rec["sender"],
            body=rec["body"],
        )
        truth = bool(rec["is_phishing"])
        if predicted and truth:
            c.tp += 1
        elif predicted and not truth:
            c.fp += 1
        elif not predicted and truth:
            c.fn += 1
        else:
            c.tn += 1
    return c


class TestPhishingFixtureIntegrity:
    def test_fixture_has_phishing_examples(self, fixture_records):
        phishing = [r for r in fixture_records if r["is_phishing"]]
        assert len(phishing) >= 20, (
            f"Fixture needs at least 20 phishing examples for a stable gate; "
            f"got {len(phishing)}"
        )

    def test_fixture_has_ham_examples(self, fixture_records):
        ham = [r for r in fixture_records if not r["is_phishing"]]
        assert len(ham) >= 20, f"Fixture needs at least 20 ham examples; got {len(ham)}"

    def test_fixture_records_have_required_fields(self, fixture_records):
        for rec in fixture_records:
            assert "id" in rec, f"Record missing 'id': {rec}"
            assert "subject" in rec, f"Record {rec['id']} missing 'subject'"
            assert "sender" in rec, f"Record {rec['id']} missing 'sender'"
            assert "body" in rec, f"Record {rec['id']} missing 'body'"
            assert "is_phishing" in rec, f"Record {rec['id']} missing 'is_phishing'"


class TestPhishingPrecisionGate:
    """Hard CI gate: precision >= 90%.

    This gate is deterministic — the detector is pure-heuristic (no LLM).
    A failure means the detector has too many false positives and must be
    tuned to be more conservative.
    """

    def test_precision_at_or_above_gate(self, confusion):
        precision = confusion.precision
        fp = confusion.fp
        tp = confusion.tp
        # Print details to help diagnose failures.
        print(
            f"\nPhishing precision gate: {precision:.3f} "
            f"(TP={tp}, FP={fp}, gate={PRECISION_GATE:.2f})"
        )
        assert precision >= PRECISION_GATE, (
            f"Phishing detection precision {precision:.3f} is below gate "
            f"{PRECISION_GATE:.2f}. "
            f"TP={tp}, FP={fp}, FN={confusion.fn}, TN={confusion.tn}. "
            "Tighten the heuristic to reduce false positives."
        )

    def test_reports_recall(self, confusion, capsys):
        """Recall is secondary — reported but not gated."""
        recall = confusion.recall
        fn = confusion.fn
        tp = confusion.tp
        fp_rate = confusion.false_positive_rate
        print(
            f"\nPhishing recall: {recall:.3f} (TP={tp}, FN={fn}), "
            f"FP-rate: {fp_rate:.3f}"
        )
        # Recall is informational; no hard gate.
        assert recall >= 0.0  # tautological — just ensures the metric runs

    def test_fp_rate_reported(self, confusion, capsys):
        """Print FP-rate so PR evidence captures it."""
        fp_rate = confusion.false_positive_rate
        print(
            f"\nFalse-positive rate: {fp_rate:.4f} "
            f"(FP={confusion.fp} / (FP+TN={confusion.fp + confusion.tn}))"
        )
        # No hard gate on FP-rate — precision is the gate. But it should
        # be finite (NaN would indicate a fixture bug).
        assert 0.0 <= fp_rate <= 1.0

    def test_no_false_positives_on_canonical_ham(self, fixture_records):
        """Canonical ham records that absolutely must NOT be flagged as phishing.

        These are the precision traps: legit onboarding verification, real
        password-reset, shipping notices, and calendar invites that share
        surface-level language with phishing.
        """
        # Ids we treat as must-not-flag:
        # ham_001 = github signup verify email
        # ham_002 = slack password reset
        # ham_005 = dropbox confirm email
        # ham_021 = notion password reset
        # ham_027 = okta 2FA verify email
        must_not_flag = {
            "ham_001",
            "ham_002",
            "ham_005",
            "ham_021",
            "ham_027",
        }
        by_id = {r["id"]: r for r in fixture_records}
        for rec_id in must_not_flag:
            if rec_id not in by_id:
                continue
            rec = by_id[rec_id]
            predicted = detect_phishing(
                subject=rec["subject"],
                sender=rec["sender"],
                body=rec["body"],
            )
            assert not predicted, (
                f"Canonical-ham record {rec_id!r} was incorrectly flagged as "
                f"phishing. Subject: {rec['subject']!r}. "
                "Tighten the heuristic to avoid flagging legitimate account-management mail."
            )
