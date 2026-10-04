# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A machine whose only Lemonade is GAIA's own starts that one, from every entry point.

The state under test is the one ``gaia init`` leaves a new user in: GAIA's
embedded server installed, currently stopped, and no system Lemonade anywhere.
Every start path used to resolve the *system* launcher here — the CLI, the
Agent base preflight, ``initialize_lemonade``, ``create_lemonade_client`` and
the daemon's ``/lemonade/start`` — and then told the user to run the
``gaia init`` they had just run.

Mocks sit at the process and daemon boundaries only: the daemon's
``ensure_lemonade`` call (which records the server the way the real embedded
start does), Lemonade's HTTP surface, ``subprocess.Popen``, and the system
launcher resolver, which here also records whether anything consulted it.
"""

import json
import os
import subprocess

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm import lemonade_launcher, lemonade_supervisor
from gaia.llm.lemonade_embedded import EmbeddedLemonade
from gaia.llm.lemonade_launcher import LemonadeTooling, gaia_runs_lemonade
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.llm.lemonade_supervisor import LemonadeStartError, LemonadeSupervisor

EMBEDDED_PORT = 45678
EMBEDDED_URL = f"http://localhost:{EMBEDDED_PORT}/api/v1"
EMBEDDED_KEY = "embedded-key"
STOPPED_DEFAULT_URL = "http://localhost:13305/api/v1"


class Machine:
    """What the start paths touched: the daemon, the system launcher, processes."""

    def __init__(self, embedded: EmbeddedLemonade):
        self.embedded = embedded
        self.daemon_starts = 0
        self.system_launcher_lookups = 0
        self.system_supervisor_calls = []
        self.spawned = []

    @property
    def embedded_up(self) -> bool:
        return self.embedded.state_path.exists()


@pytest.fixture(autouse=True)
def _reset_manager():
    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """GAIA's server installed and stopped; no system Lemonade on the machine."""
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    for var in (
        "LEMONADE_BASE_URL",
        "LEMONADE_API_KEY",
        "LEMONADE_HOST",
        "LEMONADE_PORT",
        "GAIA_LEMONADE_EMBEDDED",
        "LEMONADE_SERVER_PATH",
    ):
        monkeypatch.delenv(var, raising=False)

    embedded = EmbeddedLemonade()
    embedded.daemon_path.parent.mkdir(parents=True)
    embedded.daemon_path.write_text("")
    m = Machine(embedded)

    def no_system_install():
        m.system_launcher_lookups += 1
        return LemonadeTooling(found=False, kind="none")

    for module in (lemonade_launcher, lc, lemonade_supervisor):
        monkeypatch.setattr(module, "resolve_lemonade", no_system_install)

    def refuse_spawn(argv, *args, **kwargs):
        m.spawned.append(argv)
        raise AssertionError(f"a start path spawned a process: {argv}")

    monkeypatch.setattr(subprocess, "Popen", refuse_spawn)

    def daemon_starts_embedded(*args, **kwargs):
        # The real start records pid/port/key in state.json, which is what
        # every client then resolves its URL and API key from.
        m.daemon_starts += 1
        embedded.state_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "port": EMBEDDED_PORT,
                    "api_key": EMBEDDED_KEY,
                    "version": embedded.version,
                }
            )
        )
        return {
            "base_url": EMBEDDED_URL,
            "port": EMBEDDED_PORT,
            "version": embedded.version,
            "started": True,
        }

    monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_starts_embedded)

    def system_supervisor(**kwargs):
        m.system_supervisor_calls.append(kwargs)
        raise LemonadeStartError("system supervisor reached")

    monkeypatch.setattr(
        "gaia.llm.lemonade_service.ensure_lemonade_running", system_supervisor
    )

    def answers(client) -> bool:
        return m.embedded_up and client.port == EMBEDDED_PORT

    def get_status(self):
        status = lc.LemonadeStatus(url=self.base_url, running=answers(self))
        if status.running:
            status.context_size = 65536
            status.loaded_models = [{"id": "Gemma-4-E4B-it-GGUF", "type": "llm"}]
        return status

    def health_check(self, timeout=None):
        if not answers(self):
            raise lc.LemonadeClientError(f"connection refused at {self.base_url}")
        return {"status": "ok"}

    monkeypatch.setattr(lc.LemonadeClient, "get_status", get_status)
    monkeypatch.setattr(lc.LemonadeClient, "health_check", health_check)
    monkeypatch.setattr(
        lc.LemonadeClient, "get_model_max_context_window", lambda *a, **k: None
    )
    return m


def _assert_started_gaias_own(m: Machine):
    assert m.daemon_starts == 1
    assert m.system_launcher_lookups == 0
    assert m.system_supervisor_calls == []
    assert m.spawned == []


