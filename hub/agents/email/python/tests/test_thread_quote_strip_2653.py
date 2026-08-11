# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for #2653: banner stripping misses banners inside Outlook quoted
reply trails.

A banner stripped from a message's own top-of-body (#2642) still reappears
inside the inlined quoted trail of every later reply in a thread, so a
thread summary can name a classification banner ("AMD General") as a
participant. This module covers the fix: ``_thread_message_blocks``
(``tools/read_tools.py``) now cuts the quoted trail out of each rendered
block via ``body_normalize.strip_quoted_trail`` — reusing
``voice_profile.strip_quoted_text``, never a second quote-chain detector.

Covered:
- AC1/AC2: the transcript sent to ``chat.send_messages`` for a multi-reply
  thread summary contains no classification-banner string, asserted on the
  constructed prompt (deterministic), never generated prose.
- AC3: content that exists ONLY in a quoted trail is not lost — a bare
  "+1" above a quoted question survives, and a message whose SOLE content
  is a quote does not collapse to an empty block.
- AC4: per-message/transcript body budget measurably improves on a long
  thread — before/after sizes recorded in the assertion.
- AC5 (regression guard): ``strip_quoted_text`` itself, and its existing
  Sent-mail voice-profile caller path, are unaffected — see
  ``tests/test_email_voice_profile.py::TestStripQuotedText`` (unchanged,
  still green) and the sanity check below.

Fixtures use only synthetic ``example.invalid`` addresses and invented
content — never a real thread (see the plan's Privacy constraint). Uses the
Gmail-style single-line ``On <date>, <name> <<addr>> wrote:`` attribution
that ``voice_profile._ATTRIBUTION_RE`` recognizes.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# parents[0] = tests/, [1] = email/, [2] = python/, [3] = agents/, [4] = hub/,
# [5] = repo-root.
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import body_normalize  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    _thread_message_blocks,
    summarize_thread_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

_AMD_GENERAL_BANNER = "AMD General"


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    thread_id: str,
    sender: str,
    body: str,
    internal_date_ms: int,
    date_header: str,
    subject: str = "Contributing to GAIA",
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": str(internal_date_ms),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "user@example.invalid"},
                {"name": "Date", "value": date_header},
            ],
            "body": {"data": _b64url(body), "size": len(body)},
        },
        "sizeEstimate": len(body),
    }


def _build_backend(messages: List[Dict[str, Any]]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email="user@example.invalid")
    for msg in messages:
        gmail.add_message(msg)
    return gmail


class _CapturingChat:
    """Records every ``send_messages`` call — the fits path issues exactly
    one, carrying the full transcript as the user-turn prompt."""

    def __init__(self, *, text: str = "thread summary") -> None:
        self.calls: List[Dict[str, Any]] = []
        self._text = text

    def send_messages(self, messages, system_prompt="", **kwargs):
        content = messages[0].get("content", "") if messages else ""
        self.calls.append({"system_prompt": system_prompt, "content": content})
        return SimpleNamespace(text=self._text)


_THREAD_ID = "thread-2653-quoted-banner"
_BASE_MS = 1_800_000_000_000

_M1_BODY = (
    f"{_AMD_GENERAL_BANNER}\n\n"
    "There are quite a number of ways that you could help out, from "
    "testing to reviewing pull requests."
)
_M2_BODY = (
    "Thanks, I'll start with code review.\n\n"
    "On Wed, Jul 22, 2026 at 6:50 PM, Iniewicz, Tomasz "
    "<maintainer@example.invalid> wrote:\n"
    f"> {_AMD_GENERAL_BANNER}\n"
    ">\n"
    "> There are quite a number of ways that you could help out, from "
    "testing to reviewing pull requests.\n"
)
_M3_BODY = (
    "+1\n\n"
    "On Wed, Jul 22, 2026 at 7:10 PM, dev2@example.invalid wrote:\n"
    "> Thanks, I'll start with code review.\n"
    ">\n"
    "> On Wed, Jul 22, 2026 at 6:50 PM, Iniewicz, Tomasz "
    "<maintainer@example.invalid> wrote:\n"
    f"> > {_AMD_GENERAL_BANNER}\n"
    "> >\n"
    "> > There are quite a number of ways that you could help out, from "
    "testing to reviewing pull requests.\n"
)

