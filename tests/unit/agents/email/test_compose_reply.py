# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Offline unit tests for tone+context-aware draft-reply composition (#1269).

Goal: verify that compose_reply_impl (and the registered compose_reply tool):
  (a) produces a non-empty draft body that includes thread context (subject /
      sender / snippet appear in the prompt the compose path builds), and
  (b) creates a Gmail draft via ``create_draft`` — it does NOT auto-send.

No Lemonade required — the LLM chat is a deterministic double throughout.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.agents.email.tools.reply_tools import (  # noqa: E402
    _COMPOSE_THREAD_BUDGET_CHARS,
    ReplyToolsMixin,
    _build_compose_prompt,
    compose_reply_impl,
)
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers — build in-memory Gmail messages
# ---------------------------------------------------------------------------

THREAD_ID = "thread-compose-001"
_BOSS_SENDER = "Boss <boss@company.example>"
_USER_EMAIL = "user@example.com"


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _make_msg(
    *,
    msg_id: str,
    sender: str,
    subject: str,
    body_text: str,
    thread_id: str = THREAD_ID,
    msg_header_id: str | None = None,
) -> dict:
    """Return a Gmail-API-shape message dict."""
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": body_text[:200],
        "internalDate": "1746450000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": _USER_EMAIL},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": msg_header_id or f"<{msg_id}@test>"},
            ],
            "body": {"size": len(body_text), "data": _b64url(body_text)},
        },
    }


# ---------------------------------------------------------------------------
# Chat doubles
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeChat:
    """Returns a fixed body string; records what it was called with."""

    def __init__(self, body: str) -> None:
        self._body = body
        self.calls: list[dict] = []

    def send_messages(self, messages, system_prompt=None, **kwargs):
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
        return _Resp(self._body)


class _RaisingChat:
    def send_messages(self, *a, **k):
        raise ConnectionError("lemonade unreachable")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SUBJECT = "Q2 headcount projections needed by 3pm"
_ORIGINAL_BODY = (
    "Hi,\n\nThe Q2 budget review is this afternoon and I need your headcount "
    "projections by 3pm. Please prioritize.\n\nThanks,\nBoss"
)
_LLM_DRAFT_BODY = (
    "Hi Boss,\n\nThanks for the heads-up. I'll have the headcount projections "
    "over to you by 3pm.\n\nBest,\nUser"
)


@pytest.fixture
def gmail_with_msg():
    gmail = FakeGmailBackend(user_email=_USER_EMAIL)
    msg = _make_msg(
        msg_id="msg-compose-001",
        sender=_BOSS_SENDER,
        subject=_SUBJECT,
        body_text=_ORIGINAL_BODY,
        msg_header_id="<stub-boss-q2@company.example>",
    )
    gmail.add_message(msg)
    return gmail, msg["id"]


@pytest.fixture
def gmail_with_thread(gmail_with_msg):
    """Two-message thread: original from Boss + user's prior reply."""
    gmail, first_id = gmail_with_msg
    reply_body = "Hi Boss, noted — working on it now.\n\nUser"
    reply = _make_msg(
        msg_id="msg-compose-002",
        sender=f"User <{_USER_EMAIL}>",
        subject=f"Re: {_SUBJECT}",
        body_text=reply_body,
        thread_id=THREAD_ID,
        msg_header_id="<stub-user-reply@example.com>",
    )
    gmail.add_message(reply)
    return gmail, first_id


# ---------------------------------------------------------------------------
# compose_reply_impl — pure function tests
# ---------------------------------------------------------------------------


