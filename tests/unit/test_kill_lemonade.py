# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia kill --lemonade` stops the Lemonade Server GAIA actually uses.

It used to run the removed `lemonade-server stop` and then kill whatever held
port 13305, so it never reached GAIA's own server, which picks its own port,
or one set with LEMONADE_BASE_URL (#4437).
"""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from gaia.llm.lemonade_embedded import EmbeddedLemonadeError


class _OwnServer:
    """Stands in for GAIA's embedded Lemonade."""

    def __init__(self, pid=None, unresponsive_pid=None, stop_error=None):
        self._status = SimpleNamespace(
            running=pid is not None, pid=pid, unresponsive_pid=unresponsive_pid
        )
        self._stop_error = stop_error
        self.stop_result = True
        self.status_calls = 0
        self.stops = 0

    def status(self):
        self.status_calls += 1
        return self._status

    def stop(self):
        self.stops += 1
        if self._stop_error:
            raise self._stop_error
        return self.stop_result


@pytest.fixture
def kill_env(monkeypatch):
    """Run `gaia kill --lemonade` against a chosen URL and own server."""
    killed_ports = []
    commands = []
    real_run = subprocess.run

    def recording_run(args, *a, **kw):
        commands.append(args)
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", recording_run)

    def run(
        own=None,
        configured=None,
        url="http://localhost:13305/api/v1",
        port_result=None,
    ):
        own = own or _OwnServer()
        monkeypatch.setattr(
            "gaia.llm.lemonade_client.configured_lemonade_url", lambda: configured
        )
        monkeypatch.setattr(
            "gaia.cli.resolve_lemonade_base_url",
            lambda base_url=None: configured or url,
        )
        monkeypatch.setattr("gaia.llm.lemonade_embedded.EmbeddedLemonade", lambda: own)

        def kill_port(port, lemonade_only=False):
            assert lemonade_only, "a configured port was hunted by interpreter name"
            killed_ports.append(port)
            return port_result or {
                "success": True,
                "message": f"No process is listening on port {port}",
            }

        monkeypatch.setattr("gaia.cli.kill_process_by_port", kill_port)
        monkeypatch.setattr(sys, "argv", ["gaia", "kill", "--lemonade"])
        from gaia.cli import main

        with pytest.raises(SystemExit) as exit_info:
            main()
        return SimpleNamespace(code=exit_info.value.code, own=own, ports=killed_ports)

    yield run
    assert not any(
        "lemonade-server" in " ".join(map(str, c if isinstance(c, list) else [c]))
        for c in commands
    ), f"ran the removed lemonade-server CLI: {commands}"


def test_gaias_own_server_is_stopped_through_its_owner(kill_env, capsys):
    result = kill_env(own=_OwnServer(pid=4242))

    assert result.code == 0
    assert result.own.stops == 1
    assert result.ports == [], "GAIA's own server was hunted by port"
    assert "Stopped GAIA's Lemonade Server (pid 4242)" in capsys.readouterr().out


def test_an_own_server_that_stopped_answering_is_stopped_too(kill_env):
    result = kill_env(own=_OwnServer(unresponsive_pid=4343))

    assert result.code == 0
    assert result.own.stops == 1


def test_an_own_server_gone_before_the_stop_is_not_reported_as_stopped(
    kill_env, capsys
):
    own = _OwnServer(pid=4242)
    own.stop_result = False

    result = kill_env(own=own)

    assert result.code == 0
    assert "had already stopped" in capsys.readouterr().out


def test_an_own_server_that_will_not_stop_fails_loudly(kill_env, capsys):
    own = _OwnServer(
        pid=4242, stop_error=EmbeddedLemonadeError("pid 4242 is not GAIA's")
    )

    result = kill_env(own=own)

    assert result.code == 1
    assert "Could not stop GAIA's Lemonade Server (pid 4242)" in capsys.readouterr().out


def test_a_server_set_with_lemonade_base_url_is_stopped_on_its_port(kill_env):
    result = kill_env(configured="http://127.0.0.1:8123/api/v1")

    assert result.code == 0
    assert result.ports == [8123]
    assert result.own.status_calls == 0, "a chosen server is not GAIA's to look up"


def test_with_no_own_server_running_the_server_gaia_uses_is_stopped(kill_env):
    result = kill_env(url="http://localhost:13305/api/v1")

    assert result.code == 0
    assert result.ports == [13305]


def test_a_server_on_another_machine_is_refused_not_killed(kill_env, capsys):
    result = kill_env(configured="http://10.0.0.5:13305/api/v1")

    assert result.code == 1
    assert result.ports == []
    assert "http://10.0.0.5:13305/api/v1" in capsys.readouterr().out


def test_a_process_that_cannot_be_stopped_is_an_error(kill_env):
    refused = {"success": False, "message": "Refused to kill 77 (python)"}

    result = kill_env(configured="http://localhost:9000", port_result=refused)

    assert result.code == 1


@pytest.mark.parametrize("host", ["0.0.0.0", "[::]", "[::1]", "127.0.0.2"])
def test_an_address_that_means_this_machine_is_stopped_here(kill_env, host):
    result = kill_env(configured=f"http://{host}:8123/api/v1")

    assert result.code == 0
    assert result.ports == [8123]


def test_this_machines_own_hostname_is_stopped_here(kill_env):
    import socket

    result = kill_env(configured=f"http://{socket.gethostname()}:8123/api/v1")

    assert result.code == 0
    assert result.ports == [8123]


def test_another_machine_is_not_called_this_one(kill_env, capsys):
    result = kill_env(configured="http://lemonade-box.example.invalid:13305/api/v1")

    assert result.code == 1
    assert result.ports == []
    assert "isn't this machine" in capsys.readouterr().out


@pytest.fixture
def port_listing(monkeypatch):
    """The real kill_process_by_port over a mocked listing; nothing is killed."""
    terminated = []

    def run(listeners):
        monkeypatch.setattr("gaia.cli.listeners_on_port", lambda port: listeners)
        monkeypatch.setattr("gaia.cli.terminate_pid", terminated.append)
        return terminated

    return run


@pytest.mark.parametrize(
    "name",
    [
        "python.exe",
        "python3",
        "pythonw.exe",
        "node",
        "node.exe",
        "electron",
        "gaia.exe",
        "",
        "nginx",
    ],
)
def test_a_listener_that_is_not_lemonade_is_refused(port_listing, name):
    from gaia.cli import kill_process_by_port

    terminated = port_listing([(4321, name)])

    result = kill_process_by_port(8000, lemonade_only=True)

    assert terminated == []
    assert result["success"] is False
    assert "4321" in result["message"]
    assert "isn't a Lemonade Server" in result["message"]
    assert "lemonade-server" not in result["message"]


@pytest.mark.parametrize(
    "name", ["lemonadeserver.exe", "lemond", "lemond.exe", "llama-server.exe"]
)
def test_a_lemonade_listener_is_stopped(port_listing, name):
    from gaia.cli import kill_process_by_port

    terminated = port_listing([(4321, name)])

    result = kill_process_by_port(8000, lemonade_only=True)

    assert terminated == [4321]
    assert result["success"] is True


def test_a_lemonade_listener_beside_a_foreign_one_spares_the_foreign_one(
    port_listing,
):
    from gaia.cli import kill_process_by_port

    terminated = port_listing([(1, "lemond"), (2, "python.exe")])

    result = kill_process_by_port(8000, lemonade_only=True)

    assert terminated == [1]
    assert result["success"] is False


def test_gaia_kill_lemonade_spares_a_dev_server_on_the_configured_port(
    port_listing, monkeypatch, capsys
):
    terminated = port_listing([(4321, "python.exe")])
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.configured_lemonade_url",
        lambda: "http://localhost:8000/api/v1",
    )
    monkeypatch.setattr(
        "gaia.cli.resolve_lemonade_base_url",
        lambda base_url=None: "http://localhost:8000/api/v1",
    )
    monkeypatch.setattr(sys, "argv", ["gaia", "kill", "--lemonade"])
    from gaia.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 1
    assert terminated == []
    assert "4321 (python.exe)" in capsys.readouterr().out
