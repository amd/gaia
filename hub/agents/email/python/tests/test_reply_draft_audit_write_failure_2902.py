# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply/draft/send action must not be reported as failed when the real
action already succeeded (#2902).

``state.db`` is shared with a scheduler-built autonomy agent on a separate
connection (``agent.py:_open_db`` / #1115) and ``PRAGMA busy_timeout=5000``
bounds but does not eliminate ``sqlite3.OperationalError: database is
locked``. ``send_now_impl`` already guards its post-success audit write
(``record_draft`` / ``mark_draft_sent``) with ``try/except sqlite3.Error:
log.warning(...)`` — the external send already happened, so a bookkeeping
failure must be logged, not raised. ``draft_reply_impl``, ``draft_forward_impl``,
and ``send_draft_impl`` lacked that guard: an audit-write failure after a
successful Gmail/Outlook call propagated into the tool's generic
``except Exception`` and was reported to the user as total failure, even
though the draft was created / the mail was sent. A retry then targets an
already-consumed draft id (Gmail/Outlook delete a draft on send), producing a
second, more confusing failure.

Tests are hermetic: ``FakeGmailBackend`` only, no Lemonade, no network. Audit
failures are injected by patching ``action_store.record_draft`` /
``action_store.mark_draft_sent`` (as imported into ``reply_tools``) to raise
``sqlite3.OperationalError`` — the exact exception class + message shape a
locked ``state.db`` produces.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/,
# [4] = hub/, [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.reply_tools import (  # noqa: E402
    draft_forward_impl,
    draft_reply_impl,
    send_draft_impl,
    send_now_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.connectors.errors import ConnectorsError  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

_LOCKED = sqlite3.OperationalError("database is locked")


def _msg(msg_id: str = "m1", *, sender: str = "boss@example.com") -> dict:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "internalDate": "1700000000000",
        "snippet": "Can you take a look and get back to me?",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": "Q3 plan"},
                {"name": "Date", "value": "Thu, 23 Jul 2026 09:00:00 +0000"},
            ]
        },
    }


# ---------------------------------------------------------------------------
# An audit-write failure AFTER the real action already succeeded must not be
# reported as a failure — log, don't raise.
# ---------------------------------------------------------------------------


class TestAuditWriteFailureDoesNotMaskSuccess:
    def test_draft_reply_succeeds_despite_locked_db(self):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg())

        with patch(
            "gaia_agent_email.tools.reply_tools.action_store.record_draft",
            side_effect=_LOCKED,
        ):
            result = draft_reply_impl(
                backend, db=object(), message_id="m1", body="Sounds good."
            )

        assert result["draft_id"]
        assert result["to"] == "boss@example.com"
        # The draft really was created in the mailbox despite the audit
        # write failing — the caller must never retry a create_draft call
        # that already succeeded.
        create_calls = [c for c in backend.transport.calls if c[0] == "create_draft"]
        assert len(create_calls) == 1

    def test_guard_catches_sqlite_error_only_and_does_not_over_catch(self):
        """The guard exists to stop a *bookkeeping* failure masking a real
        mailbox success — not to swallow arbitrary bugs. A non-sqlite error
        from the audit write is a genuine defect and must still propagate,
        or the guard becomes the silent fallback it was written to prevent."""
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg())

        with patch(
            "gaia_agent_email.tools.reply_tools.action_store.record_draft",
            side_effect=TypeError("record_draft() got an unexpected keyword"),
        ):
            with pytest.raises(TypeError):
                draft_reply_impl(
                    backend, db=object(), message_id="m1", body="Sounds good."
                )

    def test_draft_forward_succeeds_despite_locked_db(self):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg())

        with patch(
            "gaia_agent_email.tools.reply_tools.action_store.record_draft",
            side_effect=_LOCKED,
        ):
            result = draft_forward_impl(
                backend,
                db=object(),
                message_id="m1",
                to="colleague@example.com",
                body="FYI",
            )

        assert result["draft_id"]
        create_calls = [c for c in backend.transport.calls if c[0] == "create_draft"]
        assert len(create_calls) == 1

    def test_send_draft_succeeds_despite_locked_db(self):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg())
        created = backend.create_draft(
            to="boss@example.com", subject="Re: Q3 plan", body="Sounds good."
        )

        with patch(
            "gaia_agent_email.tools.reply_tools.action_store.mark_draft_sent",
            side_effect=_LOCKED,
        ):
            result = send_draft_impl(backend, db=object(), draft_id=created["id"])

        assert result["sent"] is True
        assert result["draft_id"] == created["id"]
        send_calls = [c for c in backend.transport.calls if c[0] == "send_draft"]
        assert len(send_calls) == 1

    def test_send_now_succeeds_despite_locked_db(self):
        """Pin the already-correct sibling too, so it can't silently regress."""
        backend = FakeGmailBackend(user_email="me@example.com")

        with (
            patch(
                "gaia_agent_email.tools.reply_tools.action_store.record_draft",
                side_effect=_LOCKED,
            ),
            patch(
                "gaia_agent_email.tools.reply_tools.action_store.mark_draft_sent",
                side_effect=_LOCKED,
            ),
        ):
            result = send_now_impl(
                backend,
                db=object(),
                to="boss@example.com",
                subject="Q3 plan",
                body="Sounds good.",
            )

        assert result["sent"] is True
        assert result["sent_id"]
        send_calls = [c for c in backend.transport.calls if c[0] == "send_message"]
        assert len(send_calls) == 1


