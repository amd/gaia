# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Relative-time resolution for schedule_send / snooze_message (#2526).

The agent used to hand relative phrases ("tomorrow morning") straight to
``_parse_future_ts``, which only accepts strict ISO-8601 — so it demanded
ISO-8601 formatting from the user in chat, and (verified against the DB)
created NO scheduled job at all. These tests assert the PERSISTED job row,
not just a tool return value, because that was precisely what silently
didn't happen.

Every phrase is resolved against an INJECTED "now" (never the real clock) so
resolution is deterministic. "now" is expressed as a naive local datetime —
the same "machine/process local timezone" convention ``_parse_future_ts``
already used for naive ISO timestamps (see schedule_tools.py docstring):
local time is whatever the host's OS ``TZ`` resolves to, not UTC and not a
per-user profile setting.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import schedule_store  # noqa: E402
from gaia_agent_email.tools.schedule_tools import (  # noqa: E402
    _parse_future_ts,
    _resolve_relative_time,
    cancel_scheduled_job_impl,
    list_scheduled_jobs_impl,
    snooze_message_impl,
)

from gaia.database.mixin import DatabaseMixin  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


class _DB(DatabaseMixin):
    pass


def _make_db(tmp_path: Path) -> _DB:
    db = _DB()
    db.init_db(str(tmp_path / "state.db"))
    schedule_store.init_schema(db)
    from gaia_agent_email import action_store

    action_store.init_schema(db)
    return db


def _inbox_message(message_id: str = "msg_1") -> dict:
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": str(int(time.time() * 1000)),
        "snippet": "hello",
        "payload": {
            "headers": [
                {"name": "From", "value": "Boss <boss@example.com>"},
                {"name": "Subject", "value": "Need your input"},
            ],
        },
    }


# A fixed injected "now": Tuesday 2026-07-28 08:00 local. Deterministic —
# never derived from the real clock.
_NOW_DT = datetime(2026, 7, 28, 8, 0, 0)
_NOW_S = _NOW_DT.timestamp()


class TestRelativePhrasesResolve:
    """A spread of phrases resolve to a concrete future local datetime."""

    @pytest.mark.parametrize(
        "phrase,expected",
        [
            ("tomorrow morning", datetime(2026, 7, 29, 9, 0)),
            ("in 2 hours", datetime(2026, 7, 28, 10, 0)),
            ("in 3 hours", datetime(2026, 7, 28, 11, 0)),
            ("next Monday", datetime(2026, 8, 3, 9, 0)),
            ("this evening", datetime(2026, 7, 28, 18, 0)),
            ("tomorrow at 7", datetime(2026, 7, 29, 7, 0)),
        ],
    )
    def test_phrase_resolves(self, phrase, expected):
        resolved = _resolve_relative_time(phrase, _NOW_DT)
        assert resolved == expected, f"{phrase!r} -> {resolved}, want {expected}"

    def test_case_insensitive(self):
        assert _resolve_relative_time("TOMORROW MORNING", _NOW_DT) == datetime(
            2026, 7, 29, 9, 0
        )


class TestUtcDayBoundary:
    """'tomorrow morning' near a UTC-day boundary must still resolve to the
    correct LOCAL day, not accidentally roll an extra day via UTC math."""

    def test_tomorrow_morning_near_utc_midnight(self):
        # 23:30 local, which is a different calendar date in UTC for most
        # timezones — if resolution ever leaked through UTC conversion, the
        # "tomorrow" here would land on the wrong local date.
        now_local = datetime(2026, 7, 28, 23, 30, 0)
        resolved = _resolve_relative_time("tomorrow morning", now_local)
        assert resolved == datetime(2026, 7, 29, 9, 0, 0)


class TestPersistedJobRow:
    """The observed failure created no scheduled_jobs row at all — assert
    the persisted row, not just the tool's return value."""

    def test_tomorrow_morning_snooze_creates_persisted_job(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))

        assert schedule_store.list_jobs(db, status=schedule_store.STATUS_PENDING) == []

        result = snooze_message_impl(
            backend,
            db,
            message_id="msg_1",
            until="tomorrow morning",
            mailbox="google",
            now=_NOW_S,
        )

        job_id = result["job_id"]
        row = schedule_store.get_job(db, job_id=job_id)
        assert row is not None, "no scheduled job row was persisted"
        assert row["status"] == "pending"
        assert row["kind"] == schedule_store.KIND_SNOOZE
        fired_dt = datetime.fromtimestamp(row["due_at"])
        assert fired_dt == datetime(2026, 7, 29, 9, 0, 0)
        # And the message actually left INBOX now, per snooze semantics.
        assert "INBOX" not in backend.get_message("msg_1")["labelIds"]


class TestShortIntervalSchedulesAllowed:
    """#2537 needs jobs that fire in seconds/minutes to verify on hardware
    without waiting hours — 'in 2 minutes' must resolve to a near-future
    timestamp, not be rejected as too soon."""

    def test_in_minutes_resolves_close_to_now(self):
        resolved = _resolve_relative_time("in 2 minutes", _NOW_DT)
        assert resolved == datetime(2026, 7, 28, 8, 2, 0)

    def test_in_seconds_resolves_close_to_now(self):
        resolved = _resolve_relative_time("in 30 seconds", _NOW_DT)
        assert resolved == datetime(2026, 7, 28, 8, 0, 30)


class TestUnresolvablePhrase:
    """A phrase that can't be resolved must fail with an actionable message
    that PROPOSES a concrete time — never a bare 'give me ISO-8601' demand."""

    def test_unresolvable_phrase_proposes_concrete_time(self):
        with pytest.raises(ValueError) as excinfo:
            _parse_future_ts("whenever is convenient I guess", now=_NOW_S)
        message = str(excinfo.value)
        # Must offer an actual next-step time, not just reject.
        assert "2026-07-29" in message, message
        assert "09:00" in message, message

    def test_unresolvable_phrase_does_not_only_demand_iso(self):
        with pytest.raises(ValueError) as excinfo:
            _parse_future_ts("sometime soonish", now=_NOW_S)
        message = str(excinfo.value).lower()
        # It may still mention ISO-8601 as an option, but must not be the
        # only path offered — a concrete proposed time must also appear.
        assert "tomorrow" in message or "09:00" in message, message


class TestCancelByPosition:
    """Cancelling should work from a position in the just-shown listing —
    the user has no way to know the raw job id from chat."""

    def test_cancel_second_job_by_position(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))
        backend.add_message(_inbox_message("msg_2"))

        first = snooze_message_impl(
            backend, db, message_id="msg_1", until="in 2 hours", now=_NOW_S
        )
        second = snooze_message_impl(
            backend, db, message_id="msg_2", until="in 3 hours", now=_NOW_S
        )

        listing = list_scheduled_jobs_impl(db)["pending"]
        assert [j["job_id"] for j in listing] == [first["job_id"], second["job_id"]]

        out = cancel_scheduled_job_impl(db, job_id="2")
        assert out["cancelled"] is True
        assert out["job_id"] == second["job_id"]

        assert schedule_store.get_job(db, job_id=second["job_id"])["status"] == "cancelled"
        assert schedule_store.get_job(db, job_id=first["job_id"])["status"] == "pending"

    def test_cancel_position_out_of_range_is_loud(self, tmp_path):
        db = _make_db(tmp_path)
        with pytest.raises(ValueError, match="no job at position"):
            cancel_scheduled_job_impl(db, job_id="5")
