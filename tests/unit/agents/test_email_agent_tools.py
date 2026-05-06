# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Behavioral tests for the email-agent tool mixins.

Each tool's pure ``*_impl`` function is exercised against a
``FakeGmailBackend`` + an in-memory ``DatabaseMixin``. Tests cover:

- Read tools return the expected envelope shape and wrap body content
  in untrusted-input delimiters (Phase I1).
- Organize tools record actions with the prior-labels payload so the
  undo path can restore them.
- Reply tools set ``In-Reply-To`` / ``References`` correctly across a
  References chain.
- Delete tools enforce the action-store ordering invariant: Gmail call
  first, DB row only on success (Adversarial B2).
- Restore enforces the undo window.
- Calendar tools surface ``missing_organizer`` when an invite has no
  organizer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make tests.fixtures importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.agents.email import action_store  # noqa: E402
from gaia.agents.email.tools.calendar_tools import (  # noqa: E402
    list_calendar_events_impl,
)
from gaia.agents.email.tools.delete_tools import (  # noqa: E402
    permanent_delete_impl,
    restore_message_impl,
    trash_message_impl,
)
from gaia.agents.email.tools.organize_tools import (  # noqa: E402
    archive_message_impl,
    label_message_impl,
)
from gaia.agents.email.tools.read_tools import (  # noqa: E402
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    list_inbox_impl,
    triage_inbox_impl,
)
from gaia.agents.email.tools.reply_tools import (  # noqa: E402
    draft_reply_impl,
)
from gaia.database.mixin import DatabaseMixin  # noqa: E402
from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeCalendarBackend,
    FakeGmailBackend,
)


@pytest.fixture
def db():
    class _DB(DatabaseMixin):
        def __init__(self):
            self.init_db(":memory:")

    db = _DB()
    action_store.init_schema(db)
    yield db
    db.close_db()


@pytest.fixture
def fake_gmail():
    return FakeGmailBackend(
        _REPO_ROOT / "tests" / "fixtures" / "email" / "_stub_inbox.mbox"
    )


@pytest.fixture
def fake_calendar():
    return FakeCalendarBackend()


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


class TestReadTools:
    def test_list_inbox_returns_messages(self, fake_gmail):
        out = list_inbox_impl(fake_gmail, max_results=50)
        assert "messages" in out
        assert len(out["messages"]) > 0
        # The fixture has 9 INBOX messages.
        assert len(out["messages"]) == 9

    def test_body_wrapped_in_untrusted_delimiters(self, fake_gmail):
        """Phase I1 / S2.M3: every body shown to the LLM is delimited."""
        out = list_inbox_impl(fake_gmail, max_results=5)
        for msg in out["messages"]:
            assert UNTRUSTED_BODY_OPEN in msg["body"]
            assert UNTRUSTED_BODY_CLOSE in msg["body"]

    def test_html_only_message_is_stripped_to_plain_text(self, fake_gmail):
        # Stub message 005 is the phishing payload — text/html only.
        out = list_inbox_impl(fake_gmail, max_results=50)
        phish = next(
            m for m in out["messages"] if "Verify your account" in m["subject"]
        )
        # Body should NOT contain the literal HTML tags.
        assert "<a href" not in phish["body"]
        assert "<html>" not in phish["body"]
        # Should still contain the human-readable phrase.
        assert "verify" in phish["body"].lower()

    def test_triage_inbox_emits_per_message_results(self, fake_gmail):
        out = triage_inbox_impl(fake_gmail, max_messages=50)
        assert "results" in out
        assert "grouped" in out
        # Every result has the required fields.
        for r in out["results"]:
            assert "id" in r
            assert "category" in r
            assert "is_spam" in r
            assert "is_phishing" in r
            assert "confident" in r

    def test_triage_emits_848_taxonomy_only(self, fake_gmail):
        out = triage_inbox_impl(fake_gmail, max_messages=50)
        legacy = {"URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"}
        for r in out["results"]:
            assert r["category"] not in legacy
            assert r["category"] in (
                "urgent",
                "actionable",
                "informational",
                "low priority",
            )

    def test_triage_flags_phishing_payload(self, fake_gmail):
        out = triage_inbox_impl(fake_gmail, max_messages=50)
        phish = [r for r in out["results"] if r["is_phishing"]]
        assert phish, "phishing payload should be flagged in stub fixture"


# ---------------------------------------------------------------------------
# Organize tools
# ---------------------------------------------------------------------------


