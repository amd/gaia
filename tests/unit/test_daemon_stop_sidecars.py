# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stopping the daemon also stops the agents it left running (#6572).

A daemon that was terminated, or whose shutdown hung, never tears its sidecars
down, so `gaia daemon stop` and `gaia uninstall` reap them from the ledger.
"""

import os
import subprocess
import sys
import time
from unittest import mock

import pytest

from gaia.daemon import client, instance
from gaia.daemon.errors import DaemonError
from gaia.daemon.instance import DaemonInstance
from gaia.daemon.sidecars import ledger


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "host"))
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")


def _inst(pid=4242):
    return DaemonInstance(
        pid=pid, port=1, token="t", host="127.0.0.1", started_at=time.time()
    )


# --- ledger.reap_after_stop --------------------------------------------------


def test_reap_after_stop_with_no_ledger_kills_nothing():
    assert ledger.reap_after_stop(timeout=0) == ([], [])


def test_reap_after_stop_reports_a_pid_that_outlived_its_kill(monkeypatch):
    monkeypatch.setattr(ledger, "reap_stale", lambda specs: [11, 12])
    monkeypatch.setattr(ledger, "_pid_alive", lambda pid: pid == 12)

    killed, survivors = ledger.reap_after_stop(timeout=0.2)

    assert killed == [11, 12]
    assert survivors == [12]


def test_reap_after_stop_waits_for_a_slow_exit(monkeypatch):
    monkeypatch.setattr(ledger, "reap_stale", lambda specs: [11])
    deaths = iter([True, True, False])
    monkeypatch.setattr(ledger, "_pid_alive", lambda pid: next(deaths))

    assert ledger.reap_after_stop(timeout=5) == ([11], [])


def test_reap_after_stop_kills_a_real_recorded_sidecar():
    """A live process the ledger records, with its argv and port, is ended."""
    port = 45999
    argv = [
        sys.executable,
        "-c",
        "import time; print('ready', flush=True); time.sleep(60)",
        "--port",
        str(port),
    ]
    # Its own session, as the daemon starts agents: the reap kills the process group.
    proc = subprocess.Popen(
        argv, start_new_session=True, stdout=subprocess.PIPE, text=True
    )
    try:
        # Reap once it runs: macOS framework Python has re-exec'd as Python.app by then.
        assert proc.stdout.readline().strip() == "ready"
        if os.name != "nt":
            assert os.getpgid(proc.pid) != os.getpgid(0)
        ledger.record_spawn(
            agent_id="gaia",
            pid=proc.pid,
            port=port,
            mode="user",
            argv=argv,
            started_at=time.time(),
        )

        killed, survivors = ledger.reap_after_stop(timeout=10)

        assert killed == [proc.pid]
        assert survivors == []
        assert proc.wait(timeout=10) is not None
        assert ledger.read_entries() == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        proc.stdout.close()


@pytest.mark.parametrize(
    "cmdline, reason",
    [
        (None, "its command line could not be read"),
        ("/bin/python serve --port 1", "its command line lacks --port 45999"),
    ],
)
def test_a_skipped_live_pid_says_why(monkeypatch, caplog, cmdline, reason):
    """The log names what failed the identity check, never the foreign cmdline."""
    from gaia.daemon.sidecars.spec import builtin_specs

    monkeypatch.setattr(ledger, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(ledger, "_pid_cmdline", lambda pid: cmdline)
    monkeypatch.setattr(ledger, "_probe_health", lambda port: None)
    ledger.record_spawn(
        agent_id="gaia",
        pid=4242,
        port=45999,
        mode="user",
        argv=["/bin/python", "--port", "45999"],
        started_at=time.time(),
    )

    with caplog.at_level("INFO", logger="gaia.daemon.sidecars.ledger"):
        assert ledger.reap_stale(builtin_specs()) == []

    assert reason in caplog.text
    assert "serve" not in caplog.text


_FRAMEWORK = "/Library/Frameworks/Python.framework/Versions"
_APP = "Resources/Python.app/Contents/MacOS/Python"


@pytest.mark.parametrize(
    "live_exe, live_port, reaped",
    [
        # python.org's macOS build runs bin/python as Python.app's binary.
        (f"{_FRAMEWORK}/3.12/{_APP}", 45999, True),
        (f"{_FRAMEWORK}/3.11/{_APP}", 45999, False),
        (f"{_FRAMEWORK}/3.12/{_APP}", 46000, False),
    ],
)
def test_a_framework_python_sidecar_is_recognised_by_its_real_executable(
    monkeypatch, live_exe, live_port, reaped
):
    from gaia.daemon.sidecars.spec import builtin_specs

    recorded = f"{_FRAMEWORK}/3.12/bin/python"
    monkeypatch.setattr(ledger, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(ledger, "_probe_health", lambda port: None)
    monkeypatch.setattr(
        ledger, "_pid_cmdline", lambda pid: f"{live_exe} -m app --port {live_port}"
    )
    monkeypatch.setattr(ledger, "_pid_exe", lambda pid: live_exe)
    killed = []
    monkeypatch.setattr(ledger, "_tree_kill", killed.append)
    ledger.record_spawn(
        agent_id="gaia",
        pid=4242,
        port=45999,
        mode="user",
        argv=[recorded, "-m", "app", "--port", "45999"],
        started_at=time.time(),
    )

    assert ledger.reap_stale(builtin_specs()) == ([4242] if reaped else [])
    assert killed == ([4242] if reaped else [])


def _reap_one(monkeypatch, recorded_argv, port, live_cmdline, live_exe):
    """Reap one recorded live pid against a faked process; return the kills."""
    from gaia.daemon.sidecars.spec import builtin_specs

    monkeypatch.setattr(ledger, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(ledger, "_probe_health", lambda port: None)
    monkeypatch.setattr(ledger, "_pid_cmdline", lambda pid: live_cmdline)
    monkeypatch.setattr(ledger, "_pid_exe", lambda pid: live_exe)
    killed = []
    monkeypatch.setattr(ledger, "_tree_kill", killed.append)
    ledger.record_spawn(
        agent_id="gaia",
        pid=4242,
        port=port,
        mode="user",
        argv=recorded_argv,
        started_at=time.time(),
    )
    assert ledger.reap_stale(builtin_specs()) == killed
    return killed


def test_a_same_version_framework_python_running_another_module_is_spared(
    monkeypatch,
):
    live = f"{_FRAMEWORK}/3.12/{_APP}"
    killed = _reap_one(
        monkeypatch,
        [f"{_FRAMEWORK}/3.12/bin/python", "-m", "app", "--port", "45999"],
        45999,
        f"{live} -m other --port 45999",
        live,
    )
    assert killed == []


_VENV_PY = "/home/u/.venv/bin/python"
_BASE_PY = "/usr/bin/python3.12"


@pytest.mark.parametrize(
    "live_args, reaped",
    [
        ("-m app --port 45999", True),
        ("serve.py --port 45999", False),
    ],
)
def test_a_venv_sidecar_matches_its_base_interpreter_only_with_its_own_args(
    monkeypatch, live_args, reaped
):
    """A venv's python resolves to the base interpreter; that alone is not identity."""
    real = ledger.os.path.realpath
    monkeypatch.setattr(
        ledger.os.path,
        "realpath",
        lambda p, **kw: _BASE_PY if str(p) in (_VENV_PY, _BASE_PY) else real(p, **kw),
    )
    killed = _reap_one(
        monkeypatch,
        [_VENV_PY, "-m", "app", "--port", "45999"],
        45999,
        f"{_BASE_PY} {live_args}",
        _BASE_PY,
    )
    assert killed == ([4242] if reaped else [])


