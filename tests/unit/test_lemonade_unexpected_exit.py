# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How GAIA reports its embedded Lemonade disappearing on its own (#5261)."""

import logging
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from gaia.llm import lemonade_embedded
from gaia.llm.lemonade_embedded import EmbeddedLemonade

PID = 48213
PORT = 60293
WARNING = "exited without GAIA stopping it"


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(lemonade_embedded, "_SPAWNED", {})
    monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: None)
    monkeypatch.setattr(EmbeddedLemonade, "_identify", lambda *a: "other")
    return EmbeddedLemonade(home=tmp_path)


@pytest.fixture
def caplog_warnings(caplog):
    caplog.set_level(logging.DEBUG, logger=lemonade_embedded.log.name)
    return caplog


def _record(manager, **extra):
    manager._write_state(
        {
            "pid": PID,
            "port": PORT,
            "api_key": "k",
            "version": manager.version,
            "started_at": time.time() - 23,
            **extra,
        }
    )


def _write_log(manager, *lines):
    manager.root.mkdir(parents=True, exist_ok=True)
    manager.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _exited(code):
    return SimpleNamespace(pid=PID, poll=lambda: code, returncode=code)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_an_exit_on_its_own_names_pid_port_uptime_code_and_log_tail(
    manager, caplog_warnings, monkeypatch
):
    monkeypatch.setattr(lemonade_embedded, "_windows_exit_info", lambda pid: None)
    lemonade_embedded._SPAWNED[PID] = _exited(3)
    _record(manager)
    _write_log(
        manager,
        "[Info] (Router) Loading user.embeddinggemma-300m-GGUF",
        "[Info] (Server) GET /api/v1/health 200",
        "[Info] (LlamaCpp) Model loaded",
        "[Info] (Server) POST /api/v1/embeddings 200",
        "[Info] (Server) GET /api/v1/health 200",
    )

    assert manager.status().running is False

    (message,) = _warnings(caplog_warnings)
    assert WARNING in message
    assert f"pid {PID}" in message and f"port {PORT}" in message
    assert "found gone 23s later" in message
    assert "exit code 3" in message
    assert str(manager.log_path) in message
    assert "Loading user.embeddinggemma" in message
    assert "Model loaded | [Info] (Server) POST /api/v1/embeddings 200" in message
    assert "health" not in message.split(str(manager.log_path))[1]
    assert PID not in lemonade_embedded._SPAWNED
    assert not manager.state_path.exists()


def test_a_windows_exit_reports_when_it_exited_and_its_ntstatus_in_hex(
    manager, caplog_warnings, monkeypatch
):
    started = time.time() - 40
    monkeypatch.setattr(
        lemonade_embedded,
        "_windows_exit_info",
        lambda pid: (0xC0000409, started + 21),
    )
    _record(manager, started_at=started)

    manager.status()

    (message,) = _warnings(caplog_warnings)
    assert "exited 21s later" in message
    assert "exit code 3221226505 (0xC0000409)" in message
    assert "not started by this process" in message


def test_no_handle_and_no_pid_says_exit_code_unavailable(
    manager, caplog_warnings, monkeypatch
):
    monkeypatch.setattr(lemonade_embedded, "_windows_exit_info", lambda pid: None)
    _record(manager, started_at=None)

    assert manager.status().running is False

    (message,) = _warnings(caplog_warnings)
    assert "exit code unavailable" in message
    assert "start time not recorded" in message
    assert "unreadable" in message, "a missing log must be named, not crash"


def test_a_signal_kill_is_named_as_one(manager, caplog_warnings, monkeypatch):
    monkeypatch.setattr(lemonade_embedded, "_windows_exit_info", lambda pid: None)
    lemonade_embedded._SPAWNED[PID] = _exited(-9)
    _record(manager)

    manager.status()

    assert "killed by signal 9" in _warnings(caplog_warnings)[0]


def test_a_deliberate_stop_never_warns(manager, caplog_warnings, monkeypatch):
    lemonade_embedded._SPAWNED[PID] = SimpleNamespace(
        pid=PID, poll=lambda: None, returncode=None
    )
    killed = []
    identities = iter(["own", "other"])
    monkeypatch.setattr(EmbeddedLemonade, "_identify", lambda *a: next(identities))
    monkeypatch.setattr(EmbeddedLemonade, "status", _unresponsive)

    def kill(pid):
        killed.append(pid)
        assert manager._read_state()["stop_requested_by"], "stop left no marker"

    monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(kill))
    _record(manager)

    assert manager.stop() is True

    assert killed == [PID]
    assert _warnings(caplog_warnings) == []
    assert PID not in lemonade_embedded._SPAWNED


def test_another_process_finding_a_stopped_server_does_not_warn(
    manager, caplog_warnings
):
    # The marker is in state.json, so a client racing the daemon's stop sees it.
    _record(manager, stop_requested_by=1234)

    assert manager.status().running is False

    assert _warnings(caplog_warnings) == []
    assert "GAIA stopped it" in caplog_warnings.text


def test_a_failed_stop_takes_its_marker_back(manager, monkeypatch):
    identities = iter(["own"])
    monkeypatch.setattr(EmbeddedLemonade, "_identify", lambda *a: next(identities))
    monkeypatch.setattr(EmbeddedLemonade, "status", _unresponsive)

    def kill(pid):
        raise lemonade_embedded.EmbeddedLemonadeError("access denied")

    monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(kill))
    _record(manager)

    with pytest.raises(lemonade_embedded.EmbeddedLemonadeError):
        manager.stop()

    assert "stop_requested_by" not in manager._read_state()


def _unresponsive(self):
    return lemonade_embedded.EmbeddedStatus(
        installed=True, running=False, version=self.version, unresponsive_pid=PID
    )


def test_start_records_its_start_time_and_keeps_the_handle(manager, monkeypatch):
    monkeypatch.setattr(manager, "is_installed", lambda: True)
    monkeypatch.setattr(manager, "write_config", lambda: None)
    monkeypatch.setattr(manager, "_health", lambda *a, **k: {"status": "ok"})
    process = SimpleNamespace(pid=PID, poll=lambda: None, returncode=None)
    monkeypatch.setattr(
        "gaia.llm.lemonade_embedded.subprocess.Popen", lambda *a, **k: process
    )

    before = time.time()
    manager.start(port=65530)

    assert manager._read_state()["started_at"] >= before
    assert lemonade_embedded._SPAWNED[PID] is process


def test_a_real_child_exit_code_is_read_back(monkeypatch):
    """The real Popen/OpenProcess path, not a stub."""
    monkeypatch.setattr(lemonade_embedded, "_SPAWNED", {})
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(7)"])
    lemonade_embedded._SPAWNED[child.pid] = child
    child.wait(timeout=30)

    code, exited_at = lemonade_embedded._exit_details(child.pid)

    assert code == 7
    if sys.platform == "win32":
        assert exited_at is not None and abs(exited_at - time.time()) < 60
