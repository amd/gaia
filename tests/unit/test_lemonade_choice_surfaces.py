# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every front end honours the "use my Lemonade" choice the same way.

The daemon routes, the Agent UI router, ``gaia init`` and ``gaia config set``.
Probes are mocked at :func:`gaia.llm.lemonade_choice.probe` here; its own
request shape is pinned in ``test_lemonade_choice.py``.
"""

from __future__ import annotations

import time
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
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))


@pytest.fixture
def keyring(monkeypatch):
    store = {}
    from gaia.connectors import store as store_mod

    monkeypatch.setattr(store_mod, "save_secret", store.__setitem__)
    monkeypatch.setattr(store_mod, "peek_secret", store.get)
    monkeypatch.setattr(store_mod, "delete_secret", lambda n: store.pop(n, None))
    return store


def _ok(url=USER_URL, version="11.8.1"):
    return choice.ProbeResult(url, choice.OK, version=version, minimum="10.9.0")


def _save(**fields):
    cfg = GaiaConfig.load()
    for key, value in fields.items():
        setattr(cfg, key, value)
    cfg.save()


# ---------------------------------------------------------------------------
# Daemon routes
# ---------------------------------------------------------------------------


class TestDaemonRoutes:
    def _client(self, owner):
        from fastapi.testclient import TestClient

        from gaia.daemon.app import create_app

        app = create_app(
            token="tok", port=1, pid=1, started_at=time.time(), lemonade_owner=owner
        )
        return TestClient(app)

    def test_target_hands_the_tui_the_saved_key(self, keyring):
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner

        keyring[choice.API_KEY_SECRET_NAME] = "s3cret"
        _save(
            lemonade_mode="external",
            lemonade_base_url=USER_URL,
            lemonade_api_key_stored=True,
        )
        r = self._client(EmbeddedLemonadeOwner(factory=mock.Mock())).get(
            "/daemon/v1/lemonade/target", headers={"Authorization": "Bearer tok"}
        )
        assert r.status_code == 200
        body = r.json()
        assert (body["source"], body["base_url"], body["api_key"]) == (
            "config",
            USER_URL,
            "s3cret",
        )

    def test_target_never_hands_out_a_key_for_an_env_chosen_server(
        self, keyring, monkeypatch
    ):
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner

        keyring[choice.API_KEY_SECRET_NAME] = "s3cret"
        _save(
            lemonade_mode="external",
            lemonade_base_url=USER_URL,
            lemonade_api_key_stored=True,
        )
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://other:13305")
        r = self._client(EmbeddedLemonadeOwner(factory=mock.Mock())).get(
            "/daemon/v1/lemonade/target", headers={"Authorization": "Bearer tok"}
        )
        assert r.json()["api_key"] is None

    def test_target_needs_the_daemon_token(self):
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner

        r = self._client(EmbeddedLemonadeOwner(factory=mock.Mock())).get(
            "/daemon/v1/lemonade/target"
        )
        assert r.status_code == 401

    def test_reconfigure_route_stops_gaias_own(self):
        from gaia.daemon.lemonade import EmbeddedLemonadeOwner

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        embedded = mock.Mock()
        embedded.stop.return_value = True
        r = self._client(EmbeddedLemonadeOwner(factory=lambda: embedded)).post(
            "/daemon/v1/lemonade/reconfigure", headers={"Authorization": "Bearer tok"}
        )
        assert r.status_code == 200
        assert r.json()["action"] == "stopped_gaia_lemonade"
        embedded.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Agent UI router
# ---------------------------------------------------------------------------


class TestAgentUiRouter:
    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from gaia.ui.routers import lemonade as router_mod

        app = FastAPI()
        app.include_router(router_mod.router)
        self.applied = mock.Mock(return_value={"action": "stopped_gaia_lemonade"})
        self.dropped = mock.Mock()
        monkeypatch.setattr(choice, "apply_to_daemon", self.applied)
        monkeypatch.setattr(router_mod, "_drop_cached_agents", self.dropped)
        client = TestClient(app)
        # tests/conftest.py adds the header to every TestClient; opt in per call.
        client.headers.pop("x-gaia-ui", None)
        return client

    H = {"X-Gaia-UI": "1"}

    def test_target_offers_a_switch_until_someone_chooses(self, client):
        body = client.get("/api/lemonade/target", headers=self.H).json()
        assert body["mode"] == "gaia" and body["offer"] is True
        assert "embedding model" in body["what_gaia_does"]
        _save(lemonade_mode="gaia")
        assert (
            client.get("/api/lemonade/target", headers=self.H).json()["offer"] is False
        )

    def test_detect_says_which_ports_it_could_not_check(self, client, monkeypatch):
        url = "http://localhost:13305/api/v1"
        monkeypatch.setattr(
            choice, "detect_lemonade", lambda: choice.Detection(None, (url,))
        )
        r = client.get("/api/lemonade/detect", headers={"X-Gaia-UI": "1"})
        assert r.json() == {"found": None, "unchecked": [url]}

    def test_reads_need_the_ui_header(self, client):
        assert client.get("/api/lemonade/target").status_code == 403
        assert client.get("/api/lemonade/detect").status_code == 403

    def test_use_mine_saves_switches_the_daemon_and_drops_agents(
        self, client, keyring, monkeypatch
    ):
        probe = mock.Mock(return_value=_ok())
        monkeypatch.setattr(choice, "probe", probe)
        r = client.post(
            "/api/lemonade/target",
            json={
                "mode": "external",
                "base_url": "http://localhost:13305",
                "api_key": "k",
            },
            headers=self.H,
        )
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "external" and r.json()["base_url"] == USER_URL
        assert "api_key" not in r.json()
        probe.assert_called_once_with(USER_URL, "k")
        self.applied.assert_called_once()
        self.dropped.assert_called_once()

    def test_needs_key_comes_back_as_a_status_the_ui_can_act_on(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            choice,
            "probe",
            mock.Mock(return_value=choice.ProbeResult(USER_URL, choice.NEEDS_KEY)),
        )
        r = client.post(
            "/api/lemonade/target",
            json={"mode": "external", "base_url": USER_URL},
            headers=self.H,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["status"] == "needs_key"
        assert GaiaConfig.load().lemonade_mode is None
        self.applied.assert_not_called()

    def test_a_dead_address_is_refused_in_plain_words(self, client, monkeypatch):
        import urllib3

        conn = urllib3.exceptions.NewConnectionError(None, "Failed to establish")
        conn.__cause__ = ConnectionRefusedError(10061, "actively refused")
        refused = requests.exceptions.ConnectionError(
            urllib3.exceptions.MaxRetryError(None, "/api/v1/health", conn)
        )
        monkeypatch.setattr(requests, "get", mock.Mock(side_effect=refused))
        r = client.post(
            "/api/lemonade/target",
            json={"mode": "external", "base_url": "http://127.0.0.1:9"},
            headers=self.H,
        )
        assert r.status_code == 422
        message = r.json()["detail"]["message"]
        assert message == (
            "No Lemonade Server is answering at http://127.0.0.1:9/api/v1: "
            "nothing is listening on that port. Start it, or check the address "
            "and port."
        )
        assert "gaia config set" not in message
        self.applied.assert_not_called()

    def test_malformed_url_is_rejected(self, client):
        r = client.post(
            "/api/lemonade/target",
            json={"mode": "external", "base_url": "file:///etc/passwd"},
            headers=self.H,
        )
        assert r.status_code == 422
        self.applied.assert_not_called()

    def test_not_over_mobile_access(self, client):
        r = client.post(
            "/api/lemonade/target",
            json={"mode": "gaia"},
            headers={**self.H, "X-Forwarded-For": "1.2.3.4"},
        )
        assert r.status_code == 403

    def test_back_to_gaias_own(self, client, monkeypatch):
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            lambda self: True,
        )
        monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", lambda: {})
        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        r = client.post("/api/lemonade/target", json={"mode": "gaia"}, headers=self.H)
        assert r.status_code == 200
        assert r.json()["mode"] == "gaia"
        self.applied.assert_called_once()

    def test_a_daemon_that_would_not_switch_is_reported(self, client):
        from gaia.daemon.errors import DaemonError

        self.applied.side_effect = DaemonError("boom")
        r = client.post("/api/lemonade/target", json={"mode": "gaia"}, headers=self.H)
        assert r.status_code == 502
        assert "could not switch" in r.json()["detail"]
        assert GaiaConfig.load().lemonade_mode is None


# ---------------------------------------------------------------------------
# gaia init
# ---------------------------------------------------------------------------


class TestInit:
    def _cmd(self, **kwargs):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", **kwargs)
        cmd._printed = []
        cmd._print = cmd._printed.append
        cmd._print_error = lambda m: cmd._printed.append(f"ERROR {m}")
        cmd._print_success = lambda m: cmd._printed.append(f"OK {m}")
        return cmd

    @pytest.fixture
    def fresh(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            lambda self: False,
        )

    def test_yes_never_adopts_a_found_server_on_its_own(self, fresh, monkeypatch):
        monkeypatch.setattr(choice, "detect_running_lemonade", lambda: _ok())
        cmd = self._cmd(yes=True)
        assert cmd._choose_lemonade() is True
        assert cmd._lemonade_base_url is None
        assert GaiaConfig.load().lemonade_mode is None
        assert any("gaia lemonade connect" in line for line in cmd._printed)

    def test_interactive_yes_saves_external(self, fresh, monkeypatch, keyring):
        monkeypatch.setattr(choice, "detect_running_lemonade", lambda: _ok())
        monkeypatch.setattr(choice, "probe", lambda url, key=None, **k: _ok())
        cmd = self._cmd()
        monkeypatch.setattr(cmd, "_prompt_yes_no", lambda *a, **k: True)
        assert cmd._choose_lemonade() is True
        assert cmd._lemonade_base_url == USER_URL
        assert GaiaConfig.load().lemonade_mode == "external"

    def test_interactive_no_saves_gaia_only_once_it_runs(self, fresh, monkeypatch):
        monkeypatch.setattr(choice, "detect_running_lemonade", lambda: _ok())
        cmd = self._cmd()
        monkeypatch.setattr(cmd, "_prompt_yes_no", lambda *a, **k: False)
        assert cmd._choose_lemonade() is True
        assert GaiaConfig.load().lemonade_mode is None
        assert cmd._lemonade_base_url is None
        assert cmd._gaia_choice_pending is True

    def test_no_offer_once_gaias_own_is_installed(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            lambda self: True,
        )
        detect = mock.Mock()
        monkeypatch.setattr(choice, "detect_running_lemonade", detect)
        assert self._cmd()._choose_lemonade() is True
        detect.assert_not_called()

    def test_explicit_url_that_is_too_old_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            choice,
            "probe",
            lambda url, key=None, **k: choice.ProbeResult(
                USER_URL, choice.TOO_OLD, version="10.2.0", minimum="10.9.0"
            ),
        )
        cmd = self._cmd(yes=True, lemonade_url="http://localhost:13305")
        assert cmd._choose_lemonade() is False
        assert any("v10.2.0" in line and "v10.9.0" in line for line in cmd._printed)
        assert GaiaConfig.load().lemonade_mode is None

    def test_explicit_gaia_waits_for_the_server_before_saving(self):
        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        cmd = self._cmd(yes=True, gaia_lemonade=True)
        assert cmd._choose_lemonade() is True
        assert GaiaConfig.load().lemonade_mode == "external"
        # Setup installs GAIA's own, not the user's server it would check.
        assert cmd._lemonade_base_url is None

    def test_both_flags_conflict(self):
        from gaia.installer.init_command import InitCommand

        with pytest.raises(ValueError, match="Choose one"):
            InitCommand(lemonade_url=USER_URL, gaia_lemonade=True)

    def test_init_does_not_clobber_the_users_server(self):
        """No unload and no saved per-model options on someone else's server."""
        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        cmd = self._cmd(yes=True)
        cmd.profile = "chat"
        client = mock.Mock()
        client.check_model_loaded.return_value = True
        client.list_models.return_value = {"data": []}
        cmd._test_model_inference(client, "Gemma-4-E4B-it-GGUF")
        client.unload_model.assert_not_called()
        assert client.load_model.call_args.kwargs["save_options"] is False