def test_a_recorded_port_does_not_match_a_longer_live_port(monkeypatch):
    killed = _reap_one(
        monkeypatch,
        ["/bin/python", "-m", "app", "--port", "4599"],
        4599,
        "/bin/python -m app --port 45999",
        "/bin/python",
    )
    assert killed == []


def test_a_cmdline_psutil_cannot_read_skips_the_pid_instead_of_crashing(
    monkeypatch, caplog
):
    """psutil's macOS cmdline read can raise SystemError for a live process."""
    import psutil

    from gaia.daemon.sidecars.spec import builtin_specs

    class _Unreadable:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            raise SystemError(
                "<built-in function proc_cmdline> returned a result with an "
                "exception set"
            )

    monkeypatch.setattr(ledger, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(ledger, "_probe_health", lambda port: None)
    monkeypatch.setattr(psutil, "Process", _Unreadable)
    killed = []
    monkeypatch.setattr(ledger, "_tree_kill", killed.append)
    ledger.record_spawn(
        agent_id="gaia",
        pid=4242,
        port=45999,
        mode="user",
        argv=["/bin/python", "--port", "45999"],
        started_at=time.time(),
    )

    with caplog.at_level("INFO", logger="gaia.daemon.sidecars.ledger"):
        assert ledger.reap_stale(builtin_specs()) == []

    assert killed == []
    assert "cannot read pid=4242's command line (SystemError)" in caplog.text


# --- gaia daemon stop --------------------------------------------------------


def _no_lemonade(monkeypatch):
    monkeypatch.setattr(
        "gaia.llm.lemonade_embedded.EmbeddedLemonade",
        mock.Mock(side_effect=AssertionError("Lemonade must not be touched")),
    )


def test_stop_with_no_daemon_still_reaps_its_agents(monkeypatch, capsys):
    from gaia import cli

    _no_lemonade(monkeypatch)
    monkeypatch.setattr(instance, "read_instance", lambda: None)
    reap = mock.Mock(return_value=([77], []))
    monkeypatch.setattr(ledger, "reap_after_stop", reap)

    cli._handle_daemon_stop()

    reap.assert_called_once_with()
    assert "pid 77" in capsys.readouterr().out


def test_stop_exits_nonzero_naming_an_agent_that_survives(monkeypatch, capsys):
    from gaia import cli

    _no_lemonade(monkeypatch)
    monkeypatch.setattr(instance, "read_instance", lambda: None)
    monkeypatch.setattr(ledger, "reap_after_stop", lambda: ([77, 78], [78]))

    with pytest.raises(SystemExit) as exc:
        cli._handle_daemon_stop()

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "78 are still running" in out
    assert "gaia daemon stop" in out


def test_a_terminated_daemon_has_its_agents_reaped_after_it_is_gone(monkeypatch):
    from gaia import cli

    _no_lemonade(monkeypatch)
    order = []
    inst = _inst()
    monkeypatch.setattr(instance, "read_instance", lambda: inst)
    monkeypatch.setattr(instance, "pid_alive", lambda pid: True)
    monkeypatch.setattr(instance, "remove_instance", lambda only_pid: None)
    monkeypatch.setattr(
        client, "request_shutdown", mock.Mock(side_effect=DaemonError("hung"))
    )
    monkeypatch.setattr(
        instance, "terminate_instance", lambda i: order.append("terminate")
    )
    monkeypatch.setattr(
        client, "wait_until_gone", lambda i, timeout: order.append("gone") or True
    )
    monkeypatch.setattr(
        ledger, "reap_after_stop", lambda: order.append("reap") or ([], [])
    )

    cli._handle_daemon_stop()

    assert order == ["terminate", "gone", "reap"]


def test_a_daemon_that_survives_terminate_fails_the_stop(monkeypatch, capsys):
    from gaia import cli

    _no_lemonade(monkeypatch)
    inst = _inst(pid=5150)
    monkeypatch.setattr(instance, "read_instance", lambda: inst)
    monkeypatch.setattr(instance, "pid_alive", lambda pid: True)
    removed = mock.Mock()
    monkeypatch.setattr(instance, "remove_instance", removed)
    monkeypatch.setattr(client, "request_shutdown", lambda i: None)
    monkeypatch.setattr(instance, "terminate_instance", lambda i: None)
    monkeypatch.setattr(client, "wait_until_gone", lambda i, timeout: False)
    reap = mock.Mock()
    monkeypatch.setattr(ledger, "reap_after_stop", reap)

    with pytest.raises(SystemExit) as exc:
        cli._handle_daemon_stop()

    assert exc.value.code == 1
    assert "pid 5150 is still running" in capsys.readouterr().out
    removed.assert_not_called()
    reap.assert_not_called()


# --- gaia uninstall ----------------------------------------------------------


def test_uninstall_with_no_daemon_reaps_its_agents(monkeypatch):
    from gaia.installer import uninstall_command

    monkeypatch.setattr(instance, "read_instance", lambda: None)
    monkeypatch.setattr(ledger, "reap_after_stop", lambda: ([77], []))
    lines = []

    assert uninstall_command._stop_daemon(printer=lines.append) is True
    assert any("pid 77" in line for line in lines)


def test_uninstall_stops_when_an_agent_survives(monkeypatch):
    from gaia.installer import uninstall_command

    monkeypatch.setattr(instance, "read_instance", lambda: None)
    monkeypatch.setattr(ledger, "reap_after_stop", lambda: ([77], [77]))
    lines = []

    assert uninstall_command._stop_daemon(printer=lines.append) is False
    assert any("77 are still running" in line for line in lines)
