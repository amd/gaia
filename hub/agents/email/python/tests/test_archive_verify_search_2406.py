# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for #2406.

Two defects in one archive flow:

1. **False success** — ``archive_message_impl`` claimed success even when the
   message never left the inbox. It must verify the INBOX label was actually
   removed (from the provider's own post-archive response) and fail loudly and
   actionably otherwise — never a phantom undo row, never a false "archived".

2. **Same-day search miss** — a ``from:X received today`` search returned zero
   for a message that is present and dated today. The model's ``after:today``
   phrasing must resolve to the timezone-robust relative window
   ``newer_than:1d``, and the FakeGmailBackend must model date operators so the
   fix is covered end-to-end.

All hermetic: FakeGmailBackend + in-memory SQLite, no Lemonade, no network.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# parents[0] = tests/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import action_store  # noqa: E402
from gaia_agent_email.tools.organize_tools import archive_message_impl  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    normalize_gmail_date_operators,
    search_messages_impl,
)

from gaia.database.mixin import DatabaseMixin  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DB(DatabaseMixin):
    pass


def _fresh_db() -> _DB:
    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)
    return db


def _msg(
    *,
    gid: str,
    sender: str,
    subject: str,
    when_ms: int,
    labels=("INBOX", "UNREAD"),
) -> dict:
    return {
        "id": gid,
        "threadId": gid,
        "labelIds": list(labels),
        "snippet": subject,
        "internalDate": str(when_ms),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ],
            "body": {"size": 0},
        },
        "sizeEstimate": 100,
    }


def _count_action_rows(db: _DB) -> int:
    rows = db.query("SELECT COUNT(*) AS n FROM email_actions", {}, one=True)
    return int(rows["n"])


# ---------------------------------------------------------------------------
# Defect 1 — archive must confirm the message left the inbox
# ---------------------------------------------------------------------------


def test_archive_success_when_inbox_removed():
    gmail = FakeGmailBackend()
    gmail.add_message(
        _msg(gid="a1", sender="news@x.com", subject="Hello", when_ms=1_700_000_000_000)
    )
    db = _fresh_db()

    result = archive_message_impl(gmail, db, message_id="a1")

    assert result["message_id"] == "a1"
    assert result["action_id"]
    # The message really left the inbox.
    assert "INBOX" not in gmail.get_message("a1")["labelIds"]
    # Exactly one undo row recorded.
    assert _count_action_rows(db) == 1


def test_archive_surfaces_archived_message_identity():
    """AC(b): the return payload carries the id/subject/sender actually archived
    so the success message can cite the concrete message, not just a sender."""
    gmail = FakeGmailBackend()
    gmail.add_message(
        _msg(
            gid="a2",
            sender="Claude Team <no-reply@email.claude.com>",
            subject="Welcome to Claude",
            when_ms=1_700_000_000_000,
        )
    )
    db = _fresh_db()

    result = archive_message_impl(gmail, db, message_id="a2")

    assert result["message_id"] == "a2"
    assert result.get("subject") == "Welcome to Claude"
    assert "no-reply@email.claude.com" in (result.get("sender") or "")


class _NoOpArchiveBackend(FakeGmailBackend):
    """Models a backend whose archive silently fails to remove INBOX (the
    #2406 false-success case): the modify response still carries INBOX."""

    def archive_message(self, message_id: str):
        self._transport.record("archive_message", message_id=message_id)
        # Deliberately DO NOT remove INBOX — return the message unchanged.
        return self._messages[message_id]


def test_archive_false_success_raises_and_records_no_row():
    gmail = _NoOpArchiveBackend()
    gmail.add_message(
        _msg(gid="b1", sender="news@x.com", subject="Stuck", when_ms=1_700_000_000_000)
    )
    db = _fresh_db()

    with pytest.raises(RuntimeError, match=r"(?i)inbox"):
        archive_message_impl(gmail, db, message_id="b1")

    # No phantom undo row on a failed archive (ordering invariant).
    assert _count_action_rows(db) == 0
    # And the message is (correctly) still in the inbox — we did not lie.
    assert "INBOX" in gmail.get_message("b1")["labelIds"]


def test_archive_skips_label_check_when_result_has_no_labels():
    """Folder-based backends (Outlook) return an id-only result with no labelIds;
    verification is skipped rather than false-failing (provider-correct)."""

    class _FolderBackend(FakeGmailBackend):
        def archive_message(self, message_id: str):
            self._transport.record("archive_message", message_id=message_id)
            # Simulate a folder move: remove from store-view, return new id only.
            return {"id": "moved_" + message_id}

    gmail = _FolderBackend()
    gmail.add_message(
        _msg(gid="c1", sender="news@x.com", subject="Moved", when_ms=1_700_000_000_000)
    )
    db = _fresh_db()

    result = archive_message_impl(gmail, db, message_id="c1")
    assert result["post_archive_id"] == "moved_c1"
    assert _count_action_rows(db) == 1


# ---------------------------------------------------------------------------
# Defect 2 — same-day search must find a present, today-dated message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("from:x@y.com after:today", "from:x@y.com newer_than:1d"),
        ("after:today", "newer_than:1d"),
        ("newer:today", "newer_than:1d"),
        ("from:x@y.com after:yesterday", "from:x@y.com newer_than:2d"),
        ("after:Today", "newer_than:1d"),
    ],
)
def test_relative_day_words_normalize_to_relative_window(query, expected):
    assert normalize_gmail_date_operators(query) == expected


def test_explicit_dates_still_normalize_to_absolute():
    # The relative-word path must not disturb concrete dates.
    assert normalize_gmail_date_operators("after:July 1, 2026") == "after:2026/07/01"


def test_fake_backend_honors_newer_than_window():
    gmail = FakeGmailBackend()
    now_ms = int(time.time() * 1000)
    gmail.add_message(
        _msg(gid="today", sender="news@x.com", subject="Fresh", when_ms=now_ms)
    )
    gmail.add_message(
        _msg(
            gid="old",
            sender="news@x.com",
            subject="Stale",
            when_ms=now_ms - 10 * 86_400_000,
        )
    )
    listing = gmail.list_messages(query="newer_than:1d")
    ids = {m["id"] for m in listing["messages"]}
    assert ids == {"today"}


def test_search_finds_same_day_message_from_sender():
    """AC(d): from:X received today finds a present, same-day inbox message."""
    gmail = FakeGmailBackend()
    now_ms = int(time.time() * 1000)
    gmail.add_message(
        _msg(
            gid="fresh",
            sender="Claude Team <no-reply@email.claude.com>",
            subject="Welcome to Claude",
            when_ms=now_ms,
        )
    )
    out = search_messages_impl(
        gmail, query="from:no-reply@email.claude.com after:today"
    )
    assert len(out["messages"]) == 1
    # The outgoing query used the robust relative window.
    listed = [c for c in gmail.transport.calls if c[0] == "list_messages"]
    assert any("newer_than:1d" in (c[1]["query"] or "") for c in listed)
