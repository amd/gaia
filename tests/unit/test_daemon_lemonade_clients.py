# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""When GAIA's front ends close, GAIA's Lemonade gives its memory back.

Rule 1: the last app to close unloads the models, unless another GAIA client
is still using them. Rule 2: with nobody connected for the idle timeout, the
server is stopped. Both only ever reach GAIA's own server.
"""

import time
from unittest import mock

import pytest

from gaia.daemon.lemonade import EmbeddedLemonadeOwner, LemonadeActivity
from gaia.daemon.lemonade_clients import ClientRejected, LemonadeClients
from gaia.llm.lemonade_embedded import EmbeddedLemonadeError, EmbeddedStatus


@pytest.fixture(autouse=True)
def _no_configured_server(monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)


class _World:
    """Fake processes (pid -> start time), a fake clock and a fake owner."""

    def __init__(self):
        self.procs = {}
        self.now = 1000.0
        self.busy = False
        self.timeout = 15 * 60.0
        # GAIA's server is up and serving nothing.
        self.activity = LemonadeActivity(busy=False, marker=("idle",))
        self.owner = mock.Mock()
        self.owner.unload_models.return_value = True
        self.owner.stop.return_value = True
        # Sidecar agents the daemon runs, and each process's parent.
        self.agents = set()
        self.parents = {}
        self.stop_agents = mock.Mock(side_effect=self._stop_agents)

    def _stop_agents(self):
        for pid in list(self.agents):
            self.agents.discard(pid)
            for child in [c for c, p in self.parents.items() if p == pid]:
                self.procs.pop(child, None)
            self.procs.pop(pid, None)

    def ancestors(self, pid):
        chain = []
        while pid in self.parents:
            pid = self.parents[pid]
            chain.append(pid)
        return chain

    def sidecar(self, pid, child=None):
        """A sidecar agent; *child* is a one-file binary's worker process."""
        self.agents.add(self.spawn(pid))
        if child is not None:
            self.parents[self.spawn(child)] = pid
        return child or pid

    def spawn(self, pid, started=1.0):
        self.procs[pid] = started
        return pid

    def exit(self, pid):
        del self.procs[pid]

    def identity(self, pid):
        return (pid, self.procs[pid]) if pid in self.procs else None

    def clients(self):
        return LemonadeClients(
            self.owner,
            busy=lambda: self.busy,
            agent_pids=lambda: set(self.agents),
            stop_agents=self.stop_agents,
            ancestors=self.ancestors,
            activity=lambda: self.activity,
            timeout=lambda: self.timeout,
            identity=self.identity,
            clock=lambda: self.now,
        )


@pytest.fixture
def world():
    return _World()


