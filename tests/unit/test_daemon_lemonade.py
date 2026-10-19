# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon owns GAIA's embedded Lemonade: starts it on demand, claims one
someone else started, reaps one a crashed daemon left, and stops it on exit."""

import threading
import time
from unittest import mock

import pytest

from gaia.daemon.lemonade import EmbeddedLemonadeOwner, LemonadeNotManaged
from gaia.llm.lemonade_embedded import EmbeddedLemonadeError, EmbeddedStatus

VERSION = "11.8.1"
#: This daemon's pid in every test.
OWNER = 1000
#: A daemon that ran before this one and has since exited.
CRASHED = 999


@pytest.fixture(autouse=True)
def _no_configured_server(monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)


def _status(**kwargs):
    fields = {"installed": True, "running": False, "version": VERSION}
    fields.update(kwargs)
    return EmbeddedStatus(**fields)


def _running(port=51234, version=VERSION, owner_pid=None):
    return _status(
        running=True,
        port=port,
        pid=7,
        version=version,
        base_url=f"http://localhost:{port}/api/v1",
        owner_pid=owner_pid,
    )


def _embedded(status, installed=True):
    embedded = mock.Mock()
    embedded.version = VERSION
    embedded.is_installed.return_value = installed
    embedded.status.return_value = status
    embedded.start.return_value = _running(port=60000)
    embedded.log_path = "/gaia/lemonade/lemond.log"
    return embedded


def _owner(embedded, alive=()):
    """An owner with pid OWNER; only the pids in *alive* (and OWNER) still run."""
    return EmbeddedLemonadeOwner(
        factory=lambda: embedded,
        owner_pid=OWNER,
        owner_alive=lambda pid, started: pid == OWNER or pid in alive,
    )


class TestEnsure:
    def test_running_server_is_returned_untouched(self):
        embedded = _embedded(_running())
        ensured = _owner(embedded).ensure()

        assert ensured.base_url == "http://localhost:51234/api/v1"
        assert ensured.started is False
        embedded.start.assert_not_called()
        embedded.stop.assert_not_called()

    def test_stopped_server_is_started_without_installing(self):
        embedded = _embedded(_status())
        ensured = _owner(embedded).ensure()

        assert ensured.started is True
        assert ensured.port == 60000
        assert ensured.pid == 7
        embedded.start.assert_called_once_with(
            install_if_missing=False, owner_pid=OWNER, owner_started=mock.ANY
        )

    def test_older_version_is_reported_not_replaced(self):
        """Replacing a running server is `gaia init`'s call, not the daemon's."""
        embedded = _embedded(_running(version="0.0.1"))
        with pytest.raises(EmbeddedLemonadeError, match="gaia init"):
            _owner(embedded).ensure()

        embedded.stop.assert_not_called()
        embedded.start.assert_not_called()

    def test_slow_server_is_reported_never_killed(self):
        """A server busy loading a big model can miss a health check."""
        embedded = _embedded(_status(port=1, unresponsive_pid=9))
        with pytest.raises(EmbeddedLemonadeError, match="pid 9"):
            _owner(embedded).ensure()

        embedded.stop.assert_not_called()
        embedded.start.assert_not_called()

    def test_not_installed_names_gaia_init(self):
        embedded = _embedded(_status(installed=False), installed=False)
        with pytest.raises(LemonadeNotManaged, match="gaia init"):
            _owner(embedded).ensure()
        embedded.start.assert_not_called()

    def test_gaias_own_env_file_is_still_managed(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:50601/api/v1")
        monkeypatch.setenv("GAIA_LEMONADE_EMBEDDED", "1")
        embedded = _embedded(_status())

        assert _owner(embedded).ensure().started is True

    def test_configured_server_is_left_alone(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box:13305")
        factory = mock.Mock()
        with pytest.raises(LemonadeNotManaged, match="gpu-box"):
            EmbeddedLemonadeOwner(factory=factory).ensure()
        factory.assert_not_called()

    def test_server_gaia_init_started_is_claimed_not_restarted(self):
        """First launch: `gaia init` started it, so it has no owner until now."""
        embedded = _embedded(_running(owner_pid=None))
        ensured = _owner(embedded).ensure()

        assert ensured.started is False
        embedded.claim.assert_called_once_with(OWNER, mock.ANY)
        embedded.stop.assert_not_called()
        embedded.start.assert_not_called()

    def test_own_server_is_not_claimed_again(self):
        embedded = _embedded(_running(owner_pid=OWNER))
        _owner(embedded).ensure()
        embedded.claim.assert_not_called()

    def test_healthy_server_a_crashed_daemon_left_is_adopted(self):
        embedded = _embedded(_running(owner_pid=CRASHED))
        ensured = _owner(embedded).ensure()

        assert ensured.started is False
        embedded.claim.assert_called_once_with(OWNER, mock.ANY)
        embedded.stop.assert_not_called()

    def test_stalled_server_a_crashed_daemon_left_is_reaped_and_replaced(self):
        embedded = _embedded(_status(port=1, unresponsive_pid=9, owner_pid=CRASHED))
        ensured = _owner(embedded).ensure()

        assert ensured.started is True
        embedded.stop.assert_called_once_with()
        embedded.start.assert_called_once_with(
            install_if_missing=False, owner_pid=OWNER, owner_started=mock.ANY
        )

    def test_old_version_a_crashed_daemon_left_is_reaped_and_replaced(self):
        embedded = _embedded(_running(version="0.0.1", owner_pid=CRASHED))
        assert _owner(embedded).ensure().started is True
        embedded.stop.assert_called_once_with()
        embedded.start.assert_called_once()

    def test_stalled_server_whose_owner_still_runs_is_never_killed(self):
        """An owner pid that is alive -- another daemon, or a recycled pid --
        means the server may be busy, so it is reported, not reaped."""
        embedded = _embedded(_status(port=1, unresponsive_pid=9, owner_pid=CRASHED))
        with pytest.raises(EmbeddedLemonadeError, match="pid 9"):
            _owner(embedded, alive={CRASHED}).ensure()
        embedded.stop.assert_not_called()
        embedded.start.assert_not_called()

    def test_start_failure_propagates(self):
        embedded = _embedded(_status())
        embedded.start.side_effect = EmbeddedLemonadeError("read lemond.log")
        with pytest.raises(EmbeddedLemonadeError, match="read lemond.log"):
            _owner(embedded).ensure()

    def test_concurrent_callers_start_the_server_once(self):
        """A client asking during the daemon's own startup waits for that start."""
        state = {"running": False}
        embedded = mock.Mock()
        embedded.version = VERSION
        embedded.is_installed.return_value = True
        embedded.status.side_effect = lambda: (
            _running() if state["running"] else _status()
        )

        def _slow_start(**_):
            time.sleep(0.05)
            state["running"] = True
            return _running()

        embedded.start.side_effect = _slow_start
        owner = _owner(embedded)
        threads = [threading.Thread(target=owner.ensure) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert embedded.start.call_count == 1


class TestBackgroundAndStop:
    def test_background_start_logs_rather_than_raising(self, caplog):
        embedded = _embedded(_status())
        embedded.start.side_effect = EmbeddedLemonadeError("port taken")
        _owner(embedded).ensure_in_background().join(timeout=5)

        assert "port taken" in caplog.text

    def test_stop_stops_a_server_this_daemon_started(self):
        embedded = _embedded(_status())
        embedded.stop.return_value = True
        owner = _owner(embedded)
        owner.ensure()
        embedded.status.return_value = _running(port=60000)

        assert owner.stop() is True
        embedded.stop.assert_called_once()

    def test_stop_does_not_wait_out_a_start_in_flight(self, monkeypatch):
        """Shutdown must fit `gaia daemon stop`'s wait, even mid-start."""
        import gaia.daemon.lemonade as owner_mod

        monkeypatch.setattr(owner_mod, "STOP_LOCK_TIMEOUT", 0.05)
        embedded = _embedded(_running())
        owner = _owner(embedded)
        owner._lock.acquire()  # a start holding the lock
        try:
            started = time.monotonic()
            assert owner.stop() is False
            assert time.monotonic() - started < 1.0
        finally:
            owner._lock.release()
        embedded.stop.assert_not_called()

    def test_stop_with_nothing_running_stops_nothing(self):
        embedded = _embedded(_status())
        assert _owner(embedded).stop() is False
        embedded.stop.assert_not_called()

    def test_stop_stops_the_server_gaia_init_started(self):
        """`gaia daemon stop` used to leave the first-launch server running."""
        embedded = _embedded(_running(owner_pid=None))
        embedded.stop.return_value = True

        assert _owner(embedded).stop() is True
        embedded.stop.assert_called_once_with()

    def test_stop_reaches_a_server_that_stopped_answering(self):
        embedded = _embedded(_status(port=1, unresponsive_pid=9))
        embedded.stop.return_value = True
        assert _owner(embedded).stop() is True

    def test_stop_never_touches_a_configured_server(self, monkeypatch):
        """External mode: the user's Lemonade is theirs, even at shutdown."""
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")
        factory = mock.Mock()
        owner = EmbeddedLemonadeOwner(factory=factory, owner_pid=OWNER)

        assert owner.stop() is False
        assert owner.unload_models() is False
        factory.assert_not_called()

    def test_unload_goes_to_gaias_own_server(self):
        embedded = _embedded(_running())
        embedded.unload_models.return_value = True
        assert _owner(embedded).unload_models() is True
        embedded.unload_models.assert_called_once_with()


class TestRoute:
    def _client(self, owner):
        from fastapi.testclient import TestClient

        from gaia.daemon.app import create_app

        app = create_app(
            token="tok", port=1, pid=1, started_at=time.time(), lemonade_owner=owner
        )
        return TestClient(app)

    def _post(self, owner, token="tok"):
        return self._client(owner).post(
            "/daemon/v1/lemonade/ensure", headers={"Authorization": f"Bearer {token}"}
        )

    def test_ensure_returns_where_the_server_listens(self):
        r = self._post(_owner(_embedded(_running())))
        assert r.status_code == 200
        assert r.json() == {
            "base_url": "http://localhost:51234/api/v1",
            "port": 51234,
            "version": VERSION,
            "started": False,
            "pid": 7,
        }

    def test_nothing_to_start_is_409_with_the_remedy(self):
        r = self._post(_owner(_embedded(_status(installed=False), installed=False)))
        assert r.status_code == 409
        assert "gaia init" in r.json()["detail"]

    def test_start_failure_is_503_with_the_reason(self):
        embedded = _embedded(_status())
        embedded.start.side_effect = EmbeddedLemonadeError("read lemond.log")
        r = self._post(_owner(embedded))
        assert r.status_code == 503
        assert "read lemond.log" in r.json()["detail"]

    def test_requires_the_daemon_token(self):
        r = self._post(_owner(_embedded(_running())), token="wrong")
        assert r.status_code == 401


class TestClientCall:
    def _inst(self, api_version):
        from gaia.daemon.instance import DaemonInstance

        return DaemonInstance(
            pid=1, port=2, token="T", host="127.0.0.1", api_version=api_version
        )

    def test_older_daemon_is_told_to_restart(self):
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonVersionError

        with mock.patch.object(
            client, "start_or_attach", return_value=self._inst("1.1")
        ):
            with pytest.raises(DaemonVersionError, match="gaia daemon restart"):
                client.ensure_lemonade()

    def test_refusal_carries_the_daemon_detail(self):
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonError

        response = mock.Mock(status_code=409)
        response.json.return_value = {"detail": "Run `gaia init` to install it."}
        with (
            mock.patch.object(
                client, "start_or_attach", return_value=self._inst("1.2")
            ),
            mock.patch("requests.post", return_value=response) as post,
        ):
            with pytest.raises(DaemonError, match="gaia init"):
                client.ensure_lemonade()
        assert post.call_args.args[0].endswith("/daemon/v1/lemonade/ensure")


@pytest.mark.embedded_start
class TestManagerHook:
    """LemonadeManager only reaches for the daemon when GAIA's own server is
    installed and stopped -- never in CI or on a machine that didn't set it up."""

    def _patch_embedded(self, installed, running):
        embedded = mock.Mock()
        embedded.is_installed.return_value = installed
        embedded.status.return_value = _running() if running else _status()
        return mock.patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade", return_value=embedded
        )

    def test_stopped_server_is_started_through_the_daemon(self):
        from gaia.llm.lemonade_manager import LemonadeManager

        with (
            self._patch_embedded(installed=True, running=False),
            mock.patch(
                "gaia.daemon.client.ensure_lemonade", return_value={"started": True}
            ) as ensure,
        ):
            assert LemonadeManager.start_embedded_if_stopped() is True
        ensure.assert_called_once()

    @pytest.mark.parametrize(
        "installed,running", [(False, False), (True, True)], ids=["absent", "running"]
    )
    def test_daemon_is_not_contacted_when_there_is_nothing_to_start(
        self, installed, running
    ):
        from gaia.llm.lemonade_manager import LemonadeManager

        with (
            self._patch_embedded(installed=installed, running=running),
            mock.patch("gaia.daemon.client.ensure_lemonade") as ensure,
        ):
            assert LemonadeManager.start_embedded_if_stopped() is False
        ensure.assert_not_called()

    def test_configured_server_never_touches_embedded(self, monkeypatch):
        from gaia.llm.lemonade_manager import LemonadeManager

        monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box:13305")
        with (
            mock.patch("gaia.llm.lemonade_embedded.EmbeddedLemonade") as embedded,
            mock.patch("gaia.daemon.client.ensure_lemonade") as ensure,
        ):
            assert LemonadeManager.start_embedded_if_stopped() is False
        embedded.assert_not_called()
        ensure.assert_not_called()

    def test_ensure_ready_fails_with_the_daemon_message(self, capsys):
        from gaia.daemon.errors import DaemonError
        from gaia.llm.lemonade_manager import LemonadeManager

        LemonadeManager.reset()
        with (
            mock.patch(
                "gaia.llm.lemonade_manager.gaia_runs_lemonade", return_value=True
            ),
            mock.patch.object(
                LemonadeManager,
                "start_embedded_if_stopped",
                side_effect=DaemonError("port taken; read lemond.log"),
            ),
        ):
            assert LemonadeManager.ensure_ready(quiet=False) is False
        assert "port taken; read lemond.log" in capsys.readouterr().err


