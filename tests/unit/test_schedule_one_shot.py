# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One-time schedules: ``gaia schedule add --at`` (issue #560).

A one-time schedule runs exactly once, at or after its time, including once on
the next daemon start when the daemon was down at that time, and never again.

Hermetic: every store lives under ``tmp_path``, HOME points there too, the
agent run (``runner.fire``) is stubbed, and the clock is passed in or patched.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from gaia.cli import _handle_schedule, build_parser
from gaia.schedule import daemon
from gaia.schedule.store import (
    ONE_SHOT_COMPLETED,
    ONE_SHOT_FAILED,
    ONE_SHOT_PENDING,
    ONE_SHOT_RUNNING,
    Schedule,
    TomlScheduleStore,
    parse_at,
)

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _temp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.fixture
def store(tmp_path):
    return TomlScheduleStore(tmp_path / "schedules.toml")


@pytest.fixture
def fired(monkeypatch):
    """Stub the agent run; record each schedule it fires."""
    calls = []

    def fake_fire(schedule):
        calls.append(schedule.name)
        return "ok"

    monkeypatch.setattr(daemon.runner, "fire", fake_fire)
    return calls


def _one_shot(name="once", at=NOW, **overrides):
    kwargs = {"name": name, "at": at.isoformat(), "prompt": "hello"}
    kwargs.update(overrides)
    return Schedule(**kwargs)


class _FrozenDatetime(datetime):
    frozen = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.frozen if tz is None else cls.frozen.astimezone(tz)


@pytest.fixture
def clock(monkeypatch):
    """Freeze the daemon's clock; set ``clock.frozen`` to move it."""
    _FrozenDatetime.frozen = NOW
    monkeypatch.setattr(daemon, "datetime", _FrozenDatetime)
    return _FrozenDatetime


# ---------------------------------------------------------------------------
# parse_at
# ---------------------------------------------------------------------------


class TestParseAt:
    @pytest.mark.parametrize(
        "value, delta",
        [
            ("+30m", timedelta(minutes=30)),
            ("+2h", timedelta(hours=2)),
            ("+1d", timedelta(days=1)),
            ("+1h30m", timedelta(hours=1, minutes=30)),
            ("+1d12h", timedelta(days=1, hours=12)),
            ("  +2h  ", timedelta(hours=2)),
        ],
    )
    def test_relative_delay_counts_from_now(self, value, delta):
        assert parse_at(value, NOW) == NOW + delta

    def test_iso_with_offset_is_converted_to_utc(self):
        got = parse_at("2026-11-02T09:00+02:00", NOW)
        assert got == datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)
        assert got.tzinfo == timezone.utc

    def test_iso_with_z_suffix(self):
        assert parse_at("2026-11-02T09:00Z", NOW) == datetime(
            2026, 11, 2, 9, 0, tzinfo=timezone.utc
        )

    def test_naive_iso_is_local_wall_clock(self):
        got = parse_at("2030-01-02 09:00", NOW)
        assert got == datetime(2030, 1, 2, 9, 0).astimezone().astimezone(timezone.utc)

    @pytest.mark.parametrize(
        "value",
        ["2026-11-01T11:59:59+00:00", "2026-11-01T12:00:00+00:00", "2020-01-01T00:00Z"],
    )
    def test_past_or_present_absolute_time_is_rejected(self, value):
        with pytest.raises(ValueError, match="in the past"):
            parse_at(value, NOW)

    @pytest.mark.parametrize("value", ["+0m", "+0h0m", "+0d"])
    def test_zero_delay_is_rejected(self, value):
        with pytest.raises(ValueError, match="not in the future"):
            parse_at(value, NOW)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "tomorrow",
            "in 2 hours",
            "+",
            "+2",
            "+2x",
            "+-1h",
            "+2h junk",
            "+1.5h",
            "2h",
            "2026-11-02",
            "2026-13-02T09:00",
            "09:00",
            "2026-11-02T25:00",
        ],
    )
    def test_unreadable_values_are_rejected(self, value):
        with pytest.raises(ValueError, match="not a time GAIA can read"):
            parse_at(value, NOW)


