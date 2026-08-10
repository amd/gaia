# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Priority-sender / low-priority-sender severity regression tests
(#2632, #2666).

Bug (#2632): a priority-sender match forced ``category`` to ``URGENT`` in
``_apply_session_preferences``, overriding whatever the heuristic (or the
LLM) had already decided from the message's actual content. A Substack
newsletter from a priority sender — Gmail's own ``CATEGORY_UPDATES`` label,
heuristically FYI — was promoted all the way to URGENT, with a reason line
that named the heuristic's real (non-urgent) verdict right next to the
contradicting URGENT category.

Bug (#2666, the mirror direction): a low-priority-sender match forced
``category`` to ``PROMOTIONAL`` the same way — "I don't care about most of
this sender's mail" is not "this specific message is never urgent", so a
genuinely urgent message from a muted sender was buried as promotional.

Resolution rule under test: a sender preference may only affect metadata
(``preference_applied``, the reason line) — it must never move ``category``.
Content (the heuristic, an SLM, or the LLM) decides severity. The
priority-sender classes below exercise this through ``triage_inbox_impl`` /
``pre_scan_inbox_impl`` with NO ``classifier`` wired in, so the heuristic's
own verdict is what "content decided" means. The low-priority-sender classes
need a genuinely URGENT verdict to prove the point, and the heuristic alone
never produces ``CATEGORY_URGENT`` (that category is LLM/SLM-only in this
codebase) — so those use a deterministic ``slm_classifier`` stub instead of
a live LLM, the same technique ``test_rationale_no_classifier_leak_2743.py``
uses for content-decided categories the heuristic can't reach on its own.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    pre_scan_inbox_impl,
    triage_inbox_impl,
)
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_FYI,
    CATEGORY_URGENT,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: List[str],
    body: str = "Body text for the fixture message.",
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": body[:120],
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(body), "data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


# The issue's own repro: a Substack newsletter Gmail already labeled
# CATEGORY_UPDATES (heuristically confident FYI, reason "Gmail
# CATEGORY_UPDATES label set") from a sender the user separately flagged as
# priority. No commitment/deadline signal in the body, so the heuristic
# commits without LLM escalation.
_NEWSLETTER_FROM_PRIORITY_SENDER = _msg(
    "urgent_fp_001",
    subject="Codex from 0 to 10M Users: Building ChatGPT Work",
    sender="Latent.Space <swyx@substack.com>",
    label_ids=["INBOX", "UNREAD", "CATEGORY_UPDATES"],
    body="Upgrade your subscription. Refer a friend. Unsubscribe anytime.",
)

_PRIORITY_ADDR = "swyx@substack.com"


def _gmail() -> FakeGmailBackend:
    gmail = FakeGmailBackend()
    gmail.add_message(_NEWSLETTER_FROM_PRIORITY_SENDER)
    return gmail


def _prefs(priority_senders: Optional[set] = None) -> Dict[str, Any]:
    return {
        "priority_senders": priority_senders or set(),
        "low_priority_senders": set(),
        "category_defaults": {},
    }


class TestPriorityMatchNeverElevatesToUrgent:
    def test_category_updates_message_stays_fyi_not_urgent(self):
        """AC1/AC3: the heuristic's own verdict (FYI, from Gmail's
        CATEGORY_UPDATES label) survives a priority-sender match untouched —
        it must never become URGENT, and must not silently become
        PROMOTIONAL either: the message's real content decided FYI, and a
        sender preference is not content.
        """
        triage = triage_inbox_impl(
            _gmail(), max_messages=25, session_preferences=_prefs({_PRIORITY_ADDR})
        )
        decision = triage["results"][0]

        assert decision["category"] != "URGENT"
        assert decision["category"] == CATEGORY_FYI
        assert decision["preference_applied"] == "priority_sender"

    def test_reason_line_carries_no_urgency_claim(self):
        """AC2: the resolution rule is stated in the reason line, and the
        line must not itself assert urgency the category doesn't back
        (the pre-fix line literally said 'priority sender ... URGENT'
        while quoting the heuristic's own non-urgent verdict next to it).
        """
        triage = triage_inbox_impl(
            _gmail(), max_messages=25, session_preferences=_prefs({_PRIORITY_ADDR})
        )
        rationale = triage["results"][0]["rationale"]

        assert "urgent" not in rationale.lower()
        # The rule must be explicit, not merely absent: it should name that
        # category is unaffected by the preference.
        assert "category unchanged" in rationale.lower()

    def test_no_prefs_and_priority_sender_prefs_produce_the_same_category(self):
        """AC2 acceptance example: with PREFS={} the category is
        unchanged relative to a priority-sender match on the same sender —
        i.e. the preference must not move severity in either direction.
        """
        baseline = triage_inbox_impl(
            _gmail(), max_messages=25, session_preferences=_prefs()
        )
        with_pref = triage_inbox_impl(
            _gmail(), max_messages=25, session_preferences=_prefs({_PRIORITY_ADDR})
        )

        assert baseline["results"][0]["category"] == with_pref["results"][0]["category"]

    def test_pre_scan_does_not_place_priority_sender_newsletter_in_urgent(self):
        """Full pre_scan_inbox_impl path (the surface the TUI card renders
        from): the message must not land in the urgent section.
        """
        out = pre_scan_inbox_impl(
            _gmail(), max_messages=25, session_preferences=_prefs({_PRIORITY_ADDR})
        )

        urgent_ids = {item["message_id"] for item in out["urgent"]}
        assert "urgent_fp_001" not in urgent_ids
        assert out["totals"]["urgent"] == 0


class TestPhishingSafetyOverrideStillWins:
    def test_phishing_message_from_priority_sender_is_not_promoted(self):
        """Regression guard: the #2632 fix must not weaken the pre-existing
        phishing/spam safety override — a priority-sender match still must
        not touch a phishing-flagged message's category.
        """
        phishing_msg = _msg(
            "phish_001",
            subject="Verify your account now",
            sender="Alerts <alerts@paypa1-secure.tk>",
            label_ids=["INBOX"],
            body="Your account has been compromised, click to verify your account.",
        )
        gmail = FakeGmailBackend()
        gmail.add_message(phishing_msg)
        addr = "alerts@paypa1-secure.tk"

        triage = triage_inbox_impl(
            gmail, max_messages=25, session_preferences=_prefs({addr})
        )
        decision = triage["results"][0]

        assert decision["is_phishing"] is True
        assert decision["category"] != "URGENT"
        assert decision.get("preference_applied") == "skipped_phishing_or_spam"


# ---------------------------------------------------------------------------
# #2666 — the mirror direction: low-priority-sender match forcing PROMOTIONAL.
# ---------------------------------------------------------------------------

# No label/subject/sender-keyword match -> heuristic falls through to its
# final "no clear signal" branch, confident=False. That is the only route to
# an SLM-decided category in this codebase: the heuristic itself never
# emits CATEGORY_URGENT (grep-verified — it's LLM/SLM-only).
_URGENT_FROM_LOW_PRIORITY_SENDER = _msg(
    "urgent_muted_001",
    subject="Heads up",
    sender="Vendor Status <status@vendor-updates.example>",
    label_ids=["INBOX", "UNREAD"],
    body=(
        "Production database is down and customers are affected -- need "
        "someone to look at this right now."
    ),
)

_LOW_PRIORITY_ADDR = "status@vendor-updates.example"


def _urgent_slm_classifier(*, subject, sender, body, message_id=""):
    """Deterministic stand-in for the on-device SLM/LLM category call —
    proves content, not the sender preference, decides severity, without
    depending on a live model (same technique
    ``test_rationale_no_classifier_leak_2743.py``'s ``_slm_by_id`` uses).
    """
    if message_id != "urgent_muted_001":
        return None
    return {"category": CATEGORY_URGENT, "confidence": 0.95, "source": "slm"}


def _low_priority_gmail() -> FakeGmailBackend:
    gmail = FakeGmailBackend()
    gmail.add_message(_URGENT_FROM_LOW_PRIORITY_SENDER)
    return gmail


def _low_prefs(low_priority_senders: Optional[set] = None) -> Dict[str, Any]:
    return {
        "priority_senders": set(),
        "low_priority_senders": low_priority_senders or set(),
        "category_defaults": {},
    }


class TestLowPriorityMatchNeverForcesPromotional:
    """Mirror of ``TestPriorityMatchNeverElevatesToUrgent`` for the opposite
    direction (#2666): "I don't care about most of this sender's mail" is
    not "this specific message is never urgent." A low-priority-sender
    match must not force PROMOTIONAL over a content-derived category
    either.
    """

    def test_urgent_content_survives_low_priority_match(self):
        """AC1/AC3: content says URGENT (via the SLM stub — the heuristic
        alone never produces URGENT); a low-priority-sender match must not
        downgrade that to PROMOTIONAL. A muted sender's genuinely urgent
        message still surfaces at the severity its content supports.
        """
        triage = triage_inbox_impl(
            _low_priority_gmail(),
            max_messages=25,
            session_preferences=_low_prefs({_LOW_PRIORITY_ADDR}),
            slm_classifier=_urgent_slm_classifier,
        )
        decision = triage["results"][0]

        assert decision["category"] != "PROMOTIONAL"
        assert decision["category"] == CATEGORY_URGENT
        assert decision["preference_applied"] == "low_priority_sender"

    def test_reason_line_states_the_resolution_rule(self):
        """AC2: the resolution rule is stated in the reason line, and the
        line must not itself assert a promotional demotion the category
        doesn't back.
        """
        triage = triage_inbox_impl(
            _low_priority_gmail(),
            max_messages=25,
            session_preferences=_low_prefs({_LOW_PRIORITY_ADDR}),
            slm_classifier=_urgent_slm_classifier,
        )
        rationale = triage["results"][0]["rationale"]

        assert "promotional" not in rationale.lower()
        # The rule must be explicit, not merely absent: it should name that
        # category is unaffected by the preference.
        assert "category unchanged" in rationale.lower()

    def test_no_prefs_and_low_priority_prefs_produce_the_same_category(self):
        """AC2 acceptance example: with PREFS={} the category is unchanged
        relative to a low-priority-sender match on the same sender — i.e.
        the preference must not move severity in either direction.
        """
        baseline = triage_inbox_impl(
            _low_priority_gmail(),
            max_messages=25,
            session_preferences=_low_prefs(),
            slm_classifier=_urgent_slm_classifier,
        )
        with_pref = triage_inbox_impl(
            _low_priority_gmail(),
            max_messages=25,
            session_preferences=_low_prefs({_LOW_PRIORITY_ADDR}),
            slm_classifier=_urgent_slm_classifier,
        )

        assert baseline["results"][0]["category"] == with_pref["results"][0]["category"]

    def test_pre_scan_still_places_urgent_muted_sender_in_urgent(self):
        """Full pre_scan_inbox_impl path (the surface the TUI card renders
        from): a genuinely urgent message from a muted sender must land in
        the urgent section, not be buried in suggested_archives.
        """
        out = pre_scan_inbox_impl(
            _low_priority_gmail(),
            max_messages=25,
            session_preferences=_low_prefs({_LOW_PRIORITY_ADDR}),
            slm_classifier=_urgent_slm_classifier,
        )

        urgent_ids = {item["message_id"] for item in out["urgent"]}
        archive_ids = {item["message_id"] for item in out["suggested_archives"]}
        assert "urgent_muted_001" in urgent_ids
        assert "urgent_muted_001" not in archive_ids
        assert out["totals"]["urgent"] == 1


class TestLowPriorityPhishingSafetyOverrideStillWins:
    def test_phishing_message_from_low_priority_sender_is_not_archived(self):
        """Regression guard: the #2666 fix must not weaken the pre-existing
        phishing/spam safety override — a low-priority-sender match still
        must not touch a phishing-flagged message's category (silently
        archiving it would hide the threat from the user instead of
        surfacing it).
        """
        phishing_msg = _msg(
            "phish_muted_001",
            subject="Verify your account now",
            sender="Alerts <alerts@paypa1-secure.tk>",
            label_ids=["INBOX"],
            body="Your account has been compromised, click to verify your account.",
        )
        gmail = FakeGmailBackend()
        gmail.add_message(phishing_msg)
        addr = "alerts@paypa1-secure.tk"

        triage = triage_inbox_impl(
            gmail, max_messages=25, session_preferences=_low_prefs({addr})
        )
        decision = triage["results"][0]

        assert decision["is_phishing"] is True
        assert decision["category"] != "PROMOTIONAL"
        assert decision.get("preference_applied") == "skipped_phishing_or_spam"
