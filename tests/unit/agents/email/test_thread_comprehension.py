# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Offline unit tests for full-thread comprehension (#1268). No Lemonade.

The agent must summarize the FULL thread, not only the latest message. These
tests build a 3-message thread in-memory (via ``FakeGmailBackend.add_message``)
where a decision is announced in the FIRST message and the latest message does
NOT repeat it, then assert:

  * the full transcript fed to the LLM contains the early (non-latest) decision,
  * all messages are present oldest-first,
  * each body is wrapped in the untrusted-input delimiters,
  * the summarize-thread tool returns a success envelope over the whole thread,
  * LLM failure / empty thread fail loudly (error envelope), never silently
    collapsing to a latest-only summary.

The chat client is a deterministic double — mirrors the chat-double pattern in
``test_summarize_tools.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.agents.email.tools.read_tools import (  # noqa: E402
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    ReadToolsMixin,
    summarize_thread_impl,
)
from gaia.agents.email.tools.summarize_tools import (  # noqa: E402
    DEFAULT_SUMMARY_CHAR_LIMIT,
    EmailSummarizeError,
)
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

THREAD_ID = "thread-decision-001"

# The decision lives ONLY in the first message; the latest message references it
# obliquely without restating it. A latest-only summary would miss it.
_DECISION_PHRASE = "we will standardize on PostgreSQL"
_MIDDLE_PHRASE = "I can have the migration script ready by Friday"
_LATEST_PHRASE = "sounds good, kicking it off now"


def _msg(
    *,
    msg_id: str,
    sender: str,
    date_rfc: str,
    internal_ms: str,
    body: str,
    subject: str = "Re: Database choice for the new service",
    label_ids=None,
) -> dict:
    """Build one Gmail-API-shape message in the shared thread."""
    return {
        "id": msg_id,
        "threadId": THREAD_ID,
        "labelIds": list(label_ids or ["INBOX", "UNREAD"]),
        "snippet": body[:80],
        "internalDate": internal_ms,
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "User <user@example.com>"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date_rfc},
            ],
            "body": {
                "size": len(body),
                "data": __import__("base64")
                .urlsafe_b64encode(body.encode("utf-8"))
                .decode("ascii")
                .rstrip("="),
            },
        },
        "sizeEstimate": len(body),
    }


def _seed_thread(gmail: FakeGmailBackend) -> None:
    """Inject a 3-message thread. Decision in #1, NOT repeated in the latest."""
    # Insert out of chronological order on purpose: the impl must sort by
    # internalDate, not rely on insertion order.
    gmail.add_message(
        _msg(
            msg_id="m-latest",
            sender="Alice <alice@company.example>",
            date_rfc="Wed, 7 May 2026 11:00:00 -0700",
            internal_ms="1715104800000",  # newest
            body=(
                f"Great — {_LATEST_PHRASE}. Thanks everyone, no further "
                "questions from my side."
            ),
        )
    )
    gmail.add_message(
        _msg(
            msg_id="m-first",
            sender="Bob <bob@company.example>",
            date_rfc="Mon, 5 May 2026 09:00:00 -0700",
            internal_ms="1714924800000",  # oldest
            body=(
                "After comparing the options for the new billing service, "
                f"{_DECISION_PHRASE} as our primary datastore. MySQL is out."
            ),
        )
    )
    gmail.add_message(
        _msg(
            msg_id="m-middle",
            sender="Carol <carol@company.example>",
            date_rfc="Tue, 6 May 2026 14:00:00 -0700",
            internal_ms="1715022000000",  # middle
            body=f"Acknowledged. {_MIDDLE_PHRASE} so we can start testing.",
        )
    )


