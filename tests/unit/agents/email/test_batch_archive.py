# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Issue #1270 — batch archive of 20+ emails in a single action, reversible
within the undo window.

These tests pin two acceptance criteria:

1. ``archive_message_batch`` archives 20+ selected messages in ONE tool
   call (one shared ``batch_id``, one row per message, all out of INBOX).
2. The batch is reversible within the undo window via
   ``undo_archive_batch`` — every message returns to its prior label set
   (INBOX restored) and every action row is marked undone. Outside the
   window the undo fails loudly rather than silently no-op'ing.

Everything runs against the in-memory ``FakeGmailBackend`` + an
``EmailTriageAgent`` whose ``AgentSDK`` is mocked — no Lemonade, no eval.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# Make tests.fixtures importable.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email import action_store  # noqa: E402

from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeCalendarBackend,
    FakeGmailBackend,
)

# Number of messages in the batch. Must be >= 20 per the AC.
_BATCH_SIZE = 25


def _make_inbox_message(idx: int, *, extra_labels=None) -> dict:
    """Build a minimal Gmail-API-shape INBOX message."""
    labels = ["INBOX", "UNREAD"]
    if extra_labels:
        labels.extend(extra_labels)
    return {
        "id": f"msg-{idx:03d}",
        "threadId": f"thread-{idx:03d}",
        "labelIds": labels,
        "snippet": f"snippet {idx}",
        "internalDate": str(1_700_000_000_000 + idx),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "From", "value": f"sender{idx}@example.com"},
                {"name": "Subject", "value": f"Bulk message {idx}"},
                {"name": "Message-ID", "value": f"<bulk-{idx}@example.com>"},
            ],
            "body": {"size": 0, "data": ""},
        },
        "sizeEstimate": 100,
    }


@pytest.fixture
def bulk_gmail():
    """A fake Gmail backend pre-loaded with ``_BATCH_SIZE`` INBOX messages.

    One message also carries STARRED so the undo path is forced to restore
    the FULL prior label set, not merely re-add INBOX.
    """
    gmail = FakeGmailBackend()
    for i in range(_BATCH_SIZE):
        extra = ["STARRED"] if i == 0 else None
        gmail.add_message(_make_inbox_message(i, extra_labels=extra))
    return gmail


@pytest.fixture
def fake_calendar():
    return FakeCalendarBackend()


def _make_email_agent(gmail, calendar, tmp_path):
    """EmailTriageAgent with backends injected and AgentSDK mocked."""
    from unittest.mock import MagicMock, patch

    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=gmail,
        calendar_backend=calendar,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


