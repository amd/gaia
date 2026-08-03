# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2643 lever 3 — ``List-Unsubscribe`` header as a supplementary bulk-mail
signal for ``classify_category_heuristic``.

RFC 2369's ``List-Unsubscribe`` header arrives with a Gmail ``format=metadata``
fetch (no body read needed) and is a stronger bulk-mail signal than a body
keyword scan. It is SUPPLEMENTARY, not a replacement for Gmail's own
``CATEGORY_PROMOTIONS``/``CATEGORY_UPDATES`` labels or the existing subject-
keyword / automated-sender fallbacks — those all still win when they already
resolve a message. The header only kicks in for messages that would otherwise
fall through to the LLM (no label, no subject keyword, no automated-sender
match, not IMPORTANT/STARRED), and it never overrides Gmail's own IMPORTANT/
STARRED flag or the #2113 deadline/commitment veto.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_FYI,
    CATEGORY_NEEDS_RESPONSE,
    CATEGORY_PROMOTIONAL,
    LABEL_CATEGORY_PROMOTIONS,
    LABEL_IMPORTANT,
    LABEL_INBOX,
    LABEL_STARRED,
    classify_category_heuristic,
)


class TestListUnsubscribeSupplementarySignal:
    def test_unlabeled_bulk_mail_with_list_unsubscribe_is_confident_promotional(self):
        r = classify_category_heuristic(
            subject="This week at Northbay Supply",
            sender="Northbay Supply <news@northbay-supply.example>",
            label_ids=[LABEL_INBOX],
            body="Check out what's new this week.",
            has_list_unsubscribe=True,
        )
        assert r.confident is True
        assert r.category == CATEGORY_PROMOTIONAL
        # #2744: a fact about the message (it reads like a newsletter),
        # never the RFC 2369 header name that triggered it.
        assert "newsletter" in r.reason.lower()

    def test_no_list_unsubscribe_no_other_signal_still_escalates(self):
        """Sanity: the flag is what makes the difference, not an unrelated change."""
        r = classify_category_heuristic(
            subject="This week at Northbay Supply",
            sender="Northbay Supply <news@northbay-supply.example>",
            label_ids=[LABEL_INBOX],
            body="Check out what's new this week.",
            has_list_unsubscribe=False,
        )
        assert r.confident is False

    def test_defaults_to_false_for_existing_callers(self):
        """Every existing call site omits the new kwarg -- must default off."""
        r = classify_category_heuristic(
            subject="This week at Northbay Supply",
            sender="Northbay Supply <news@northbay-supply.example>",
            label_ids=[LABEL_INBOX],
            body="Check out what's new this week.",
        )
        assert r.confident is False


class TestListUnsubscribeDoesNotOverrideStrongerSignals:
    def test_gmail_promotions_label_still_wins_and_is_unaffected(self):
        """A label match resolves the message before the header check ever
        runs -- the header is supplementary coverage, not a replacement."""
        with_header = classify_category_heuristic(
            subject="Big sale",
            sender="Deals <deals@shop.example>",
            label_ids=[LABEL_CATEGORY_PROMOTIONS],
            body="Save big today.",
            has_list_unsubscribe=True,
        )
        without_header = classify_category_heuristic(
            subject="Big sale",
            sender="Deals <deals@shop.example>",
            label_ids=[LABEL_CATEGORY_PROMOTIONS],
            body="Save big today.",
            has_list_unsubscribe=False,
        )
        assert with_header == without_header

    def test_important_flag_still_escalates_not_silently_archived(self):
        """Gmail flagged this significant -- List-Unsubscribe must not
        override that into a confident low-priority archive."""
        r = classify_category_heuristic(
            subject="Re: budget review",
            sender="ceo@company.example",
            label_ids=[LABEL_INBOX, LABEL_IMPORTANT],
            body="Please review before Friday.",
            has_list_unsubscribe=True,
        )
        assert r.confident is False
        assert r.category == CATEGORY_NEEDS_RESPONSE
        assert LABEL_IMPORTANT in r.matched_label_ids

    def test_starred_flag_still_escalates_not_silently_archived(self):
        r = classify_category_heuristic(
            subject="Recipe roundup",
            sender="chef@example.com",
            label_ids=[LABEL_STARRED],
            body="A few dishes to try.",
            has_list_unsubscribe=True,
        )
        assert r.confident is False
        assert LABEL_STARRED in r.matched_label_ids