class TestResolver:
    def test_gaias_own_server_is_the_one_to_start(self, machine):
        assert gaia_runs_lemonade() is True
        # The stopped-state default is what every caller resolved before start.
        assert gaia_runs_lemonade(STOPPED_DEFAULT_URL) is True

    def test_a_different_server_the_caller_named_is_not(self, machine):
        assert gaia_runs_lemonade("http://localhost:9999/api/v1") is False
        assert gaia_runs_lemonade("http://gpu-box:13305/api/v1") is False

    def test_lemonade_base_url_opts_out(self, machine, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box:13305")
        assert gaia_runs_lemonade() is False

    def test_no_embedded_install_means_system(self, machine):
        machine.embedded.daemon_path.unlink()
        assert gaia_runs_lemonade() is False


@pytest.mark.embedded_start
class TestEnsureReady:
    @pytest.mark.parametrize(
        "kwargs",
        [
            # initialize_lemonade_for_agent (gaia llm / chat / api) pins the
            # env-resolved host and port.
            {"host": "localhost", "port": 13305},
            # The Agent base preflight pins the URL it resolved before start.
            {"base_url": STOPPED_DEFAULT_URL},
            # The Agent UI server pins nothing.
            {},
        ],
        ids=["cli", "agent", "ui-server"],
    )
    def test_starts_gaias_own_server_and_reports_its_port(self, machine, kwargs):
        assert LemonadeManager.ensure_ready(min_context_size=65536, **kwargs)

        _assert_started_gaias_own(machine)
        assert LemonadeManager.get_base_url() == EMBEDDED_URL

    def test_a_server_the_caller_named_is_not_redirected(self, machine):
        other = "http://localhost:9999/api/v1"

        ready = LemonadeManager.ensure_ready(min_context_size=65536, base_url=other)

        assert ready is False

        assert machine.daemon_starts == 0
        assert machine.system_supervisor_calls[0]["base_url"] == other

    def test_a_failed_start_is_reported_not_retried_on_a_system_install(
        self, machine, monkeypatch, capsys
    ):
        from gaia.daemon.errors import DaemonError

        def daemon_refuses(*args, **kwargs):
            raise DaemonError("lemond exited; read lemond.log")

        monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_refuses)

        ready = LemonadeManager.ensure_ready(
            min_context_size=65536, quiet=False, host="localhost", port=13305
        )

        assert ready is False
        assert "lemond exited; read lemond.log" in capsys.readouterr().err
        assert machine.system_launcher_lookups == 0
        assert machine.system_supervisor_calls == []


@pytest.mark.embedded_start
def test_agent_base_talks_to_the_port_gaias_server_bound(machine):
    from gaia.agents.base.agent import Agent

    class _StubAgent(Agent):
        def _register_tools(self):
            return None

    agent = _StubAgent(silent_mode=True)

    _assert_started_gaias_own(machine)
    assert agent.chat.config.base_url == EMBEDDED_URL


class TestClientAutoStart:
    def test_initialize_lemonade_starts_gaias_own_server(self, machine):
        status = lc.initialize_lemonade(agent="chat", quiet=True)

        assert status.running is True
        assert machine.daemon_starts == 1
        assert machine.system_supervisor_calls == []
        assert machine.spawned == []

    def test_create_lemonade_client_points_at_gaias_own_server(self, machine):
        client = lc.create_lemonade_client(auto_start=True, auto_load=False)

        _assert_started_gaias_own(machine)
        assert client.base_url == EMBEDDED_URL
        # GAIA's server only accepts the key it generated.
        assert client.api_key == EMBEDDED_KEY

    def test_a_failed_start_raises_without_trying_a_system_install(
        self, machine, monkeypatch
    ):
        from gaia.daemon.errors import DaemonError

        def daemon_refuses(*args, **kwargs):
            raise DaemonError("lemond exited; read lemond.log")

        monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", daemon_refuses)

        with pytest.raises(lc.LemonadeClientError, match="read lemond.log"):
            lc.LemonadeClient().launch_server()
        assert machine.system_launcher_lookups == 0
        assert machine.spawned == []


class FakeOwner:
    """The daemon's EmbeddedLemonadeOwner, at its ``ensure`` boundary."""

    def __init__(self, error=None):
        self.error = error
        self.calls = 0
        self.started_pid = None

    def ensure(self):
        from gaia.daemon.lemonade import EnsuredLemonade

        self.calls += 1
        if self.error is not None:
            raise self.error
        self.started_pid = 4242
        return EnsuredLemonade(
            base_url=EMBEDDED_URL, port=EMBEDDED_PORT, version="v", started=True
        )


class TestDaemonSupervisor:
    """``POST /daemon/v1/lemonade/start`` (the TUI, and every ``_autostart``)."""

    def test_starts_gaias_own_server_through_its_owner(self, machine, tmp_path):
        owner = FakeOwner()
        state = LemonadeSupervisor(log_dir=tmp_path, embedded=owner).ensure_running(
            ctx_size=65536
        )

        assert owner.calls == 1
        assert state.base_url == EMBEDDED_URL
        assert state.started is True and state.owned is True and state.pid == 4242
        assert machine.system_launcher_lookups == 0
        assert machine.spawned == []

    def test_an_owner_failure_is_raised_not_retried_on_a_system_install(
        self, machine, tmp_path
    ):
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        owner = FakeOwner(error=EmbeddedLemonadeError("port taken; read lemond.log"))
        supervisor = LemonadeSupervisor(log_dir=tmp_path, embedded=owner)

        with pytest.raises(LemonadeStartError, match="read lemond.log"):
            supervisor.ensure_running(ctx_size=65536)
        assert machine.system_launcher_lookups == 0
        assert machine.spawned == []
