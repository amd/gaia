# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for LemonadeClient's Lemonade Server version floor."""

import logging
from unittest.mock import patch

import pytest

from gaia.installer.init_command import INIT_PROFILES
from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    LemonadeStatus,
    LemonadeVersionError,
)
from gaia.version import LEMONADE_MIN_VERSION, LEMONADE_VERSION

_REMEDY = "lemonade-server.ai"


@pytest.fixture(autouse=True)
def _users_own_server(monkeypatch):
    """Judge a server the user runs, whatever this machine has installed."""
    monkeypatch.setattr(
        "gaia.llm.lemonade_launcher.gaia_runs_lemonade", lambda base_url=None: False
    )


def _make_client():
    return LemonadeClient(host="localhost", port=13305)


class TestCheckVersionCompatibility:
    """``_check_version_compatibility``: pass, raise below the floor, warn on unknown."""

    # -- below the floor (incompatible) ---------------------------------

    @pytest.mark.parametrize("found", ["11.9.0", "2026.39.1", "2026.44.0~4.deadbee"])
    def test_below_floor_raises_with_both_versions_and_the_fix(self, found):
        with pytest.raises(LemonadeVersionError) as excinfo:
            _make_client()._check_version_compatibility(
                "2026.50.0", actual_version=found, quiet=True
            )
        message = str(excinfo.value)
        assert found in message
        assert LEMONADE_MIN_VERSION in message
        assert _REMEDY in message
        assert excinfo.value.found_version == found
        assert excinfo.value.min_version == LEMONADE_MIN_VERSION

    def test_version_error_is_a_client_error(self):
        assert issubclass(LemonadeVersionError, LemonadeClientError)

    def test_gaias_own_server_asks_the_user_for_nothing(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.llm.lemonade_launcher.gaia_runs_lemonade",
            lambda base_url=None: True,
        )
        with pytest.raises(LemonadeVersionError) as excinfo:
            _make_client()._check_version_compatibility(
                LEMONADE_VERSION, actual_version="11.9.0", quiet=True
            )
        message = str(excinfo.value)
        assert "GAIA updates its own Lemonade Server" in message
        assert "`gaia daemon restart`" in message
        assert "gaia init" not in message
        assert _REMEDY not in message

    # -- at/above the floor but below the expected pin (compatible) -----
    # The #2130 scenario: a supported Lemonade older than the one GAIA pins.

    def test_at_floor_but_below_expected_returns_true(self):
        result = _make_client()._check_version_compatibility(
            "2026.50.0", actual_version=LEMONADE_MIN_VERSION, quiet=True
        )
        assert result is True

    def test_at_floor_but_below_expected_prints_low_key_note(self, capsys):
        _make_client()._check_version_compatibility(
            "2026.50.0", actual_version=LEMONADE_MIN_VERSION, quiet=False
        )
        captured = capsys.readouterr().out
        assert LEMONADE_MIN_VERSION in captured
        assert "2026.50.0" in captured
        assert "consider updating" in captured.lower()

    def test_exact_match_is_silent(self, capsys):
        result = _make_client()._check_version_compatibility(
            LEMONADE_VERSION, actual_version=LEMONADE_VERSION, quiet=False
        )
        assert result is True
        assert capsys.readouterr().out == ""

    def test_newer_than_expected_returns_true(self):
        result = _make_client()._check_version_compatibility(
            LEMONADE_VERSION, actual_version="2027.1.0", quiet=True
        )
        assert result is True

    def test_quiet_suppresses_the_note(self, capsys):
        _make_client()._check_version_compatibility(
            "2026.50.0", actual_version=LEMONADE_MIN_VERSION, quiet=True
        )
        assert capsys.readouterr().out == ""

    # -- a server reporting no readable version is refused, not waved on --

    @pytest.mark.parametrize(
        "found,named",
        [("not-a-version", "'not-a-version'"), ("dev", "'dev'"), ("", "no version")],
    )
    def test_unreadable_server_version_is_refused_naming_it(self, found, named):
        with pytest.raises(LemonadeVersionError) as excinfo:
            _make_client()._check_version_compatibility(
                LEMONADE_VERSION, actual_version=found, quiet=True
            )
        message = str(excinfo.value)
        assert named in message
        assert LEMONADE_MIN_VERSION in message
        assert _REMEDY in message

    @pytest.mark.parametrize(
        "found",
        [f"{LEMONADE_MIN_VERSION}~3.abc1234", f"v{LEMONADE_MIN_VERSION}", "2026.45.0"],
    )
    def test_development_and_newer_builds_pass(self, found):
        result = _make_client()._check_version_compatibility(
            LEMONADE_VERSION, actual_version=found, quiet=True
        )
        assert result is True

    @patch.object(LemonadeClient, "get_lemonade_version", return_value=None)
    def test_unreadable_cli_version_defers_to_the_server(self, _mock, capsys):
        """Before a start there is no server to ask; its report is checked later."""
        result = _make_client()._check_version_compatibility(
            LEMONADE_VERSION, quiet=False
        )
        assert result is None
        assert capsys.readouterr().out == ""


