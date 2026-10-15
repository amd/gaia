# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
""" "Use my Lemonade": the saved choice, its checks, and that GAIA starts nothing.

Every HTTP call is mocked at ``requests.get``, and the tests assert the request
GAIA sent (URL, auth header, no redirects), not only what it did with the
answer. The unit conftest points ``config.json`` at a per-test tmp file.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from gaia.config import GaiaConfig
from gaia.llm import lemonade_choice as choice

USER_URL = "http://localhost:13305/api/v1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(name, raising=False)
    # No embedded state or install from the developer's real ~/.gaia.
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))


@pytest.fixture
def keyring(monkeypatch):
    """An in-memory OS credential store."""
    store = {}
    from gaia.connectors import store as store_mod

    monkeypatch.setattr(store_mod, "save_secret", store.__setitem__)
    monkeypatch.setattr(store_mod, "peek_secret", store.get)
    monkeypatch.setattr(store_mod, "delete_secret", lambda name: store.pop(name, None))
    return store


class FakeResponse:
    def __init__(
        self, status=200, body=None, text=None, content_type="application/json"
    ):
        self.status_code = status
        self._body = body
        self._text = text
        self.headers = {"content-type": content_type}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _lemonade(version="11.8.1"):
    return FakeResponse(body={"status": "ok", "version": version, "model_loaded": None})


@pytest.fixture
def http(monkeypatch):
    """Map a URL to a response (or exception); record every request sent."""
    routes = {}
    calls = []

    def fake_get(url, headers=None, timeout=None, allow_redirects=True):
        calls.append(
            SimpleNamespace(
                url=url, headers=dict(headers or {}), allow_redirects=allow_redirects
            )
        )
        answer = routes.get(url)
        if answer is None:
            raise requests.exceptions.ConnectionError("connection refused")
        if isinstance(answer, Exception):
            raise answer
        return answer(headers or {}) if callable(answer) else answer

    monkeypatch.setattr(requests, "get", fake_get)
    return SimpleNamespace(routes=routes, calls=calls)


def _save(**fields):
    cfg = GaiaConfig.load()
    for key, value in fields.items():
        setattr(cfg, key, value)
    cfg.save()


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


class TestNormalizeUrl:
    def test_bare_origin_gains_api_path(self):
        assert choice.normalize_external_url("http://localhost:13305/") == USER_URL

    def test_explicit_path_is_kept(self):
        assert (
            choice.normalize_external_url("https://box.lan/proxy/v1")
            == "https://box.lan/proxy/v1"
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "localhost:13305",
            "ftp://localhost:13305",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://user:pw@localhost:13305",
            "http://localhost:13305/?x=1",
            "http://localhost:13305/#frag",
            "http://localhost:99999",
            "http://",
        ],
    )
    def test_malformed_urls_are_refused(self, bad):
        with pytest.raises(choice.LemonadeChoiceError):
            choice.normalize_external_url(bad)

    # Looked up at call time: test_cli_config reloads gaia.config.
    def test_config_set_rejects_a_bad_url_and_mode(self):
        from gaia import config as config_mod

        cfg = config_mod.GaiaConfig()
        with pytest.raises(config_mod.GaiaConfigError, match="lemonade_base_url"):
            cfg.set("lemonade_base_url", "ftp://x")
        with pytest.raises(config_mod.GaiaConfigError, match="gaia, external"):
            cfg.set("lemonade_mode", "system")

    def test_a_hand_edited_bad_mode_fails_loudly_on_load(self, tmp_path):
        from gaia import config as config_mod

        config_mod.GAIA_CONFIG_FILE.write_text(
            json.dumps({"lemonade_mode": "mine"}), encoding="utf-8"
        )
        with pytest.raises(config_mod.GaiaConfigError, match="lemonade_mode"):
            config_mod.GaiaConfig.load()


# ---------------------------------------------------------------------------
# Which server: env var vs the saved choice
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_nothing_chosen_means_gaias_own(self):
        from gaia.llm.lemonade_client import configured_lemonade_url

        assert configured_lemonade_url() is None
        assert choice.describe()["mode"] == "gaia"

    def test_saved_external_choice_is_the_configured_server(self):
        from gaia.llm.lemonade_client import (
            configured_lemonade_url,
            resolve_lemonade_base_url,
        )

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        assert configured_lemonade_url() == USER_URL
        assert resolve_lemonade_base_url() == USER_URL
        assert choice.external_mode_active() is True
        assert choice.describe()["source"] == "config"

    def test_env_var_wins_over_the_saved_choice(self, monkeypatch):
        from gaia.llm.lemonade_client import configured_lemonade_url

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box:13305")
        assert configured_lemonade_url() == "http://gpu-box:13305"
        assert choice.configured_source() == "env"
        assert choice.external_mode_active() is False
        assert "LEMONADE_BASE_URL" in choice.switch_back_hint()

    def test_gaias_own_exported_env_does_not_hide_the_saved_choice(self, monkeypatch):
        from gaia.llm.lemonade_client import configured_lemonade_url

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:50601/api/v1")
        monkeypatch.setenv("GAIA_LEMONADE_EMBEDDED", "1")
        assert configured_lemonade_url() == USER_URL

    def test_gaia_mode_keeps_the_url_but_does_not_use_it(self):
        from gaia.llm.lemonade_client import configured_lemonade_url

        _save(lemonade_mode="gaia", lemonade_base_url=USER_URL)
        assert configured_lemonade_url() is None

    def test_external_without_url_fails_loudly(self):
        from gaia.llm.lemonade_client import configured_lemonade_url

        _save(lemonade_mode="external")
        with pytest.raises(choice.LemonadeChoiceError, match="lemonade connect"):
            configured_lemonade_url()

    def test_gaia_runs_lemonade_is_false_in_external_mode(self):
        from gaia.llm.lemonade_launcher import gaia_runs_lemonade

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        with mock.patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            return_value=True,
        ):
            assert gaia_runs_lemonade() is False

    def test_switching_back_to_gaia_works(self):
        from gaia.llm.lemonade_client import configured_lemonade_url
        from gaia.llm.lemonade_launcher import gaia_runs_lemonade

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        with mock.patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            return_value=True,
        ):
            choice.set_mode("gaia")
            assert GaiaConfig.load().lemonade_mode == "gaia"
            assert configured_lemonade_url() is None
            assert gaia_runs_lemonade() is True


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


class TestApiKey:
    def test_saved_key_goes_only_to_the_saved_server(self, keyring):
        from gaia.llm.lemonade_client import resolve_lemonade_api_key

        keyring[choice.API_KEY_SECRET_NAME] = "s3cret"
        _save(
            lemonade_mode="external",
            lemonade_base_url=USER_URL,
            lemonade_api_key_stored=True,
        )
        assert resolve_lemonade_api_key(base_url=USER_URL) == "s3cret"
        assert resolve_lemonade_api_key(base_url="http://localhost:13305") == "s3cret"
        assert resolve_lemonade_api_key(base_url="http://evil.example:13305") is None

    def test_env_key_wins(self, keyring, monkeypatch):
        from gaia.llm.lemonade_client import resolve_lemonade_api_key

        keyring[choice.API_KEY_SECRET_NAME] = "s3cret"
        _save(
            lemonade_mode="external",
            lemonade_base_url=USER_URL,
            lemonade_api_key_stored=True,
        )
        monkeypatch.setenv("LEMONADE_API_KEY", "from-env")
        assert resolve_lemonade_api_key(base_url=USER_URL) == "from-env"

    def test_a_stored_key_the_store_lost_is_an_error(self, keyring):
        _save(
            lemonade_mode="external",
            lemonade_base_url=USER_URL,
            lemonade_api_key_stored=True,
        )
        with pytest.raises(choice.LemonadeChoiceError, match="credential store"):
            choice.external_api_key(USER_URL)

    def test_key_is_never_written_to_config(self, keyring, http):
        http.routes[f"{USER_URL}/health"] = lambda h: (
            _lemonade()
            if h.get("Authorization") == "Bearer s3cret"
            else FakeResponse(401)
        )
        choice.use_external("http://localhost:13305", "s3cret")

        from gaia import config as config_mod

        raw = config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8")
        assert "s3cret" not in raw
        assert json.loads(raw)["lemonade_api_key_stored"] is True
        assert keyring[choice.API_KEY_SECRET_NAME] == "s3cret"


# ---------------------------------------------------------------------------
# Probing: what answers, and is it Lemonade
# ---------------------------------------------------------------------------


class TestProbe:
    def test_request_shape(self, http):
        http.routes[f"{USER_URL}/health"] = _lemonade()
        result = choice.probe("http://localhost:13305", "k")
        assert result.status == choice.OK
        (call,) = http.calls
        assert call.url == f"{USER_URL}/health"
        assert call.headers == {"Authorization": "Bearer k"}
        assert call.allow_redirects is False

    def test_no_key_sends_no_auth_header(self, http):
        http.routes[f"{USER_URL}/health"] = _lemonade()
        choice.probe(USER_URL)
        assert "Authorization" not in http.calls[0].headers

    def test_html_server_is_not_lemonade(self, http):
        http.routes[f"{USER_URL}/health"] = FakeResponse(
            body=None, text="<html>", content_type="text/html"
        )
        result = choice.probe(USER_URL)
        assert result.status == choice.NOT_LEMONADE
        assert "not a Lemonade" in result.message()

    @pytest.mark.parametrize(
        "body",
        [
            {"status": "ok"},
            {"status": "healthy", "version": "11.0.0"},
            {"version": "11.0.0"},
            ["ok"],
            {"status": "ok", "version": "banana"},
        ],
    )
    def test_other_json_is_not_lemonade(self, http, body):
        http.routes[f"{USER_URL}/health"] = FakeResponse(body=body)
        assert choice.probe(USER_URL).status == choice.NOT_LEMONADE

    def test_redirect_is_not_followed_or_trusted(self, http):
        http.routes[f"{USER_URL}/health"] = FakeResponse(302)
        assert choice.probe(USER_URL).status == choice.NOT_LEMONADE

    def test_needs_key_and_bad_key(self, http):
        http.routes[f"{USER_URL}/health"] = FakeResponse(401)
        assert choice.probe(USER_URL).status == choice.NEEDS_KEY
        assert choice.probe(USER_URL, "wrong").status == choice.BAD_KEY

    def test_too_old_names_both_versions_and_the_upgrade(self, http):
        http.routes[f"{USER_URL}/health"] = _lemonade("10.2.0")
        result = choice.probe(USER_URL)
        assert result.status == choice.TOO_OLD
        msg = result.message()
        assert "v10.2.0" in msg and f"v{choice.minimum_version()}" in msg
        assert "Upgrade" in msg and "lemonade_mode gaia" in msg

    def test_calver_is_new_enough(self, http):
        http.routes[f"{USER_URL}/health"] = _lemonade("2026.40.0")
        assert choice.probe(USER_URL).status == choice.OK

    def test_down_server_is_unreachable(self, http):
        result = choice.probe(USER_URL)
        assert result.status == choice.UNREACHABLE
        assert "Start it" in result.message()


class TestDetect:
    def test_finds_lemonade_on_the_default_port(self, http):
        http.routes[f"{USER_URL}/health"] = _lemonade()
        found = choice.detect_running_lemonade()
        assert found.status == choice.OK and found.url == USER_URL

    def test_never_triggers_on_a_port_owned_by_something_else(self, http):
        http.routes[f"{USER_URL}/health"] = FakeResponse(
            body=None, text="<html>", content_type="text/html"
        )
        http.routes["http://localhost:8000/api/v1/health"] = FakeResponse(
            body={"status": "ok"}
        )
        assert choice.detect_running_lemonade() is None

    def test_a_401_counts_only_where_lemonade_is_installed(self, http):
        from gaia.llm.lemonade_launcher import LemonadeTooling

        http.routes[f"{USER_URL}/health"] = FakeResponse(401)
        with mock.patch(
            "gaia.llm.lemonade_launcher.resolve_lemonade",
            return_value=LemonadeTooling(found=False, kind="none"),
        ):
            assert choice.detect_running_lemonade() is None
        with mock.patch(
            "gaia.llm.lemonade_launcher.resolve_lemonade",
            return_value=LemonadeTooling(found=True, kind="modern"),
        ):
            assert choice.detect_running_lemonade().status == choice.NEEDS_KEY

    def test_gaias_own_server_is_never_offered(self, http):
        http.routes[f"{USER_URL}/health"] = _lemonade()
        with mock.patch(
            "gaia.llm.lemonade_client.running_embedded_lemonade_url",
            return_value=USER_URL,
        ):
            assert choice.detect_running_lemonade() is None

    def test_too_old_is_reported_not_offered(self, http):
        http.routes[f"{USER_URL}/health"] = _lemonade("10.2.0")
        assert choice.detect_running_lemonade().status == choice.TOO_OLD


# ---------------------------------------------------------------------------
# Saving the choice
# ---------------------------------------------------------------------------


class TestUseExternal:
    def test_saves_a_working_server(self, http, keyring):
        http.routes[f"{USER_URL}/health"] = _lemonade()
        choice.use_external("http://localhost:13305")
        cfg = GaiaConfig.load()
        assert (cfg.lemonade_mode, cfg.lemonade_base_url) == ("external", USER_URL)
        assert cfg.lemonade_api_key_stored is False

    @pytest.mark.parametrize(
        "response,status",
        [
            (FakeResponse(401), choice.NEEDS_KEY),
            (_lemonade("10.2.0"), choice.TOO_OLD),
            (FakeResponse(body=None, content_type="text/html"), choice.NOT_LEMONADE),
            (None, choice.UNREACHABLE),
        ],
    )
    def test_refuses_and_saves_nothing(self, http, keyring, response, status):
        if response is not None:
            http.routes[f"{USER_URL}/health"] = response
        with pytest.raises(choice.LemonadeChoiceError) as err:
            choice.use_external(USER_URL)
        assert err.value.status == status
        assert GaiaConfig.load().lemonade_mode is None

    def test_reconnecting_reuses_the_saved_key(self, http, keyring):
        http.routes[f"{USER_URL}/health"] = lambda h: (
            _lemonade()
            if h.get("Authorization") == "Bearer s3cret"
            else FakeResponse(401)
        )
        choice.use_external(USER_URL, "s3cret")
        with mock.patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            return_value=True,
        ):
            choice.set_mode("gaia")
        choice.use_external(USER_URL)
        assert GaiaConfig.load().lemonade_mode == "external"

    def test_set_mode_external_checks_the_server_first(self, http, keyring):
        _save(lemonade_mode="gaia", lemonade_base_url=USER_URL)
        with pytest.raises(choice.LemonadeChoiceError) as err:
            choice.set_mode("external")
        assert err.value.status == choice.UNREACHABLE
        assert GaiaConfig.load().lemonade_mode == "gaia"

    def test_set_mode_external_needs_a_saved_server(self):
        with pytest.raises(choice.LemonadeChoiceError, match="lemonade connect"):
            choice.set_mode("external")

    def test_a_new_url_drops_the_old_servers_key(self, keyring):
        keyring[choice.API_KEY_SECRET_NAME] = "old"
        _save(
            lemonade_mode="gaia",
            lemonade_base_url=USER_URL,
            lemonade_api_key_stored=True,
        )
        choice.set_url("http://other:13305")
        cfg = GaiaConfig.load()
        assert cfg.lemonade_base_url == "http://other:13305/api/v1"
        assert cfg.lemonade_api_key_stored is False
        assert choice.API_KEY_SECRET_NAME not in keyring


# ---------------------------------------------------------------------------
# External mode never starts a Lemonade
# ---------------------------------------------------------------------------


class TestNeverSpawns:
    @pytest.fixture(autouse=True)
    def _external(self):
        _save(lemonade_mode="external", lemonade_base_url=USER_URL)

    def test_supervisor_reports_the_down_server_and_starts_nothing(
        self, tmp_path, monkeypatch
    ):
        from gaia.llm import lemonade_supervisor as sup

        client = SimpleNamespace(
            base_url=USER_URL,
            host="localhost",
            port=13305,
            health_check=mock.Mock(side_effect=ConnectionError("refused")),
        )
        embedded = mock.Mock()
        s = sup.LemonadeSupervisor(log_dir=tmp_path, embedded=embedded)
        monkeypatch.setattr(s, "_client", lambda: client)
        popen = mock.Mock()
        resolve = mock.Mock()
        monkeypatch.setattr(sup.subprocess, "Popen", popen)
        monkeypatch.setattr(sup, "resolve_lemonade", resolve)

        with pytest.raises(sup.LemonadeStartError) as err:
            s.ensure_running(ctx_size=65536)

        msg = str(err.value)
        assert USER_URL in msg and "lemonade_mode gaia" in msg
        popen.assert_not_called()
        resolve.assert_not_called()
        embedded.ensure.assert_not_called()

    def test_daemon_owner_declines_to_start_gaias_own(self):
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner, LemonadeNotManaged

        factory = mock.Mock()
        with pytest.raises(LemonadeNotManaged, match="your Lemonade Server"):
            EmbeddedLemonadeOwner(factory=factory).ensure()
        factory.assert_not_called()

    def test_client_launch_refuses(self, monkeypatch):
        from gaia.llm import lemonade_client as lc

        popen = mock.Mock()
        monkeypatch.setattr(lc.subprocess, "Popen", popen)
        client = lc.LemonadeClient(verbose=False)
        assert client.base_url == USER_URL
        with pytest.raises(lc.LemonadeClientError, match="not answering"):
            client.launch_server(background="silent")
        popen.assert_not_called()

    def test_start_embedded_if_stopped_is_a_no_op(self):
        from gaia.llm.lemonade_manager import LemonadeManager

        with mock.patch("gaia.daemon.client.ensure_lemonade") as ensure:
            assert LemonadeManager.start_embedded_if_stopped() is False
        ensure.assert_not_called()


# ---------------------------------------------------------------------------
# The daemon follows a changed setting
# ---------------------------------------------------------------------------


class TestDaemonReconfigure:
    def test_switch_to_external_stops_gaias_own(self):
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        embedded = mock.Mock()
        embedded.stop.return_value = True
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded)
        result = owner.reconfigure()
        embedded.stop.assert_called_once()
        embedded.start.assert_not_called()
        assert result["action"] == "stopped_gaia_lemonade"
        assert result["base_url"] == USER_URL

    def test_switch_back_starts_gaias_own(self):
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner

        _save(lemonade_mode="gaia")
        embedded = mock.Mock()
        embedded.is_installed.return_value = True
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded)
        with mock.patch.object(owner, "ensure_in_background") as bg:
            result = owner.reconfigure()
        bg.assert_called_once()
        embedded.stop.assert_not_called()
        assert result["action"] == "starting_gaia_lemonade"

    def _inst(self, api_version="1.5"):
        from gaia.daemon.instance import DaemonInstance

        return DaemonInstance(
            pid=1,
            port=5,
            token="t",
            host="127.0.0.1",
            started_at=0.0,
            api_version=api_version,
        )

    def test_no_running_daemon_is_left_alone(self):
        from gaia.daemon import client

        with mock.patch.object(client, "attach", return_value=None):
            assert client.reconfigure_lemonade() is None

    def test_running_daemon_is_told_once(self):
        from gaia.daemon import client

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "mode": "external",
            "base_url": USER_URL,
            "action": "none",
        }
        with (
            mock.patch.object(client, "attach", return_value=self._inst()),
            mock.patch("requests.post", return_value=response) as post,
            mock.patch.object(client, "request_shutdown") as shutdown,
        ):
            result = client.reconfigure_lemonade()
        url = post.call_args.args[0]
        assert url.endswith("/daemon/v1/lemonade/reconfigure")
        assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer t"}
        assert result["restarted"] is False
        shutdown.assert_not_called()

    def test_daemon_on_another_server_is_restarted(self):
        """It was spawned with a LEMONADE_BASE_URL this shell no longer has."""
        from gaia.daemon import client

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        stale = mock.Mock(status_code=200)
        stale.json.return_value = {
            "mode": "external",
            "base_url": "http://old:1/api/v1",
        }
        fresh = mock.Mock(status_code=200)
        fresh.json.return_value = {"mode": "external", "base_url": USER_URL}
        with (
            mock.patch.object(client, "attach", return_value=self._inst()),
            mock.patch("requests.post", side_effect=[stale, fresh]),
            mock.patch.object(
                client, "request_shutdown", return_value=True
            ) as shutdown,
            mock.patch.object(client, "wait_until_gone", return_value=True),
            mock.patch.object(
                client, "start_or_attach", return_value=self._inst()
            ) as start,
        ):
            result = client.reconfigure_lemonade()
        shutdown.assert_called_once()
        start.assert_called_once()
        assert result["restarted"] is True

    def test_old_daemon_without_the_route_is_restarted(self):
        from gaia.daemon import client

        fresh = mock.Mock(status_code=200)
        fresh.json.return_value = {"mode": "gaia", "base_url": None}
        with (
            mock.patch.object(client, "attach", return_value=self._inst("1.4")),
            mock.patch("requests.post", return_value=fresh) as post,
            mock.patch.object(
                client, "request_shutdown", return_value=True
            ) as shutdown,
            mock.patch.object(client, "wait_until_gone", return_value=True),
            mock.patch.object(client, "start_or_attach", return_value=self._inst()),
        ):
            result = client.reconfigure_lemonade()
        shutdown.assert_called_once()
        assert post.call_count == 1
        assert result["restarted"] is True

    def test_daemon_that_will_not_exit_is_an_error(self):
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonError

        with (
            mock.patch.object(client, "attach", return_value=self._inst("1.4")),
            mock.patch.object(client, "request_shutdown", return_value=True),
            mock.patch.object(client, "wait_until_gone", return_value=False),
        ):
            with pytest.raises(DaemonError, match="gaia daemon restart"):
                client.reconfigure_lemonade()


class TestReviewRegressions:
    def test_a_start_waiting_on_the_lock_sees_the_switch(self):
        """A boot-time ensure queued behind reconfigure() must not restart GAIA's."""
        import threading

        from gaia.daemon.lemonade import EmbeddedLemonadeOwner, LemonadeNotManaged

        embedded = mock.Mock()
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded)
        errors = []
        owner._lock.acquire()
        worker = threading.Thread(
            target=lambda: errors.append(_raises(owner.ensure)), daemon=True
        )
        worker.start()
        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        owner._lock.release()
        worker.join(5)
        assert isinstance(errors[0], LemonadeNotManaged)
        embedded.start.assert_not_called()

    def test_daemon_follows_the_setting_not_this_shells_env(self, monkeypatch):
        from gaia.daemon import client
        from gaia.daemon.instance import DaemonInstance

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://shell-only:13305")
        inst = DaemonInstance(
            pid=1,
            port=5,
            token="t",
            host="127.0.0.1",
            started_at=0.0,
            api_version="1.5",
        )
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {
            "mode": "external",
            "base_url": USER_URL,
            "source": "config",
        }
        with (
            mock.patch.object(client, "attach", return_value=inst),
            mock.patch("requests.post", return_value=ok),
            mock.patch.object(client, "request_shutdown") as shutdown,
        ):
            assert client.reconfigure_lemonade()["restarted"] is False
        shutdown.assert_not_called()

    def test_an_env_pinned_daemon_restarts_without_the_env(self):
        from gaia.daemon import client
        from gaia.daemon.instance import DaemonInstance

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        inst = DaemonInstance(
            pid=1,
            port=5,
            token="t",
            host="127.0.0.1",
            started_at=0.0,
            api_version="1.5",
        )
        pinned = mock.Mock(status_code=200)
        pinned.json.return_value = {
            "mode": "external",
            "base_url": "http://old:1/api/v1",
        }
        fresh = mock.Mock(status_code=200)
        fresh.json.return_value = {"mode": "external", "base_url": USER_URL}
        with (
            mock.patch.object(client, "attach", return_value=inst),
            mock.patch("requests.post", side_effect=[pinned, fresh]),
            mock.patch.object(client, "request_shutdown", return_value=True),
            mock.patch.object(client, "wait_until_gone", return_value=True),
            mock.patch.object(client, "start_or_attach", return_value=inst) as start,
        ):
            client.reconfigure_lemonade()
        assert set(start.call_args.kwargs["drop_env"]) == {
            "LEMONADE_BASE_URL",
            "LEMONADE_API_KEY",
        }

    def test_a_restart_that_still_disagrees_is_an_error(self):
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonError
        from gaia.daemon.instance import DaemonInstance

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        inst = DaemonInstance(
            pid=1,
            port=5,
            token="t",
            host="127.0.0.1",
            started_at=0.0,
            api_version="1.5",
        )
        wrong = mock.Mock(status_code=200)
        wrong.json.return_value = {"mode": "gaia", "base_url": None}
        with (
            mock.patch.object(client, "attach", return_value=inst),
            mock.patch("requests.post", return_value=wrong),
            mock.patch.object(client, "request_shutdown", return_value=True),
            mock.patch.object(client, "wait_until_gone", return_value=True),
            mock.patch.object(client, "start_or_attach", return_value=inst),
        ):
            with pytest.raises(DaemonError, match="still uses"):
                client.reconfigure_lemonade()

    def test_daemon_env_drops_named_variables(self, monkeypatch):
        from gaia.daemon.client import _daemon_env

        monkeypatch.setenv("LEMONADE_BASE_URL", "http://x:1")
        assert "LEMONADE_BASE_URL" in _daemon_env()
        assert "LEMONADE_BASE_URL" not in _daemon_env(("LEMONADE_BASE_URL",))


def _raises(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - the test inspects it
        return e
    return None