class TestOrganizeTools:
    def test_archive_records_prior_labels(self, fake_gmail, db):
        # Pick the first inbox message.
        msg_id = list(fake_gmail._messages.keys())[0]
        prior = list(fake_gmail.get_message(msg_id).get("labelIds", []))
        out = archive_message_impl(fake_gmail, db, message_id=msg_id)
        # INBOX removed from the message.
        post = fake_gmail.get_message(msg_id)
        assert "INBOX" not in post["labelIds"]
        # Action row preserves the prior labels for undo.
        action_id = out["action_id"]
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        assert row["payload"]["prior_labels"] == prior

    def test_label_message_records_label_id(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        new_label = fake_gmail.create_label(name="follow-up")
        out = label_message_impl(
            fake_gmail, db, message_id=msg_id, label_id=new_label["id"]
        )
        action_id = out["action_id"]
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row["payload"]["label_id"] == new_label["id"]


# ---------------------------------------------------------------------------
# Delete tools — ordering invariant + undo window
# ---------------------------------------------------------------------------


class TestOrderingInvariantParameterized:
    """Adversarial B2: every mutate tool must call Gmail BEFORE the DB write.

    A 5xx / KeyError / scope-revoke from Gmail must leave zero rows in
    ``email_actions`` — no phantom undo entries.
    """

    @pytest.mark.parametrize(
        "name,call",
        [
            (
                "archive",
                lambda gmail, db: archive_message_impl(gmail, db, message_id="nope"),
            ),
            (
                "label",
                lambda gmail, db: label_message_impl(
                    gmail, db, message_id="nope", label_id="Label_1"
                ),
            ),
            (
                "trash",
                lambda gmail, db: trash_message_impl(gmail, db, message_id="nope"),
            ),
        ],
    )
    def test_mutate_tool_no_phantom_row_on_gmail_failure(
        self, fake_gmail, db, name, call
    ):
        with pytest.raises(KeyError):
            call(fake_gmail, db)
        rows = db.query("SELECT COUNT(*) AS n FROM email_actions", one=True)
        assert rows["n"] == 0, f"{name} wrote a phantom row on Gmail failure"


class TestBatchThresholdEnforcement:
    """Phase I3 / S2.M2: organize closures MUST refuse to fire past the
    batch threshold and surface an error envelope (so the agent's
    planning loop asks the user for confirmation).
    """

    def test_organize_closure_refuses_past_threshold(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        from unittest.mock import MagicMock, patch

        from gaia.agents.email.agent import EmailTriageAgent
        from gaia.agents.email.config import EmailAgentConfig

        cfg = EmailAgentConfig(
            gmail_backend=fake_gmail,
            calendar_backend=fake_calendar,
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
        )
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            agent = EmailTriageAgent(config=cfg)

        # Force the threshold trip by hand-bumping the counter past
        # the boundary (>5 ops, >3 senders).
        for sender in ("a", "b", "c", "d"):
            agent._record_organize_op(f"m-{sender}-1", sender)
        agent._record_organize_op("m-x", "a")
        agent._record_organize_op("m-y", "b")
        assert agent._organize_batch_threshold_exceeded() is True

        # archive_message must now refuse and return an error envelope
        # WITHOUT touching the Gmail backend.
        from gaia.agents.base.tools import _TOOL_REGISTRY

        archive_fn = _TOOL_REGISTRY["archive_message"]["function"]
        msg_id = list(fake_gmail._messages.keys())[0]
        # Capture pre-state — message should still be in INBOX after.
        prior = fake_gmail.get_message(msg_id)
        prior_labels = list(prior["labelIds"])
        result_str = archive_fn(msg_id)
        result = json.loads(result_str)
        assert result["ok"] is False
        assert "batch threshold" in result["error"].lower()
        # Message labels unchanged — closure refused before any Gmail call.
        post = fake_gmail.get_message(msg_id)
        assert post["labelIds"] == prior_labels
        agent.close_db()


class TestSendNowAuditTrail:
    """``send_now_impl`` must record an audit row (sent_at populated)
    so a one-shot send is visible alongside drafted-then-sent flows.
    """

    def test_send_now_writes_to_email_drafts(self, fake_gmail, db):
        from gaia.agents.email.tools.reply_tools import send_now_impl

        result = send_now_impl(
            fake_gmail,
            db,
            to="bob@example.com",
            subject="Hi",
            body="One-shot send from a unit test.",
        )
        sent_id = result["sent_id"]
        # The draft row exists AND is marked sent.
        row = action_store.fetch_draft(db, draft_id=sent_id)
        assert row is not None
        assert row["sent_at"] is not None


class TestMoveToLabelAtomicity:
    """``move_to_label_impl`` issues a single ``_modify_labels`` call so
    a partial-failure cannot leave a message half-moved.
    """

    def test_move_uses_single_modify_call(self, fake_gmail, db):
        from gaia.agents.email.tools.organize_tools import move_to_label_impl

        new_label = fake_gmail.create_label(name="archive-target")
        msg_id = list(fake_gmail._messages.keys())[0]
        # Reset transport call log.
        fake_gmail.transport.reset()
        move_to_label_impl(fake_gmail, db, message_id=msg_id, label_id=new_label["id"])
        # Recorded calls: one get_message (for prior_labels) + the
        # single underlying modify (NOT add_label + archive_message
        # as two separate calls).
        method_names = [c[0] for c in fake_gmail.transport.calls]
        assert "add_label" not in method_names, (
            "move_to_label should issue a SINGLE atomic modify, "
            "not separate add_label + archive_message"
        )
        assert "archive_message" not in method_names


class TestDeleteTools:
    def test_trash_records_after_gmail_call(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = trash_message_impl(fake_gmail, db, message_id=msg_id)
        action_id = out["action_id"]
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        # Message is now in TRASH, not INBOX.
        post = fake_gmail.get_message(msg_id)
        assert "TRASH" in post["labelIds"]
        assert "INBOX" not in post["labelIds"]

    def test_trash_no_phantom_row_when_gmail_raises(self, fake_gmail, db):
        """Adversarial B2: no DB write if Gmail call fails."""
        # Trigger a failure by trashing a non-existent message.
        with pytest.raises(KeyError):
            trash_message_impl(fake_gmail, db, message_id="nope")
        rows = db.query("SELECT COUNT(*) AS n FROM email_actions", one=True)
        assert rows["n"] == 0

    def test_restore_within_window(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = trash_message_impl(fake_gmail, db, message_id=msg_id)
        action_id = out["action_id"]
        result = restore_message_impl(
            fake_gmail, db, action_id=action_id, window_seconds=30
        )
        assert result["restored"] is True
        post = fake_gmail.get_message(msg_id)
        assert "INBOX" in post["labelIds"]
        assert "TRASH" not in post["labelIds"]

    def test_restore_after_window_raises(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = trash_message_impl(fake_gmail, db, message_id=msg_id)
        action_id = out["action_id"]
        # Force the row's created_at into the past.
        import time

        db.update(
            "email_actions",
            {"created_at": time.time() - 3600},
            "action_id = :id",
            {"id": action_id},
        )
        with pytest.raises(RuntimeError) as exc:
            restore_message_impl(fake_gmail, db, action_id=action_id, window_seconds=30)
        assert "undo window" in str(exc.value)

    def test_permanent_delete_removes_from_store(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = permanent_delete_impl(fake_gmail, db, message_id=msg_id)
        assert out["irreversible"] is True
        assert msg_id not in fake_gmail._messages


# ---------------------------------------------------------------------------
# Reply tools — threading headers
# ---------------------------------------------------------------------------


class TestReplyTools:
    def test_draft_reply_sets_in_reply_to_and_references(self, fake_gmail, db):
        # Inject a synthetic message with a deep References chain.
        chain = " ".join(f"<msg-{i}@example.com>" for i in range(12))
        synthetic = {
            "id": "thread-test-1",
            "threadId": "thread-test-1",
            "labelIds": ["INBOX"],
            "snippet": "test",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "text/plain",
                "filename": "",
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Test thread"},
                    {"name": "Message-ID", "value": "<latest@example.com>"},
                    {"name": "References", "value": chain},
                ],
                "body": {"size": 0, "data": ""},
            },
            "sizeEstimate": 100,
        }
        fake_gmail.add_message(synthetic)
        out = draft_reply_impl(
            fake_gmail, db, message_id="thread-test-1", body="Got it, thanks."
        )
        # Inspect the recorded draft headers via the fake's transport.
        last_call = [c for c in fake_gmail.transport.calls if c[0] == "create_draft"][
            -1
        ]
        headers = last_call[1].get("headers") or {}
        assert headers["In-Reply-To"] == "<latest@example.com>"
        # Existing references are preserved + the new message-id appended.
        assert "<msg-0@example.com>" in headers["References"]
        assert "<latest@example.com>" in headers["References"]
        assert out["draft_id"]

    def test_draft_reply_persists_draft_metadata(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = draft_reply_impl(
            fake_gmail, db, message_id=msg_id, body="Test reply body"
        )
        draft = action_store.fetch_draft(db, draft_id=out["draft_id"])
        assert draft is not None
        # body_preview is truncated.
        assert len(draft["body_preview"]) <= action_store.BODY_PREVIEW_MAX_CHARS


# ---------------------------------------------------------------------------
# Calendar tools
# ---------------------------------------------------------------------------


class TestCalendarTools:
    def test_list_events_flags_missing_organizer(self, fake_calendar):
        # Create an event with no organizer.
        fake_calendar.events["evt-1"] = {
            "id": "evt-1",
            "summary": "lonely event",
            "start": {"dateTime": "2026-05-06T14:00:00Z"},
            "end": {"dateTime": "2026-05-06T15:00:00Z"},
        }
        out = list_calendar_events_impl(fake_calendar, time_min=None, time_max=None)
        events = out["events"]
        assert events[0]["missing_organizer"] is True

    def test_list_events_does_not_flag_when_organizer_present(self, fake_calendar):
        fake_calendar.events["evt-2"] = {
            "id": "evt-2",
            "summary": "with organizer",
            "start": {"dateTime": "2026-05-06T14:00:00Z"},
            "end": {"dateTime": "2026-05-06T15:00:00Z"},
            "organizer": {"email": "alice@example.com"},
        }
        out = list_calendar_events_impl(fake_calendar, time_min=None, time_max=None)
        events = out["events"]
        assert events[0]["missing_organizer"] is False