class TestAppQuitUnloads:
    def test_closing_the_only_app_unloads_the_models(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)], "agent-ui")["client_id"]

        result = clients.release(app)

        assert result == {"clients": 0, "unloaded": True}
        world.owner.unload_models.assert_called_once()
        world.owner.stop.assert_not_called()

    def test_two_apps_unload_only_when_the_second_closes(self, world):
        clients = world.clients()
        ui = clients.register("app", [world.spawn(10)], "agent-ui")["client_id"]
        tui = clients.register("app", [world.spawn(20)], "tui")["client_id"]

        assert clients.release(ui)["unloaded"] is False
        world.owner.unload_models.assert_not_called()

        assert clients.release(tui)["unloaded"] is True
        world.owner.unload_models.assert_called_once()

    def test_a_running_cli_command_keeps_the_models(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        clients.register("cli", [world.spawn(30)], "gaia chat")

        assert clients.release(app)["unloaded"] is False
        world.owner.unload_models.assert_not_called()

        # The command finishes; the app's unload was owed and now happens.
        world.exit(30)
        clients.sweep()
        world.owner.unload_models.assert_called_once()

    def test_a_cli_command_alone_never_unloads(self, world):
        """`gaia llm` twice in a row must not reload the model in between."""
        clients = world.clients()
        cli = clients.register("cli", [world.spawn(30)])["client_id"]
        assert clients.release(cli)["unloaded"] is False
        world.exit(30)
        clients.sweep()
        world.owner.unload_models.assert_not_called()

    def test_an_app_killed_without_releasing_still_unloads(self, world):
        """Electron on Windows terminates the backend: no exit hook runs."""
        clients = world.clients()
        clients.register("app", [world.spawn(10), world.spawn(11)], "agent-ui")

        world.exit(11)  # the Electron app is gone; the backend lingers
        clients.sweep()

        assert clients.snapshot()["clients"] == []
        world.owner.unload_models.assert_called_once()

    def test_a_recycled_pid_does_not_keep_a_dead_app_registered(self, world):
        clients = world.clients()
        clients.register("app", [world.spawn(10, started=1.0)])

        world.exit(10)
        world.spawn(10, started=99.0)  # another program, same pid
        clients.sweep()

        assert clients.snapshot()["clients"] == []
        world.owner.unload_models.assert_called_once()

    def test_an_open_agent_turn_keeps_the_models(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        world.busy = True

        assert clients.release(app)["unloaded"] is False
        world.busy = False
        clients.sweep()
        world.owner.unload_models.assert_called_once()

    def test_a_failed_unload_is_reported_not_swallowed(self, world):
        world.owner.unload_models.side_effect = EmbeddedLemonadeError("refused")
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]

        result = clients.release(app)
        assert result["unloaded"] is False
        assert result["unload_error"] == "refused"

    def test_releasing_twice_is_a_key_error(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        clients.release(app)
        with pytest.raises(KeyError):
            clients.release(app)

    @pytest.mark.parametrize(
        "kind,pids", [("tablet", [1]), ("app", []), ("app", [404])]
    )
    def test_a_bad_registration_is_rejected(self, world, kind, pids):
        world.spawn(1)
        with pytest.raises(ClientRejected):
            world.clients().register(kind, pids)


class TestSidecarAgentsDoNotPinTheModels:
    """#8504: an idle sidecar kept 7.5 GB loaded after the app quit, for good."""

    def test_quitting_the_app_unloads_and_stops_the_idle_sidecar(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10), world.spawn(11)])
        # The frozen agent's worker process leases GAIA's server for its life.
        clients.register("cli", [world.sidecar(500, child=501)], "gaia-agent")

        assert clients.release(app["client_id"]) == {"clients": 0, "unloaded": True}
        world.stop_agents.assert_called_once()
        world.owner.unload_models.assert_called_once()

    def test_the_app_crashing_does_the_same(self, world):
        """Electron ended from Task Manager: no release, only its pid exits."""
        clients = world.clients()
        clients.register("app", [world.spawn(10), world.spawn(11)])
        clients.register("cli", [world.sidecar(500, child=501)], "gaia-agent")

        world.exit(11)
        clients.sweep()

        world.stop_agents.assert_called_once()
        world.owner.unload_models.assert_called_once()

    def test_the_app_lease_lapsing_does_the_same(self, world):
        """A hung backend that stops renewing still lets the models go."""
        clients = world.clients()
        clients.register("app", [world.spawn(10)])
        clients.register("cli", [world.sidecar(500, child=501)], "gaia-agent")

        for _ in range(30):
            world.now += 5
            clients.sweep()

        world.stop_agents.assert_called_once()
        world.owner.unload_models.assert_called_once()

    def test_agents_are_stopped_before_the_unload(self, world):
        order = []
        world.stop_agents.side_effect = lambda: order.append("agents")
        world.owner.unload_models.side_effect = lambda **_: order.append("unload")
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        world.sidecar(500)

        clients.release(app)
        assert order == ["agents", "unload"]

    def test_an_agents_lease_alone_lets_the_idle_timeout_fire(self, world):
        clients = world.clients()
        clients.register("cli", [world.sidecar(500, child=501)], "gaia-agent")

        world.now += world.timeout + 1
        clients.sweep()
        world.owner.stop.assert_called_once()

    def test_an_agents_lease_does_not_count_as_connected(self, world):
        clients = world.clients()
        result = clients.register("cli", [world.sidecar(500)], "gaia-agent")

        assert result["clients"] == 0
        assert clients.snapshot()["clients"][0]["agent"] is True
        assert clients.snapshot()["idle_seconds"] == 0

    def test_another_app_still_open_keeps_models_and_agents(self, world):
        clients = world.clients()
        ui = clients.register("app", [world.spawn(10)], "agent-ui")["client_id"]
        clients.register("app", [world.spawn(20)], "tui")
        clients.register("cli", [world.sidecar(500)], "gaia-agent")

        assert clients.release(ui)["unloaded"] is False
        world.stop_agents.assert_not_called()
        world.owner.unload_models.assert_not_called()

    def test_a_running_cli_command_keeps_models_and_agents(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        clients.register("cli", [world.spawn(30)], "gaia api")
        world.sidecar(500)

        clients.release(app)
        world.stop_agents.assert_not_called()

        world.exit(30)
        clients.sweep()
        world.stop_agents.assert_called_once()
        world.owner.unload_models.assert_called_once()

    def test_an_open_turn_defers_both_until_it_ends(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        world.sidecar(500)
        world.busy = True

        clients.release(app)
        clients.sweep()
        world.stop_agents.assert_not_called()
        world.owner.unload_models.assert_not_called()

        world.busy = False
        clients.sweep()
        world.stop_agents.assert_called_once()
        world.owner.unload_models.assert_called_once()

    def test_a_turn_opening_mid_decision_keeps_the_agent(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        world.sidecar(500)
        # Quiet when the unload is decided, a turn open by the time agents stop.
        answers = iter([False])
        clients._busy = lambda: next(answers, True)

        clients.release(app)
        world.stop_agents.assert_not_called()

    def test_a_client_arriving_mid_decision_keeps_the_agent(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        world.sidecar(500)
        real_observe = clients._observe

        def _observe():
            clients.register("app", [world.spawn(20)], "late")
            return real_observe()

        clients._observe = _observe
        clients.release(app)
        world.stop_agents.assert_not_called()
        still_wanted = world.owner.unload_models.call_args.kwargs["still_wanted"]
        assert still_wanted() is False

    def test_a_process_the_agent_did_not_start_is_a_client(self, world):
        """Only the agent's own process tree is discounted."""
        clients = world.clients()
        world.sidecar(500)
        assert clients.register("cli", [world.spawn(30)], "gaia llm")["clients"] == 1

        world.now += world.timeout + 1
        clients.sweep()
        world.owner.stop.assert_not_called()

    def test_a_lease_naming_an_agent_and_another_process_is_a_client(self, world):
        clients = world.clients()
        agent = world.sidecar(500)
        assert clients.register("app", [agent, world.spawn(10)])["clients"] == 1

    def test_an_orphaned_worker_is_a_client(self, world):
        """No parent reported (it exited, or a recycled pid is younger)."""
        clients = world.clients()
        world.sidecar(500)
        assert clients.register("cli", [world.spawn(600)])["clients"] == 1

    def test_an_agent_registering_calls_off_no_pending_decision(self, world):
        clients = world.clients()
        generation = clients._generation
        clients.register("cli", [world.sidecar(500)], "gaia-agent")
        assert clients._generation == generation

    def test_a_cli_command_leaving_does_not_stop_agents(self, world):
        """Rule 1 is for apps; `gaia llm` back to back keeps its sidecar."""
        clients = world.clients()
        cli = clients.register("cli", [world.spawn(30)])["client_id"]
        world.sidecar(500)
        clients.release(cli)
        world.stop_agents.assert_not_called()

    def test_an_agent_registered_as_an_app_does_not_unload_on_exit(self, world):
        clients = world.clients()
        lease = clients.register("app", [world.sidecar(500)])["client_id"]
        clients.release(lease)
        world.stop_agents.assert_not_called()
        world.owner.unload_models.assert_not_called()

    def test_an_agent_that_will_not_stop_is_logged_and_models_still_unload(
        self, world, caplog
    ):
        from gaia.daemon.sidecars.errors import StopFailedError

        world.stop_agents.side_effect = StopFailedError("pid 500 survived")
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]

        assert clients.release(app)["unloaded"] is True
        assert "pid 500 survived" in caplog.text

    def test_no_process_tree_is_walked_when_no_agent_runs(self, world):
        world.ancestors = mock.Mock(side_effect=AssertionError("walked"))
        clients = world.clients()
        assert clients.register("cli", [world.spawn(30)])["clients"] == 1


class TestAncestorPids:
    """Real processes: a one-file sidecar's worker is the child of its pid."""

    def test_a_child_names_this_process_among_its_ancestors(self):
        import os
        import subprocess
        import sys

        from gaia.daemon.lemonade_clients import ancestor_pids

        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
        )
        try:
            assert os.getpid() in ancestor_pids(child.pid)
        finally:
            child.kill()
            child.wait(timeout=10)

    @staticmethod
    def _chain(failure):
        """A process whose parent is 2, whose parent read raises *failure*."""
        grandparent = mock.Mock(pid=2)
        grandparent.parent.side_effect = failure
        child = mock.Mock(pid=3)
        child.parent.return_value = grandparent
        return child

    def test_a_denied_walk_keeps_the_parents_found_and_says_so(self, caplog):
        import psutil

        from gaia.daemon.lemonade_clients import ancestor_pids

        child = self._chain(psutil.AccessDenied(2))
        with mock.patch("psutil.Process", return_value=child):
            assert ancestor_pids(3) == [2]
        assert "could not read the parents of pid 3" in caplog.text

    def test_a_process_gone_mid_walk_is_not_an_error(self, caplog):
        import psutil

        from gaia.daemon.lemonade_clients import ancestor_pids

        child = self._chain(psutil.NoSuchProcess(2))
        with mock.patch("psutil.Process", return_value=child):
            assert ancestor_pids(3) == [2]
        assert "could not read" not in caplog.text

    def test_a_process_already_gone_has_no_ancestors(self):
        import psutil

        from gaia.daemon.lemonade_clients import ancestor_pids

        with mock.patch("psutil.Process", side_effect=psutil.NoSuchProcess(9)):
            assert ancestor_pids(9) == []


class TestIdleTimeout:
    def test_fires_once_the_timeout_passes_with_nobody_connected(self, world):
        clients = world.clients()
        world.now += world.timeout - 1
        clients.sweep()
        world.owner.stop.assert_not_called()

        world.now += 2
        clients.sweep()
        world.owner.stop.assert_called_once()
        assert "15 minutes" in world.owner.stop.call_args.kwargs["reason"]

        world.now += 10
        clients.sweep()
        assert world.owner.stop.call_count == 1, "idle stop repeated every sweep"

    def test_the_clock_starts_when_the_last_client_leaves(self, world):
        clients = world.clients()
        app = clients.register("app", [world.spawn(10)])["client_id"]
        world.now += 10 * world.timeout
        clients.sweep()
        world.owner.stop.assert_not_called()

        clients.release(app)
        world.now += world.timeout - 1
        clients.sweep()
        world.owner.stop.assert_not_called()
        world.now += 2
        clients.sweep()
        world.owner.stop.assert_called_once()

    def test_an_open_agent_turn_holds_off_the_timeout(self, world):
        clients = world.clients()
        world.busy = True
        world.now += 10 * world.timeout
        clients.sweep()
        world.owner.stop.assert_not_called()

    def test_zero_turns_it_off(self, world):
        world.timeout = None
        clients = world.clients()
        world.now += 1e9
        clients.sweep()
        world.owner.stop.assert_not_called()

    def test_a_broken_config_stops_nothing_and_says_why(self, world, caplog):
        from gaia.config import GaiaConfigError

        clients = world.clients()
        clients._timeout = mock.Mock(side_effect=GaiaConfigError("bad json"))
        world.now += 1e9
        clients.sweep()
        world.owner.stop.assert_not_called()
        assert clients.snapshot()["idle_timeout_error"] == "bad json"
        assert "bad json" in caplog.text

    def test_the_default_is_fifteen_minutes(self, monkeypatch, tmp_path):
        from gaia.daemon.lemonade_clients import idle_timeout_seconds

        monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", tmp_path / "none.json")
        assert idle_timeout_seconds() == 15 * 60.0


def _gaia_server():
    """A running embedded server stand-in."""
    embedded = mock.Mock()
    embedded.status.return_value = EmbeddedStatus(
        installed=True, running=True, version="1", port=1, pid=7
    )
    embedded.stop.return_value = True
    embedded.unload_models.return_value = True
    embedded.recorded_health.return_value = (True, {"all_models_loaded": []})
    return embedded


class TestOnlyGaiasServer:
    """Both rules act through the owner, so the user's Lemonade is unreachable."""

    def test_idle_timeout_stops_gaias_server(self, world):
        embedded = _gaia_server()
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded, owner_pid=1)
        clients = LemonadeClients(
            owner,
            timeout=lambda: 60.0,
            identity=world.identity,
            clock=lambda: world.now,
        )
        world.now += 61
        clients.sweep()
        embedded.stop.assert_called_once_with()

    def test_nothing_happens_to_a_configured_server(self, world, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")
        factory = mock.Mock()
        owner = EmbeddedLemonadeOwner(factory=factory, owner_pid=1)
        clients = LemonadeClients(
            owner,
            timeout=lambda: 60.0,
            identity=world.identity,
            clock=lambda: world.now,
        )
        app = clients.register("app", [world.spawn(10)])["client_id"]
        clients.release(app)
        world.now += 61
        clients.sweep()
        factory.assert_not_called()


class TestNoStopUnderANewClient:
    """A client that arrives while the daemon is deciding keeps its server."""

    def _owner(self, embedded):
        return EmbeddedLemonadeOwner(factory=lambda: embedded, owner_pid=1)

    def test_idle_stop_is_called_off_for_a_client_that_just_arrived(self, world):
        embedded = _gaia_server()
        clients = LemonadeClients(
            self._owner(embedded),
            identity=world.identity,
            clock=lambda: world.now,
        )

        def _timeout():
            # Lands after the sweep decided the server is idle.
            clients.register("app", [world.spawn(10)], "late")
            return 60.0

        clients._timeout = _timeout
        world.now += 61
        clients.sweep()

        embedded.stop.assert_not_called()

    def test_unload_is_called_off_for_a_client_that_just_arrived(
        self, world, monkeypatch
    ):
        embedded = _gaia_server()
        owner = self._owner(embedded)
        clients = LemonadeClients(
            owner, identity=world.identity, clock=lambda: world.now
        )
        app = clients.register("app", [world.spawn(10)])["client_id"]

        def _configured():
            # Lands after release decided to unload.
            clients.register("app", [world.spawn(20)], "late")
            return None

        monkeypatch.setattr(owner, "configured_url", _configured)
        assert clients.release(app)["unloaded"] is False
        embedded.unload_models.assert_not_called()

    def test_repeating_a_registration_adds_nothing(self, world):
        clients = world.clients()
        first = clients.register("app", [world.spawn(10)])
        again = clients.register("app", [10], client_id=first["client_id"])

        assert again == {
            "client_id": first["client_id"],
            "clients": 1,
            "added": False,
        }

    def test_a_restarted_daemon_learns_the_client_from_its_renewal(self, world):
        """The old daemon's id is accepted, so the release still finds it."""
        clients = world.clients()
        result = clients.register("app", [world.spawn(10)], client_id="abcdefgh1234")
        assert result["added"] is True
        assert clients.release("abcdefgh1234")["unloaded"] is True

    def test_a_malformed_client_id_is_rejected(self, world):
        with pytest.raises(ClientRejected):
            world.clients().register("app", [world.spawn(10)], client_id="../x")


class TestRoutes:
    def _client(self, world, owner=None):
        from fastapi.testclient import TestClient

        from gaia.daemon.app import create_app

        owner = owner or world.owner
        clients = LemonadeClients(
            owner,
            activity=lambda: world.activity,
            identity=world.identity,
            clock=lambda: world.now,
        )
        app = create_app(
            token="tok",
            port=1,
            pid=1,
            started_at=time.time(),
            lemonade_owner=owner,
            lemonade_clients=clients,
        )
        return TestClient(app)

    def test_register_then_release_unloads(self, world):
        http = self._client(world)
        auth = {"Authorization": "Bearer tok"}
        r = http.post(
            "/daemon/v1/lemonade/clients",
            json={"kind": "app", "pids": [world.spawn(10)], "name": "tui"},
            headers=auth,
        )
        assert r.status_code == 200
        # A client wants the server: bring it back if idle stopped it.
        world.owner.ensure_in_background.assert_called_once_with()

        listed = http.get("/daemon/v1/lemonade/clients", headers=auth).json()
        assert [c["name"] for c in listed["clients"]] == ["tui"]

        r = http.delete(
            f"/daemon/v1/lemonade/clients/{r.json()['client_id']}", headers=auth
        )
        assert r.status_code == 200
        assert r.json()["unloaded"] is True

    def test_a_renewal_does_not_restart_anything(self, world):
        http = self._client(world)
        auth = {"Authorization": "Bearer tok"}
        body = {"kind": "app", "pids": [world.spawn(10)]}
        first = http.post("/daemon/v1/lemonade/clients", json=body, headers=auth)
        http.post(
            "/daemon/v1/lemonade/clients",
            json={**body, "client_id": first.json()["client_id"]},
            headers=auth,
        )
        world.owner.ensure_in_background.assert_called_once_with()

    def test_a_dead_pid_is_a_400_naming_it(self, world):
        r = self._client(world).post(
            "/daemon/v1/lemonade/clients",
            json={"kind": "app", "pids": [404]},
            headers={"Authorization": "Bearer tok"},
        )
        assert r.status_code == 400
        assert "404" in r.json()["detail"]

    def test_an_unknown_client_is_a_404(self, world):
        r = self._client(world).delete(
            "/daemon/v1/lemonade/clients/nope",
            headers={"Authorization": "Bearer tok"},
        )
        assert r.status_code == 404

    def test_requires_the_daemon_token(self, world):
        r = self._client(world).post(
            "/daemon/v1/lemonade/clients", json={"kind": "app", "pids": [1]}
        )
        assert r.status_code == 401


class _BusyLemonade:
    """GAIA's embedded server as its ``/health`` describes it.

    ``request()`` is one request served start to finish between two looks;
    ``generating`` is one still streaming.
    """

    def __init__(self):
        self.generating = False
        self.last_use = 1000
        self.answering = True
        self.stopped = 0
        self.unloaded = 0
        # Runs once the owner holds its lock, just before it would stop.
        self.on_status = None

    def request(self):
        self.last_use += 1

    def recorded_health(self):
        if not self.answering:
            return True, None
        return True, {
            "model_loaded": "Gemma-4-E4B-it-GGUF",
            "all_models_loaded": [
                {
                    "model_name": "Gemma-4-E4B-it-GGUF",
                    "last_use": self.last_use,
                    "is_busy": self.generating,
                    "is_streaming": self.generating,
                }
            ],
        }

    def status(self):
        if self.on_status:
            self.on_status()
        return EmbeddedStatus(installed=True, running=True, version="1", port=1, pid=7)

    def stop(self):
        self.stopped += 1
        return True

    def unload_models(self):
        self.unloaded += 1
        return True


@pytest.fixture
def lemonade():
    return _BusyLemonade()


def _watching(world, lemonade, timeout=15 * 60.0):
    owner = EmbeddedLemonadeOwner(factory=lambda: lemonade, owner_pid=1)
    return LemonadeClients(
        owner,
        timeout=lambda: timeout,
        identity=world.identity,
        clock=lambda: world.now,
    )


def _sweep_for(world, clients, seconds, each=None, step=5.0):
    """Sweep every *step* seconds for *seconds*, calling *each* between looks."""
    end = world.now + seconds
    while world.now < end:
        world.now += step
        if each:
            each()
        clients.sweep()


class TestNeverStopsAServerInUse:
    """No GAIA client is registered in any of these: the only evidence of use
    is the server's own traffic, as for a process under another GAIA_HOME."""

    def test_a_generation_in_progress_is_never_stopped(self, world, lemonade):
        clients = _watching(world, lemonade)
        lemonade.generating = True
        _sweep_for(world, clients, 3 * 15 * 60.0)
        assert lemonade.stopped == 0

    def test_an_unregistered_process_streaming_turns_keeps_it(self, world, lemonade):
        """A turn is many short requests; a look rarely lands inside one."""
        clients = _watching(world, lemonade)
        _sweep_for(world, clients, 3 * 15 * 60.0, each=lemonade.request)
        assert lemonade.stopped == 0

    def test_requests_spaced_under_the_timeout_keep_it(self, world, lemonade):
        clients = _watching(world, lemonade)
        for _ in range(4):
            _sweep_for(world, clients, 14 * 60.0)
            lemonade.request()
        assert lemonade.stopped == 0

    def test_the_timeout_counts_from_the_last_request(self, world, lemonade):
        clients = _watching(world, lemonade)
        _sweep_for(world, clients, 20 * 60.0, each=lemonade.request)

        _sweep_for(world, clients, 14 * 60.0)
        assert lemonade.stopped == 0
        _sweep_for(world, clients, 2 * 60.0)
        assert lemonade.stopped == 1

    def test_a_server_that_does_not_answer_is_left_alone(self, world, lemonade):
        """It may be loading a large model."""
        clients = _watching(world, lemonade)
        lemonade.answering = False
        _sweep_for(world, clients, 3 * 15 * 60.0)
        assert lemonade.stopped == 0


class TestTrulyIdleStillStops:
    def test_stopped_once_the_timeout_passes(self, world, lemonade):
        clients = _watching(world, lemonade)
        _sweep_for(world, clients, 15 * 60.0 - 5)
        assert lemonade.stopped == 0
        _sweep_for(world, clients, 10)
        assert lemonade.stopped == 1

    def test_a_timeout_of_zero_never_stops(
        self, world, lemonade, tmp_path, monkeypatch
    ):
        from gaia.daemon.lemonade_clients import idle_timeout_seconds

        config = tmp_path / "config.json"
        config.write_text('{"lemonade_idle_timeout_minutes": 0}')
        monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", config)
        assert idle_timeout_seconds() is None

        owner = EmbeddedLemonadeOwner(factory=lambda: lemonade, owner_pid=1)
        clients = LemonadeClients(
            owner, identity=world.identity, clock=lambda: world.now
        )
        _sweep_for(world, clients, 24 * 60 * 60.0, step=600.0)
        assert lemonade.stopped == 0


class TestARequestMidDecisionWins:
    def test_a_stream_that_starts_as_the_stop_is_decided(self, world, lemonade):
        clients = _watching(world, lemonade, timeout=60.0)
        clients.sweep()

        def _arrives():
            lemonade.generating = True

        lemonade.on_status = _arrives
        world.now += 61
        clients.sweep()

        assert lemonade.stopped == 0

    def test_a_request_served_while_the_stop_is_decided(self, world, lemonade):
        clients = _watching(world, lemonade, timeout=60.0)
        clients.sweep()
        lemonade.on_status = lemonade.request
        world.now += 61
        clients.sweep()

        assert lemonade.stopped == 0
        # The request restarted the clock: no second attempt before it runs out.
        lemonade.on_status = None
        world.now += 30
        clients.sweep()
        assert lemonade.stopped == 0

    def test_a_request_served_while_the_unload_is_decided(
        self, world, lemonade, monkeypatch
    ):
        clients = _watching(world, lemonade)
        app = clients.register("app", [world.spawn(10)])["client_id"]
        owner = clients._owner
        real = owner.configured_url

        def _configured():
            lemonade.request()  # lands after release decided to unload
            return real()

        monkeypatch.setattr(owner, "configured_url", _configured)
        assert clients.release(app)["unloaded"] is False
        assert lemonade.unloaded == 0

        # Owed, not forgotten: once the server has been quiet, it happens.
        monkeypatch.setattr(owner, "configured_url", real)
        _sweep_for(world, clients, 2 * 60.0)
        assert lemonade.unloaded == 1


class TestLastAppClosesUnderAnotherProcess:
    def test_no_unload_while_another_process_is_mid_turn(self, world, lemonade):
        clients = _watching(world, lemonade)
        app = clients.register("app", [world.spawn(10)])["client_id"]
        lemonade.generating = True

        assert clients.release(app)["unloaded"] is False
        _sweep_for(world, clients, 10 * 60.0)
        assert lemonade.unloaded == 0

        # That process's turn goes on: more requests, no unload.
        lemonade.generating = False
        _sweep_for(world, clients, 10 * 60.0, each=lemonade.request)
        assert lemonade.unloaded == 0

        # It finishes; after a quiet minute the closed app's unload happens.
        _sweep_for(world, clients, 2 * 60.0)
        assert lemonade.unloaded == 1
        assert lemonade.stopped == 0

    def test_an_idle_server_still_unloads_at_once(self, world, lemonade):
        clients = _watching(world, lemonade)
        app = clients.register("app", [world.spawn(10)])["client_id"]
        assert clients.release(app)["unloaded"] is True
        assert lemonade.unloaded == 1


class TestReadingActivity:
    def test_busy_and_streaming_both_count(self):
        from gaia.daemon.lemonade import activity_from_health

        for flag in ("is_busy", "is_streaming"):
            seen = activity_from_health(
                {"all_models_loaded": [{"model_name": "m", "last_use": 1, flag: True}]}
            )
            assert seen.busy and "m" in seen.detail

    def test_the_marker_moves_with_last_use_and_loads(self):
        from gaia.daemon.lemonade import activity_from_health

        def marker(models):
            return activity_from_health({"all_models_loaded": models}).marker

        one = [{"model_name": "m", "last_use": 1}]
        assert marker(one) == marker([{"model_name": "m", "last_use": 1}])
        assert marker(one) != marker([{"model_name": "m", "last_use": 2}])
        assert marker(one) != marker(one + [{"model_name": "e", "last_use": 1}])

    def test_no_running_server_is_none(self):
        embedded = mock.Mock()
        embedded.recorded_health.return_value = (False, None)
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded, owner_pid=1)
        assert owner.activity() is None

    def test_an_unanswering_server_counts_as_busy(self):
        embedded = mock.Mock()
        embedded.recorded_health.return_value = (True, None)
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded, owner_pid=1)
        assert owner.activity().busy is True

    def test_a_stopped_server_is_not_busy(self, tmp_path, monkeypatch):
        """Read as busy, it kept an outdated daemon from being replaced."""
        import psutil

        from gaia.llm.lemonade_embedded import EmbeddedLemonade

        embedded = EmbeddedLemonade(home=tmp_path)
        embedded._write_state({"pid": 1, "port": 4321, "api_key": "k"})
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda self, port, key: None)

        def gone(pid):
            raise psutil.NoSuchProcess(pid)

        monkeypatch.setattr(psutil, "Process", gone)
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded, owner_pid=1)
        assert owner.activity() is None

    def test_a_configured_server_is_never_read(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")
        factory = mock.Mock()
        owner = EmbeddedLemonadeOwner(factory=factory, owner_pid=1)
        assert owner.activity() is None
        factory.assert_not_called()


class TestLeases:
    """A lease lasts as long as its holder renews it, not as long as it sends
    requests: a process can sit quiet for an hour and still need its server."""

    def _renewing(self, world, clients, lease, seconds):
        return _sweep_for(
            world,
            clients,
            seconds,
            each=lambda: clients.register("cli", [10], client_id=lease),
            step=30.0,
        )

    def test_a_quiet_leased_process_is_not_stopped(self, world, lemonade):
        clients = _watching(world, lemonade)
        lease = clients.register("cli", [world.spawn(10)], "eval")["client_id"]
        self._renewing(world, clients, lease, 20 * 60.0)
        assert lemonade.stopped == 0
        assert [c["name"] for c in clients.snapshot()["clients"]] == ["eval"]

    def test_a_hung_process_whose_lease_lapses_lets_it_stop(self, world, lemonade):
        """Its pid lives on, but it stopped renewing."""
        from gaia.daemon.lemonade_clients import LEASE_TTL

        clients = _watching(world, lemonade)
        clients.register("cli", [world.spawn(10)], "hung")
        _sweep_for(world, clients, LEASE_TTL + 5)
        assert clients.snapshot()["clients"] == []

        _sweep_for(world, clients, 15 * 60.0 - 10)
        assert lemonade.stopped == 0
        _sweep_for(world, clients, 10)
        assert lemonade.stopped == 1

    def test_a_crashed_process_is_dropped_at_once(self, world, lemonade):
        clients = _watching(world, lemonade)
        clients.register("cli", [world.spawn(10)], "crashed")
        world.exit(10)
        clients.sweep()
        assert clients.snapshot()["clients"] == []

    def test_a_lapsed_lease_comes_back_on_its_next_renewal(self, world, lemonade):
        from gaia.daemon.lemonade_clients import LEASE_TTL

        clients = _watching(world, lemonade)
        lease = clients.register("app", [world.spawn(10)])["client_id"]
        _sweep_for(world, clients, LEASE_TTL + 5)
        assert clients.register("app", [10], client_id=lease)["added"] is True

    def test_a_sleeping_machine_lapses_no_lease(self, world, lemonade):
        """No client could renew while the daemon itself was suspended."""
        clients = _watching(world, lemonade)
        clients.register("app", [world.spawn(10)])
        world.now += 8 * 60 * 60.0
        clients.sweep()
        assert len(clients.snapshot()["clients"]) == 1
        assert lemonade.unloaded == 0
