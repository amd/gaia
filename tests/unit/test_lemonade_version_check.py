# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for LemonadeClient version compatibility checking."""

from unittest.mock import patch

from gaia.installer.init_command import INIT_PROFILES
from gaia.llm.lemonade_client import LemonadeClient
from gaia.version import LEMONADE_MIN_VERSION, LEMONADE_VERSION


class TestCheckVersionCompatibility:
    """Test _check_version_compatibility version warning behaviour."""

    def _make_client(self):
        return LemonadeClient(host="localhost", port=13305)

    # -- below the floor (incompatible) ---------------------------------

    def test_below_floor_returns_false(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "11.0.0", actual_version="10.1.0", quiet=True
        )
        assert result is False

    def test_well_below_floor_returns_false(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "11.0.0", actual_version="9.5.0", quiet=True
        )
        assert result is False

    def test_below_floor_prints_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "11.0.0", actual_version="9.5.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert "too old" in captured.lower()
        assert "9.5.0" in captured
        assert "10.2.0" in captured

    # -- at/above the floor but below the expected pin (compatible) -----
    # This is the #2130 scenario: Lemonade 10.10.0 with GAIA pinned to 11.0.0.

    def test_at_floor_but_below_expected_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "11.0.0", actual_version="10.10.0", quiet=True
        )
        assert result is True

    def test_at_floor_but_below_expected_does_not_warn_of_mismatch(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "11.0.0", actual_version="10.10.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert "mismatch detected" not in captured.lower()
        assert "may cause compatibility issues" not in captured.lower()

    def test_at_floor_but_below_expected_prints_low_key_note(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "11.0.0", actual_version="10.10.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert "10.10.0" in captured
        assert "11.0.0" in captured
        assert "consider updating" in captured.lower()

    # -- exact match (no warning) --------------------------------------

    def test_exact_match_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "11.0.0", actual_version="11.0.0", quiet=True
        )
        assert result is True

    def test_exact_match_no_output(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "11.0.0", actual_version="11.0.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert captured == ""

    # -- newer than expected (no upper bound) --------------------------

    def test_newer_than_expected_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "11.0.0", actual_version="12.0.0", quiet=True
        )
        assert result is True

    # -- version unknown (assume compatible) ---------------------------

    def test_none_version_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "10.0.0", actual_version=None, quiet=True
        )
        assert result is True

    @patch.object(LemonadeClient, "get_lemonade_version", return_value=None)
    def test_cli_version_unavailable_returns_true(self, _mock):
        client = self._make_client()
        # When actual_version is not passed, falls back to CLI detection
        result = client._check_version_compatibility("10.0.0", quiet=True)
        assert result is True

    # -- quiet suppresses output ---------------------------------------

    def test_quiet_suppresses_below_floor_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "11.0.0", actual_version="9.0.0", quiet=True
        )
        assert capsys.readouterr().out == ""

    def test_quiet_suppresses_minor_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "11.1.0", actual_version="11.0.0", quiet=True
        )
        assert capsys.readouterr().out == ""

    # -- malformed version (don't crash) -------------------------------

    def test_malformed_version_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "10.0.0", actual_version="not-a-version", quiet=True
        )
        assert result is True


class TestInitCommandAndClientGatesAgree:
    """Regression test: the installer's profile-minimum gate and the client's
    version-floor gate must not contradict each other for the same install.

    A version accepted by every profile's ``min_lemonade_version`` must not be
    reported incompatible by ``LemonadeClient._check_version_compatibility``.
    """

    def test_all_profile_minimums_are_accepted_by_the_client_gate(self):
        client = LemonadeClient(host="localhost", port=13305)
        for profile_name, profile_config in INIT_PROFILES.items():
            min_version = profile_config["min_lemonade_version"]
            result = client._check_version_compatibility(
                LEMONADE_VERSION, actual_version=min_version, quiet=True
            )
            assert result is True, (
                f"profile {profile_name!r} accepts Lemonade {min_version}, but "
                "the client's version-floor check rejects it"
            )

    def test_lemonade_min_version_matches_lowest_profile_floor(self):
        lowest_profile_floor = min(
            tuple(int(p) for p in cfg["min_lemonade_version"].split("."))
            for cfg in INIT_PROFILES.values()
        )
        assert (
            tuple(int(p) for p in LEMONADE_MIN_VERSION.split("."))
            <= lowest_profile_floor
        )