# ---------------------------------------------------------------------------
# gaia config set lemonade_*
# ---------------------------------------------------------------------------


class TestConfigSet:
    def _run(self, key, value):
        from gaia.cli import handle_config_command

        handle_config_command(
            SimpleNamespace(config=None, config_action="set", key=key, value=value)
        )

    def test_back_to_gaia_tells_the_daemon(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            lambda self: True,
        )
        monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", lambda: {})
        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        applied = mock.Mock(return_value={"action": "starting_gaia_lemonade"})
        monkeypatch.setattr(choice, "apply_to_daemon", applied)
        self._run("lemonade_mode", "gaia")
        assert GaiaConfig.load().lemonade_mode == "gaia"
        applied.assert_called_once()
        assert "Starting GAIA's own" in capsys.readouterr().out

    def test_external_with_a_down_server_changes_nothing(self, monkeypatch):
        _save(lemonade_mode="gaia", lemonade_base_url=USER_URL)
        monkeypatch.setattr(
            choice,
            "probe",
            lambda url, key=None, **k: choice.ProbeResult(USER_URL, choice.UNREACHABLE),
        )
        applied = mock.Mock()
        monkeypatch.setattr(choice, "apply_to_daemon", applied)
        with pytest.raises(SystemExit):
            self._run("lemonade_mode", "external")
        assert GaiaConfig.load().lemonade_mode == "gaia"
        applied.assert_not_called()

    def test_the_key_flag_is_not_settable_by_hand(self):
        with pytest.raises(SystemExit):
            self._run("lemonade_api_key_stored", "true")


