# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A daemon exits on its own once nothing can reach it (#5293).

Test runs used to leave dozens of daemons polling a deleted temp home forever.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from unittest import mock

import pytest

from gaia.daemon import instance as instance_mod
from gaia.daemon import lifetime
from gaia.daemon import server as daemon_server
from gaia.daemon.errors import DaemonStartError


@pytest.fixture()
def daemon_home(tmp_path, monkeypatch):
    home = tmp_path / "host"
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(home))
    return home


@pytest.fixture()
def registered(daemon_home):
    instance_mod.write_instance(
        instance_mod.DaemonInstance(pid=os.getpid(), port=1, token="t")
    )
    return daemon_home


@pytest.fixture()
def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


# -- GAIA_DAEMON_EXIT_WITH_PID ------------------------------------------------


def test_exit_with_pid_unset_means_no_tie(monkeypatch):
    monkeypatch.delenv(lifetime.EXIT_WITH_PID_ENV, raising=False)
    assert lifetime.exit_with_pid_from_env() is None


@pytest.mark.parametrize("raw", ["", "   "])
def test_exit_with_pid_blank_means_no_tie(monkeypatch, raw):
    monkeypatch.setenv(lifetime.EXIT_WITH_PID_ENV, raw)
    assert lifetime.exit_with_pid_from_env() is None


def test_exit_with_pid_reads_a_pid(monkeypatch):
    monkeypatch.setenv(lifetime.EXIT_WITH_PID_ENV, " 4321 ")
    assert lifetime.exit_with_pid_from_env() == 4321


@pytest.mark.parametrize("raw", ["abc", "12x", "0", "-5", "1.5"])
def test_exit_with_pid_rejects_a_non_pid_loudly(monkeypatch, raw):
    monkeypatch.setenv(lifetime.EXIT_WITH_PID_ENV, raw)
    with pytest.raises(DaemonStartError, match=lifetime.EXIT_WITH_PID_ENV):
        lifetime.exit_with_pid_from_env()


def test_test_session_ties_its_daemons_to_itself():
    # Set by the root conftest; every daemon a test starts inherits it.
    assert os.environ[lifetime.EXIT_WITH_PID_ENV] == str(os.getpid())


# -- exit_reason ---------------------------------------------------------------


def test_no_reason_while_home_and_state_file_exist(registered):
    assert lifetime.exit_reason() is None
    assert lifetime.exit_reason(os.getpid()) is None


def test_reason_when_home_is_gone(daemon_home):
    reason = lifetime.exit_reason()
    assert reason is not None and str(daemon_home) in reason


def test_reason_when_home_was_deleted_after_registering(registered):
    shutil.rmtree(registered, ignore_errors=True)
    assert lifetime.exit_reason() is not None


def test_reason_when_only_the_state_file_is_gone(registered):
    # What a Windows rmtree leaves when the daemon holds its log open.
    (registered / "daemon.log").write_text("held open", encoding="utf-8")
    instance_mod.remove_instance()
    reason = lifetime.exit_reason()
    assert reason is not None and "instance.json" in reason


def test_a_state_path_that_is_a_folder_is_not_a_state_file(registered):
    instance_mod.remove_instance()
    (registered / "instance.json").mkdir()
    assert lifetime.exit_reason() is not None


def test_reason_when_the_tied_process_has_exited(registered, dead_pid):
    reason = lifetime.exit_reason(dead_pid)
    assert reason is not None
    assert str(dead_pid) in reason and lifetime.EXIT_WITH_PID_ENV in reason


def test_home_is_checked_before_the_tied_pid(daemon_home, dead_pid):
    reason = lifetime.exit_reason(dead_pid)
    assert str(daemon_home) in reason


# -- LifetimeWatch ---------------------------------------------------------------


def _watch(on_exit, **kwargs):
    return lifetime.LifetimeWatch(on_exit, interval=0.02, **kwargs)


def test_watch_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        lifetime.LifetimeWatch(lambda _r: None, interval=0)


def test_watch_does_nothing_while_the_daemon_is_reachable(registered):
    calls = []
    watch = _watch(calls.append)
    watch.start()
    try:
        threading.Event().wait(0.2)
    finally:
        watch.stop()
    assert calls == []


def test_watch_exits_once_when_the_state_file_disappears(registered, caplog):
    fired = threading.Event()
    calls = []

    def on_exit(reason):
        calls.append(reason)
        fired.set()

    watch = _watch(on_exit)
    with caplog.at_level(logging.WARNING, logger="gaia.daemon.lifetime"):
        watch.start()
        try:
            instance_mod.remove_instance()
            assert fired.wait(5), "the watch never noticed instance.json was gone"
            threading.Event().wait(0.2)  # would a second call follow?
        finally:
            watch.stop()
    assert len(calls) == 1 and "instance.json" in calls[0]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(registered) in warnings[0].getMessage()


def test_watch_exits_when_the_tied_process_exits(registered):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    fired = threading.Event()
    watch = _watch(lambda _r: fired.set(), exit_with_pid=proc.pid)
    watch.start()
    try:
        threading.Event().wait(0.1)
        assert not fired.is_set()
        proc.kill()
        proc.wait(timeout=30)
        assert fired.wait(5)
    finally:
        watch.stop()
        if proc.poll() is None:
            proc.kill()