class TestComposeReplyImpl:
    def test_creates_draft_with_non_empty_body(self, gmail_with_msg, tmp_path):
        """Headline gate (a): a draft is created with a non-empty body."""
        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)
        chat = _FakeChat(_LLM_DRAFT_BODY)

        result = compose_reply_impl(gmail, db, chat=chat, message_id=msg_id)

        assert result["draft_id"]
        assert result["body_preview"]  # non-empty body preview
        # Draft exists in the fake backend.
        drafts = gmail.list_drafts()
        assert len(drafts) == 1
        assert drafts[0]["body"] == _LLM_DRAFT_BODY

    def test_no_send_side_effect(self, gmail_with_msg, tmp_path):
        """Headline gate (b): NONE of the send paths is invoked."""
        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)
        chat = _FakeChat(_LLM_DRAFT_BODY)

        compose_reply_impl(gmail, db, chat=chat, message_id=msg_id)

        send_calls = [
            m for m in gmail.transport.calls if m[0] in ("send_draft", "send_message")
        ]
        assert send_calls == [], f"Unexpected send side-effects: {send_calls}"

    def test_prompt_includes_subject(self, gmail_with_msg, tmp_path):
        """Structural context test: subject appears in the composed prompt."""
        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)
        chat = _FakeChat(_LLM_DRAFT_BODY)

        compose_reply_impl(gmail, db, chat=chat, message_id=msg_id)

        assert chat.calls, "compose path must call the LLM"
        prompt_content = chat.calls[0]["messages"][0]["content"]
        assert (
            _SUBJECT in prompt_content
        ), f"Subject {_SUBJECT!r} not found in LLM prompt. Prompt:\n{prompt_content}"

    def test_prompt_includes_sender(self, gmail_with_msg, tmp_path):
        """Structural context test: sender appears in the composed prompt."""
        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)
        chat = _FakeChat(_LLM_DRAFT_BODY)

        compose_reply_impl(gmail, db, chat=chat, message_id=msg_id)

        prompt_content = chat.calls[0]["messages"][0]["content"]
        # The From header is "Boss <boss@company.example>" — either the name
        # or address should appear.
        assert (
            "boss@company.example" in prompt_content or "Boss" in prompt_content
        ), f"Sender not found in LLM prompt. Prompt:\n{prompt_content}"

    def test_prompt_includes_original_body(self, gmail_with_msg, tmp_path):
        """Structural context test: original body (snippet) included in prompt."""
        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)
        chat = _FakeChat(_LLM_DRAFT_BODY)

        compose_reply_impl(gmail, db, chat=chat, message_id=msg_id)

        prompt_content = chat.calls[0]["messages"][0]["content"]
        # The original body phrase should appear in the compose prompt.
        assert (
            "headcount" in prompt_content.lower()
        ), f"Original body content not found in LLM prompt:\n{prompt_content}"

    def test_prompt_contains_tone_guidance(self, gmail_with_msg, tmp_path):
        """System prompt must instruct the LLM to match the user's tone/style."""
        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)
        chat = _FakeChat(_LLM_DRAFT_BODY)

        compose_reply_impl(gmail, db, chat=chat, message_id=msg_id)

        system_prompt = chat.calls[0]["system_prompt"] or ""
        # The system prompt must mention tone or style guidance.
        assert any(
            kw in system_prompt.lower() for kw in ("tone", "style", "match")
        ), f"System prompt lacks tone guidance:\n{system_prompt}"

    def test_thread_context_included_when_thread_has_multiple_messages(
        self, gmail_with_thread, tmp_path
    ):
        """When the thread has multiple messages, all are included in the prompt."""
        gmail, first_msg_id = gmail_with_thread
        db = _make_db_agent(tmp_path)
        chat = _FakeChat(_LLM_DRAFT_BODY)

        compose_reply_impl(gmail, db, chat=chat, message_id=first_msg_id)

        prompt_content = chat.calls[0]["messages"][0]["content"]
        # The user's prior reply body should appear as thread context.
        assert (
            "working on it now" in prompt_content.lower()
        ), f"Thread context (prior reply) not found in prompt:\n{prompt_content}"

    def test_llm_failure_raises_never_defaults(self, gmail_with_msg, tmp_path):
        """LLM unreachable must raise, never silently return an empty draft."""
        from gaia.agents.email.tools.reply_tools import ComposeReplyError  # noqa

        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)

        with pytest.raises(ComposeReplyError, match="failed"):
            compose_reply_impl(gmail, db, chat=_RaisingChat(), message_id=msg_id)

    def test_llm_empty_body_raises_never_defaults(self, gmail_with_msg, tmp_path):
        """LLM returning blank text must raise, not create an empty draft."""
        from gaia.agents.email.tools.reply_tools import ComposeReplyError  # noqa

        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)

        with pytest.raises(ComposeReplyError, match="empty"):
            compose_reply_impl(gmail, db, chat=_FakeChat("   \n  "), message_id=msg_id)


# ---------------------------------------------------------------------------
# _build_compose_prompt — transcript budget bound
# ---------------------------------------------------------------------------


class TestComposePromptBudget:
    def test_long_thread_transcript_is_bounded(self):
        """A thread with many messages must not blow past the budget.

        The per-message floor (max(200, budget // n)) alone lets a long thread
        exceed _COMPOSE_THREAD_BUDGET_CHARS; the joined transcript is hard-capped.
        """
        # 100 messages × ~600-char bodies would be ~60K of body before headers;
        # the per-message floor is 200, so the per-message path alone would emit
        # ~20K+ of transcript. The hard cap must bring it back under budget.
        big_body = "This is a sentence in a long thread. " * 30  # ~1100 chars
        thread = [
            _make_msg(
                msg_id=f"m-{i:03d}",
                sender=f"Person {i} <p{i}@example.com>",
                subject="Long thread",
                body_text=big_body,
            )
            for i in range(100)
        ]

        prompt = _build_compose_prompt(
            subject="Long thread",
            original_sender="Person 0 <p0@example.com>",
            thread_messages=thread,
        )

        # The transcript section sits between the "Full thread" header and the
        # closing instruction; assert it stays within budget (plus a small
        # allowance for the truncation marker and surrounding prompt scaffolding).
        assert "[transcript truncated]" in prompt
        # The whole prompt must stay close to the budget, not balloon to 20K+.
        assert len(prompt) <= _COMPOSE_THREAD_BUDGET_CHARS + 500, len(prompt)