def _tool(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


class TestBatchArchive20Plus:
    def test_archives_20_plus_in_one_action(self, bulk_gmail, fake_calendar, tmp_path):
        """AC #1: a single ``archive_message_batch`` call archives 20+
        messages — one shared batch_id, all removed from INBOX.
        """
        agent = _make_email_agent(bulk_gmail, fake_calendar, tmp_path)
        try:
            ids = list(bulk_gmail._messages.keys())
            assert len(ids) >= 20

            result = json.loads(_tool("archive_message_batch")(ids))
            assert result["ok"] is True
            data = result["data"]
            assert data["total"] == len(ids)
            assert len(data["succeeded"]) == len(ids)
            assert data["failed"] == []

            # One shared batch_id across every recorded row.
            assert data["batch_id"]
            all_rows = agent.query("SELECT batch_id FROM email_actions")
            batch_ids = {r["batch_id"] for r in all_rows}
            assert batch_ids == {data["batch_id"]}
            assert len(all_rows) == len(ids)

            # Every message is out of INBOX.
            for mid in ids:
                post = bulk_gmail.get_message(mid)
                assert "INBOX" not in post["labelIds"], f"{mid} still in INBOX"
        finally:
            agent.close_db()

    def test_batch_archive_reversible_within_undo_window(
        self, bulk_gmail, fake_calendar, tmp_path
    ):
        """AC #2: ``undo_archive_batch`` restores all 20+ messages to their
        prior label set within the window.
        """
        agent = _make_email_agent(bulk_gmail, fake_calendar, tmp_path)
        try:
            ids = list(bulk_gmail._messages.keys())
            prior_labels = {
                mid: list(bulk_gmail.get_message(mid)["labelIds"]) for mid in ids
            }

            archive_result = json.loads(_tool("archive_message_batch")(ids))
            batch_id = archive_result["data"]["batch_id"]

            # Sanity: all archived.
            for mid in ids:
                assert "INBOX" not in bulk_gmail.get_message(mid)["labelIds"]

            undo_result = json.loads(_tool("undo_archive_batch")(batch_id))
            assert undo_result["ok"] is True, undo_result
            data = undo_result["data"]
            assert data["restored"] == len(ids)

            # Every message is back with its FULL prior label set restored —
            # including the message that also carried STARRED.
            for mid in ids:
                post = bulk_gmail.get_message(mid)
                assert "INBOX" in post["labelIds"], f"{mid} not restored to INBOX"
                assert set(post["labelIds"]) == set(prior_labels[mid]), (
                    f"{mid} prior labels not fully restored: "
                    f"{post['labelIds']} != {prior_labels[mid]}"
                )

            # Every row in the batch is now marked undone.
            rows = agent.query(
                "SELECT undone_at FROM email_actions WHERE batch_id = :b",
                {"b": batch_id},
            )
            assert len(rows) == len(ids)
            assert all(r["undone_at"] is not None for r in rows)
        finally:
            agent.close_db()

    def test_batch_archive_undo_fails_after_window(
        self, bulk_gmail, fake_calendar, tmp_path
    ):
        """Outside the undo window the batch undo must fail loudly (no
        silent no-op) and leave the messages archived.
        """
        agent = _make_email_agent(bulk_gmail, fake_calendar, tmp_path)
        try:
            ids = list(bulk_gmail._messages.keys())
            archive_result = json.loads(_tool("archive_message_batch")(ids))
            batch_id = archive_result["data"]["batch_id"]

            # Force every row's created_at into the past (beyond the window).
            agent.update(
                "email_actions",
                {"created_at": time.time() - 3600},
                "batch_id = :b",
                {"b": batch_id},
            )

            undo_result = json.loads(_tool("undo_archive_batch")(batch_id))
            assert undo_result["ok"] is False
            assert "undo window" in undo_result["error"].lower()

            # Messages remain archived — undo did not partially fire.
            for mid in ids:
                assert "INBOX" not in bulk_gmail.get_message(mid)["labelIds"]
        finally:
            agent.close_db()


class TestFetchBatchUndoable:
    """``action_store.fetch_batch_undoable`` returns the not-yet-undone rows for
    a batch while the batch's window (anchored to COMPLETION — the latest op —
    since #2163) is still open, and excludes undone ones.
    """

    @pytest.fixture
    def db(self):
        from gaia.database.mixin import DatabaseMixin

        class _DB(DatabaseMixin):
            def __init__(self):
                self.init_db(":memory:")

        db = _DB()
        action_store.init_schema(db)
        yield db
        db.close_db()

    def test_returns_all_in_window_rows_for_batch(self, db):
        for i in range(3):
            action_store.record_action(
                db,
                action_type="archive",
                message_id=f"m{i}",
                payload={"prior_labels": ["INBOX"]},
                batch_id="b1",
            )
        # A row in a different batch must not leak in.
        action_store.record_action(
            db, action_type="archive", message_id="other", batch_id="b2"
        )
        rows = action_store.fetch_batch_undoable(db, batch_id="b1", window_seconds=30)
        assert {r["message_id"] for r in rows} == {"m0", "m1", "m2"}

    def test_undone_excluded_and_window_anchored_to_completion(self, db):
        # #2163 — the window is anchored to batch COMPLETION (the latest op), not
        # per row. An individually-older row that shares a batch whose latest op
        # is fresh stays undoable, so the earliest items of a multi-second run no
        # longer expire mid-run. Already-undone rows are still excluded per row.
        a_fresh = action_store.record_action(
            db, action_type="archive", message_id="fresh", batch_id="b1"
        )
        a_early = action_store.record_action(
            db, action_type="archive", message_id="early", batch_id="b1"
        )
        a_undone = action_store.record_action(
            db, action_type="archive", message_id="undone", batch_id="b1"
        )
        # ``early`` is older than the window in isolation, but the batch's latest
        # op (``fresh``) is well within it — so the whole batch is still undoable.
        db.update(
            "email_actions",
            {"created_at": time.time() - 20},
            "action_id = :id",
            {"id": a_early},
        )
        action_store.mark_undone(db, action_id=a_undone)

        rows = action_store.fetch_batch_undoable(db, batch_id="b1", window_seconds=30)
        ids = {r["action_id"] for r in rows}
        assert a_fresh in ids
        assert a_early in ids  # #2163: kept — window runs from completion
        assert a_undone not in ids

    def test_unknown_batch_returns_empty(self, db):
        assert (
            action_store.fetch_batch_undoable(db, batch_id="nope", window_seconds=30)
            == []
        )
