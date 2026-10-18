# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Front ends tell the daemon they are using GAIA's Lemonade, and `gaia daemon
stop` stops GAIA's Lemonade whoever started it."""

import os
from unittest import mock

import pytest

from gaia.daemon import client
from gaia.daemon.errors import DaemonError, DaemonVersionError
from gaia.daemon.instance import DaemonInstance
from gaia.llm import lemonade_service


def _inst(api_version="1.4"):
    return DaemonInstance(
        pid=1, port=2, token="T", host="127.0.0.1", api_version=api_version
    )


@pytest.fixture(autouse=True)
def _no_hold(monkeypatch):
    monkeypatch.setattr(lemonade_service, "_LEASE", None)
    monkeypatch.setattr(lemonade_service, "_AUTO_LEASED", False)
    monkeypatch.delenv(lemonade_service.APP_PID_ENV, raising=False)
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)


class TestRegisterCall:
    def test_posts_kind_and_pids(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"client_id": "abc", "clients": 1}
        with (
            mock.patch.object(client, "attach", return_value=_inst()),
            mock.patch("requests.post", return_value=response) as post,
        ):
            assert client.register_lemonade_client("app", [10, 11], "tui") == "abc"
        assert post.call_args.args[0].endswith("/daemon/v1/lemonade/clients")
        assert post.call_args.kwargs["json"] == {
            "kind": "app",
            "pids": [10, 11],
            "name": "tui",
            "client_id": None,
        }

    def test_another_homes_daemon_is_reached_through_its_instance_file(
        self, tmp_path, monkeypatch
    ):
        import json

        (tmp_path / "instance.json").write_text(
            json.dumps({"pid": 1, "port": 4321, "token": "OTHER", "api_version": "1.4"})
        )
        monkeypatch.setattr(client, "_answers", lambda inst, timeout=1.5: True)
        response = mock.Mock(status_code=200)
        response.json.return_value = {"client_id": "abc"}
        with mock.patch("requests.post", return_value=response) as post:
            client.register_lemonade_client("app", [10], host_dir=tmp_path)
        assert ":4321/" in post.call_args.args[0]
        assert post.call_args.kwargs["headers"]["Authorization"].endswith("OTHER")

    def test_an_older_daemon_is_told_to_restart(self):
        with mock.patch.object(client, "attach", return_value=_inst("1.3")):
            with pytest.raises(DaemonVersionError, match="gaia daemon restart"):
                client.register_lemonade_client("app", [10])

    def test_no_daemon_is_an_error_naming_the_start(self):
        with mock.patch.object(client, "attach", return_value=None):
            with pytest.raises(DaemonError, match="gaia daemon start"):
                client.register_lemonade_client("app", [10])

    def test_release_of_an_already_dropped_client_is_quiet(self):
        with (
            mock.patch.object(client, "attach", return_value=_inst()),
            mock.patch("requests.delete", return_value=mock.Mock(status_code=404)),
        ):
            assert client.release_lemonade_client("abc") is None

    def test_release_with_the_daemon_gone_is_quiet(self):
        with mock.patch.object(client, "attach", return_value=None):
            assert client.release_lemonade_client("abc") is None


class TestHold:
    def test_an_app_registers_with_the_app_that_spawned_it(self, monkeypatch):
        monkeypatch.setenv(lemonade_service.APP_PID_ENV, "4242")
        registered = []
        exit_hooks = []
        monkeypatch.setattr(
            client,
            "register_lemonade_client",
            lambda kind, pids, name: registered.append((kind, pids, name)) or "id",
        )
        monkeypatch.setattr(lemonade_service.atexit, "register", exit_hooks.append)

        lemonade_service.hold_lemonade_open("app", "agent-ui")

        assert registered == [("app", [os.getpid(), 4242], "agent-ui")]
        assert exit_hooks == [lemonade_service.release_lemonade_hold]
        # Not inherited by what the backend spawns.
        assert lemonade_service.APP_PID_ENV not in os.environ

    def test_a_cli_command_registers_only_itself(self, monkeypatch):
        monkeypatch.setenv(lemonade_service.APP_PID_ENV, "4242")
        registered = []
        monkeypatch.setattr(
            client,
            "register_lemonade_client",
            lambda kind, pids, name: registered.append(pids) or "id",
        )
        monkeypatch.setattr(lemonade_service.atexit, "register", lambda fn: None)

        lemonade_service.hold_lemonade_open("cli")
        lemonade_service.hold_lemonade_open("cli")  # once per process

        assert registered == [[os.getpid()]]

    def test_exit_releases_the_hold(self, monkeypatch):
        lease = lemonade_service._Lease(
            kind="cli", name="cli", pids=[1], owner_dir=None, ids={None: "id"}
        )
        monkeypatch.setattr(lemonade_service, "_LEASE", lease)
        released = []
        monkeypatch.setattr(client, "release_lemonade_client", released.append)

        lemonade_service.release_lemonade_hold()
        lemonade_service.release_lemonade_hold()

        assert released == ["id"]

    def test_a_refused_registration_is_logged_not_raised(self, monkeypatch, caplog):
        monkeypatch.setattr(client, "start_or_attach", lambda: _inst())
        monkeypatch.setattr(
            client,
            "register_lemonade_client",
            mock.Mock(side_effect=DaemonError("daemon said no")),
        )
        lemonade_service.ensure_daemon_owns_lemonade(kind="app")
        assert "daemon said no" in caplog.text


class TestAnotherGaiaHome:
    """A process under its own GAIA_HOME that uses the default home's Lemonade
    must register with the daemon that owns that server, or that daemon stops
    the server under it."""

    @pytest.fixture
    def homes(self, monkeypatch, tmp_path):
        from pathlib import Path

        user = tmp_path / "user"
        (user / ".gaia" / "lemonade").mkdir(parents=True)
        (user / ".gaia" / "lemonade" / "state.json").write_text('{"port": 60293}')
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
        monkeypatch.setenv("GAIA_HOME", str(tmp_path / "eval-home"))
        monkeypatch.delenv("GAIA_DAEMON_HOME", raising=False)
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:60293/api/v1")
        return user / ".gaia" / "host"

    def test_the_owning_daemon_is_found_by_port(self, homes):
        assert lemonade_service.owning_daemon_dir() == homes

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:13305/api/v1",  # another server
            "http://10.0.0.5:60293/api/v1",  # another machine
        ],
    )
    def test_another_server_has_no_owner_here(self, homes, monkeypatch, url):
        monkeypatch.setenv("LEMONADE_BASE_URL", url)
        assert lemonade_service.owning_daemon_dir() is None

    def test_the_default_home_needs_no_second_registration(self, homes, monkeypatch):
        monkeypatch.delenv("GAIA_HOME")
        assert lemonade_service.owning_daemon_dir() is None

    def test_a_stopped_server_has_no_owner(self, homes):
        (homes.parent / "lemonade" / "state.json").unlink()
        assert lemonade_service.owning_daemon_dir() is None

    def test_both_daemons_hear_of_it_and_both_are_released(self, homes, monkeypatch):
        registered, released = [], []

        def _register(kind, pids, name, client_id=None, host_dir=None):
            registered.append(host_dir)
            return "owner-id" if host_dir else "own-id"

        monkeypatch.setattr(client, "register_lemonade_client", _register)
        monkeypatch.setattr(
            client,
            "release_lemonade_client",
            lambda cid, host_dir=None: released.append((cid, host_dir)),
        )
        monkeypatch.setattr(lemonade_service.atexit, "register", lambda fn: None)

        lemonade_service.hold_lemonade_open("app", "agent-ui")
        lemonade_service.release_lemonade_hold()

        assert registered == [homes, None]
        assert sorted(released, key=str) == sorted(
            [("owner-id", homes), ("own-id", None)], key=str
        )

    def test_the_owner_hears_of_it_even_without_a_daemon_of_our_own(
        self, homes, monkeypatch
    ):
        registered = []

        def _register(kind, pids, name, client_id=None, host_dir=None):
            if host_dir is None:
                raise DaemonError("the GAIA daemon is not running")
            registered.append(host_dir)
            return "owner-id"

        monkeypatch.setattr(
            client,
            "start_or_attach",
            mock.Mock(side_effect=DaemonError("no daemon here")),
        )
        monkeypatch.setattr(client, "register_lemonade_client", _register)
        monkeypatch.setattr(client, "release_lemonade_client", lambda *a, **k: None)
        monkeypatch.setattr(lemonade_service.atexit, "register", lambda fn: None)

        lemonade_service.ensure_daemon_owns_lemonade(kind="app")
        lemonade_service.release_lemonade_hold()

        assert registered == [homes]


class TestAutomaticLease:
    """Every client of GAIA's server leases it, however the process found it."""

    def test_the_daemon_never_leases_its_own_server(self, monkeypatch):
        monkeypatch.setattr(lemonade_service, "_SUPERVISOR", object())
        monkeypatch.setattr(
            lemonade_service,
            "hold_lemonade_open",
            mock.Mock(side_effect=AssertionError("the daemon leased itself")),
        )
        lemonade_service.lease_gaia_lemonade()
        assert lemonade_service._LEASE is None

    def test_a_client_leases_once_per_process_off_thread(self, monkeypatch, tmp_path):
        import threading

        held = []
        done = threading.Event()

        def _hold(kind, name="", owner_dir=None):
            held.append((kind, owner_dir))
            done.set()

        monkeypatch.setattr(lemonade_service, "hold_lemonade_open", _hold)
        lemonade_service.lease_gaia_lemonade(tmp_path)
        lemonade_service.lease_gaia_lemonade(tmp_path)

        assert done.wait(2)
        assert held == [("cli", tmp_path)]

    def test_an_app_takes_over_a_cli_lease(self, monkeypatch):
        """Else closing the Agent UI would not unload: its lease said cli."""
        monkeypatch.setenv(lemonade_service.APP_PID_ENV, "4242")
        monkeypatch.setattr(lemonade_service.atexit, "register", lambda fn: None)
        registered, released = [], []

        def _register(kind, pids, name, client_id=None):
            registered.append((kind, pids))
            return f"{kind}-id"

        monkeypatch.setattr(client, "register_lemonade_client", _register)
        monkeypatch.setattr(client, "release_lemonade_client", released.append)

        lemonade_service.hold_lemonade_open("cli", "auto")
        lemonade_service.hold_lemonade_open("app", "agent-ui")
        lemonade_service.hold_lemonade_open("cli", "auto")  # no step back

        assert registered == [("cli", [os.getpid()]), ("app", [os.getpid(), 4242])]
        assert released == ["cli-id"]
        lemonade_service.release_lemonade_hold()

    def test_a_lease_taken_before_the_daemon_is_up_registers_later(self, monkeypatch):
        import threading

        monkeypatch.setattr(lemonade_service, "HOLD_RENEW_SECONDS", 0.01)
        monkeypatch.setattr(lemonade_service.atexit, "register", lambda fn: None)
        daemon_up = threading.Event()
        registered = threading.Event()
        monkeypatch.setattr(
            client, "attach", lambda: _inst() if daemon_up.is_set() else None
        )

        def _register(kind, pids, name, client_id=None):
            if not daemon_up.is_set():
                raise DaemonError("the GAIA daemon is not running")
            registered.set()
            return "id"

        monkeypatch.setattr(client, "register_lemonade_client", _register)
        monkeypatch.setattr(client, "release_lemonade_client", lambda cid: None)

        with pytest.raises(DaemonError):
            lemonade_service.hold_lemonade_open("cli")
        daemon_up.set()
        assert registered.wait(2), "the lease never reached the daemon"
        lemonade_service.release_lemonade_hold()


class TestRenewal:
    """A daemon restarted while an app runs must learn of the app again, or its
    idle timeout stops Lemonade under an open window."""

    def test_the_hold_is_repeated_with_its_id_and_stops_on_release(self, monkeypatch):
        import threading

        monkeypatch.setattr(lemonade_service, "HOLD_RENEW_SECONDS", 0.01)
        monkeypatch.setattr(lemonade_service.atexit, "register", lambda fn: None)
        monkeypatch.setattr(client, "attach", lambda: _inst())
        renewed = threading.Event()
        calls = []

        def _register(kind, pids, name, client_id=None):
            calls.append(client_id)
            if client_id:
                renewed.set()
            return "id-1"

        monkeypatch.setattr(client, "register_lemonade_client", _register)
        monkeypatch.setattr(client, "release_lemonade_client", lambda cid: None)

        lemonade_service.hold_lemonade_open("app", "agent-ui")
        assert renewed.wait(2), "the registration was never renewed"
        lemonade_service.release_lemonade_hold()
        settled = len(calls)
        threading.Event().wait(0.1)

        assert calls[0] is None and "id-1" in calls[1:]
        assert len(calls) <= settled + 1, "renewal kept running after release"

    def test_renewal_never_starts_a_daemon(self, monkeypatch):
        import threading

        monkeypatch.setattr(lemonade_service, "HOLD_RENEW_SECONDS", 0.01)
        monkeypatch.setattr(lemonade_service.atexit, "register", lambda fn: None)
        monkeypatch.setattr(
            client, "start_or_attach", mock.Mock(side_effect=AssertionError)
        )
        polled = threading.Event()

        def _attach():
            polled.set()
            return None

        monkeypatch.setattr(client, "attach", _attach)
        monkeypatch.setattr(
            client, "register_lemonade_client", mock.Mock(return_value="id-1")
        )
        monkeypatch.setattr(client, "release_lemonade_client", lambda cid: None)

        lemonade_service.hold_lemonade_open("cli")
        assert polled.wait(2)
        lemonade_service.release_lemonade_hold()
        # Only the first registration: no daemon, nothing renewed.
        assert client.register_lemonade_client.call_count == 1


class TestDaemonStop:
    """`gaia daemon stop` must stop the server `gaia init` started on first
    launch, which no daemon ever owned."""

    def _stop(self, monkeypatch, embedded_stop):
        from gaia import cli

        embedded = mock.Mock()
        embedded.stop.side_effect = embedded_stop
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade", lambda: embedded
        )
        monkeypatch.setattr("gaia.daemon.instance.read_instance", lambda: None)
        cli._handle_daemon_stop()
        return embedded

    def test_with_no_daemon_gaias_server_is_still_stopped(self, monkeypatch, capsys):
        embedded = self._stop(monkeypatch, lambda: True)
        embedded.stop.assert_called_once_with()
        assert "Lemonade Server stopped" in capsys.readouterr().out

    def test_a_configured_server_is_never_stopped(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")
        embedded = self._stop(monkeypatch, lambda: True)
        embedded.stop.assert_not_called()

    def test_a_stop_that_fails_exits_nonzero_with_the_reason(self, monkeypatch, capsys):
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        def _fail():
            raise EmbeddedLemonadeError("pid 9 is not it; killed nothing")

        with pytest.raises(SystemExit) as exc:
            self._stop(monkeypatch, _fail)
        assert exc.value.code == 1
        assert "killed nothing" in capsys.readouterr().out


class TestDaemonRestart:
    def test_a_lemonade_that_will_not_stop_does_not_block_the_restart(
        self, monkeypatch, capsys
    ):
        """Every remedy saying `gaia daemon restart` must leave a daemon up."""
        from gaia import cli
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        embedded = mock.Mock()
        embedded.stop.side_effect = EmbeddedLemonadeError("needs an admin terminal")
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade", lambda: embedded
        )
        monkeypatch.setattr("gaia.daemon.instance.read_instance", lambda: None)
        started = []
        monkeypatch.setattr(cli, "_handle_daemon_start", lambda: started.append(1))

        cli._handle_daemon_restart()

        assert started == [1]
        assert "needs an admin terminal" in capsys.readouterr().out


class TestIdleTimeoutSetting:
    def test_set_takes_a_whole_number(self, tmp_path):
        from gaia.config import GaiaConfig

        cfg = GaiaConfig()
        cfg.set("lemonade_idle_timeout_minutes", "30")
        assert cfg.lemonade_idle_timeout_minutes == 30
        cfg.save(tmp_path / "c.json")
        assert GaiaConfig.load(tmp_path / "c.json").lemonade_idle_timeout_minutes == 30

    @pytest.mark.parametrize("bad", ["soon", "-1", "1.5", "true"])
    def test_set_refuses_anything_else(self, bad):
        from gaia.config import GaiaConfig, GaiaConfigError

        with pytest.raises(GaiaConfigError, match="whole number"):
            GaiaConfig().set("lemonade_idle_timeout_minutes", bad)

    @pytest.mark.parametrize("bad", ['"30"', "true", "-5", "2.5"])
    def test_a_bad_value_on_disk_fails_loudly(self, tmp_path, bad):
        from gaia.config import GaiaConfig, GaiaConfigError

        path = tmp_path / "c.json"
        path.write_text(f'{{"lemonade_idle_timeout_minutes": {bad}}}')
        with pytest.raises(GaiaConfigError, match="whole number"):
            GaiaConfig.load(path)