_MULTI_REPLY_THREAD = [
    _msg(
        "m1",
        thread_id=_THREAD_ID,
        sender="Iniewicz, Tomasz <maintainer@example.invalid>",
        body=_M1_BODY,
        internal_date_ms=_BASE_MS,
        date_header="Wed, 22 Jul 2026 18:50:00 -0700",
    ),
    _msg(
        "m2",
        thread_id=_THREAD_ID,
        sender="dev2@example.invalid",
        body=_M2_BODY,
        internal_date_ms=_BASE_MS + 20 * 60_000,
        date_header="Wed, 22 Jul 2026 19:10:00 -0700",
    ),
    _msg(
        "m3",
        thread_id=_THREAD_ID,
        sender="dev3@example.invalid",
        body=_M3_BODY,
        internal_date_ms=_BASE_MS + 40 * 60_000,
        date_header="Wed, 22 Jul 2026 19:30:00 -0700",
    ),
]


class TestThreadSummaryPromptExcludesQuotedBanner:
    """AC1/AC2: the banner reappears in every reply's quoted trail
    (2 extra occurrences beyond m1's own, stripped by #2642 already); the
    fix must remove all of them from the constructed prompt."""

    def test_banner_absent_from_prompt_across_every_message(self):
        # Sanity: the fixture actually reproduces the defect shape — the
        # banner appears in the RAW bodies of m2 and m3 (inside their
        # quoted trail), not just m1's own top-of-body.
        assert _AMD_GENERAL_BANNER in _M2_BODY
        assert _AMD_GENERAL_BANNER in _M3_BODY

        gmail = _build_backend(_MULTI_REPLY_THREAD)
        chat = _CapturingChat()
        summarize_thread_impl(gmail, chat, thread_id=_THREAD_ID)

        assert len(chat.calls) == 1
        prompt = chat.calls[0]["content"]
        assert _AMD_GENERAL_BANNER not in prompt, (
            "quoted-trail copies of the banner must be stripped, not just "
            "the leading one in m1's own body"
        )

    def test_every_message_from_header_still_reaches_the_prompt(self):
        """The real per-message From/Date headers (the actual participant
        identity) survive stripping — only the inlined BODY quote is cut,
        never the structural envelope a summary needs to attribute content
        correctly."""
        gmail = _build_backend(_MULTI_REPLY_THREAD)
        chat = _CapturingChat()
        summarize_thread_impl(gmail, chat, thread_id=_THREAD_ID)
        prompt = chat.calls[0]["content"]
        assert "maintainer@example.invalid" in prompt
        assert "dev2@example.invalid" in prompt
        assert "dev3@example.invalid" in prompt

    def test_own_new_content_of_each_reply_survives(self):
        gmail = _build_backend(_MULTI_REPLY_THREAD)
        chat = _CapturingChat()
        summarize_thread_impl(gmail, chat, thread_id=_THREAD_ID)
        prompt = chat.calls[0]["content"]
        assert "testing to reviewing pull requests" in prompt
        assert "Thanks, I'll start with code review." in prompt
        assert "+1" in prompt

    def test_block_count_unchanged(self):
        ordered = sorted(_MULTI_REPLY_THREAD, key=lambda m: int(m["internalDate"]))
        blocks, _decoded = _thread_message_blocks(ordered, per_message_body_limit=4000)
        assert len(blocks) == 3
        assert "--- Message 1 of 3 ---" in blocks[0]
        assert "--- Message 3 of 3 ---" in blocks[2]


class TestContentPreservationAgainstOverAggressiveStripping:
    """AC3 — content that exists ONLY in a quoted trail is not lost."""

    def test_bare_plus_one_above_quoted_question_survives(self):
        body = (
            "+1\n\n"
            "On Wed, Jul 22, 2026 at 7:10 PM, dev2@example.invalid wrote:\n"
            "> Are we all still on for Thursday's sync?\n"
        )
        out = body_normalize.strip_quoted_trail(body)
        assert out.strip() == "+1"

    def test_message_whose_sole_content_is_a_quote_does_not_become_empty(self):
        """A message with NO original content above the attribution line —
        e.g. a bare forward — must not collapse to an empty transcript
        block; the fallback keeps the original (quote included) rather
        than silently dropping the message's only content."""
        body = (
            "On Wed, Jul 22, 2026 at 7:10 PM, dev2@example.invalid wrote:\n"
            "> Are we all still on for Thursday's sync?\n"
        )
        out = body_normalize.strip_quoted_trail(body)
        assert out.strip(), "stripping a quote-only body must not yield an empty result"
        assert "Thursday's sync" in out

    def test_block_for_quote_only_message_is_not_empty(self):
        solo = _msg(
            "quote-only-1",
            thread_id="thread-quote-only",
            sender="dev2@example.invalid",
            body=(
                "On Wed, Jul 22, 2026 at 7:10 PM, dev3@example.invalid "
                "wrote:\n> Are we all still on for Thursday's sync?\n"
            ),
            internal_date_ms=_BASE_MS,
            date_header="Wed, 22 Jul 2026 19:10:00 -0700",
        )
        blocks, _decoded = _thread_message_blocks([solo], per_message_body_limit=4000)
        assert len(blocks) == 1
        assert "Thursday's sync" in blocks[0]

    def test_plain_reply_with_no_quote_is_unaffected(self):
        body = "Can you review the attached proposal before our call tomorrow?"
        assert body_normalize.strip_quoted_trail(body) == body

    def test_empty_body_returns_empty(self):
        assert body_normalize.strip_quoted_trail("") == ""