class TestListUnsubscribeCommitmentVeto:
    """Mirrors test_commitment_veto_2113.py's shape for the new signal --
    the NOTUS-style regression guard #2643's AC explicitly names: a bulk
    sender with a real deadline in the body must still reach the LLM."""

    def test_list_unsubscribe_with_commitment_signal_still_escalates(self):
        r = classify_category_heuristic(
            subject="NOTUS Weekly",
            sender="NOTUS Newsletter <news@notus.example>",
            label_ids=[LABEL_INBOX],
            body=(
                "Your community pass renewal is due. Confirm by Friday or "
                "your membership will be suspended."
            ),
            has_list_unsubscribe=True,
        )
        assert r.confident is False
        assert "mentions a deadline" in r.reason

    def test_list_unsubscribe_without_commitment_signal_is_confident(self):
        r = classify_category_heuristic(
            subject="NOTUS Weekly",
            sender="NOTUS Newsletter <news@notus.example>",
            label_ids=[LABEL_INBOX],
            body="This week's roundup of community news.",
            has_list_unsubscribe=True,
        )
        assert r.confident is True
        assert r.category == CATEGORY_PROMOTIONAL


class TestListUnsubscribeSpamFields:
    def test_is_spam_fields_resolved_as_promotional(self):
        """is_spam/spam_confident go through the same _spam_fields gate as
        every other confident-PROMOTIONAL branch -- no special-casing. A
        sender that doesn't match the narrow spam-sender pattern leaves
        spam_confident False (the LLM still gets a say on is_spam), exactly
        like the label-driven PROMOTIONAL rules above this one."""
        r = classify_category_heuristic(
            subject="This week at Northbay Supply",
            sender="Northbay Supply <news@northbay-supply.example>",
            label_ids=[LABEL_INBOX],
            body="Check out what's new this week.",
            has_list_unsubscribe=True,
        )
        assert r.is_spam is False
        assert r.spam_confident is False

    def test_spam_sender_signal_still_commits_is_spam(self):
        """The narrow, mechanical spam-sender pattern (auto-generated
        anonymous local-part) still commits is_spam=True here exactly as it
        does for the label-driven PROMOTIONAL rules -- same _spam_fields
        gate, no new logic."""
        r = classify_category_heuristic(
            subject="Special offer",
            sender="contact.4821@example.com",
            label_ids=[LABEL_INBOX],
            body="Check out what's new.",
            has_list_unsubscribe=True,
        )
        assert r.is_spam is True
        assert r.spam_confident is True


class TestListUnsubscribeCategoryFallbackNeverWins:
    def test_subject_keyword_promo_fallback_still_takes_priority(self):
        """Rule 6 (subject keyword) fires before the header check -- both
        agree here, but the reason string must come from the earlier rule."""
        r = classify_category_heuristic(
            subject="50% off — limited time",
            sender="Deals <deals@shop.example>",
            label_ids=[LABEL_INBOX],
            body="Shop now.",
            has_list_unsubscribe=True,
        )
        assert r.confident is True
        assert r.category == CATEGORY_PROMOTIONAL
        # #2744: rule 6's reason text (a fact: this looks promotional) must
        # win over rule 8.5's ("looks like a newsletter") -- proves
        # precedence without asserting the retired internal keyword-match
        # wording.
        assert r.reason == "Looks like a promotional email"

    def test_automated_sender_fallback_still_takes_priority(self):
        """Rule 7 (automated sender) fires before the header check -- the
        reason string must still name the automated-sender rule, and the
        category stays FYI (automated-sender's own category), not PROMOTIONAL."""
        r = classify_category_heuristic(
            subject="Your weekly digest",
            sender="notifications@service.example",
            label_ids=[LABEL_INBOX],
            body="Here is your weekly digest.",
            has_list_unsubscribe=True,
        )
        assert r.confident is True
        assert r.category == CATEGORY_FYI
        # #2744: rule 7's reason (a fact: sent by an automated address)
        # must win over rule 8.5's ("looks like a newsletter").
        assert r.reason == "Automated or no-reply sender"
