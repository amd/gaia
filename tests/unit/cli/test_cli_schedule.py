# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for `gaia schedule` CLI dispatch (_handle_schedule, issue #1995).

Exercises the real argparse parser (build_parser().parse_args(argv)) end to
end, then mocks only the schedule-store / schedule-package boundary
(``gaia.schedule.store.TomlScheduleStore``, ``gaia.schedule.daemon``,
``gaia.schedule.runner``) and asserts the *actual* constructed ``Schedule``
object / store-call arguments produced from parsed CLI args — not just that
a mock was called (see issue #1655).
"""

from __future__ import annotations

import datetime as datetime_module

import pytest

from gaia.cli import _handle_schedule, build_parser
from gaia.schedule.store import Schedule

# ---- Shared fixtures ----


@pytest.fixture(scope="module")
def parser():
    """Module-scoped real argparse parser, built once (mirrors test_cli_smoke.py)."""
    return build_parser()


def _parse(parser_, argv):
    return parser_.parse_args(["schedule", *argv])


@pytest.fixture
def mock_store(mocker):
    """Patch the store class at its source so _handle_schedule's local
    `from gaia.schedule.store import TomlScheduleStore` picks up the mock."""
    store_cls = mocker.patch("gaia.schedule.store.TomlScheduleStore")
    return store_cls.return_value


@pytest.fixture
def mock_next_fire_time(mocker):
    return mocker.patch(
        "gaia.schedule.daemon.next_fire_time", return_value="2026-07-18T07:00:00+00:00"
    )


@pytest.fixture
def mock_run_daemon(mocker):
    return mocker.patch("gaia.schedule.daemon.run_daemon")


@pytest.fixture
def mock_fire(mocker):
    return mocker.patch("gaia.schedule.runner.fire", return_value="ok")


# ---- add ----


def test_add_prompt_constructs_schedule_from_parsed_args(parser, mock_store, capsys):
    args = _parse(
        parser,
        [
            "add",
            "--name",
            "daily-standup",
            "--cron",
            "0 7 * * 1-5",
            "--prompt",
            "summarize my day",
            "--sink",
            "telegram",
            "--to",
            "12345",
        ],
    )

    _handle_schedule(args)

    mock_store.add.assert_called_once()
    (constructed,) = mock_store.add.call_args.args
    assert isinstance(constructed, Schedule)
    assert constructed.name == "daily-standup"
    assert constructed.cron == "0 7 * * 1-5"
    assert constructed.prompt == "summarize my day"
    assert constructed.skill is None
    assert constructed.sink == "telegram"
    assert constructed.sink_args == {"to": "12345"}

    out = capsys.readouterr().out
    assert "daily-standup" in out
    assert "0 7 * * 1-5" in out


def test_add_skill_variant_is_rejected_until_888(parser, mock_store, capsys):
    """A --skill schedule would register but never fire, so `add` rejects it (#888)."""
    args = _parse(
        parser,
        [
            "add",
            "--name",
            "morning-brief",
            "--cron",
            "0 8 * * *",
            "--skill",
            "brainstorming",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        _handle_schedule(args)

    assert excinfo.value.code == 1
    # Nothing may be persisted — a stored skill schedule is the silent-failure case.
    mock_store.add.assert_not_called()

    err = capsys.readouterr().err
    assert "--skill is not supported yet" in err
    assert "--prompt" in err  # names the workaround
    assert "888" in err  # points at the tracking issue


def test_add_default_sink_without_to_has_empty_sink_args(parser, mock_store):
    """sink_args must only carry `to` when --to was actually passed (#1995 munging risk)."""
    args = _parse(
        parser,
        ["add", "--name", "n", "--cron", "* * * * *", "--prompt", "hi"],
    )

    _handle_schedule(args)

    (constructed,) = mock_store.add.call_args.args
    assert constructed.sink == "stdout"
    assert constructed.sink_args == {}
    assert "to" not in constructed.sink_args


def test_add_requires_name_and_cron(parser):
    with pytest.raises(SystemExit) as excinfo:
        _parse(parser, ["add", "--prompt", "hi"])
    assert excinfo.value.code == 2


def test_add_requires_exactly_one_of_skill_or_prompt(parser):
    with pytest.raises(SystemExit) as excinfo:
        _parse(
            parser,
            ["add", "--name", "n", "--cron", "* * * * *"],
        )
    assert excinfo.value.code == 2


def test_add_rejects_both_skill_and_prompt(parser):
    with pytest.raises(SystemExit) as excinfo:
        _parse(
            parser,
            [
                "add",
                "--name",
                "n",
                "--cron",
                "* * * * *",
                "--prompt",
                "hi",
                "--skill",
                "brainstorming",
            ],
        )
    assert excinfo.value.code == 2


# ---- list ----


def test_list_prints_enabled_schedule_with_next_fire(
    parser, mock_store, mock_next_fire_time, capsys
):
    mock_store.load.return_value = {
        "daily-standup": Schedule(
            name="daily-standup",
            cron="0 7 * * 1-5",
            prompt="summarize my day",
            sink="telegram",
            enabled=True,
        ),
    }

    args = _parse(parser, ["list"])
    _handle_schedule(args)

    mock_next_fire_time.assert_called_once_with("0 7 * * 1-5")
    out = capsys.readouterr().out
    assert "daily-standup" in out
    assert "[enabled]" in out
    assert "cron='0 7 * * 1-5'" in out
    assert "sink=telegram" in out
    assert "next=2026-07-18T07:00:00+00:00" in out


def test_list_paused_schedule_skips_next_fire_lookup(
    parser, mock_store, mock_next_fire_time, capsys
):
    mock_store.load.return_value = {
        "paused-job": Schedule(
            name="paused-job",
            cron="0 9 * * *",
            prompt="hi",
            enabled=False,
        ),
    }

    args = _parse(parser, ["list"])
    _handle_schedule(args)

    mock_next_fire_time.assert_not_called()
    out = capsys.readouterr().out
    assert "[paused]" in out
    assert "next=" not in out


def test_list_empty_prints_hint(parser, mock_store, capsys):
    mock_store.load.return_value = {}

    args = _parse(parser, ["list"])
    _handle_schedule(args)

    out = capsys.readouterr().out
    assert "No schedules registered" in out
    assert "gaia schedule add" in out


# ---- show ----


def test_show_prints_prompt_target(parser, mock_store, mock_next_fire_time, capsys):
    mock_store.get.return_value = Schedule(
        name="daily-standup",
        cron="0 7 * * 1-5",
        prompt="summarize my day",
        sink="stdout",
        enabled=True,
        created_at="2026-01-01T00:00:00+00:00",
    )

    args = _parse(parser, ["show", "daily-standup"])
    _handle_schedule(args)

    mock_store.get.assert_called_once_with("daily-standup")
    mock_next_fire_time.assert_called_once_with("0 7 * * 1-5")
    out = capsys.readouterr().out
    assert "name:       daily-standup" in out
    assert "target:     prompt=summarize my day" in out
    assert "next_fire:  2026-07-18T07:00:00+00:00" in out


def test_show_prints_skill_target(parser, mock_store, mock_next_fire_time, capsys):
    mock_store.get.return_value = Schedule(
        name="morning-brief",
        cron="0 8 * * *",
        skill="brainstorming",
        enabled=True,
    )

    args = _parse(parser, ["show", "morning-brief"])
    _handle_schedule(args)

    out = capsys.readouterr().out
    assert "target:     skill=brainstorming" in out


# ---- remove ----


def test_remove_dispatches_name_from_parsed_args(parser, mock_store, capsys):
    args = _parse(parser, ["remove", "daily-standup"])
    _handle_schedule(args)

    mock_store.remove.assert_called_once_with("daily-standup")
    out = capsys.readouterr().out
    assert "Removed schedule 'daily-standup'" in out


# ---- pause / resume ----


def test_pause_disables_named_schedule(parser, mock_store, capsys):
    args = _parse(parser, ["pause", "daily-standup"])
    _handle_schedule(args)

    mock_store.set_enabled.assert_called_once_with("daily-standup", False)
    out = capsys.readouterr().out
    assert "Paused schedule 'daily-standup'" in out


def test_resume_enables_named_schedule(parser, mock_store, capsys):
    args = _parse(parser, ["resume", "daily-standup"])
    _handle_schedule(args)

    mock_store.set_enabled.assert_called_once_with("daily-standup", True)
    out = capsys.readouterr().out
    assert "Resumed schedule 'daily-standup'" in out


# ---- run ----


class _FixedDatetime(datetime_module.datetime):
    """Deterministic stand-in so `mark_run`'s last_run timestamp is assertable."""

    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 18, 6, 30, 0, tzinfo=tz)


def test_run_fires_schedule_and_marks_run_with_next_fire_time(
    parser, mock_store, mock_next_fire_time, mock_fire, monkeypatch
):
    schedule = Schedule(
        name="daily-standup",
        cron="0 7 * * 1-5",
        prompt="summarize my day",
        enabled=True,
    )
    mock_store.get.return_value = schedule
    monkeypatch.setattr(datetime_module, "datetime", _FixedDatetime)

    args = _parse(parser, ["run", "daily-standup"])
    _handle_schedule(args)

    mock_store.get.assert_called_once_with("daily-standup")
    mock_fire.assert_called_once_with(schedule)
    mock_next_fire_time.assert_called_once_with("0 7 * * 1-5")
    mock_store.mark_run.assert_called_once_with(
        "daily-standup",
        "2026-07-18T06:30:00+00:00",
        next_run="2026-07-18T07:00:00+00:00",
    )


# ---- daemon ----


def test_daemon_dispatches_with_no_extra_args(parser, mock_store, mock_run_daemon):
    args = _parse(parser, ["daemon"])
    _handle_schedule(args)

    mock_run_daemon.assert_called_once_with()


# ---- no subcommand ----


def test_no_subcommand_prints_usage_hint_to_stderr(parser, mock_store, capsys):
    args = _parse(parser, [])
    assert args.schedule_action is None

    _handle_schedule(args)

    err = capsys.readouterr().err
    assert "No schedule action specified" in err
    assert "add|list|show|remove|pause|resume|run|daemon" in err
