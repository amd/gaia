# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for the gaia init command.

These tests use mocking to avoid actual network calls and installations.
"""

import unittest
from unittest.mock import MagicMock, patch

from gaia.installer.lemonade_installer import (
    InstallResult,
    LemonadeInfo,
    LemonadeInstaller,
)
from gaia.version import LEMONADE_VERSION


class TestLemonadeInfo(unittest.TestCase):
    """Test LemonadeInfo dataclass."""

    def test_version_tuple_valid(self):
        """Test version parsing with valid version."""
        info = LemonadeInfo(installed=True, version="9.1.4")
        self.assertEqual(info.version_tuple, (9, 1, 4))

    def test_version_tuple_with_v_prefix(self):
        """Test version parsing with v prefix."""
        info = LemonadeInfo(installed=True, version="v9.1.4")
        self.assertEqual(info.version_tuple, (9, 1, 4))

    def test_version_tuple_none(self):
        """Test version parsing with no version."""
        info = LemonadeInfo(installed=True, version=None)
        self.assertIsNone(info.version_tuple)

    def test_version_tuple_invalid(self):
        """Test version parsing with invalid version."""
        info = LemonadeInfo(installed=True, version="invalid")
        self.assertIsNone(info.version_tuple)


class TestLemonadeInstaller(unittest.TestCase):
    """Test LemonadeInstaller class."""

    def test_init_with_default_version(self):
        """Test installer initialization with default version."""
        installer = LemonadeInstaller()
        self.assertEqual(installer.target_version, LEMONADE_VERSION)

    def test_init_with_custom_version(self):
        """Test installer initialization with custom version."""
        installer = LemonadeInstaller(target_version="10.0.0")
        self.assertEqual(installer.target_version, "10.0.0")

    def test_init_strips_v_prefix(self):
        """Test installer strips v prefix from version."""
        installer = LemonadeInstaller(target_version="v10.0.0")
        self.assertEqual(installer.target_version, "10.0.0")

    @patch("platform.system")
    def test_is_platform_supported_windows(self, mock_system):
        """Test platform support on Windows."""
        mock_system.return_value = "Windows"
        installer = LemonadeInstaller()
        self.assertTrue(installer.is_platform_supported())

    @patch("platform.system")
    def test_is_platform_supported_linux(self, mock_system):
        """Test platform support on Linux."""
        mock_system.return_value = "Linux"
        installer = LemonadeInstaller()
        self.assertTrue(installer.is_platform_supported())

    @patch("platform.system")
    def test_is_platform_supported_macos(self, mock_system):
        """Test platform support on macOS (not supported)."""
        mock_system.return_value = "Darwin"
        installer = LemonadeInstaller()
        self.assertFalse(installer.is_platform_supported())

    @patch("platform.system")
    def test_get_download_url_windows(self, mock_system):
        """Test download URL generation for Windows."""
        mock_system.return_value = "Windows"
        installer = LemonadeInstaller(target_version="9.1.4")
        url = installer.get_download_url()
        self.assertIn("v9.1.4/lemonade.msi", url)
        self.assertIn("github.com", url)

    @patch("platform.system")
    def test_get_download_url_linux(self, mock_system):
        """Test download URL generation for Linux."""
        mock_system.return_value = "Linux"
        installer = LemonadeInstaller(target_version="9.1.4")
        url = installer.get_download_url()
        self.assertIn("v9.1.4/lemonade_9.1.4_amd64.deb", url)
        self.assertIn("github.com", url)

    @patch("platform.system")
    def test_get_download_url_unsupported(self, mock_system):
        """Test download URL raises error for unsupported platform."""
        mock_system.return_value = "Darwin"
        installer = LemonadeInstaller(target_version="9.1.4")
        with self.assertRaises(RuntimeError) as ctx:
            installer.get_download_url()
        self.assertIn("not supported", str(ctx.exception))

    @patch("platform.system")
    def test_get_download_url_windows_minimal(self, mock_system):
        """Test download URL generation for Windows minimal installer."""
        mock_system.return_value = "Windows"
        installer = LemonadeInstaller(target_version="9.1.4", minimal=True)
        url = installer.get_download_url()
        self.assertIn("v9.1.4/lemonade-server-minimal.msi", url)
        self.assertIn("github.com", url)

    @patch("platform.system")
    def test_get_installer_filename_windows_minimal(self, mock_system):
        """Test installer filename for Windows minimal installer."""
        mock_system.return_value = "Windows"
        installer = LemonadeInstaller(target_version="9.1.4", minimal=True)
        filename = installer.get_installer_filename()
        self.assertEqual(filename, "lemonade-server-minimal.msi")

    @patch("platform.system")
    def test_get_installer_filename_windows_full(self, mock_system):
        """Test installer filename for Windows full installer."""
        mock_system.return_value = "Windows"
        installer = LemonadeInstaller(target_version="9.1.4", minimal=False)
        filename = installer.get_installer_filename()
        self.assertEqual(filename, "lemonade.msi")

    def test_needs_install_not_installed(self):
        """Test needs_install when not installed."""
        installer = LemonadeInstaller(target_version="9.1.4")
        info = LemonadeInfo(installed=False)
        self.assertTrue(installer.needs_install(info))

    def test_needs_install_no_version(self):
        """Test needs_install when installed but no version."""
        installer = LemonadeInstaller(target_version="9.1.4")
        info = LemonadeInfo(installed=True, version=None)
        self.assertTrue(installer.needs_install(info))

    def test_needs_install_older_version(self):
        """Test needs_install with older version."""
        installer = LemonadeInstaller(target_version="9.2.0")
        info = LemonadeInfo(installed=True, version="9.1.4")
        self.assertTrue(installer.needs_install(info))

    def test_needs_install_same_version(self):
        """Test needs_install with same version."""
        installer = LemonadeInstaller(target_version="9.1.4")
        info = LemonadeInfo(installed=True, version="9.1.4")
        self.assertFalse(installer.needs_install(info))

    def test_needs_install_newer_version(self):
        """Test needs_install with newer version installed."""
        installer = LemonadeInstaller(target_version="9.1.0")
        info = LemonadeInfo(installed=True, version="9.1.4")
        self.assertFalse(installer.needs_install(info))

    @patch("shutil.which")
    def test_check_installation_not_found(self, mock_which):
        """Test check_installation when lemonade-server not found."""
        mock_which.return_value = None
        installer = LemonadeInstaller()
        info = installer.check_installation()
        self.assertFalse(info.installed)
        self.assertIn("not found", info.error)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_check_installation_found(self, mock_which, mock_run):
        """Test check_installation when lemonade-server is found."""
        mock_which.return_value = "/usr/bin/lemonade-server"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="lemonade-server 9.1.4", stderr=""
        )
        installer = LemonadeInstaller()
        info = installer.check_installation()
        self.assertTrue(info.installed)
        self.assertEqual(info.version, "9.1.4")
        self.assertEqual(info.path, "/usr/bin/lemonade-server")


class TestInstallResult(unittest.TestCase):
    """Test InstallResult dataclass."""

    def test_success_result(self):
        """Test successful installation result."""
        result = InstallResult(
            success=True, version="9.1.4", message="Installed successfully"
        )
        self.assertTrue(result.success)
        self.assertEqual(result.version, "9.1.4")
        self.assertIsNone(result.error)

    def test_failure_result(self):
        """Test failed installation result."""
        result = InstallResult(success=False, error="Permission denied")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Permission denied")


class TestInitCommand(unittest.TestCase):
    """Test InitCommand class."""

    def test_invalid_profile(self):
        """Test that invalid profile raises ValueError."""
        from gaia.installer.init_command import InitCommand

        with self.assertRaises(ValueError) as ctx:
            InitCommand(profile="invalid")
        self.assertIn("Invalid profile", str(ctx.exception))

    def test_valid_profiles(self):
        """Test that valid profiles are accepted."""
        from gaia.installer.init_command import InitCommand

        valid_profiles = ["minimal", "chat", "code", "rag", "all"]
        for profile in valid_profiles:
            cmd = InitCommand(profile=profile, yes=True)
            self.assertEqual(cmd.profile, profile)

    @patch("gaia.installer.init_command.LemonadeInstaller")
    def test_init_creates_installer(self, mock_installer_class):
        """Test that InitCommand creates a LemonadeInstaller."""
        from gaia.installer.init_command import InitCommand

        InitCommand(profile="chat", yes=True)
        mock_installer_class.assert_called_once()


class TestRunInit(unittest.TestCase):
    """Test run_init entry point function."""

    @patch("gaia.installer.init_command.InitCommand")
    def test_run_init_returns_exit_code(self, mock_cmd_class):
        """Test run_init returns the exit code from InitCommand."""
        from gaia.installer.init_command import run_init

        mock_instance = MagicMock()
        mock_instance.run.return_value = 0
        mock_cmd_class.return_value = mock_instance

        result = run_init(profile="chat", yes=True)
        self.assertEqual(result, 0)

    @patch("gaia.installer.init_command.InitCommand")
    def test_run_init_handles_value_error(self, mock_cmd_class):
        """Test run_init handles ValueError gracefully."""
        from gaia.installer.init_command import run_init

        mock_cmd_class.side_effect = ValueError("Invalid profile")

        result = run_init(profile="invalid", yes=True)
        self.assertEqual(result, 1)


class TestInitProfiles(unittest.TestCase):
    """Test init profile definitions."""

    def test_profiles_exist(self):
        """Test that expected profiles are defined."""
        from gaia.installer.init_command import INIT_PROFILES

        expected = ["minimal", "chat", "code", "rag", "all"]
        for profile in expected:
            self.assertIn(profile, INIT_PROFILES)

    def test_minimal_profile_uses_qwen3_0_6b(self):
        """Test that minimal profile uses Qwen3-0.6B model."""
        from gaia.installer.init_command import INIT_PROFILES

        minimal = INIT_PROFILES["minimal"]
        self.assertIn("Qwen3-0.6B-GGUF", minimal["models"])

    def test_profiles_have_required_keys(self):
        """Test that all profiles have required keys."""
        from gaia.installer.init_command import INIT_PROFILES

        required_keys = ["description", "agent", "models", "approx_size"]
        for name, profile in INIT_PROFILES.items():
            for key in required_keys:
                self.assertIn(key, profile, f"Profile '{name}' missing key '{key}'")


class TestRemoteAutoDetection(unittest.TestCase):
    """Test auto-detection of remote mode from LEMONADE_BASE_URL."""

    @patch.dict("os.environ", {"LEMONADE_BASE_URL": "http://192.168.1.100:8000/api/v1"})
    def test_remote_url_sets_remote_true(self):
        """Test that a non-localhost LEMONADE_BASE_URL enables remote mode."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertTrue(cmd.remote)
        self.assertEqual(cmd._lemonade_base_url, "http://192.168.1.100:8000/api/v1")

    @patch.dict("os.environ", {"LEMONADE_BASE_URL": "http://localhost:8000/api/v1"})
    def test_localhost_url_keeps_remote_false(self):
        """Test that localhost LEMONADE_BASE_URL does not enable remote mode."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertFalse(cmd.remote)

    @patch.dict("os.environ", {"LEMONADE_BASE_URL": "http://127.0.0.1:8000/api/v1"})
    def test_loopback_url_keeps_remote_false(self):
        """Test that 127.0.0.1 LEMONADE_BASE_URL does not enable remote mode."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertFalse(cmd.remote)

    @patch.dict(
        "os.environ",
        {"LEMONADE_BASE_URL": "http://localhost:8000/api/v1"},
    )
    def test_explicit_remote_flag_overrides_localhost(self):
        """Test that --remote flag takes effect even with localhost URL."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True, remote=True)
        self.assertTrue(cmd.remote)

    @patch.dict("os.environ", {}, clear=False)
    def test_no_env_var_no_flag_remote_false(self):
        """Test that without env var or flag, remote stays False."""
        import os

        from gaia.installer.init_command import InitCommand

        os.environ.pop("LEMONADE_BASE_URL", None)
        cmd = InitCommand(profile="minimal", yes=True)
        self.assertFalse(cmd.remote)
        self.assertIsNone(cmd._lemonade_base_url)


class TestPullModelsViaApi(unittest.TestCase):
    """Test _pull_models_via_api method."""

    def _make_cmd(self):
        """Create an InitCommand instance for testing."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        return cmd

    def test_successful_download(self):
        """Test successful model download via API."""
        cmd = self._make_cmd()
        mock_client = MagicMock()
        mock_client.pull_model_stream.return_value = iter(
            [
                {
                    "event": "progress",
                    "percent": 50,
                    "bytes_downloaded": 100,
                    "bytes_total": 200,
                },
                {"event": "complete"},
            ]
        )
        mock_client.check_model_available.return_value = True

        result = cmd._pull_models_via_api(mock_client, ["Qwen3-0.6B-GGUF"])
        self.assertTrue(result)
        mock_client.pull_model_stream.assert_called_once_with(
            model_name="Qwen3-0.6B-GGUF"
        )

    def test_error_event_returns_false(self):
        """Test that an error event causes failure."""
        cmd = self._make_cmd()
        mock_client = MagicMock()
        mock_client.pull_model_stream.return_value = iter(
            [{"event": "error", "error": "disk full"}]
        )

        result = cmd._pull_models_via_api(mock_client, ["Qwen3-0.6B-GGUF"])
        self.assertFalse(result)

    def test_exception_returns_false(self):
        """Test that an exception causes failure."""
        cmd = self._make_cmd()
        mock_client = MagicMock()
        mock_client.pull_model_stream.side_effect = ConnectionError("unreachable")

        result = cmd._pull_models_via_api(mock_client, ["Qwen3-0.6B-GGUF"])
        self.assertFalse(result)


class TestDownloadModelsFallback(unittest.TestCase):
    """Test _download_models fallback to API when CLI unavailable."""

    @patch("gaia.installer.init_command.LemonadeInstaller")
    @patch.dict(
        "os.environ",
        {"LEMONADE_BASE_URL": "http://192.168.1.100:8000/api/v1"},
    )
    def test_remote_mode_uses_api(self, mock_installer_class):
        """Test that remote mode routes to _pull_models_via_api."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertTrue(cmd.remote)

        cmd._pull_models_via_api = MagicMock(return_value=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_required_models.return_value = []
            mock_client.check_model_available.return_value = False
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            cmd._pull_models_via_api.assert_called_once()

    @patch("gaia.installer.init_command.LemonadeInstaller")
    def test_no_cli_falls_back_to_api(self, mock_installer_class):
        """Test that missing CLI falls back to _pull_models_via_api."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)

        cmd._find_lemonade_server = MagicMock(return_value=None)
        cmd._pull_models_via_api = MagicMock(return_value=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_required_models.return_value = []
            mock_client.check_model_available.return_value = False
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            cmd._pull_models_via_api.assert_called_once()


if __name__ == "__main__":
    unittest.main()