# ---------------------------------------------------------------------------
# Schedule / store
# ---------------------------------------------------------------------------


class TestScheduleModel:
    def test_cron_and_at_together_rejected(self):
        with pytest.raises(ValueError, match="exactly one of --cron or --at"):
            Schedule(name="x", cron="* * * * *", at=NOW.isoformat(), prompt="hi")

    def test_neither_cron_nor_at_rejected(self):
        with pytest.raises(ValueError, match="exactly one of --cron or --at"):
            Schedule(name="x", prompt="hi")

    def test_new_one_shot_starts_pending(self):
        assert _one_shot().state == ONE_SHOT_PENDING

    def test_unknown_state_rejected(self):
        with pytest.raises(ValueError, match="unknown state"):
            _one_shot(state="done-ish")

    def test_toml_round_trip_keeps_at_and_state(self, store):
        store.add(_one_shot())
        loaded = store.get("once")
        assert loaded.at == NOW.isoformat()
        assert loaded.cron is None
        assert loaded.state == ONE_SHOT_PENDING
        raw = store.path.read_text(encoding="utf-8")
        assert "cron" not in raw

    def test_cron_schedule_still_round_trips(self, store):
        store.add(Schedule(name="c", cron="0 9 * * *", prompt="hi"))
        loaded = store.get("c")
        assert (loaded.cron, loaded.at, loaded.state) == ("0 9 * * *", None, None)

    def test_hand_edited_unreadable_at_fails_loudly(self, store):
        store.path.write_text(
            '[schedules.x]\nat = "soon"\nprompt = "hi"\n', encoding="utf-8"
        )
        with pytest.raises(ValueError, match="unreadable at"):
            store.get("x").due_at()


class TestClaim:
    def test_claim_when_due_moves_to_running_on_disk(self, store):
        store.add(_one_shot())
        claimed = store.claim_one_shot("once", NOW)
        assert claimed is not None and claimed.state == ONE_SHOT_RUNNING
        assert store.get("once").state == ONE_SHOT_RUNNING

    def test_second_claim_loses(self, store):
        store.add(_one_shot())
        assert store.claim_one_shot("once", NOW) is not None
        assert store.claim_one_shot("once", NOW) is None
        assert store.claim_one_shot("once", NOW, due_only=False) is None

    def test_not_yet_due_is_not_claimed(self, store):
        store.add(_one_shot())
        assert store.claim_one_shot("once", NOW - timedelta(seconds=1)) is None
        assert store.get("once").state == ONE_SHOT_PENDING

    def test_paused_is_not_claimed_by_the_daemon(self, store):
        store.add(_one_shot(enabled=False))
        assert store.claim_one_shot("once", NOW) is None

    def test_manual_run_claims_regardless_of_time(self, store):
        store.add(_one_shot(enabled=False))
        assert store.claim_one_shot("once", NOW - timedelta(days=1), due_only=False)

    def test_cron_schedule_and_missing_name_are_never_claimed(self, store):
        store.add(Schedule(name="c", cron="* * * * *", prompt="hi"))
        assert store.claim_one_shot("c", NOW, due_only=False) is None
        assert store.claim_one_shot("ghost", NOW) is None

    def test_concurrent_claims_have_exactly_one_winner(self, store):
        store.add(_one_shot())
        barrier = threading.Barrier(8)
        winners = []

        def claim():
            barrier.wait()
            if store.claim_one_shot("once", NOW) is not None:
                winners.append(1)

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(winners) == 1

    def test_finish_records_outcome(self, store):
        store.add(_one_shot("a"))
        store.add(_one_shot("b"))
        store.finish_one_shot("a", NOW.isoformat(), ok=True)
        store.finish_one_shot("b", NOW.isoformat(), ok=False)
        assert store.get("a").state == ONE_SHOT_COMPLETED
        assert store.get("a").last_run == NOW.isoformat()
        assert store.get("b").state == ONE_SHOT_FAILED

    def test_finish_after_removal_fails_loudly(self, store):
        with pytest.raises(KeyError, match="removed while its one-time run"):
            store.finish_one_shot("ghost", NOW.isoformat(), ok=True)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