class TestInitializeReportsTheFloor:
    """``initialize()`` acts on the verdict instead of discarding it."""

    def _client(self, mocker, *, running, server_version, cli_version="2026.40.0"):
        client = _make_client()
        mocker.patch.object(client, "_check_lemonade_installed", return_value=True)
        mocker.patch.object(client, "get_lemonade_version", return_value=cli_version)
        status = LemonadeStatus(
            running=running, version=server_version, context_size=65536
        )
        mocker.patch.object(client, "get_status", return_value=status)
        launch = mocker.patch.object(client, "launch_server")
        return client, launch

    def test_running_server_below_floor_is_an_error(self, mocker, capsys):
        client, _ = self._client(mocker, running=True, server_version="9.1.4")

        status = client.initialize(agent="chat", quiet=False)

        assert "9.1.4" in status.error
        assert LEMONADE_MIN_VERSION in status.error
        assert _REMEDY in status.error
        assert status.error in capsys.readouterr().out

    def test_stopped_server_below_floor_is_not_started(self, mocker):
        client, launch = self._client(
            mocker, running=False, server_version=None, cli_version="9.1.4"
        )

        status = client.initialize(agent="chat", quiet=True)

        launch.assert_not_called()
        assert "9.1.4" in status.error
        assert _REMEDY in status.error

    def test_supported_running_server_has_no_error(self, mocker):
        client, _ = self._client(mocker, running=True, server_version=LEMONADE_VERSION)

        status = client.initialize(agent="chat", quiet=True)

        assert status.error is None

    def test_running_server_with_an_unreadable_version_is_an_error(self, mocker):
        client, _ = self._client(
            mocker, running=True, server_version="weird-build", cli_version=None
        )

        status = client.initialize(agent="chat", quiet=True)

        assert "'weird-build'" in status.error
        assert LEMONADE_MIN_VERSION in status.error

    def test_a_started_server_below_the_floor_is_an_error(self, mocker):
        """The CLI could not say; the server it started does."""
        client, launch = self._client(
            mocker, running=False, server_version=None, cli_version=None
        )
        mocker.patch.object(client, "health_check", return_value={"status": "ok"})
        client.get_status.side_effect = [
            LemonadeStatus(running=False),
            LemonadeStatus(running=True, version="11.9.0"),
        ]

        status = client.initialize(agent="chat", quiet=True)

        launch.assert_called_once()
        assert "11.9.0" in status.error
        assert LEMONADE_MIN_VERSION in status.error


class TestEnsureReadyRefusesAnOldServer:
    """Agents and `gaia llm` go through ensure_ready, not initialize()."""

    _URL = "http://gpu-box:13305/api/v1"

    @pytest.fixture(autouse=True)
    def _fresh_manager(self, monkeypatch):
        from gaia.llm.lemonade_manager import LemonadeManager

        monkeypatch.setenv("LEMONADE_BASE_URL", self._URL)
        LemonadeManager.reset()
        yield
        LemonadeManager.reset()

    def _serve(self, mocker, version):
        from gaia.llm.lemonade_manager import LemonadeManager

        status = LemonadeStatus(url=self._URL, running=True, version=version)
        mocker.patch.object(LemonadeClient, "get_status", return_value=status)
        return mocker.patch.object(LemonadeManager, "_try_preload_with_ctx")

    @pytest.mark.parametrize("found", ["10.2.0", "2026.39.1", None, "dev"])
    def test_an_old_server_is_refused_before_any_model_load(self, mocker, found):
        from gaia.llm.lemonade_manager import LemonadeManager

        preload = self._serve(mocker, found)

        with pytest.raises(LemonadeVersionError) as excinfo:
            LemonadeManager.ensure_ready(min_context_size=32768, base_url=self._URL)

        message = str(excinfo.value)
        assert (found or "no version") in message
        assert LEMONADE_MIN_VERSION in message
        assert _REMEDY in message
        preload.assert_not_called()
        assert LemonadeManager._initialized is False

    @pytest.mark.parametrize(
        "found", [LEMONADE_MIN_VERSION, f"{LEMONADE_MIN_VERSION}~3.abc1234", "2999.1.0"]
    )
    def test_a_supported_server_is_not_refused(self, mocker, found):
        from gaia.llm.lemonade_manager import LemonadeManager

        self._serve(mocker, found)

        assert LemonadeManager.ensure_ready(
            min_context_size=32768, base_url=self._URL, preload=False
        )


class TestGetStatusCatalogFailure:
    def test_failed_catalog_lookup_is_logged_and_loaded_models_still_report(
        self, mocker, caplog
    ):
        client = _make_client()
        mocker.patch.object(
            client,
            "health_check",
            return_value={
                "status": "ok",
                "version": LEMONADE_VERSION,
                "all_models_loaded": [
                    {
                        "model_name": "Gemma-4-E4B-it-GGUF",
                        "type": "llm",
                        "recipe_options": {"ctx_size": 65536},
                    }
                ],
            },
        )
        mocker.patch.object(
            client, "list_models", side_effect=LemonadeClientError("catalog down")
        )

        with caplog.at_level(logging.WARNING):
            status = client.get_status()

        assert "catalog down" in caplog.text
        assert [m["model_name"] for m in status.loaded_models] == [
            "Gemma-4-E4B-it-GGUF"
        ]


class TestTheFloorIsDefinedOnce:
    """The installer and the client gate read the same floor."""

    def test_no_profile_declares_its_own_floor(self):
        for profile_name, profile_config in INIT_PROFILES.items():
            assert "min_lemonade_version" not in profile_config, profile_name

    def test_the_floor_is_accepted_by_the_client_gate(self):
        result = _make_client()._check_version_compatibility(
            LEMONADE_VERSION, actual_version=LEMONADE_MIN_VERSION, quiet=True
        )
        assert result is True
