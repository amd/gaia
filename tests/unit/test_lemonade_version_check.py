# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for LemonadeClient version compatibility checking."""

from unittest.mock import patch

from gaia.llm.lemonade_client import LemonadeClient


class TestCheckVersionCompatibility:
    """Test _check_version_compatibility version warning behaviour."""

    def _make_client(self):
        return LemonadeClient(host="localhost", port=8000)

    # -- major mismatch (incompatible) --------------------------------

    def test_major_mismatch_returns_false(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "10.0.0", actual_version="9.5.0", quiet=True
        )
        assert result is False

    def test_major_mismatch_prints_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "10.0.0", actual_version="9.5.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert "version mismatch detected" in captured.lower()
        assert "10" in captured
        assert "9.5.0" in captured

    # -- minor/patch mismatch (compatible, but warning) ----------------

    def test_minor_mismatch_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "10.1.0", actual_version="10.0.0", quiet=True
        )
        assert result is True

    def test_minor_mismatch_prints_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "10.1.0", actual_version="10.0.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert "10.0.0" in captured
        assert "10.1.0" in captured
        assert "consider updating" in captured.lower()

    def test_patch_mismatch_prints_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "10.0.1", actual_version="10.0.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert "10.0.0" in captured
        assert "10.0.1" in captured

    # -- exact match (no warning) --------------------------------------

    def test_exact_match_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "10.0.0", actual_version="10.0.0", quiet=True
        )
        assert result is True

    def test_exact_match_no_output(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "10.0.0", actual_version="10.0.0", quiet=False
        )
        captured = capsys.readouterr().out
        assert captured == ""

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

    def test_quiet_suppresses_major_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "10.0.0", actual_version="9.0.0", quiet=True
        )
        assert capsys.readouterr().out == ""

    def test_quiet_suppresses_minor_warning(self, capsys):
        client = self._make_client()
        client._check_version_compatibility(
            "10.1.0", actual_version="10.0.0", quiet=True
        )
        assert capsys.readouterr().out == ""

    # -- malformed version (don't crash) -------------------------------

    def test_malformed_version_returns_true(self):
        client = self._make_client()
        result = client._check_version_compatibility(
            "10.0.0", actual_version="not-a-version", quiet=True
        )
        assert result is True
