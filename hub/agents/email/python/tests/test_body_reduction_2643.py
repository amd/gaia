# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2643 lever 4 — body reduction for what actually reaches the LLM during
triage's classifier follow-up.

Reuses (never reimplements) ``voice_profile.strip_quoted_text`` and
``voice_profile._SIGNOFF_RE`` / ``_SIGNOFF_SCAN_LINES`` — the SAME
machinery #2642 (``body_normalize.py``) needs, per the issue: one shared
body-normalisation path, not three. ``strip_reply_chain_and_signature``
(new, in ``body_normalize.py``) is that shared seam: it cuts a message down
to the sender's own new content by dropping the quoted reply chain and the
trailing signature block, and is wired ONLY into
``triage_inbox_impl``'s classifier escalation body -- never into the
read-tool display paths (``get_message`` / ``get_thread`` /
``summarize_thread``), where a user asking to read a message wants to see
the whole thing, quoted chain included.

This is an EVAL-AFFECTING change (it changes body text the LLM classifier
actually reads) -- unlike levers 1-3, which must change cost, never
classification input. Token reduction must never remove the NEW,
substantive content — only quoted history and signature boilerplate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.body_normalize import (  # noqa: E402
    strip_reply_chain_and_signature,
)
from gaia_agent_email.tools.read_tools import triage_inbox_impl  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


class TestStripReplyChainAndSignature:
    def test_cuts_quoted_reply_chain(self):
        body = (
            "Sure, sounds good, let's do Thursday at 2pm.\n\n"
            "On Mon, Jun 2, 2026 at 9:00 AM Maria <m@x.com> wrote:\n"
            "> Can we meet this week to go over the numbers?\n"
            "> Let me know what works.\n"
        )
        out = strip_reply_chain_and_signature(body)
        assert "sounds good" in out
        assert "Thursday at 2pm" in out
        assert "wrote:" not in out
        assert "go over the numbers" not in out

    def test_cuts_trailing_signature_block(self):
        body = (
            "The report is attached, let me know if anything looks off.\n\n"
            "Best,\n"
            "Alex Rivera\n"
            "Senior Analyst, Northbay Supply\n"
            "+1 (555) 019-2834\n"
            "This message may contain confidential information.\n"
        )
        out = strip_reply_chain_and_signature(body)
        assert "report is attached" in out
        assert "Alex Rivera" not in out
        assert "555" not in out
        assert "confidential" not in out

    def test_cuts_both_quote_chain_and_signature(self):
        body = (
            "Confirmed for Thursday.\n\n"
            "Thanks,\n"
            "Dana\n\n"
            "On Mon, Jun 2, 2026 at 9:00 AM Maria <m@x.com> wrote:\n"
            "> Does Thursday work?\n"
        )
        out = strip_reply_chain_and_signature(body)
        assert "Confirmed for Thursday" in out
        assert "Dana" not in out
        assert "Does Thursday work" not in out

    def test_preserves_substantive_body_with_no_quote_or_signature(self):
        body = "Can you review the attached proposal before our call tomorrow?"
        out = strip_reply_chain_and_signature(body)
        assert out == body

    def test_preserves_content_that_merely_mentions_a_signoff_word_midsentence(self):
        """A hard negative: 'regards' appearing INSIDE a sentence (not as
        its own standalone line) must not truncate real content -- _SIGNOFF_RE
        only matches a full line, mirroring voice_profile's own anchoring."""
        body = (
            "With regards to your question about the invoice, the total is "
            "$450 and it's due at the end of the month."
        )
        out = strip_reply_chain_and_signature(body)
        assert "$450" in out
        assert "due at the end of the month" in out

    def test_empty_body_returns_empty(self):
        assert strip_reply_chain_and_signature("") == ""

    def test_does_not_mutate_voice_profile_signoff_constants(self):
        """Sanity: this function must reuse, not fork, the existing
        constants -- if voice_profile's own list changes, this function's
        behavior changes with it, never independently."""
        from gaia_agent_email.voice_profile import _SIGNOFF_RE

        assert _SIGNOFF_RE.match("Best regards,")
        assert _SIGNOFF_RE.match("Cheers")


def _b64url(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(msg_id: str, *, subject: str, sender: str, body: str) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": "1700000000000",
        "sizeEstimate": len(body),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(body), "data": _b64url(body)},
        },
    }


class TestTriageEscalationBodyIsReduced:
    def test_classifier_receives_reduced_body_not_raw_decoded_body(self):
        gmail = FakeGmailBackend()
        raw_body = (
            "Can you review the attached proposal before our call tomorrow?\n\n"
            "Best,\n"
            "Dana Whitfield\n"
            "Account Manager\n"
            "+1 (555) 019-2834\n\n"
            "On Fri, Jun 1, 2026 at 8:00 AM Sam <sam@example.com> wrote:\n"
            "> Sending over the draft for your review.\n"
        )
        gmail.add_message(
            _msg(
                "m1",
                subject="Re: proposal review",
                sender="dana@example.com",
                body=raw_body,
            )
        )
        received = {}

        def _classifier(*, subject, sender, body, message_id=""):
            received["body"] = body
            return {"category": "NEEDS_RESPONSE", "is_spam": False, "confidence": 0.9}

        triage_inbox_impl(gmail, max_messages=1, classifier=_classifier)

        assert "review the attached proposal" in received["body"]
        assert "before our call tomorrow" in received["body"]
        assert "Dana Whitfield" not in received["body"], (
            "signature block must be stripped before the body reaches the "
            "classifier"
        )
        assert "555" not in received["body"]
        assert "Sending over the draft" not in received["body"], (
            "quoted reply chain must be stripped before the body reaches "
            "the classifier"
        )
