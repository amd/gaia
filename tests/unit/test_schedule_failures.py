# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A recurring schedule's failed runs are recorded on the schedule (#8263).

A failed run was written to the daemon log and nowhere else: `gaia schedule
list` and `show` kept looking healthy while the schedule produced nothing,
and after a restart the failed run was even reported as missed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gaia.cli import _handle_schedule, build_parser
from gaia.schedule import daemon, runner
from gaia.schedule.store import Schedule, TomlScheduleStore

HOURLY = "0 * * * *"
NOT_DOWNLOADED = "Model Qwen3.6-35B-A3B-GGUF is not downloaded. Run `gaia init` first."


def _hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _store(tmp_path, **fields) -> TomlScheduleStore:
    store = TomlScheduleStore(tmp_path / "schedules.toml")
    store.add(Schedule(name="brief", cron=HOURLY, prompt="say hello", **fields))
    return store


def _run(store, mocker, error=None):
    fire = mocker.patch.object(runner, "fire")
    if error is None:
        fire.return_value = "output"
    else:
        fire.side_effect = error
    daemon._job(store.get("brief"), store)


def test_a_failed_run_is_recorded_on_the_schedule(tmp_path, mocker):
    store = _store(tmp_path)

    _run(store, mocker, RuntimeError(NOT_DOWNLOADED + "\n  at runner.fire"))

    brief = store.get("brief")
    assert brief.failed_runs == 1
    assert brief.last_error == NOT_DOWNLOADED
    datetime.fromisoformat(brief.last_failed)
    # last_run stays the last run that succeeded; there was none.
    assert brief.last_run is None
    assert datetime.fromisoformat(brief.next_run) > datetime.now(timezone.utc)


def test_failures_count_until_a_run_succeeds(tmp_path, mocker):
    store = _store(tmp_path)

    _run(store, mocker, RuntimeError("Lemonade Server is not reachable"))
    _run(store, mocker, RuntimeError(NOT_DOWNLOADED))
    failing = store.get("brief")
    assert failing.failed_runs == 2
    assert failing.last_error == NOT_DOWNLOADED

    _run(store, mocker)
    healthy = store.get("brief")
    assert healthy.failed_runs == 0
    assert healthy.last_error is None and healthy.last_failed is None
    assert healthy.last_run is not None


def test_a_failed_run_is_not_counted_as_missed(tmp_path, mocker):
    now = datetime.now(timezone.utc)
    # The previous run succeeded; this hour's run is the one that fails.
    store = _store(
        tmp_path,
        created_at=(now - timedelta(days=2)).isoformat(),
        missed_since=(now - timedelta(days=2)).isoformat(),
        last_run=(_hour(now) - timedelta(minutes=59)).isoformat(),
        next_run=_hour(now).isoformat(),
    )

    _run(store, mocker, RuntimeError(NOT_DOWNLOADED))
    # The daemon restarts and counts what it missed while it was down.
    daemon.catch_up_missed_runs(store, datetime.now(timezone.utc))

    brief = store.get("brief")
    assert brief.missed_count == 0
    assert brief.failed_runs == 1


def test_a_failure_that_cannot_be_recorded_does_not_stop_the_daemon(tmp_path, mocker):
    store = _store(tmp_path)
    mocker.patch.object(store, "mark_failed", side_effect=OSError("disk full"))
    log = mocker.patch.object(daemon.log, "exception")

    _run(store, mocker, RuntimeError(NOT_DOWNLOADED))

    # Both the run's failure and the failed recording are logged.
    assert log.call_count == 2


@pytest.mark.parametrize(
    "error, kept",
    [
        (RuntimeError(""), "RuntimeError"),
        (RuntimeError("x" * 500), "x" * 299 + "…"),
    ],
)
def test_the_recorded_error_is_one_short_line(tmp_path, mocker, error, kept):
    store = _store(tmp_path)

    _run(store, mocker, error)

    assert store.get("brief").last_error == kept


def test_a_schedule_without_failures_saves_no_failure_fields():
    saved = Schedule(name="brief", cron=HOURLY, prompt="say hello").to_toml_dict()

    assert not {"failed_runs", "last_failed", "last_error"} & saved.keys()


FAILING = Schedule(
    name="brief",
    cron=HOURLY,
    prompt="say hello",
    last_run="2026-11-14T09:00:05+00:00",
    failed_runs=2,
    last_failed="2026-11-15T09:00:04+00:00",
    last_error=NOT_DOWNLOADED,
)


def test_show_says_the_schedule_is_failing(mocker, capsys):
    store_cls = mocker.patch("gaia.schedule.store.TomlScheduleStore")
    store_cls.return_value.get.return_value = FAILING

    _handle_schedule(build_parser().parse_args(["schedule", "show", "brief"]))

    out = capsys.readouterr().out
    local = datetime.fromisoformat
    assert f"last_run:   {local(FAILING.last_run).astimezone().isoformat()}" in out
    assert (
        "failing:    2 runs in a row failed since the last successful one "
        f"(latest {local(FAILING.last_failed).astimezone().isoformat()}): "
        f"{NOT_DOWNLOADED}"
    ) in out


def test_list_says_the_schedule_is_failing(mocker, capsys):
    store_cls = mocker.patch("gaia.schedule.store.TomlScheduleStore")
    store_cls.return_value.load.return_value = {"brief": FAILING}

    _handle_schedule(build_parser().parse_args(["schedule", "list"]))

    assert "  failing=2" in capsys.readouterr().out
