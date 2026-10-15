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

from gaia.daemon.lemonade import EmbeddedLemonadeOwner
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
        self.owner = mock.Mock()
        self.owner.unload_models.return_value = True
        self.owner.stop.return_value = True

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

    def test_a_running_sidecar_agent_keeps_the_models(self, world):
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

    def test_a_running_sidecar_holds_off_the_timeout(self, world):
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
            owner, identity=world.identity, clock=lambda: world.now
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