def test_watch_stop_is_safe_before_start_and_twice():
    watch = _watch(lambda _r: None)
    watch.stop()
    watch.start()
    watch.stop()
    watch.stop()


def test_watch_stop_from_its_own_callback_does_not_deadlock(daemon_home):
    done = threading.Event()
    holder = {}

    def on_exit(_reason):
        holder["watch"].stop()
        done.set()

    holder["watch"] = _watch(on_exit)
    holder["watch"].start()
    assert done.wait(5)


# -- server wiring ---------------------------------------------------------------


def _register(lifetime_watch, **overrides):
    kwargs = dict(
        specs={},
        pid=4242,
        port=55123,
        token="tok",
        host="127.0.0.1",
        started_at=1.0,
        refresher=mock.Mock(),
        clock=mock.Mock(),
        lemonade_owner=mock.Mock(),
        lemonade_clients=mock.Mock(),
        lifetime=lifetime_watch,
    )
    kwargs.update(overrides)
    return daemon_server._build_register(**kwargs)


def test_register_starts_the_watch_after_instance_json_exists(daemon_home):
    seen = []
    watch = mock.Mock()
    watch.start.side_effect = lambda: seen.append(
        instance_mod.read_instance() is not None
    )
    _register(watch)()
    assert seen == [True]


def test_watch_start_failure_rolls_back_registration(daemon_home):
    watch = mock.Mock()
    watch.start.side_effect = RuntimeError("watch exploded")
    refresher, clock = mock.Mock(), mock.Mock()
    register = _register(watch, refresher=refresher, clock=clock)
    with pytest.raises(RuntimeError, match="watch exploded"):
        register()
    refresher.stop.assert_called_once()
    clock.stop.assert_called_once()
    assert instance_mod.read_instance() is None


def test_deregister_stops_the_watch_first(daemon_home):
    calls = []
    watch = mock.Mock()
    watch.stop.side_effect = lambda: calls.append("lifetime.stop")
    clients = mock.Mock()
    clients.stop.side_effect = lambda: calls.append("clients.stop")
    daemon_server._build_deregister(
        registry=mock.Mock(),
        custody_store=mock.Mock(),
        pid=1,
        refresher=mock.Mock(),
        clock=mock.Mock(),
        lemonade=mock.Mock(),
        lemonade_owner=mock.Mock(),
        lemonade_clients=clients,
        lifetime=watch,
    )()
    assert calls[:2] == ["lifetime.stop", "clients.stop"]


# -- idle exit outside the default home ----------------------------------------


def test_idle_limit_defaults_to_two_hours(monkeypatch):
    monkeypatch.delenv(lifetime.IDLE_EXIT_ENV, raising=False)
    assert lifetime.idle_exit_seconds_from_env() == 120 * 60.0


def test_idle_limit_reads_minutes_and_zero_turns_it_off(monkeypatch):
    monkeypatch.setenv(lifetime.IDLE_EXIT_ENV, " 30 ")
    assert lifetime.idle_exit_seconds_from_env() == 1800.0
    monkeypatch.setenv(lifetime.IDLE_EXIT_ENV, "0")
    assert lifetime.idle_exit_seconds_from_env() is None


@pytest.mark.parametrize("raw", ["abc", "1.5", "-1"])
def test_idle_limit_rejects_a_bad_value_loudly(monkeypatch, raw):
    monkeypatch.setenv(lifetime.IDLE_EXIT_ENV, raw)
    with pytest.raises(DaemonStartError, match=lifetime.IDLE_EXIT_ENV):
        lifetime.idle_exit_seconds_from_env()


def test_a_scratch_home_is_not_the_default(registered):
    assert lifetime.is_default_home() is False


def test_the_machine_home_is_the_default(monkeypatch, tmp_path):
    monkeypatch.setattr(lifetime.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / ".gaia" / "host"))
    assert lifetime.is_default_home() is True


def test_an_unused_scratch_daemon_exits_after_the_limit(registered):
    reason = lifetime.exit_reason(idle_seconds=7200.0, idle_limit=7200.0)
    assert reason is not None and "nothing has used it" in reason


@pytest.mark.parametrize("idle", [None, 7199.0])
def test_a_used_or_recently_used_daemon_stays(registered, idle):
    assert lifetime.exit_reason(idle_seconds=idle, idle_limit=7200.0) is None


def test_no_limit_means_no_idle_exit(registered):
    assert lifetime.exit_reason(idle_seconds=10**9, idle_limit=None) is None


def test_the_default_home_never_idles_out(registered, monkeypatch):
    monkeypatch.setattr(lifetime, "is_default_home", lambda: True)
    assert lifetime.exit_reason(idle_seconds=10**9, idle_limit=60.0) is None


def test_the_watch_shuts_an_idle_scratch_daemon_down(registered):
    reasons = []
    done = threading.Event()

    def on_exit(reason):
        reasons.append(reason)
        done.set()

    watch = lifetime.LifetimeWatch(
        on_exit, idle=lambda: 120.0, idle_limit=60.0, interval=0.05
    )
    watch.start()
    try:
        assert done.wait(5)
    finally:
        watch.stop()
    assert "nothing has used it" in reasons[0]
