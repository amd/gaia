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
from gaia.version import LEMONADE_MIN_VERSION, LEMONADE_VERSION, parse_version

_REMEDY = "gaia init --force-reinstall"


def _make_client():
    return LemonadeClient(host="localhost", port=13305)


class TestCheckVersionCompatibility:
    """``_check_version_compatibility``: pass, raise below the floor, warn on unknown."""

    # -- below the floor (incompatible) ---------------------------------

    @pytest.mark.parametrize("found", ["10.1.0", "9.5.0", "9.1.0~4.deadbee"])
    def test_below_floor_raises_with_both_versions_and_the_fix(self, found):
        with pytest.raises(LemonadeVersionError) as excinfo:
            _make_client()._check_version_compatibility(
                "11.0.0", actual_version=found, quiet=True
            )
        message = str(excinfo.value)
        assert found in message
        assert LEMONADE_MIN_VERSION in message
        assert _REMEDY in message
        assert excinfo.value.found_version == found
        assert excinfo.value.min_version == LEMONADE_MIN_VERSION

    def test_version_error_is_a_client_error(self):
        assert issubclass(LemonadeVersionError, LemonadeClientError)

    # -- at/above the floor but below the expected pin (compatible) -----
    # This is the #2130 scenario: Lemonade 10.10.0 with GAIA pinned to 11.0.0.

    def test_at_floor_but_below_expected_returns_true(self):
        result = _make_client()._check_version_compatibility(
            "11.0.0", actual_version="10.10.0", quiet=True
        )
        assert result is True

    def test_at_floor_but_below_expected_prints_low_key_note(self, capsys):
        _make_client()._check_version_compatibility(
            "11.0.0", actual_version="10.10.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert "10.10.0" in captured
        assert "11.0.0" in captured
        assert "consider updating" in captured.lower()

    def test_exact_match_is_silent(self, capsys):
        result = _make_client()._check_version_compatibility(
            "11.0.0", actual_version="11.0.0", quiet=False
        )
        assert result is True
        assert capsys.readouterr().out == ""

    def test_newer_than_expected_returns_true(self):
        result = _make_client()._check_version_compatibility(
            "11.0.0", actual_version="12.0.0", quiet=True
        )
        assert result is True

    def test_quiet_suppresses_the_note(self, capsys):
        _make_client()._check_version_compatibility(
            "11.1.0", actual_version="11.0.0", quiet=True
        )
        assert capsys.readouterr().out == ""

    # -- version unknown: a warning, never a silent pass ----------------

    @pytest.mark.parametrize("found", ["not-a-version", ""])
    def test_unknown_version_warns_and_is_not_a_pass(self, found, caplog, capsys):
        with caplog.at_level(logging.WARNING):
            result = _make_client()._check_version_compatibility(
                "10.0.0", actual_version=found, quiet=False
            )
        assert result is None
        assert LEMONADE_MIN_VERSION in caplog.text
        assert _REMEDY in capsys.readouterr().out

    @patch.object(LemonadeClient, "get_lemonade_version", return_value=None)
    def test_undetectable_cli_version_warns(self, _mock, caplog):
        with caplog.at_level(logging.WARNING):
            result = _make_client()._check_version_compatibility("10.0.0", quiet=True)
        assert result is None
        assert "no version" in caplog.text


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

    def test_running_server_with_unknown_version_warns_but_proceeds(
        self, mocker, caplog
    ):
        client, _ = self._client(
            mocker, running=True, server_version="weird-build", cli_version=None
        )

        with caplog.at_level(logging.WARNING):
            status = client.initialize(agent="chat", quiet=True)

        assert status.error is None
        assert "weird-build" in caplog.text


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

    def test_all_profile_minimums_are_accepted_by_the_client_gate(self):
        client = _make_client()
        for profile_name, profile_config in INIT_PROFILES.items():
            min_version = profile_config["min_lemonade_version"]
            result = client._check_version_compatibility(
                LEMONADE_VERSION, actual_version=min_version, quiet=True
            )
            assert result is True, (
                f"profile {profile_name!r} accepts Lemonade {min_version}, but "
                "the client's version-floor check rejects it"
            )

    def test_lemonade_min_version_is_the_lowest_profile_floor(self):
        lowest = min(
            (cfg["min_lemonade_version"] for cfg in INIT_PROFILES.values()),
            key=parse_version,
        )
        assert lowest == LEMONADE_MIN_VERSION