# ---------------------------------------------------------------------------
# chat doubles
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _EchoChat:
    """Records the transcript it was sent; returns a summary that names the
    early (non-latest) decision — proving the model could see it."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_messages = None
        self.last_system_prompt = None

    def send_messages(self, messages, system_prompt=None, **kwargs):
        self.calls += 1
        self.last_messages = messages
        self.last_system_prompt = system_prompt
        return _Resp(
            "The team decided to standardize on PostgreSQL for the billing "
            "service; the migration is now underway."
        )


class _RaisingChat:
    def send_messages(self, *a, **k):
        raise ConnectionError("lemonade unreachable")


# ---------------------------------------------------------------------------
# summarize_thread_impl (pure function)
# ---------------------------------------------------------------------------


class TestSummarizeThreadImpl:
    def test_full_thread_transcript_includes_non_latest_decision(self):
        """AC: the FULL thread (not just the latest message) reaches the LLM."""
        gmail = FakeGmailBackend()
        _seed_thread(gmail)
        chat = _EchoChat()

        result = summarize_thread_impl(gmail, chat, thread_id=THREAD_ID)

        # The transcript actually sent to the model is the load-bearing proof:
        # it must carry the decision from the NON-latest first message.
        transcript = chat.last_messages[0]["content"]
        assert _DECISION_PHRASE in transcript, "early decision missing from prompt"
        assert _MIDDLE_PHRASE in transcript, "middle message missing from prompt"
        assert _LATEST_PHRASE in transcript, "latest message missing from prompt"

        # Oldest-first ordering: first message's decision precedes the latest.
        assert transcript.index(_DECISION_PHRASE) < transcript.index(_LATEST_PHRASE)
        assert transcript.index(_MIDDLE_PHRASE) < transcript.index(_LATEST_PHRASE)

        assert result["message_count"] == 3
        assert result["thread_id"] == THREAD_ID
        assert result["summary"]
        assert len(result["summary"]) <= DEFAULT_SUMMARY_CHAR_LIMIT
        assert chat.calls == 1

    def test_each_message_body_wrapped_in_untrusted_delimiters(self):
        gmail = FakeGmailBackend()
        _seed_thread(gmail)
        chat = _EchoChat()
        summarize_thread_impl(gmail, chat, thread_id=THREAD_ID)
        transcript = chat.last_messages[0]["content"]
        # 3 messages → 3 wrapped bodies.
        assert transcript.count(UNTRUSTED_BODY_OPEN) == 3
        assert transcript.count(UNTRUSTED_BODY_CLOSE) == 3

    def test_llm_failure_raises_never_collapses_to_latest_only(self):
        gmail = FakeGmailBackend()
        _seed_thread(gmail)
        with pytest.raises(EmailSummarizeError):
            summarize_thread_impl(gmail, _RaisingChat(), thread_id=THREAD_ID)

    def test_empty_thread_raises(self):
        gmail = FakeGmailBackend()  # no messages seeded
        with pytest.raises(EmailSummarizeError, match="no messages|empty"):
            summarize_thread_impl(gmail, _EchoChat(), thread_id="does-not-exist")

    def test_missing_chat_raises(self):
        gmail = FakeGmailBackend()
        _seed_thread(gmail)
        with pytest.raises(EmailSummarizeError, match="chat|LLM"):
            summarize_thread_impl(gmail, None, thread_id=THREAD_ID)


# ---------------------------------------------------------------------------
# summarize_thread tool (registered via ReadToolsMixin)
# ---------------------------------------------------------------------------


class _Recorder:
    """Captures the @tool functions a mixin registers without a real Agent."""

    def __init__(self, gmail, chat, debug=False):
        self._gmail = gmail
        self.chat = chat
        self._tools = {}

        class _Cfg:
            pass

        self.config = _Cfg()
        self.config.debug = debug
        self.config.force_llm = False


def _register_read_via_mixin(gmail, chat):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    _TOOL_REGISTRY.clear()
    host = _Recorder(gmail, chat)
    ReadToolsMixin._register_read_tools(host)
    return dict(_TOOL_REGISTRY)


class TestSummarizeThreadTool:
    def test_tool_summarizes_full_thread(self):
        gmail = FakeGmailBackend()
        _seed_thread(gmail)
        chat = _EchoChat()
        tools = _register_read_via_mixin(gmail, chat)
        assert "summarize_thread" in tools

        raw = tools["summarize_thread"]["function"](thread_id=THREAD_ID)
        env = json.loads(raw)
        assert env["ok"] is True
        data = env["data"]
        assert data["message_count"] == 3
        assert data["thread_id"] == THREAD_ID
        assert data["summary"]
        # The non-latest decision reached the model.
        assert _DECISION_PHRASE in chat.last_messages[0]["content"]

    def test_tool_returns_error_envelope_on_llm_failure(self):
        gmail = FakeGmailBackend()
        _seed_thread(gmail)
        tools = _register_read_via_mixin(gmail, _RaisingChat())
        raw = tools["summarize_thread"]["function"](thread_id=THREAD_ID)
        env = json.loads(raw)
        assert env["ok"] is False
        assert env["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
