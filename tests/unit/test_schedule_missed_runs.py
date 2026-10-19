# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia schedule` reports the runs it never got to make.

The daemon arms its timers from "now" each time it starts, and APScheduler
folds runs a long sleep skipped into one, so runs due while the PC was off,
asleep, or the daemon was not running vanished without a trace: `gaia schedule
show` gave no sign anything was missed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.events import (
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
    JobExecutionEvent,
    JobSubmissionEvent,
)

from gaia.cli import _handle_schedule, build_parser
from gaia.schedule import daemon
from gaia.schedule.store import Schedule, TomlScheduleStore

HOURLY = "0 * * * *"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _store(tmp_path, **fields) -> TomlScheduleStore:
    store = TomlScheduleStore(tmp_path / "schedules.toml")
    store.add(Schedule(name="brief", cron=HOURLY, prompt="say hello", **fields))
    return store


def test_runs_due_while_the_daemon_was_not_running_are_recorded(tmp_path):
    now = datetime.now(timezone.utc)
    # The last run finished, then the PC was off for the next three hours.
    last_run = _hour(now) - timedelta(hours=3, minutes=-1)
    store = _store(
        tmp_path,
        created_at=_iso(now - timedelta(days=2)),
        last_run=_iso(last_run),
        next_run=_iso(_hour(now) - timedelta(hours=2)),
    )

    scheduler = daemon.build_scheduler(store)
    scheduler.shutdown(wait=False) if scheduler.running else None

    brief = store.get("brief")
    # Due two hours ago, one hour ago, and at the top of this hour.
    assert brief.missed_count == 3
    assert brief.last_missed is not None
    assert datetime.fromisoformat(brief.next_run) > now


def test_a_restart_does_not_report_the_same_missed_runs_twice(tmp_path):
    now = datetime.now(timezone.utc)
    store = _store(
        tmp_path,
        created_at=_iso(now - timedelta(days=2)),
        next_run=_iso(_hour(now) - timedelta(hours=2)),
    )
    daemon.build_scheduler(store)
    first = store.get("brief").missed_count
    daemon.build_scheduler(store)
    assert store.get("brief").missed_count == first


def test_a_paused_schedule_does_not_count_its_pause_as_missed(tmp_path):
    now = datetime.now(timezone.utc)
    store = _store(
        tmp_path,
        created_at=_iso(now - timedelta(days=2)),
        next_run=_iso(now - timedelta(days=1)),
    )
    store.set_enabled("brief", False)
    store.set_enabled("brief", True)
    daemon.build_scheduler(store)
    assert store.get("brief").missed_count == 0


def _frozen_at(moment):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)

    return Frozen


def _sleeping_daemon(tmp_path, monkeypatch, *, woke_after):
    """A daemon running before the next run, on a PC that then sleeps."""
    due = _hour(datetime.now(timezone.utc)) + timedelta(hours=1)
    store = _store(
        tmp_path, created_at=_iso(due - timedelta(days=1)), next_run=_iso(due)
    )
    scheduler = daemon.build_scheduler(store)
    brief = store.get("brief")
    assert brief.missed_count == 0
    assert datetime.fromisoformat(brief.next_run) == due
    monkeypatch.setattr(daemon, "datetime", _frozen_at(due + woke_after))
    return due, store, scheduler


def test_runs_a_long_sleep_folded_together_are_recorded(tmp_path, monkeypatch):
    due, store, scheduler = _sleeping_daemon(
        tmp_path, monkeypatch, woke_after=timedelta(hours=3, minutes=30)
    )
    # APScheduler runs only the latest due run and says nothing about the
    # three before it.
    woke_run = due + timedelta(hours=3)
    scheduler._dispatch_event(
        JobSubmissionEvent(EVENT_JOB_SUBMITTED, "brief", None, [woke_run])
    )
    brief = store.get("brief")
    assert brief.missed_count == 3
    assert brief.last_missed == _iso(due + timedelta(hours=2))
    assert datetime.fromisoformat(brief.next_run) == woke_run + timedelta(hours=1)


def test_a_run_dropped_as_too_late_is_recorded_once(tmp_path, monkeypatch):
    due, store, scheduler = _sleeping_daemon(
        tmp_path, monkeypatch, woke_after=timedelta(hours=3, minutes=30)
    )
    # The latest run is 2.5 hours late, past the grace: APScheduler submits it,
    # then its executor drops it and sends EVENT_JOB_MISSED from another thread.
    latest = due + timedelta(hours=1)
    scheduler._dispatch_event(
        JobSubmissionEvent(EVENT_JOB_SUBMITTED, "brief", None, [latest])
    )
    scheduler._dispatch_event(
        JobExecutionEvent(EVENT_JOB_MISSED, "brief", None, latest)
    )
    brief = store.get("brief")
    assert brief.missed_count == 2
    assert brief.last_missed == _iso(latest)


def test_a_schedule_saved_before_misses_were_counted_starts_counting_now(tmp_path):
    now = datetime.now(timezone.utc)
    store = TomlScheduleStore(tmp_path / "schedules.toml")
    # An old store: its next_run is stale because failed runs never moved it.
    store.save(
        {
            "brief": Schedule(
                name="brief",
                cron=HOURLY,
                prompt="say hello",
                created_at=_iso(now - timedelta(days=30)),
                next_run=_iso(now - timedelta(days=20)),
            )
        }
    )
    daemon.build_scheduler(store)
    brief = store.get("brief")
    assert brief.missed_count == 0
    assert brief.missed_since is not None
    assert datetime.fromisoformat(brief.next_run) > now


def test_show_says_runs_were_missed(tmp_path, mocker, capsys):
    store_cls = mocker.patch("gaia.schedule.store.TomlScheduleStore")
    store_cls.return_value.get.return_value = Schedule(
        name="brief",
        cron=HOURLY,
        prompt="say hello",
        missed_count=3,
        last_missed="2026-10-18T11:00:00+00:00",
    )
    _handle_schedule(build_parser().parse_args(["schedule", "show", "brief"]))
    out = capsys.readouterr().out
    assert "missed:     3 runs in all while the PC was off or asleep" in out
    # In local time, as next_fire is.
    local = datetime.fromisoformat("2026-10-18T11:00:00+00:00").astimezone()
    assert f"latest due {local.isoformat()}" in out
