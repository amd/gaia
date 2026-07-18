# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2113 — deadline/commitment veto over confident low-priority labels.

A promotions/social/updates label used to short-circuit to a confident
classification before any body read, so a real deadline/commitment/
consequence living in the body was confidently archived or filed
informational. The commitment veto forces ``confident=False`` (LLM
escalation) for those messages while leaving ordinary promos untouched.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_FYI,
    CATEGORY_PROMOTIONAL,
    LABEL_CATEGORY_PROMOTIONS,
    LABEL_CATEGORY_SOCIAL,
    LABEL_CATEGORY_UPDATES,
    _has_commitment_signal,
    classify_category_heuristic,
)

# -- signal detector --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "attendance is required this Thursday",
        "your payment is past due",
        "action required: confirm by Friday",
        "failure to respond will result in suspension",
        "your account will be suspended",
        "you have exceeded your monthly budget",
        "please RSVP by end of week",
        "final notice before your late fee applies",
    ],
)
def test_commitment_signal_positive(text):
    assert _has_commitment_signal(text.lower(), "")
    assert _has_commitment_signal("", text.lower())  # body channel too


@pytest.mark.parametrize(
    "text",
    [
        "50% off ends today — limited time offer",
        "last chance to save on your favorite brands",
        "your weekly newsletter is here",
        "thanks for your order, it has shipped",
        "act now for the best deals of the season",
    ],
)
def test_marketing_urgency_is_not_a_commitment(text):
    assert not _has_commitment_signal(text.lower(), text.lower())


# -- veto behavior ----------------------------------------------------------


def test_promotions_with_commitment_escalates():
    r = classify_category_heuristic(
        subject="Membership renewal reminder",
        sender="Community Club <news@club.example>",
        label_ids=[LABEL_CATEGORY_PROMOTIONS],
        body=(
            "Attendance is required at this week's meeting. Failure to attend "
            "will result in suspension of your membership."
        ),
    )
    assert r.confident is False  # escalated to LLM, not confidently archived
    assert "deadline/commitment signal" in r.reason


def test_ordinary_promo_still_confident_archive():
    r = classify_category_heuristic(
        subject="50% off ends today!",
        sender="Deals <deals@shop.example>",
        label_ids=[LABEL_CATEGORY_PROMOTIONS],
        body="Limited time offer. Shop now and save big before the sale ends.",
    )
    assert r.confident is True
    assert r.category == CATEGORY_PROMOTIONAL


def test_updates_budget_alert_escalates():
    r = classify_category_heuristic(
        subject="Your monthly spending summary",
        sender="Bank <alerts@bank.example>",
        label_ids=[LABEL_CATEGORY_UPDATES],
        body="Heads up: you have exceeded your budget for this month.",
    )
    assert r.confident is False
    assert "deadline/commitment signal" in r.reason


def test_ordinary_update_still_confident_fyi():
    r = classify_category_heuristic(
        subject="Your order has shipped",
        sender="Shop <ship@shop.example>",
        label_ids=[LABEL_CATEGORY_UPDATES],
        body="Your package is on the way and will arrive soon.",
    )
    assert r.confident is True
    assert r.category == CATEGORY_FYI


def test_social_with_commitment_escalates():
    r = classify_category_heuristic(
        subject="Group event",
        sender="Social <notify@social.example>",
        label_ids=[LABEL_CATEGORY_SOCIAL],
        body="RSVP by Friday or your reserved spot will be cancelled.",
    )
    assert r.confident is False


def test_commitment_signal_in_subject_only_escalates():
    r = classify_category_heuristic(
        subject="ACTION REQUIRED: renew by Friday",
        sender="Service <noreply@svc.example>",
        label_ids=[LABEL_CATEGORY_PROMOTIONS],
        body="",
    )
    assert r.confident is False


# -- eval corpus guard ------------------------------------------------------


def test_commitment_eval_corpus_cases_present_and_non_archive():
    """The #2113 synthetic commitment cases exist in the seed and are labelled
    as needing attention (never PROMOTIONAL/archive)."""
    import json
    from pathlib import Path

    seed = (
        Path(__file__).resolve().parents[5]
        / "tests"
        / "fixtures"
        / "email"
        / "vendor_corpus_seed.jsonl"
    )
    cases = [
        json.loads(line)
        for line in seed.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    commitment = [
        c for c in cases if c.get("source_dataset") == "commitment_synth_2113"
    ]
    assert len(commitment) >= 6, "expected the #2113 commitment corpus cases"
    for c in commitment:
        assert c["category"] in ("NEEDS_RESPONSE", "URGENT"), c["subject"]
        assert c["suggestedAction"] != "archive", c["subject"]
        assert c["is_phishing"] is False