class TestTimeoutBudgets:
    """The daemon's Lemonade work has to fit inside the waits its callers allow.

    Both regressions this class pins were budget mismatches, not logic bugs, and
    both were invisible in review because each side's number lived in a different
    file — one of them in a different language. Stating the relationship in a
    comment is what let them drift in the first place.
    """

    def test_daemon_stop_outlasts_the_lemonade_teardown_it_waits_on(self):
        """`gaia daemon stop` tree-kills when its wait runs out.

        Shutdown stops the Lemonade Server this daemon started, so if the wait is
        shorter than that teardown a routine stop prints "did not exit;
        terminating" and kills the daemon before its custody store closes —
        leaving the custody SQLite store open on every stop.
        """
        from gaia.daemon.client import STOP_WAIT_TIMEOUT
        from gaia.daemon.lemonade import STOP_LOCK_TIMEOUT
        from gaia.daemon.lemonade_clients import STOP_JOIN_TIMEOUT
        from gaia.llm.lemonade_embedded import _STOP_TIMEOUT

        lemonade_teardown = STOP_JOIN_TIMEOUT + STOP_LOCK_TIMEOUT + _STOP_TIMEOUT
        assert STOP_WAIT_TIMEOUT > lemonade_teardown, (
            f"stop waits {STOP_WAIT_TIMEOUT}s but Lemonade teardown alone can take "
            f"{lemonade_teardown}s, so the daemon is killed mid-shutdown"
        )
        # The rest of shutdown (request drain, sidecar teardown, custody close)
        # runs before Lemonade is even reached.
        assert STOP_WAIT_TIMEOUT - lemonade_teardown >= 10.0, (
            "no room left for the request drain and sidecar teardown that run "
            "before Lemonade is stopped"
        )

    def test_the_lemonade_stop_lock_is_bounded(self):
        """An unbounded lock wait is the failure the margin above cannot absorb:
        `EmbeddedLemonade.start()` holds it for up to 60s, which no reasonable
        stop wait covers. Shutdown must give up and leave that server running."""
        from gaia.daemon.lemonade import STOP_LOCK_TIMEOUT
        from gaia.llm.lemonade_embedded import _START_TIMEOUT

        assert 0 < STOP_LOCK_TIMEOUT < _START_TIMEOUT

    def test_the_tui_check_waits_longer_than_the_python_side_may_spend(self):
        """`gaia init --check` is what the TUI runs on every launch.

        After a reboot that call starts the daemon and has it start Lemonade. The
        Go side used to give up first, so the TUI rendered "could not be checked"
        for a start that had actually succeeded — the exact reboot case this path
        exists to fix.
        """
        import re
        from pathlib import Path

        from gaia.daemon.client import _LEMONADE_ENSURE_TIMEOUT, _START_TIMEOUT

        source = (
            Path(__file__).resolve().parents[2]
            / "tui"
            / "internal"
            / "gaiainit"
            / "gaiainit.go"
        ).read_text(encoding="utf-8")
        match = re.search(r"CheckTimeout\s*=\s*(\d+)\s*\*\s*time\.Second", source)
        assert match, "gaiainit.go no longer declares CheckTimeout in seconds"
        check_timeout = int(match.group(1))

        # start_or_attach, then the ensure call's connect + read budget.
        python_budget = _START_TIMEOUT + sum(_LEMONADE_ENSURE_TIMEOUT)
        assert check_timeout > python_budget, (
            f"gaiainit.CheckTimeout is {check_timeout}s but `gaia init --check` "
            f"may spend {python_budget}s, so the TUI kills a start that worked"
        )


class TestOwnerAlive:
    """Whether the daemon recorded as a server's owner still runs."""

    def _process(self, monkeypatch, behaviour):
        import psutil

        def _make(pid):
            if behaviour == "gone":
                raise psutil.NoSuchProcess(pid)
            if behaviour == "denied":
                return mock.Mock(
                    create_time=mock.Mock(side_effect=psutil.AccessDenied(pid))
                )
            return mock.Mock(create_time=lambda: behaviour)

        monkeypatch.setattr(psutil, "Process", _make)

    def test_a_recycled_owner_pid_is_not_the_owner(self, monkeypatch):
        """Otherwise a stalled orphan is never reaped after pid reuse."""
        from gaia.daemon.lemonade import _owner_alive

        self._process(monkeypatch, 500.0)
        assert _owner_alive(CRASHED, 100.0) is False
        assert _owner_alive(CRASHED, 500.0) is True

    def test_a_gone_owner_is_dead_and_a_hidden_one_is_assumed_alive(self, monkeypatch):
        from gaia.daemon.lemonade import _owner_alive

        self._process(monkeypatch, "gone")
        assert _owner_alive(CRASHED, 100.0) is False
        self._process(monkeypatch, "denied")
        assert _owner_alive(CRASHED, 100.0) is True