def _armed(store):
    scheduler = BackgroundScheduler(job_defaults=daemon.JOB_DEFAULTS)
    daemon.refresh_schedules(scheduler, store)
    return scheduler


class TestDaemonFiresOnce:
    def test_due_job_fires_once_and_completes(self, store, fired, clock):
        store.add(_one_shot())
        job = _armed(store).get_job("once")
        job.func(*job.args)
        assert fired == ["once"]
        assert store.get("once").state == ONE_SHOT_COMPLETED
        assert store.get("once").last_run == NOW.isoformat()

    def test_repeated_ticks_never_fire_twice(self, store, fired, clock):
        store.add(_one_shot())
        job = _armed(store).get_job("once")
        for _ in range(5):
            job.func(*job.args)
        assert fired == ["once"]

    def test_early_tick_does_not_fire(self, store, fired, clock):
        store.add(_one_shot(at=NOW + timedelta(hours=1)))
        job = _armed(store).get_job("once")
        job.func(*job.args)
        assert fired == []
        assert store.get("once").state == ONE_SHOT_PENDING

    def test_concurrent_ticks_fire_once(self, store, monkeypatch, clock):
        calls = []
        lock = threading.Lock()

        def slow_fire(schedule):
            with lock:
                calls.append(schedule.name)
            time.sleep(0.05)
            return "ok"

        monkeypatch.setattr(daemon.runner, "fire", slow_fire)
        store.add(_one_shot())
        job = _armed(store).get_job("once")
        threads = [threading.Thread(target=job.func, args=job.args) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert calls == ["once"]

    def test_finished_one_shots_are_not_armed_after_restart(self, store, fired, clock):
        store.add(_one_shot("done"))
        store.add(_one_shot("broke"))
        store.add(_one_shot("crashed"))
        store.finish_one_shot("done", NOW.isoformat(), ok=True)
        store.finish_one_shot("broke", NOW.isoformat(), ok=False)
        # A daemon that died mid-run leaves it running: never run it again.
        store.claim_one_shot("crashed", NOW)
        restarted = TomlScheduleStore(store.path)
        assert _armed(restarted).get_jobs() == []

    def test_missed_while_down_fires_once_on_start(self, store, fired, clock):
        store.add(_one_shot(at=NOW - timedelta(days=3)))
        job = _armed(store).get_job("once")
        assert job.misfire_grace_time is None
        assert job.trigger.run_date == NOW
        job.func(*job.args)
        assert fired == ["once"]
        # Start again: nothing left to arm.
        assert _armed(TomlScheduleStore(store.path)).get_jobs() == []

    def test_future_one_shot_is_armed_at_its_time(self, store, clock):
        due = NOW + timedelta(hours=2)
        store.add(_one_shot(at=due))
        job = _armed(store).get_job("once")
        assert job.trigger.run_date == due

    def test_failure_is_logged_marked_failed_and_not_retried(
        self, store, monkeypatch, clock, caplog
    ):
        def boom(schedule):
            raise RuntimeError("agent exploded")

        monkeypatch.setattr(daemon.runner, "fire", boom)
        store.add(_one_shot())
        job = _armed(store).get_job("once")
        job.func(*job.args)  # must not raise into the scheduler thread
        assert store.get("once").state == ONE_SHOT_FAILED
        assert "will not run again" in caplog.text
        assert _armed(store).get_jobs() == []

    def test_paused_one_shot_is_not_armed_until_resumed(self, store, clock):
        store.add(_one_shot(enabled=False))
        scheduler = _armed(store)
        assert scheduler.get_jobs() == []
        store.set_enabled("once", True)
        daemon.refresh_schedules(scheduler, store)
        assert scheduler.get_job("once") is not None

    def test_hand_edit_moving_at_rearms(self, store, clock):
        store.add(_one_shot(at=NOW + timedelta(hours=1)))
        scheduler = _armed(store)
        schedules = store.load()
        schedules["once"].at = (NOW + timedelta(hours=5)).isoformat()
        store.save(schedules)
        daemon.refresh_schedules(scheduler, store)
        assert scheduler.get_job("once").trigger.run_date == NOW + timedelta(hours=5)

    def test_switching_cron_to_one_shot_swaps_the_job(self, store, clock):
        store.add(Schedule(name="x", cron="0 9 * * *", prompt="hi"))
        scheduler = _armed(store)
        schedules = store.load()
        schedules["x"] = _one_shot("x", at=NOW + timedelta(hours=1))
        store.save(schedules)
        daemon.refresh_schedules(scheduler, store)
        job = scheduler.get_job("x")
        assert job.func is daemon._one_shot_job

    def test_missed_run_accounting_skips_one_shots(self, store, clock):
        store.add(_one_shot(at=NOW - timedelta(days=1)))
        daemon.catch_up_missed_runs(store, NOW)
        assert store.get("once").missed_count == 0

    def test_real_scheduler_fires_past_due_one_shot_once(self, store, fired):
        store.add(_one_shot(at=datetime.now(timezone.utc) - timedelta(hours=6)))
        scheduler = daemon.build_scheduler(store)
        scheduler.start()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                # Reload ticks race the run: none may fire it again.
                daemon.refresh_schedules(scheduler, store)
                if store.get("once").state == ONE_SHOT_COMPLETED:
                    break
                time.sleep(0.05)
            for _ in range(10):
                daemon.refresh_schedules(scheduler, store)
                time.sleep(0.05)
        finally:
            scheduler.shutdown(wait=True)
        assert fired == ["once"]
        assert store.get("once").state == ONE_SHOT_COMPLETED


class TestNextFire:
    def test_pending_shows_local_due_time(self):
        s = _one_shot(at=NOW + timedelta(hours=1))
        assert datetime.fromisoformat(daemon.next_fire(s)) == NOW + timedelta(hours=1)

    @pytest.mark.parametrize(
        "state", [ONE_SHOT_RUNNING, ONE_SHOT_COMPLETED, ONE_SHOT_FAILED]
    )
    def test_finished_or_running_has_no_next_fire(self, state):
        assert daemon.next_fire(_one_shot(state=state)) is None

    def test_paused_has_no_next_fire(self):
        assert daemon.next_fire(_one_shot(enabled=False)) is None


# ---------------------------------------------------------------------------
# CLI (real parser, real store under tmp_path)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parser():
    return build_parser()


@pytest.fixture
def cli_store(store, mocker):
    mocker.patch("gaia.schedule.store.TomlScheduleStore", return_value=store)
    return store


def _run(parser_, *argv):
    _handle_schedule(parser_.parse_args(["schedule", *argv]))


class TestCli:
    def test_add_at_relative_stores_one_shot(self, parser, cli_store, capsys):
        before = datetime.now(timezone.utc)
        _run(parser, "add", "--name", "n", "--at", "+2h", "--prompt", "hi")
        after = datetime.now(timezone.utc)
        s = cli_store.get("n")
        assert s.cron is None and s.state == ONE_SHOT_PENDING
        assert (
            before + timedelta(hours=2, seconds=-1)
            <= s.due_at()
            <= after + timedelta(hours=2)
        )
        assert s.due_at().microsecond == 0
        assert "Added one-time schedule 'n'" in capsys.readouterr().out

    def test_add_at_absolute_stores_utc(self, parser, cli_store):
        _run(
            parser, "add", "--name", "n", "--at", "2099-01-02T09:00Z", "--prompt", "hi"
        )
        assert cli_store.get("n").at == "2099-01-02T09:00:00+00:00"

    @pytest.mark.parametrize(
        "value, reason",
        [
            ("2020-01-01T09:00Z", "in the past"),
            ("tomorrow", "not a time GAIA can read"),
            ("+0m", "not in the future"),
            ("2099-01-02", "not a time GAIA can read"),
        ],
    )
    def test_add_bad_at_fails_and_stores_nothing(
        self, parser, cli_store, capsys, value, reason
    ):
        with pytest.raises(SystemExit) as exc:
            _run(parser, "add", "--name", "n", "--at", value, "--prompt", "hi")
        assert exc.value.code == 1
        assert reason in capsys.readouterr().err
        assert cli_store.load() == {}

    def test_cron_and_at_together_rejected_by_parser(self, parser, capsys):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(
                [
                    "schedule",
                    "add",
                    "--name",
                    "n",
                    "--cron",
                    "* * * * *",
                    "--at",
                    "+2h",
                    "--prompt",
                    "hi",
                ]
            )
        assert exc.value.code == 2
        assert "not allowed with argument" in capsys.readouterr().err

    def test_neither_cron_nor_at_rejected_by_parser(self, parser):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["schedule", "add", "--name", "n", "--prompt", "hi"])
        assert exc.value.code == 2

    def test_skill_rejection_names_the_at_flag(self, parser, cli_store, capsys):
        with pytest.raises(SystemExit):
            _run(parser, "add", "--name", "n", "--at", "+2h", "--skill", "s")
        err = capsys.readouterr().err
        assert '--at "+2h" --prompt' in err
        assert "--cron" not in err

    def test_list_shows_one_time_state_and_next(self, parser, cli_store, capsys):
        due = datetime.now(timezone.utc) + timedelta(hours=3)
        cli_store.add(_one_shot("pend", at=due))
        cli_store.add(_one_shot("done", at=due))
        cli_store.finish_one_shot("done", NOW.isoformat(), ok=True)
        cli_store.add(Schedule(name="c", cron="0 9 * * *", prompt="hi"))
        _run(parser, "list")
        lines = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()}
        assert "once=pending" in lines["pend"]
        assert f"next={due.astimezone().isoformat()}" in lines["pend"]
        assert "once=completed" in lines["done"] and "next=" not in lines["done"]
        assert "cron='0 9 * * *'" in lines["c"]

    def test_show_prints_due_time_state_and_ran_at(self, parser, cli_store, capsys):
        cli_store.add(_one_shot("n", at=NOW))
        cli_store.finish_one_shot("n", NOW.isoformat(), ok=True)
        _run(parser, "show", "n")
        out = capsys.readouterr().out
        assert f"once at:    {NOW.astimezone().isoformat()}" in out
        assert "state:      completed" in out
        assert f"ran_at:     {NOW.astimezone().isoformat()}" in out
        assert "next_fire:  None" in out
        assert "cron:" not in out

    def test_run_fires_a_pending_one_shot_once(self, parser, cli_store, fired):
        cli_store.add(_one_shot("n", at=datetime.now(timezone.utc) + timedelta(days=1)))
        _run(parser, "run", "n")
        assert fired == ["n"]
        assert cli_store.get("n").state == ONE_SHOT_COMPLETED

    def test_run_refuses_a_one_shot_that_already_ran(
        self, parser, cli_store, fired, capsys
    ):
        cli_store.add(_one_shot("n"))
        cli_store.finish_one_shot("n", NOW.isoformat(), ok=True)
        with pytest.raises(SystemExit) as exc:
            _run(parser, "run", "n")
        assert exc.value.code == 1
        assert "already ran" in capsys.readouterr().err
        assert fired == []

    def test_run_failure_marks_failed_and_raises(self, parser, cli_store, monkeypatch):
        def boom(schedule):
            raise RuntimeError("agent exploded")

        monkeypatch.setattr(daemon.runner, "fire", boom)
        cli_store.add(_one_shot("n"))
        with pytest.raises(RuntimeError, match="agent exploded"):
            _run(parser, "run", "n")
        assert cli_store.get("n").state == ONE_SHOT_FAILED