# ---------------------------------------------------------------------------
# ReplyToolsMixin — registered tool surface test
# ---------------------------------------------------------------------------


def _make_db_agent(tmp_path: Path):
    """Return a DatabaseMixin-backed host with the email action/draft schema.

    Uses the real DatabaseMixin so insert/update/execute match production exactly.
    """
    from gaia.agents.email import action_store
    from gaia.database.mixin import DatabaseMixin

    class _DbHost(DatabaseMixin):
        pass

    db_path = str(tmp_path / "state.db")
    host = _DbHost()
    host.init_db(db_path)
    action_store.init_schema(host)
    return host


class _Recorder:
    """Minimal host to drive ReplyToolsMixin._register_reply_tools.

    The ``db`` argument is a real DatabaseMixin instance — all audit writes
    in the reply-tools path (draft_reply_impl → action_store.record_draft)
    delegate to it so insert/update/execute match production exactly.
    """

    def __init__(self, gmail, chat, db_host, debug=False):
        self._gmail = gmail
        self.chat = chat
        self._db_host = db_host

        class _Cfg:
            pass

        self.config = _Cfg()
        self.config.debug = debug

    # DatabaseMixin surface — delegate to the real host. Signatures mirror
    # DatabaseMixin exactly (execute takes only `sql`; query takes sql+params)
    # so the stub can't paper over a signature drift in the real path.
    def insert(self, table, data):
        return self._db_host.insert(table, data)

    def update(self, table, data, where, params):
        return self._db_host.update(table, data, where, params)

    def execute(self, sql):
        return self._db_host.execute(sql)

    def query(self, sql, params=None, one=False):
        return self._db_host.query(sql, params, one=one)


def _register_reply_tools(gmail, chat, db_host):
    """Drive ReplyToolsMixin._register_reply_tools and capture the tools it registers."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    _TOOL_REGISTRY.clear()
    host = _Recorder(gmail, chat, db_host)
    ReplyToolsMixin._register_reply_tools(host)
    return dict(_TOOL_REGISTRY)


class TestComposeReplyTool:
    def test_tool_is_registered(self, gmail_with_msg, tmp_path):
        """compose_reply is in the tool registry after _register_reply_tools."""
        gmail, _ = gmail_with_msg
        chat = _FakeChat(_LLM_DRAFT_BODY)
        db = _make_db_agent(tmp_path)
        tools = _register_reply_tools(gmail, chat, db)
        assert "compose_reply" in tools

    def test_tool_returns_ok_envelope(self, gmail_with_msg, tmp_path):
        """compose_reply tool returns {ok: true, data: {...}} on success."""
        gmail, msg_id = gmail_with_msg
        chat = _FakeChat(_LLM_DRAFT_BODY)
        db = _make_db_agent(tmp_path)
        tools = _register_reply_tools(gmail, chat, db)

        raw = tools["compose_reply"]["function"](message_id=msg_id)
        env = json.loads(raw)
        assert env["ok"] is True
        data = env["data"]
        assert data["draft_id"]
        assert data["body_preview"]

    def test_tool_no_send_side_effect(self, gmail_with_msg, tmp_path):
        """compose_reply tool must not invoke any send path."""
        gmail, msg_id = gmail_with_msg
        chat = _FakeChat(_LLM_DRAFT_BODY)
        db = _make_db_agent(tmp_path)
        tools = _register_reply_tools(gmail, chat, db)

        tools["compose_reply"]["function"](message_id=msg_id)

        send_calls = [
            m for m in gmail.transport.calls if m[0] in ("send_draft", "send_message")
        ]
        assert send_calls == [], f"compose_reply caused send side-effects: {send_calls}"

    def test_tool_returns_error_envelope_on_llm_failure(self, gmail_with_msg, tmp_path):
        """compose_reply tool returns {ok: false, error: ...} on LLM failure."""
        gmail, msg_id = gmail_with_msg
        db = _make_db_agent(tmp_path)
        tools = _register_reply_tools(gmail, _RaisingChat(), db)

        raw = tools["compose_reply"]["function"](message_id=msg_id)
        env = json.loads(raw)
        assert env["ok"] is False
        assert env["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
