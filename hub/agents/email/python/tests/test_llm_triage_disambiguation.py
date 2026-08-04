# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Short first-person human message disambiguation (#2633, first half).

Bug: a short, first-person message from a named human proposing continued
business ("Nice meeting you ... let me know what you think") classified as
informational rather than NEEDS_RESPONSE. Direct-query evidence in the issue
showed the same model correctly identifies the need for a reply when asked
explicitly, so the gap is in the triage-path prompt's judgment, not missing
information.

This module cannot prove a live model now classifies the real message
correctly -- that requires ``gaia eval`` against a running Lemonade Server,
which is out of scope here. What IS testable hermetically:

1. The new disambiguation guidance actually made it into ``_SYSTEM_PROMPT``
   (a content regression guard -- catches an accidental revert/edit, not a
   model behavior claim).
2. The plumbing from a classifier's verdict to the final triage decision is
   correct for this exact message shape -- IF the model returns
   NEEDS_RESPONSE, the pipeline records it with confident=True and never
   silently downgrades it, and a hard-negative (a short automated
   confirmation) is handled the same way without special-casing. This uses
   a stubbed chat client (``_FakeChat``, same pattern as
   test_llm_triage_usage.py), never a live Lemonade server.
"""

from __future__ import annotations

import base64
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.tools.llm_triage import (  # noqa: E402
    _SYSTEM_PROMPT,
    make_llm_classifier,
)
from gaia_agent_email.tools.read_tools import triage_inbox_impl  # noqa: E402
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    classify_category_heuristic,
    group_by_category,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


class TestSystemPromptDocumentsTheDisambiguationRule:
    def test_prompt_names_first_person_human_ask_as_needs_response(self):
        assert "first-person" in _SYSTEM_PROMPT
        assert "NEEDS_RESPONSE" in _SYSTEM_PROMPT
        # The rule must be stated as unconditional ("still needs your
        # reply"), not hedged behind the generic "when unsure, prefer
        # lower-urgency" fallback that would otherwise bias a short/
        # ambiguous message toward FYI.
        assert "still needs your reply" in _SYSTEM_PROMPT

    def test_prompt_pairs_the_rule_with_a_hard_negative(self):
        """The rule must be scoped to WHO/WHY, not message length, so a
        short automated message doesn't get swept into NEEDS_RESPONSE too.
        """
        assert "not by message length" in _SYSTEM_PROMPT or (
            "who is writing and why" in _SYSTEM_PROMPT.lower()
        )


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str, *, subject: str, sender: str, body: str
) -> Dict[str, Any]:
    """An unlabeled message with no automated-sender/promo keyword match --
    the heuristic cannot commit, so it always escalates to the classifier.
    """
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX", "UNREAD"],
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


_HUMAN_ASK_MSG = _msg(
    "human_ask_001",
    subject="Nice meeting you",
    sender="Dana Whitfield <dana@northbay-supply.example>",
    body=(
        "Hi,\n\nIt was great meeting you last week. I think there could be "
        "a real opportunity for us to work together going forward.\n\n"
        "Let me know what you think.\n\nBest,\nDana"
    ),
)

_AUTOMATED_HARD_NEGATIVE_MSG = _msg(
    "automated_short_001",
    subject="Your order has shipped",
    sender="Northbay Supply Orders <orders@northbay-supply.example>",
    body=(
        "Hi,\n\nYour recent order (#48291) has shipped and is on its way. "
        "Tracking will update within 24 hours.\n\nNorthbay Supply"
    ),
)


def _fake_chat_returning(category: str) -> Any:
    """Chat stub whose ``send_messages`` always returns a fixed verdict --
    same minimal shape as test_llm_triage_usage.py's ``_FakeChat``.
    """

    class _FakeChat:
        def send_messages(self, messages, system_prompt="", **kwargs):
            resp = types.SimpleNamespace()
            resp.text = json.dumps(
                {
                    "category": category,
                    "is_spam": False,
                    "confidence": 0.9,
                    "reasoning": "stub verdict for plumbing test",
                }
            )
            return resp

    return _FakeChat()


class TestHeuristicEscalatesBothFixturesToTheClassifier:
    """Precondition check: both fixtures must actually reach the
    classifier (no Gmail label, no keyword match) -- otherwise this test
    module would be exercising the heuristic, not the LLM disambiguation
    it's named for.
    """

    def test_human_ask_message_is_not_heuristically_confident(self):
        result = classify_category_heuristic(
            _HUMAN_ASK_MSG["payload"]["headers"][1]["value"],
            _HUMAN_ASK_MSG["payload"]["headers"][0]["value"],
            _HUMAN_ASK_MSG["labelIds"],
            body=_HUMAN_ASK_MSG["snippet"],
        )
        assert result.confident is False

    def test_automated_hard_negative_is_not_heuristically_confident(self):
        result = classify_category_heuristic(
            _AUTOMATED_HARD_NEGATIVE_MSG["payload"]["headers"][1]["value"],
            _AUTOMATED_HARD_NEGATIVE_MSG["payload"]["headers"][0]["value"],
            _AUTOMATED_HARD_NEGATIVE_MSG["labelIds"],
            body=_AUTOMATED_HARD_NEGATIVE_MSG["snippet"],
        )
        assert result.confident is False


class TestClassifierVerdictSurvivesThePipeline:
    """Not a claim about what a live model WOULD say -- a stubbed verdict
    proves the plumbing carries it through correctly, so a fix at the
    prompt level (untestable here) is not silently lost downstream.
    """

    def test_needs_response_verdict_is_recorded_confidently_not_downgraded(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_HUMAN_ASK_MSG)
        classifier = make_llm_classifier(_fake_chat_returning("NEEDS_RESPONSE"))

        triage = triage_inbox_impl(gmail, max_messages=25, classifier=classifier)
        decision = triage["results"][0]

        assert decision["category"] == "NEEDS_RESPONSE"
        assert decision["confident"] is True
        assert decision["source"] == "llm"

    def test_hard_negative_stays_fyi_when_the_model_says_so(self):
        gmail = FakeGmailBackend()
        gmail.add_message(_AUTOMATED_HARD_NEGATIVE_MSG)
        classifier = make_llm_classifier(_fake_chat_returning("FYI"))

        triage = triage_inbox_impl(gmail, max_messages=25, classifier=classifier)
        decision = triage["results"][0]

        assert decision["category"] == "FYI"

    def test_needs_response_verdict_groups_outside_informational_categories(self):
        """Mirrors the issue's own assertion shape ('appears in actionable,
        not in informational') at the level this module can test: the raw
        triage_inbox grouping, which is what a caller uses to tell buckets
        apart before any pre-scan-specific bucketing happens.
        """
        gmail = FakeGmailBackend()
        gmail.add_message(_HUMAN_ASK_MSG)
        gmail.add_message(_AUTOMATED_HARD_NEGATIVE_MSG)

        def _classifier(*, subject: str, sender: str, body: str, message_id: str = ""):
            category = "NEEDS_RESPONSE" if "human_ask" in message_id else "FYI"
            return make_llm_classifier(_fake_chat_returning(category))(
                subject=subject, sender=sender, body=body, message_id=message_id
            )

        triage = triage_inbox_impl(gmail, max_messages=25, classifier=_classifier)
        grouped = group_by_category(triage["results"])["groups"]

        assert "human_ask_001" in grouped["NEEDS_RESPONSE"]
        assert "human_ask_001" not in grouped["FYI"]
        assert "human_ask_001" not in grouped["PERSONAL"]
        assert "automated_short_001" in grouped["FYI"]
