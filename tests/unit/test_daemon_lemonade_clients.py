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