class TestInitReviewRegressions:
    def _cmd(self, **kwargs):
        return TestInit._cmd(TestInit(), **kwargs)

    def test_gaias_own_exported_key_is_never_sent_to_the_users_server(
        self, monkeypatch, keyring
    ):
        monkeypatch.setenv("LEMONADE_API_KEY", "gaias-own-key")
        monkeypatch.setenv("GAIA_LEMONADE_EMBEDDED", "1")
        seen = []
        monkeypatch.setattr(
            choice, "probe", lambda url, key=None, **k: seen.append(key) or _ok()
        )
        monkeypatch.setattr(choice, "apply_to_daemon", lambda: None)
        cmd = self._cmd(yes=True, lemonade_url="http://localhost:13305")
        assert cmd._choose_lemonade() is True
        assert seen and all(k is None for k in seen)
        assert choice.API_KEY_SECRET_NAME not in keyring

    def test_an_explicit_choice_tells_the_daemon(self, monkeypatch, keyring):
        applied = mock.Mock(return_value=None)
        monkeypatch.setattr(choice, "apply_to_daemon", applied)
        monkeypatch.setattr(choice, "probe", lambda url, key=None, **k: _ok())
        cmd = self._cmd(yes=True, lemonade_url="http://localhost:13305")
        assert cmd._choose_lemonade() is True
        applied.assert_called_once()

    def test_a_daemon_that_will_not_switch_fails_init(self, monkeypatch, keyring):
        from gaia.daemon.errors import DaemonError

        monkeypatch.setattr(
            choice, "apply_to_daemon", mock.Mock(side_effect=DaemonError("stuck"))
        )
        monkeypatch.setattr(choice, "probe", lambda url, key=None, **k: _ok())
        cmd = self._cmd(yes=True, lemonade_url="http://localhost:13305")
        assert cmd._choose_lemonade() is False
        assert any("did not switch" in line for line in cmd._printed)

    def test_gaia_lemonade_flag_repairs_a_broken_setting(self, monkeypatch):
        _save(lemonade_mode="external")  # no URL: unusable
        cmd = self._cmd(yes=True, gaia_lemonade=True)
        assert cmd._choose_lemonade() is True
        assert cmd._lemonade_base_url is None

    @pytest.mark.parametrize("starts", [True, False])
    def test_gaias_own_is_saved_only_once_it_runs(self, monkeypatch, starts):
        from gaia.llm import lemonade_embedded as emb

        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        cmd = self._cmd(yes=True, gaia_lemonade=True)
        assert cmd._choose_lemonade() is True
        embedded = mock.Mock()
        embedded.status.return_value = mock.Mock(
            unresponsive_pid=None, running=False, version="1"
        )
        embedded.version = "1"
        embedded.is_installed.return_value = True
        if starts:
            embedded.start.return_value = mock.Mock(version="1", port=1)
        else:
            embedded.start.side_effect = emb.EmbeddedLemonadeError("would not start")
        monkeypatch.setattr(emb, "EmbeddedLemonade", lambda **k: embedded)
        monkeypatch.setattr(cmd, "_hand_server_to_daemon", lambda: None)
        assert cmd._start_embedded_server() is starts
        assert GaiaConfig.load().lemonade_mode == ("gaia" if starts else "external")

    def test_force_models_never_deletes_from_the_users_server(self, monkeypatch):
        _save(lemonade_mode="external", lemonade_base_url=USER_URL)
        cmd = self._cmd(yes=True, force_models=True)
        client = mock.Mock()
        monkeypatch.setattr(
            "gaia.llm.lemonade_client.LemonadeClient", lambda *a, **k: client
        )
        assert cmd._download_models() is False
        client.delete_model.assert_not_called()
        assert any("doesn't delete models" in line for line in cmd._printed)
