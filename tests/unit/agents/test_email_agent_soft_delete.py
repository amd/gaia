# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia_agent_email.action_store``.

Pinned behaviors:
- ``record_action`` returns a UUID action_id and inserts a row.
- ``fetch_undoable`` returns the row inside the window, None outside.
- ``mark_undone`` is idempotent.
- Concurrent inserts get distinct UUIDs.
- ``record_draft`` truncates body to BODY_PREVIEW_MAX_CHARS.
- ``mark_draft_sent`` is idempotent.
"""

from __future__ import annotations

import time

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email import action_store

from gaia.database.mixin import DatabaseMixin


class _DB(DatabaseMixin):
    """Concrete-but-empty mixin user for pure store testing."""

    def __init__(self) -> None:
        self.init_db(":memory:")


@pytest.fixture
def db():
    db = _DB()
    action_store.init_schema(db)
    yield db
    db.close_db()


# ---------------------------------------------------------------------------
# email_actions
# ---------------------------------------------------------------------------


class TestRecordAndFetch:
    def test_record_action_returns_uuid(self, db):
        action_id = action_store.record_action(
            db,
            action_type="trash",
            message_id="m1",
            payload={"prior_labels": ["INBOX"]},
        )
        # Hex UUID is 32 chars.
        assert len(action_id) == 32
        assert all(c in "0123456789abcdef" for c in action_id)

    def test_fetch_within_window_returns_row(self, db):
        action_id = action_store.record_action(
            db,
            action_type="archive",
            message_id="m1",
            payload={"removed_label": "INBOX"},
        )
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        assert row["action_type"] == "archive"
        assert row["payload"] == {"removed_label": "INBOX"}

    def test_fetch_outside_window_returns_none(self, db):
        action_id = action_store.record_action(db, action_type="trash", message_id="m1")
        # Force a stale created_at.
        db.update(
            "email_actions",
            {"created_at": time.time() - 3600},
            "action_id = :id",
            {"id": action_id},
        )
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is None

    def test_fetch_after_undo_returns_none(self, db):
        action_id = action_store.record_action(db, action_type="trash", message_id="m1")
        action_store.mark_undone(db, action_id=action_id)
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is None

    def test_concurrent_inserts_distinct_uuids(self, db):
        ids = {
            action_store.record_action(db, action_type="x", message_id=f"m{i}")
            for i in range(50)
        }
        assert len(ids) == 50

    def test_mark_undone_is_idempotent(self, db):
        action_id = action_store.record_action(db, action_type="trash", message_id="m1")
        action_store.mark_undone(db, action_id=action_id)
        first_undone = db.query(
            "SELECT undone_at FROM email_actions WHERE action_id = :id",
            {"id": action_id},
            one=True,
        )["undone_at"]
        # Re-mark — should not change the timestamp.
        action_store.mark_undone(db, action_id=action_id)
        second_undone = db.query(
            "SELECT undone_at FROM email_actions WHERE action_id = :id",
            {"id": action_id},
            one=True,
        )["undone_at"]
        assert first_undone == second_undone

    def test_batch_id_round_trip(self, db):
        a1 = action_store.record_action(
            db, action_type="archive", message_id="m1", batch_id="batch-001"
        )
        a2 = action_store.record_action(
            db, action_type="archive", message_id="m2", batch_id="batch-001"
        )
        rows = db.query(
            "SELECT action_id FROM email_actions WHERE batch_id = :b",
            {"b": "batch-001"},
        )
        assert {r["action_id"] for r in rows} == {a1, a2}

    def test_unknown_action_id_returns_none(self, db):
        assert (
            action_store.fetch_undoable(
                db, action_id="not-a-real-id", window_seconds=30
            )
            is None
        )


# ---------------------------------------------------------------------------
# email_drafts
# ---------------------------------------------------------------------------


class TestDrafts:
    def test_record_draft_truncates_body_preview(self, db):
        long_body = "x" * 1000
        action_store.record_draft(
            db,
            draft_id="d1",
            to="bob@example.com",
            subject="Hi",
            body=long_body,
        )
        row = action_store.fetch_draft(db, draft_id="d1")
        assert row is not None
        assert (
            len(row["body_preview"]) == action_store.BODY_PREVIEW_MAX_CHARS
        ), f"preview length: {len(row['body_preview'])}"

    def test_mark_draft_sent_is_idempotent(self, db):
        action_store.record_draft(
            db, draft_id="d1", to="x@example.com", subject="s", body="b"
        )
        action_store.mark_draft_sent(db, draft_id="d1")
        first_sent = action_store.fetch_draft(db, draft_id="d1")["sent_at"]
        action_store.mark_draft_sent(db, draft_id="d1")
        second_sent = action_store.fetch_draft(db, draft_id="d1")["sent_at"]
        assert first_sent == second_sent

    def test_fetch_unknown_draft_returns_none(self, db):
        assert action_store.fetch_draft(db, draft_id="nope") is None
