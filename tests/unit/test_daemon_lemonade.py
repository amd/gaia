# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon owns GAIA's embedded Lemonade: starts it on demand, stops it on exit."""

import threading
import time
from unittest import mock

import pytest

from gaia.daemon.lemonade import EmbeddedLemonadeOwner, LemonadeNotManaged
from gaia.llm.lemonade_embedded import EmbeddedLemonadeError, EmbeddedStatus

VERSION = "11.8.1"


@pytest.fixture(autouse=True)
def _no_configured_server(monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)


def _status(**kwargs):
    fields = {"installed": True, "running": False, "version": VERSION}
    fields.update(kwargs)
    return EmbeddedStatus(**fields)


def _running(port=51234, version=VERSION):
    return _status(
        running=True,
        port=port,
        pid=7,
        version=version,
        base_url=f"http://localhost:{port}/api/v1",
    )


def _embedded(status, installed=True):
    embedded = mock.Mock()
    embedded.version = VERSION
    embedded.is_installed.return_value = installed
    embedded.status.return_value = status
    embedded.start.return_value = _running(port=60000)
    embedded.log_path = "/gaia/lemonade/lemond.log"
    return embedded


def _owner(embedded):
    return EmbeddedLemonadeOwner(factory=lambda: embedded)


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
        embedded.start.assert_called_once_with(install_if_missing=False)

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

    def test_stop_leaves_a_server_it_did_not_start(self):
        """Started by `gaia init`, the user, or another daemon: not ours to stop."""
        embedded = _embedded(_running())
        owner = _owner(embedded)
        owner.ensure()

        assert owner.stop() is False
        embedded.stop.assert_not_called()

    def test_stop_leaves_a_server_restarted_by_someone_else(self):
        embedded = _embedded(_status())
        owner = _owner(embedded)
        owner.ensure()
        embedded.status.return_value = EmbeddedStatus(
            installed=True,
            running=True,
            version=VERSION,
            port=1,
            pid=99,
            base_url="http://localhost:1/api/v1",
        )

        assert owner.stop() is False
        embedded.stop.assert_not_called()


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
        with mock.patch.object(
            LemonadeManager,
            "start_embedded_if_stopped",
            side_effect=DaemonError("port taken; read lemond.log"),
        ):
            assert LemonadeManager.ensure_ready(quiet=False) is False
        assert "port taken; read lemond.log" in capsys.readouterr().err