# ---------------------------------------------------------------------------
# Genuine failures still fail loudly and actionably — this fix must not
# introduce a swallow anywhere else.
# ---------------------------------------------------------------------------


class TestGenuineFailuresStillFailLoud:
    def test_draft_reply_raises_on_unresolvable_message(self):
        """No such message id — the underlying get_message failure must
        propagate; nothing here should turn a real failure into a fake
        success just because it happens near an audit write."""
        backend = FakeGmailBackend(user_email="me@example.com")
        with pytest.raises(KeyError):
            draft_reply_impl(
                backend, db=object(), message_id="does-not-exist", body="hi"
            )

    def test_send_draft_raises_when_backend_call_itself_fails(self):
        """The audit-write guard must not swallow a failure in the real
        (pre-audit-write) backend call — only a post-success bookkeeping
        failure is forgiven."""
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg())

        def _boom(_draft_id):
            raise ConnectorsError("Gmail API POST /drafts/send returned 500: boom")

        with patch.object(backend, "send_draft", side_effect=_boom):
            with pytest.raises(ConnectorsError):
                send_draft_impl(backend, db=object(), draft_id="draft_0")


# ---------------------------------------------------------------------------
# End-to-end (tool-registry level): retrying send_draft against an
# already-consumed draft id returns the targeted message, not the raw
# connector dump. Same hermetic agent-construction pattern as
# test_draft_content_authorship_2524.py.
# ---------------------------------------------------------------------------


class _MinimalCalendarBackend:
    pass


def _build_agent(tmp_path: Path, backend: FakeGmailBackend) -> EmailTriageAgent:
    import os

    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    old = os.environ.get("GAIA_MEMORY_DISABLED")
    os.environ["GAIA_MEMORY_DISABLED"] = "1"
    try:
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            return EmailTriageAgent(config=cfg)
    finally:
        if old is None:
            del os.environ["GAIA_MEMORY_DISABLED"]
        else:
            os.environ["GAIA_MEMORY_DISABLED"] = old


def _call_tool(name: str, *args, **kwargs) -> dict:
    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"{name} tool not registered"
    return json.loads(entry["function"](*args, **kwargs))


class TestSendDraftStaleIdEndToEnd:
    def test_is_message_not_found_matches_the_gone_draft_shape(self):
        """The predicate the stale-id branch keys on. Both backends collapse
        a gone draft into a ConnectorsError carrying the status code, so this
        pins the shape the end-to-end test below depends on."""
        from gaia_agent_email.tools.reply_tools import _is_message_not_found

        stale = ConnectorsError(
            "Gmail API POST /drafts/send returned 404: draft not found"
        )
        assert _is_message_not_found(stale)

    def test_retrying_already_sent_draft_gets_actionable_message(self, tmp_path):
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_msg())
        _build_agent(tmp_path, backend)

        draft = _call_tool("draft_reply", "m1", "Sounds good.")
        assert draft["ok"] is True
        draft_id = draft["data"]["draft_id"]

        first = _call_tool("send_draft", draft_id)
        assert first["ok"] is True

        # Retry against the now-consumed draft id — the fake backend raises
        # KeyError; simulate the real backends' HTTP 404 shape instead
        # (both Gmail and Outlook collapse a gone draft into a
        # ConnectorsError carrying the status code — verified against
        # gmail_backend.py / outlook_backend.py's _raise_http).
        def _stale(_draft_id):
            raise ConnectorsError(
                "Gmail API POST /drafts/send returned 404: draft not found"
            )

        with patch.object(backend, "send_draft", side_effect=_stale):
            retry = _call_tool("send_draft", draft_id)

        assert retry["ok"] is False
        err = retry["error"].lower()
        # Leads with what was actually observed — the draft is gone — and
        # hedges the cause, because a hand-deleted draft raises the same 404
        # and in that case the mail never went out. Asserting a delivery we
        # cannot confirm is the failure this whole PR removes elsewhere.
        assert "no longer in the mailbox" in err
        assert "most likely" in err and "may also have been deleted" in err
        # Still tells the user what to do.
        assert "don't retry" in err and "sent mail" in err
        # Not the raw connector-error dump.
        assert "returned 404" not in retry["error"]
