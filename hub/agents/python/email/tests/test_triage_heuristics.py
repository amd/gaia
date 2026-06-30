# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for triage_heuristics.py — pure heuristic, no LLM, no mocks needed.

Covers the Rule 7 action-request escalation added in #1900:
  - ``_subject_requests_action`` helper (new in #1900)
  - ``classify_category_heuristic`` with automated senders:
      * action-request subject  → confident=False (escalate to LLM)
      * plain informational     → confident=True, FYI  (precision regression guard)
      * urgent-signal subject   → confident=False (existing Rule 7a, unchanged)
"""
from __future__ import annotations

import pytest

from gaia_agent_email.tools.triage_heuristics import (
    CATEGORY_FYI,
    CATEGORY_NEEDS_RESPONSE,
    _subject_requests_action,
    classify_category_heuristic,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A canonical automated sender (noreply@) used throughout Rule 7 tests.
_AUTO_SENDER = "IT Systems <noreply@acme-corp.example.com>"
_INBOX_LABELS = ["INBOX"]


# ---------------------------------------------------------------------------
# _subject_requests_action — True cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        # Direct question to the reader
        "Can you review the Q3 report?",
        # Review verb
        "Please review the PR by tomorrow",
        # Decision / sign-off request
        "Need your decision on the vendor shortlist",
        # Approval request (noun phrase "your approval")
        "Your approval required for the budget change",
        # Meeting invite / RSVP
        "Meeting invite: launch readiness review",
        "RSVP: all-hands next week",
        # Ticket / task assigned
        "JIRA ticket assigned: update the onboarding docs",
    ],
)
def test_subject_requests_action_true(subject):
    """_subject_requests_action must return True for concrete asks directed at the reader."""
    assert _subject_requests_action(subject.lower()) is True


# ---------------------------------------------------------------------------
# _subject_requests_action — False cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        # Informational reminders
        "Benefits enrollment reminder",
        "Quarterly financial digest",
        # CI / system status
        "Build status: nightly passed",
        # Generic newsletter
        "Newsletter: this week in engineering",
        # The critical edge case: "approval" as a NOUN (no "your" prefix, no verb)
        # The pattern matches "approve" (verb) but NOT "approval" (noun) standalone.
        "Shipping confirmation for office equipment - expense approval",
        # Maintenance notice — no ask
        "VPN maintenance window this Saturday",
    ],
)
def test_subject_requests_action_false(subject):
    """_subject_requests_action must return False for informational, no-ask subjects."""
    assert _subject_requests_action(subject.lower()) is False


# ---------------------------------------------------------------------------
# classify_category_heuristic: Rule 7 — automated sender + action-request subject
# ---------------------------------------------------------------------------
# New #1900 behavior: an automated sender whose subject contains a concrete ask
# must escalate (confident=False) rather than being silently committed as FYI.


@pytest.mark.parametrize(
    "subject",
    [
        "Need your decision on the vendor shortlist",
        "Please review the PR by tomorrow",
        "Meeting invite: launch readiness review",
        "Can you approve this expense report?",
    ],
)
def test_automated_sender_action_subject_escalates(subject):
    """Rule 7: automated sender + action-request subject → confident=False (escalate to LLM)."""
    result = classify_category_heuristic(subject, _AUTO_SENDER, _INBOX_LABELS)
    assert not result.confident, (
        f"expected confident=False for action-request subject {subject!r}, "
        f"got confident=True (category={result.category}, reason={result.reason!r})"
    )


# ---------------------------------------------------------------------------
# classify_category_heuristic: Rule 7 — automated sender + informational subject
# ---------------------------------------------------------------------------
# Precision regression guard: informational subjects from automated senders
# must STILL be committed confidently as FYI (no over-escalation).


@pytest.mark.parametrize(
    "subject",
    [
        "Benefits enrollment reminder",
        "Build status: nightly passed",
        "Weekly newsletter",
    ],
)
def test_automated_sender_informational_subject_confident_fyi(subject):
    """Rule 7: automated sender + informational subject → confident=True, FYI (unchanged)."""
    result = classify_category_heuristic(subject, _AUTO_SENDER, _INBOX_LABELS)
    assert result.confident, (
        f"expected confident=True for informational subject {subject!r}, "
        f"got confident=False (reason={result.reason!r})"
    )
    assert result.category == CATEGORY_FYI, (
        f"expected category=FYI for informational subject {subject!r}, "
        f"got {result.category!r}"
    )


# ---------------------------------------------------------------------------
# classify_category_heuristic: Rule 7a — urgent signal still escalates
# ---------------------------------------------------------------------------
# Existing behavior (pre-#1900): urgent subjects from automated senders must
# still escalate.  Regression guard so the new action-request branch does not
# shadow the urgent-signal branch.


def test_automated_sender_urgent_signal_escalates():
    """Rule 7a (existing): automated sender + urgent subject → confident=False."""
    result = classify_category_heuristic(
        "[SEV1] prod db down", _AUTO_SENDER, _INBOX_LABELS
    )
    assert not result.confident, (
        f"expected confident=False for [SEV1] subject, got confident=True "
        f"(category={result.category}, reason={result.reason!r})"
    )


# ---------------------------------------------------------------------------
# _subject_requests_action — approve/invite inflection precision guards (#1900)
# ---------------------------------------------------------------------------
# The bare tokens "approve" / "invite" were REMOVED from _ACTION_REQUEST_PATTERNS
# because they false-matched inflected FYI forms ("approved", "invited",
# "uninvited"). The approve/invite ASK signal is now carried only by phrase
# forms ("please approve", "your approval", "for your approval", "meeting
# invite", "calendar invite", "invitation") plus the generic "?"/"can you".


@pytest.mark.parametrize(
    "subject",
    [
        "please approve the budget",
        "your approval required",
        "meeting invite: kickoff",
        "can you approve this expense?",
    ],
)
def test_approve_invite_signal_fires_via_phrase_forms(subject):
    """The approve/invite ask still fires through phrase forms and "?"/"can you"."""
    assert _subject_requests_action(subject.lower()) is True


@pytest.mark.parametrize(
    "subject",
    [
        # Inflected verb form — a completed action, not an ask. Must NOT fire
        # now that bare "approve" is gone ("approve" in "approved").
        "expense report approved",
        # Inflected invite form — must NOT fire now that bare "invite" is gone
        # ("invite" in "invited").
        "user invited to workspace",
        # "approval" as a standalone noun (no "your" prefix) — already an FYI form.
        "shipping confirmation for office equipment - expense approval",
    ],
)
def test_approve_invite_inflected_fyi_forms_do_not_fire(subject):
    """Inflected FYI forms ('approved', 'invited', bare 'approval') must NOT fire (#1900 precision fix)."""
    assert _subject_requests_action(subject.lower()) is False