class TestTranscriptBudgetImprovement:
    """AC4 — per-message body budget measurably improves on a long thread.

    Builds a 10-message thread where each reply inlines the ENTIRE prior
    conversation as a quoted trail (the realistic Outlook shape) and
    compares the actual (quote-stripped) transcript size against the size
    the same blocks would carry with banner-stripping alone (the #2642
    behavior), computed independently via ``normalize_email_body`` over the
    same decoded bodies.
    """

    @staticmethod
    def _build_long_thread(n: int) -> List[Dict[str, Any]]:
        messages = []
        quoted_so_far = ""
        for i in range(n):
            sender = f"dev{i}@example.invalid"
            new_content = f"Reply number {i}: sounds good, moving forward."
            if quoted_so_far:
                body = (
                    f"{new_content}\n\n"
                    f"On Wed, Jul 22, 2026 at {6 + i}:00 PM, prior@example.invalid "
                    "wrote:\n" + quoted_so_far
                )
            else:
                body = new_content
            messages.append(
                _msg(
                    f"m{i}",
                    thread_id="thread-2653-long",
                    sender=sender,
                    body=body,
                    internal_date_ms=_BASE_MS + i * 60_000,
                    date_header=f"Wed, 22 Jul 2026 {6 + i}:00:00 -0700",
                )
            )
            # Next message quotes THIS entire body, one quote level deeper.
            quoted_so_far = "\n".join(f"> {line}" for line in body.splitlines())
        return messages

    def test_transcript_shrinks_measurably_on_a_long_thread(self):
        messages = self._build_long_thread(10)
        ordered = sorted(messages, key=lambda m: int(m["internalDate"]))

        # AFTER: the actual production path (quote trail stripped).
        blocks_after, _decoded = _thread_message_blocks(
            ordered, per_message_body_limit=0
        )
        after_total = sum(len(b) for b in blocks_after)

        # BEFORE: what #2642 alone (banner-strip, no quote-strip) would have
        # produced for the same decoded bodies — computed independently via
        # normalize_email_body, not by re-running a reverted code path.
        from gaia_agent_email.gmail_backend import decode_message_body

        before_total = 0
        for msg in ordered:
            payload = msg.get("payload") or {}
            raw_body, _ = decode_message_body(payload)
            normalized = body_normalize.normalize_email_body((raw_body or "").strip())
            before_total += len(normalized)

        assert after_total < before_total, (
            f"quote-trail stripping did not shrink the transcript: "
            f"before={before_total} after={after_total}"
        )
        # Record the actual numbers for the PR's evidence (not just a
        # direction check) — a long thread with O(N) quote duplication
        # should shrink substantially, not marginally.
        reduction_pct = (before_total - after_total) / before_total * 100
        assert reduction_pct > 50, (
            f"expected a substantial reduction on a 10-message thread with "
            f"full-history quoting; got before={before_total} "
            f"after={after_total} ({reduction_pct:.1f}% reduction)"
        )


class TestStripQuotedTextRegressionGuard:
    """AC5 — ``strip_quoted_text`` and its existing Sent-mail voice-profile
    caller are unaffected by this change; ``strip_quoted_trail`` reuses it
    rather than forking it."""

    def test_strip_quoted_trail_delegates_to_the_shared_strip_quoted_text(self):
        from gaia_agent_email.voice_profile import strip_quoted_text

        body = (
            "Sounds good, see you then.\n\n"
            "On Mon, Jun 2, 2026 at 9:00 AM Maria <m@x.com> wrote:\n"
            "> Are we still on for Thursday?\n"
        )
        assert (
            body_normalize.strip_quoted_trail(body) == strip_quoted_text(body).strip()
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
