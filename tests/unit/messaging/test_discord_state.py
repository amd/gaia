# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The bridge's files: the one-copy lock and what status reports."""

from __future__ import annotations

import os
import time

import pytest

from gaia.messaging.discord import state


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_HOME", raising=False)


def test_a_second_bridge_cannot_take_the_lock():
    with state.BridgeLock():
        assert state.is_running()
        with pytest.raises(state.BridgeAlreadyRunning):
            state.BridgeLock().acquire()
    assert not state.is_running()


def test_a_live_state_without_the_lock_reads_as_stopped():
    with state.BridgeLock():
        state.write_status(state.STATE_CONNECTED)
    status = state.read_status()
    assert status.state == state.STATE_STOPPED
    assert "unexpectedly" in status.detail


def test_a_spawned_bridge_reads_as_starting_before_it_takes_the_lock():
    state.write_status(state.STATE_STARTING, "Starting…", pid=os.getpid())
    assert state.read_status().state == state.STATE_STARTING


def test_a_start_that_never_took_the_lock_stops_reading_as_starting(monkeypatch):
    state.write_status(state.STATE_STARTING, "Starting…", pid=os.getpid())
    later = time.time() + state.STARTING_GRACE_SECONDS + 1
    monkeypatch.setattr(state.time, "time", lambda: later)
    assert state.read_status().state == state.STATE_STOPPED


def test_a_corrupt_config_fails_loudly():
    state.config_path().parent.mkdir(parents=True, exist_ok=True)
    state.config_path().write_text("{not json", encoding="utf-8")
    with pytest.raises(state.BridgeStateError, match="not valid JSON"):
        state.load_config()


def test_a_zeroed_status_file_is_set_aside_and_the_bridge_can_still_start():
    state.status_path().parent.mkdir(parents=True, exist_ok=True)
    state.status_path().write_bytes(b"\0" * 200)
    assert state.read_status().state == state.STATE_STOPPED
    state.write_status(state.STATE_STOPPED, "ok")
    assert state.read_status().detail == "ok"
    assert list(state.status_path().parent.glob("status.json.damaged-*"))


def test_writes_reach_the_disk_before_the_rename(monkeypatch):
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(
        state.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))
    )
    monkeypatch.setattr(
        state.os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))
    )
    state.write_json(state.discord_dir() / "x.json", {"a": 1})
    assert order == ["fsync", "replace"]


def test_status_keeps_fields_a_write_does_not_name():
    state.write_status(state.STATE_STOPPED, last_message_id="42")
    state.write_status(state.STATE_STOPPED, "again")
    assert state.read_status().last_message_id == "42"


def test_disconnect_does_not_bring_the_setup_offer_straight_back(monkeypatch):
    from gaia.messaging import onboarding as offer
    from gaia.messaging.discord import cli, credentials

    monkeypatch.setattr(credentials, "clear", lambda: None)
    monkeypatch.setattr(cli, "detect_discord", lambda: True)
    cli.disconnect()
    decision = offer.load_state(cli._onboarding_path(), "Discord")
    assert not offer.should_offer(decision, detected=True)


def test_pair_keeps_the_pairing_when_the_bridge_will_not_stop(monkeypatch):
    from types import SimpleNamespace

    from gaia.messaging.discord import cli

    config = state.BridgeConfig(bot_id="9", owner_id="111", dm_channel_id="5")
    state.save_config(config)
    monkeypatch.setattr(cli, "stop", lambda **_: False)
    assert cli.main(SimpleNamespace(discord_action="pair")) == 1
    assert state.load_config().owner_id == "111"


def test_a_background_start_refuses_foreground_only_limits(capsys):
    from types import SimpleNamespace

    from gaia.messaging.discord import cli

    args = SimpleNamespace(
        discord_action="start", detach=True, deny_gated_tools=True, upload_root=None
    )
    assert cli.main(args) == 2
    assert "foreground only" in capsys.readouterr().err


def _connected(autostart=True):
    config = state.BridgeConfig(bot_id="9", owner_id="111", dm_channel_id="5")
    config.autostart = autostart
    state.save_config(config)


def test_the_users_stop_keeps_the_apps_from_bringing_it_back(monkeypatch):
    from gaia.messaging.discord import cli

    _connected()
    started = []
    monkeypatch.setattr(cli, "start_detached", lambda **_: started.append(1) or 0)
    assert cli.run_stop(emit=lambda _: None) == 0
    assert state.load_config().autostart is False
    assert cli.ensure_started() is None and started == []


def test_a_stop_inside_a_restart_does_not_turn_autostart_off():
    from gaia.messaging.discord import cli

    _connected()
    assert cli.stop()
    assert state.load_config().autostart is True


def test_a_start_turns_autostart_back_on(monkeypatch):
    from gaia.messaging.discord import cli

    _connected(autostart=False)
    monkeypatch.setattr(cli, "_start_detached_locked", lambda **_: 0)
    assert cli.start_detached(emit=lambda _: None) == 0
    assert state.load_config().autostart is True


def test_recovery_text_names_no_command():
    from gaia.messaging.discord import adapter

    assert "gaia discord" not in adapter.RESTART_HINT
    assert "Stop, then Start" in adapter.RESTART_HINT
    assert "never tell them to run a command" in adapter.DISCORD_REPLY_NOTE


def test_setup_with_no_terminal_says_so_instead_of_a_traceback(monkeypatch):
    """`gaia discord setup` with stdin closed (a pipe, CI, a service) ended in
    an EOFError traceback from getpass."""
    from gaia.messaging.discord import cli

    def no_terminal(_prompt):
        raise EOFError

    def never(_token):
        raise AssertionError("nothing was typed, so nothing may be connected")

    monkeypatch.setattr(cli, "connect", never)
    lines = []
    code = cli.run_setup(prompt=no_terminal, emit=lines.append, open_browser=False)

    assert code == 1
    assert "gaia discord connect" in lines[-1]
    assert not state.is_running()


def test_setup_with_an_empty_token_is_refused_not_connected(monkeypatch):
    from gaia.messaging.discord import cli

    started = []
    monkeypatch.setattr(cli, "start_detached", lambda **_: started.append(1) or 0)
    lines = []
    code = cli.run_setup(prompt=lambda _p: "", emit=lines.append, open_browser=False)

    assert code == 1
    assert lines[-1].startswith("\n❌")
    assert started == []
