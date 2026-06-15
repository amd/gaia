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
# mailbox column + migration (#1603 Phase 2)
# ---------------------------------------------------------------------------


class TestMailboxColumn:
    def test_fresh_db_has_mailbox_column(self, db):
        cols = {row["name"] for row in db.query("PRAGMA table_info(email_actions)")}
        assert "mailbox" in cols

    def test_record_action_stores_mailbox(self, db):
        action_id = action_store.record_action(
            db, action_type="trash", message_id="m1", mailbox="microsoft"
        )
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        assert row["mailbox"] == "microsoft"

    def test_record_action_without_mailbox_stores_null(self, db):
        action_id = action_store.record_action(db, action_type="trash", message_id="m1")
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        assert row["mailbox"] is None

    def test_fetch_batch_undoable_surfaces_mailbox(self, db):
        action_store.record_action(
            db,
            action_type="archive",
            message_id="m1",
            batch_id="b1",
            mailbox="google",
        )
        rows = action_store.fetch_batch_undoable(db, batch_id="b1", window_seconds=30)
        assert rows and rows[0]["mailbox"] == "google"


class TestMailboxMigration:
    """A pre-#1603 DB (no mailbox column) gets the column added + rows
    backfilled to 'google' — every pre-multi-inbox action could only have hit
    the single Gmail mailbox.
    """

    _LEGACY_ACTIONS_DDL = """
    CREATE TABLE email_actions (
        action_id    TEXT PRIMARY KEY,
        action_type  TEXT NOT NULL,
        message_id   TEXT NOT NULL,
        thread_id    TEXT,
        payload_json TEXT NOT NULL,
        batch_id     TEXT,
        created_at   REAL NOT NULL,
        undone_at    REAL
    );
    """

    def _legacy_db(self):
        legacy = _DB()
        legacy.execute(self._LEGACY_ACTIONS_DDL)
        legacy.execute(action_store.EMAIL_DRAFTS_DDL)
        return legacy

    def test_migration_adds_column_to_legacy_db(self):
        legacy = self._legacy_db()
        try:
            cols = {
                row["name"] for row in legacy.query("PRAGMA table_info(email_actions)")
            }
            assert "mailbox" not in cols  # genuinely legacy
            action_store.init_schema(legacy)
            cols = {
                row["name"] for row in legacy.query("PRAGMA table_info(email_actions)")
            }
            assert "mailbox" in cols
        finally:
            legacy.close_db()

    def test_migration_backfills_legacy_rows_to_google(self):
        legacy = self._legacy_db()
        try:
            legacy.insert(
                "email_actions",
                {
                    "action_id": "legacy1",
                    "action_type": "trash",
                    "message_id": "m1",
                    "thread_id": None,
                    "payload_json": "{}",
                    "batch_id": None,
                    "created_at": time.time(),
                    "undone_at": None,
                },
            )
            action_store.init_schema(legacy)
            row = legacy.query(
                "SELECT mailbox FROM email_actions WHERE action_id = 'legacy1'",
                one=True,
            )
            assert row["mailbox"] == "google"
        finally:
            legacy.close_db()

    def test_migration_is_idempotent(self):
        legacy = self._legacy_db()
        try:
            action_store.init_schema(legacy)
            # Second init must not raise (duplicate-column guard).
            action_store.init_schema(legacy)
        finally:
            legacy.close_db()

    def test_migration_does_not_overwrite_recorded_mailbox(self, db):
        # A row recorded with an explicit mailbox keeps it across re-init.
        action_id = action_store.record_action(
            db, action_type="trash", message_id="m1", mailbox="microsoft"
        )
        action_store.init_schema(db)
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row["mailbox"] == "microsoft"


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
