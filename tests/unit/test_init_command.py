# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for the gaia init command.

These tests use mocking to avoid actual network calls and installations.
"""

import sys
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

    @patch("gaia.installer.lemonade_installer.resolve_lemonade")
    def test_check_installation_not_found(self, mock_resolve):
        """check_installation when resolve_lemonade() finds nothing (AC2 regression
        guard — legacy-style 'not found' still returns installed=False)."""
        from gaia.llm.lemonade_launcher import LemonadeTooling

        mock_resolve.return_value = LemonadeTooling(
            found=False, kind="legacy", client_path=None, server_launcher=None
        )
        installer = LemonadeInstaller()
        info = installer.check_installation()
        self.assertFalse(info.installed)
        self.assertIn("not found", info.error)

    @patch("gaia.installer.lemonade_installer.get_installed_version")
    @patch("gaia.installer.lemonade_installer.resolve_lemonade")
    def test_check_installation_found(self, mock_resolve, mock_get_version):
        """check_installation when resolve_lemonade() finds a legacy install
        (AC2 — legacy path unchanged after the refactor)."""
        from gaia.llm.lemonade_launcher import LemonadeTooling

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="/usr/bin/lemonade-server",
            server_launcher="/usr/bin/lemonade-server",
        )
        mock_get_version.return_value = "9.1.4"
        installer = LemonadeInstaller()
        info = installer.check_installation()
        self.assertTrue(info.installed)
        self.assertEqual(info.version, "9.1.4")
        self.assertEqual(info.path, "/usr/bin/lemonade-server")

    @patch("gaia.installer.lemonade_installer.get_installed_version")
    @patch("gaia.installer.lemonade_installer.resolve_lemonade")
    def test_check_installation_found_modern(self, mock_resolve, mock_get_version):
        """AC1: modern-only environment -> check_installation() returns
        installed=True with the version parsed from the modern client."""
        from gaia.llm.lemonade_launcher import LemonadeTooling

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="modern",
            client_path=r"C:\lemonade_server\bin\lemonade.exe",
            server_launcher=r"C:\lemonade_server\bin\LemonadeServer.exe",
        )
        mock_get_version.return_value = "10.7.0"
        installer = LemonadeInstaller()
        info = installer.check_installation()
        self.assertTrue(info.installed)
        self.assertEqual(info.version, "10.7.0")
        self.assertEqual(info.path, r"C:\lemonade_server\bin\lemonade.exe")


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

    @patch("gaia.installer.init_command.LemonadeInstaller")
    def test_bracketed_text_not_eaten_by_rich_markup(self, _mock_installer_class):
        """Bracketed tokens like '[rag]' must survive Rich rendering (issue #2339).

        The success/warning/error/step helpers embed the message inside Rich
        markup, so an unescaped '[rag]' was parsed as a style tag and dropped,
        leaving users a broken 'uv pip install "amd-gaia"' instruction.
        """
        import io

        from gaia.installer import init_command as ic

        if not ic.RICH_AVAILABLE:
            self.skipTest("rich not installed")

        cmd = ic.InitCommand(profile="rag", yes=True)

        for helper in ("_print_success", "_print_warning", "_print_error"):
            buf = io.StringIO()
            cmd.console = ic.Console(file=buf, force_terminal=False, width=200)
            getattr(cmd, helper)(
                'Could not install [rag] extras. Run: uv pip install "amd-gaia[rag]"'
            )
            out = buf.getvalue()
            self.assertIn("[rag]", out, f"{helper} dropped bracketed token")
            self.assertIn('"amd-gaia[rag]"', out, f"{helper} dropped install spec")

        buf = io.StringIO()
        cmd.console = ic.Console(file=buf, force_terminal=False, width=200)
        cmd._print_step(4, 5, "Installing [rag] dependencies")
        self.assertIn("[rag]", buf.getvalue())


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

    def test_minimal_profile_uses_gemma_4_e4b(self):
        """Test that minimal profile uses Gemma-4-E4B model."""
        from gaia.installer.init_command import INIT_PROFILES

        minimal = INIT_PROFILES["minimal"]
        self.assertIn("Gemma-4-E4B-it-GGUF", minimal["models"])

    def test_profiles_have_required_keys(self):
        """Test that all profiles have required keys."""
        from gaia.installer.init_command import INIT_PROFILES

        required_keys = ["description", "agent", "models", "approx_size"]
        for name, profile in INIT_PROFILES.items():
            for key in required_keys:
                self.assertIn(key, profile, f"Profile '{name}' missing key '{key}'")

    def test_email_profile_defined(self):
        """`gaia init --profile email` downloads the email triage model."""
        from gaia.installer.init_command import INIT_PROFILES

        self.assertIn("email", INIT_PROFILES)
        email = INIT_PROFILES["email"]
        self.assertEqual(email["agent"], "email")
        self.assertIn("Gemma-4-E4B-it-GGUF", email["models"])

    def test_email_profile_min_version_locksteps_with_agent(self):
        """The email init profile's min Lemonade version must match the email
        agent's runtime minimum — readiness (/v1/email/init) and the installer
        must agree on what 'compatible' means."""
        from gaia.installer.init_command import INIT_PROFILES

        try:
            from gaia_agent_email.version import MIN_LEMONADE_VERSION
        except ImportError:
            self.skipTest("gaia_agent_email (standalone email wheel) not installed")
        self.assertEqual(
            INIT_PROFILES["email"]["min_lemonade_version"], MIN_LEMONADE_VERSION
        )

    def test_email_profile_is_a_cli_choice(self):
        """The init subparser must accept --profile email (argparse choices)."""
        from gaia.cli import build_parser

        ns = build_parser().parse_args(["init", "--profile", "email"])
        self.assertEqual(ns.profile, "email")


class TestRemoteAutoDetection(unittest.TestCase):
    """Test auto-detection of remote mode from LEMONADE_BASE_URL."""

    @patch.dict(
        "os.environ", {"LEMONADE_BASE_URL": "http://192.168.1.100:13305/api/v1"}
    )
    def test_remote_url_sets_remote_true(self):
        """Test that a non-localhost LEMONADE_BASE_URL enables remote mode."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertTrue(cmd.remote)
        self.assertEqual(cmd._lemonade_base_url, "http://192.168.1.100:13305/api/v1")

    @patch.dict("os.environ", {"LEMONADE_BASE_URL": "http://localhost:13305/api/v1"})
    def test_localhost_url_keeps_remote_false(self):
        """Test that localhost LEMONADE_BASE_URL does not enable remote mode."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertFalse(cmd.remote)

    @patch.dict("os.environ", {"LEMONADE_BASE_URL": "http://127.0.0.1:13305/api/v1"})
    def test_loopback_url_keeps_remote_false(self):
        """Test that 127.0.0.1 LEMONADE_BASE_URL does not enable remote mode."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertFalse(cmd.remote)

    @patch.dict(
        "os.environ",
        {"LEMONADE_BASE_URL": "http://localhost:13305/api/v1"},
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


class TestDownloadModels(unittest.TestCase):
    """Test _download_models delegates to LemonadeClient."""

    @patch("gaia.installer.init_command.LemonadeInstaller")
    def test_calls_ensure_model_downloaded_per_model(self, mock_installer_class):
        """Test that ensure_model_downloaded is called for each model."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_required_models.return_value = []
            mock_client.check_model_available.return_value = False
            mock_client.ensure_model_downloaded.return_value = True
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            # minimal profile has Qwen3-0.6B-GGUF plus DEFAULT_MODEL_NAME
            self.assertGreaterEqual(mock_client.ensure_model_downloaded.call_count, 1)

    @patch("gaia.installer.init_command.LemonadeInstaller")
    def test_returns_false_on_download_failure(self, mock_installer_class):
        """Test that a failed download returns False."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_required_models.return_value = []
            mock_client.check_model_available.return_value = False
            mock_client.ensure_model_downloaded.return_value = False
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertFalse(result)

    @patch("gaia.installer.init_command.LemonadeInstaller")
    @patch.dict(
        "os.environ",
        {"LEMONADE_BASE_URL": "http://192.168.1.100:13305/api/v1"},
    )
    def test_remote_mode_uses_ensure_model_downloaded(self, mock_installer_class):
        """Test that remote mode delegates to ensure_model_downloaded."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertTrue(cmd.remote)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_required_models.return_value = []
            mock_client.check_model_available.return_value = False
            mock_client.ensure_model_downloaded.return_value = True
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            self.assertGreaterEqual(mock_client.ensure_model_downloaded.call_count, 1)

    @patch("gaia.installer.init_command.LemonadeInstaller")
    def test_force_models_deletes_before_download(self, mock_installer_class):
        """Test that --force-models deletes models before re-downloading."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True, force_models=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_required_models.return_value = []
            mock_client.check_model_available.return_value = True
            mock_client.ensure_model_downloaded.return_value = True
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            # Should have called delete_model for each model before downloading
            self.assertGreaterEqual(mock_client.delete_model.call_count, 1)
            self.assertGreaterEqual(mock_client.ensure_model_downloaded.call_count, 1)

    @patch("gaia.installer.init_command.LemonadeInstaller")
    def test_npu_profile_pulls_builtin_model_without_recipe(self, mock_installer_class):
        """NPU/FLM models are built-in; pulling with a recipe 400s (#1655).

        The npu profile must download both the FLM chat model and the FLM-native
        embedder (#1744) via ensure_model_downloaded (pull by name), never
        pull_model(recipe=...), which Lemonade rejects unless the name carries a
        ``user.`` prefix.
        """
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="npu", yes=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.ensure_model_downloaded.return_value = True
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            pulled = {
                c.args[0] for c in mock_client.ensure_model_downloaded.call_args_list
            }
            self.assertEqual(pulled, {"gemma4-it-e2b-FLM", "embed-gemma-300m-FLM"})
            # Regression guard: no recipe-bearing pull_model call.
            mock_client.pull_model.assert_not_called()


class TestInstallPipExtras(unittest.TestCase):
    """Test _install_pip_extras frontend selection and messaging."""

    def _make_cmd(self, profile):
        from gaia.installer.init_command import InitCommand

        with patch("gaia.installer.init_command.LemonadeInstaller"):
            return InitCommand(profile=profile, yes=True)

    def test_no_extras_skips_install(self):
        """Profiles without pip_extras short-circuit without shelling out."""
        cmd = self._make_cmd("minimal")  # minimal declares no pip_extras
        with patch("subprocess.run") as mock_run:
            self.assertTrue(cmd._install_pip_extras())
            mock_run.assert_not_called()

    def test_standalone_uv_attempted_first(self):
        """The standalone ``uv`` binary leads the install attempts.

        uv-created venvs ship neither pip nor the uv module, so a bare
        ``uv pip install`` is the only frontend that works inside them.
        """
        cmd = self._make_cmd("rag")  # rag pulls the [rag] extra
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Name: amd-gaia\n"  # non-editable
            return result

        with patch("subprocess.run", side_effect=fake_run):
            self.assertTrue(cmd._install_pip_extras())

        install_calls = [c for c in calls if "install" in c]
        self.assertTrue(install_calls, "expected an install attempt")
        self.assertEqual(
            install_calls[0][:2],
            ["uv", "pip"],
            "standalone uv must be the first install frontend",
        )

    def test_warning_has_no_doubled_pip_install(self):
        """The fallback warning must not print 'pip install pip install'."""
        cmd = self._make_cmd("rag")
        warnings = []
        cmd._print_warning = lambda msg: warnings.append(msg)
        cmd._print_success = lambda msg: None

        def fail_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 1  # every frontend fails
            result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=fail_run):
            self.assertTrue(cmd._install_pip_extras())

        joined = " ".join(warnings)
        self.assertTrue(warnings, "expected a fallback warning")
        self.assertNotIn("pip install pip install", joined)
        # #2358: the fallback message must work in a stock venv with no `uv`
        # on PATH -- not a bare `uv pip install` (the same dead end
        # install_hints.source_install_command was fixed for).
        self.assertNotIn("uv pip install", joined)
        self.assertIn(f'{sys.executable} -m pip install "amd-gaia[rag]"', joined)


class TestVersionCompatibility(unittest.TestCase):
    """Test _check_version_compatibility version policy.

    Version policy:
    - Newer or equal: always accepted (no downgrade prompt)
    - Older >= profile minimum: accepted with optional upgrade
    - Older < profile minimum: upgrade required
    """

    def _make_cmd(self, profile="minimal"):
        """Create an InitCommand with mocked installer."""
        from gaia.installer.init_command import InitCommand

        with patch("gaia.installer.init_command.LemonadeInstaller"):
            cmd = InitCommand(profile=profile, yes=True)
        return cmd

    def test_newer_version_accepted(self):
        """v10.3.0 installed, v10.2.0 expected -> accepted without prompt."""
        cmd = self._make_cmd()
        info = LemonadeInfo(installed=True, version="10.3.0")
        result = cmd._check_version_compatibility(info)
        self.assertTrue(result)

    def test_same_version_accepted(self):
        """Same version -> accepted."""
        cmd = self._make_cmd()
        info = LemonadeInfo(installed=True, version=LEMONADE_VERSION)
        result = cmd._check_version_compatibility(info)
        self.assertTrue(result)

    def test_newer_major_version_accepted(self):
        """v11.0.0 installed, v10.2.0 expected -> accepted."""
        cmd = self._make_cmd()
        info = LemonadeInfo(installed=True, version="11.0.0")
        result = cmd._check_version_compatibility(info)
        self.assertTrue(result)

    def test_older_version_meets_minimum_accepted_in_ci(self):
        """v10.2.1 installed, v10.2.0 expected, min 10.2.0 -> accepted in CI (--yes)."""
        cmd = self._make_cmd(profile="minimal")
        info = LemonadeInfo(installed=True, version="10.2.1")
        result = cmd._check_version_compatibility(info)
        self.assertTrue(result)

    def test_older_version_below_minimum_triggers_upgrade(self):
        """v8.5.0 installed, min 9.0.4 -> triggers upgrade in CI (--yes)."""
        cmd = self._make_cmd(profile="minimal")
        # Mock the upgrade to succeed
        cmd._upgrade_lemonade = MagicMock(return_value=True)
        info = LemonadeInfo(installed=True, version="8.5.0")
        result = cmd._check_version_compatibility(info)
        # In CI mode (yes=True), should auto-upgrade
        cmd._upgrade_lemonade.assert_called_once_with("8.5.0")
        self.assertTrue(result)

    def test_unparseable_version_accepted(self):
        """Unparseable version -> accepted (graceful fallback)."""
        cmd = self._make_cmd()
        info = LemonadeInfo(installed=True, version="unknown")
        result = cmd._check_version_compatibility(info)
        self.assertTrue(result)

    def test_no_downgrade_prompt_for_newer_version(self):
        """Newer version should never trigger _upgrade_lemonade."""
        cmd = self._make_cmd()
        cmd._upgrade_lemonade = MagicMock(return_value=True)
        info = LemonadeInfo(installed=True, version="10.3.0")
        cmd._check_version_compatibility(info)
        cmd._upgrade_lemonade.assert_not_called()


class TestNeedsInstallConsistency(unittest.TestCase):
    """Verify that needs_install and _check_version_compatibility agree."""

    def test_newer_version_needs_no_install(self):
        """LemonadeInstaller.needs_install returns False for newer versions."""
        installer = LemonadeInstaller(target_version="9.3.0")
        info = LemonadeInfo(installed=True, version="9.3.4")
        self.assertFalse(installer.needs_install(info))

    def test_older_version_needs_install(self):
        """LemonadeInstaller.needs_install returns True for older versions."""
        installer = LemonadeInstaller(target_version="9.3.0")
        info = LemonadeInfo(installed=True, version="9.2.0")
        self.assertTrue(installer.needs_install(info))


class TestEnsureLemonadeInstalledSkipsWhenPresent(unittest.TestCase):
    """End-to-end check that _ensure_lemonade_installed() does NOT trigger a
    download or msiexec call when Lemonade is already installed.

    This locks in the contract that the bundled NSIS MSI install (pre-step)
    plus a subsequent ``gaia init`` invocation must be a no-op for Lemonade.
    """

    def _make_cmd(self, installed_info, profile="minimal"):
        """Build an InitCommand whose installer.check_installation() returns info."""
        from gaia.installer.init_command import InitCommand

        with patch("gaia.installer.init_command.LemonadeInstaller") as mock_cls:
            mock_installer = MagicMock()
            mock_installer.is_platform_supported.return_value = True
            mock_installer.get_platform_name.return_value = "Windows"
            mock_installer.check_installation.return_value = installed_info
            # If anything tries to download or install, blow up the test
            mock_installer.download_installer.side_effect = AssertionError(
                "download_installer must NOT be called when already installed"
            )
            mock_installer.install.side_effect = AssertionError(
                "install must NOT be called when already installed"
            )
            mock_cls.return_value = mock_installer
            cmd = InitCommand(profile=profile, yes=True)
        return cmd, mock_installer

    @patch("subprocess.run")
    @patch("urllib.request.urlretrieve")
    @patch("urllib.request.urlopen")
    def test_skip_when_installed_at_target_version(
        self, mock_urlopen, mock_urlretrieve, mock_subprocess
    ):
        """Case 2: installed at LEMONADE_VERSION -> needs_install False, no download."""
        info = LemonadeInfo(
            installed=True,
            version=LEMONADE_VERSION,
            path="/usr/bin/lemonade-server",
        )
        # Sanity: the installer's own needs_install agrees
        installer = LemonadeInstaller()
        self.assertFalse(installer.needs_install(info))

        cmd, mock_installer = self._make_cmd(info)
        result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.download_installer.assert_not_called()
        mock_installer.install.assert_not_called()
        # No external download attempted
        mock_urlopen.assert_not_called()
        mock_urlretrieve.assert_not_called()
        # No msiexec invoked
        for call in mock_subprocess.call_args_list:
            args = call.args[0] if call.args else []
            if isinstance(args, (list, tuple)) and args:
                self.assertNotIn(
                    "msiexec",
                    str(args[0]).lower(),
                    f"msiexec must not be invoked: {args}",
                )

    @patch("subprocess.run")
    @patch("urllib.request.urlretrieve")
    @patch("urllib.request.urlopen")
    def test_skip_when_installed_at_newer_version(
        self, mock_urlopen, mock_urlretrieve, mock_subprocess
    ):
        """Case 4 (CRITICAL): installed at NEWER version (e.g. 11.0.0) -> no download.

        Scenario: the bundled NSIS installer dropped Lemonade v10.2.0 but the
        user has since upgraded to v11.0.0. ``gaia init`` must treat this as
        compatible (newer is fine), NOT downgrade or re-download.
        """
        # Pick a version definitively newer than LEMONADE_VERSION (10.2.0)
        newer_version = "11.0.0"
        info = LemonadeInfo(
            installed=True,
            version=newer_version,
            path="/usr/bin/lemonade-server",
        )
        installer = LemonadeInstaller()
        self.assertFalse(
            installer.needs_install(info),
            "needs_install must return False for newer version",
        )

        cmd, mock_installer = self._make_cmd(info)
        result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.download_installer.assert_not_called()
        mock_installer.install.assert_not_called()
        mock_urlopen.assert_not_called()
        mock_urlretrieve.assert_not_called()

    def test_older_version_meeting_minimum_does_not_redownload_in_ci(self):
        """Case 3 (above minimum, --yes): accepted, no install."""
        # 10.2.1 is above both target (10.2.0) and profile minimum (10.2.0) — no install needed
        info = LemonadeInfo(
            installed=True,
            version="10.2.1",
            path="/usr/bin/lemonade-server",
        )
        cmd, mock_installer = self._make_cmd(info, profile="minimal")
        result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.download_installer.assert_not_called()
        mock_installer.install.assert_not_called()

    def test_older_version_below_minimum_triggers_install_in_ci(self):
        """Case 3b: installed << profile minimum, --yes -> upgrade is invoked.

        This case DOES download — verify the upgrade path is taken.
        """
        from gaia.installer.init_command import InitCommand

        info = LemonadeInfo(
            installed=True,
            version="8.0.0",  # well below profile minimum 9.0.0
            path="/usr/bin/lemonade-server",
        )

        with patch("gaia.installer.init_command.LemonadeInstaller") as mock_cls:
            mock_installer = MagicMock()
            mock_installer.is_platform_supported.return_value = True
            mock_installer.get_platform_name.return_value = "Windows"
            mock_installer.check_installation.return_value = info
            mock_cls.return_value = mock_installer
            cmd = InitCommand(profile="minimal", yes=True)
            # Stub upgrade path so it doesn't try to actually run anything
            cmd._upgrade_lemonade = MagicMock(return_value=True)

            result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        cmd._upgrade_lemonade.assert_called_once_with("8.0.0")

    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_skip_install_when_probe_succeeds(self, mock_client_cls):
        """Lemonade running (AUR/systemd) but binary not in PATH → probe short-circuits."""
        mock_client = MagicMock()
        # Probe uses _send_request with a short timeout; mock that to return
        # a healthy response so the probe short-circuits installation.
        mock_client._send_request.return_value = {"status": "ok"}
        mock_client.health_check.return_value = {"status": "ok"}
        mock_client_cls.return_value = mock_client

        cmd, mock_installer = self._make_cmd(
            LemonadeInfo(installed=False, version=None, path=None)
        )
        result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.check_installation.assert_not_called()
        mock_installer.download_installer.assert_not_called()

    @patch.dict("os.environ", {"LEMONADE_BASE_URL": "http://127.0.0.1:13305/api/v1"})
    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_skip_install_when_env_var_set_and_probe_succeeds(self, mock_client_cls):
        """If LEMONADE_BASE_URL is set to a reachable server, probe short-circuits."""
        mock_client = MagicMock()
        mock_client._send_request.return_value = {"status": "ok"}
        mock_client.health_check.return_value = {"status": "ok"}
        mock_client_cls.return_value = mock_client

        cmd, mock_installer = self._make_cmd(
            LemonadeInfo(installed=False, version=None, path=None)
        )
        result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.check_installation.assert_not_called()
        mock_installer.download_installer.assert_not_called()

    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_falls_through_to_binary_check_when_probe_fails(self, mock_client_cls):
        """No running server → probe raises, falls through to check_installation."""
        mock_client = MagicMock()
        from gaia.llm.lemonade_client import LemonadeClientError

        # Simulate _send_request raising the client's error (real-world path)
        mock_client._send_request.side_effect = LemonadeClientError(
            "connection refused"
        )
        mock_client.health_check.side_effect = LemonadeClientError("connection refused")
        mock_client_cls.return_value = mock_client

        info = LemonadeInfo(
            installed=True, version=LEMONADE_VERSION, path="/usr/bin/lemonade-server"
        )
        cmd, mock_installer = self._make_cmd(info)
        result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.check_installation.assert_called_once()

    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_falls_through_when_health_check_raises_client_error(self, mock_client_cls):
        """LemonadeClientError (real-world path) also falls through to binary check."""
        from gaia.llm.lemonade_client import LemonadeClientError

        mock_client = MagicMock()
        mock_client._send_request.side_effect = LemonadeClientError(
            "connection refused"
        )
        mock_client.health_check.side_effect = LemonadeClientError("connection refused")
        mock_client_cls.return_value = mock_client

        info = LemonadeInfo(
            installed=True, version=LEMONADE_VERSION, path="/usr/bin/lemonade-server"
        )
        cmd, mock_installer = self._make_cmd(info)
        result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.check_installation.assert_called_once()


class TestLegacyFallback(unittest.TestCase):
    """Regression: when Lemonade is NOT installed, gaia init falls through to
    the runtime install path.  Linux uses PPA; Windows uses download+install.
    """

    def test_not_installed_on_linux_proceeds_to_install_via_ppa(self):
        """On Linux: check_installation not-installed -> install called WITHOUT download."""
        from gaia.installer.init_command import InitCommand

        info = LemonadeInfo(installed=False, error="lemonade-server not found in PATH")

        with patch("gaia.installer.init_command.LemonadeInstaller") as mock_cls:
            mock_installer = MagicMock()
            mock_installer.is_platform_supported.return_value = True
            mock_installer.system = "linux"
            mock_installer.install.return_value = InstallResult(
                success=True, version=LEMONADE_VERSION, message="ok"
            )
            mock_installer.check_installation.side_effect = [
                info,
                LemonadeInfo(
                    installed=True,
                    version=LEMONADE_VERSION,
                    path="/usr/bin/lemonade-server",
                ),
            ]
            mock_cls.return_value = mock_installer

            cmd = InitCommand(profile="minimal", yes=True)
            result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.download_installer.assert_not_called()
        mock_installer.install.assert_called_once()

    def test_not_installed_on_windows_proceeds_to_download_and_install(self):
        """On Windows: check_installation not-installed -> download + install called."""
        from pathlib import Path as _P

        from gaia.installer.init_command import InitCommand

        info = LemonadeInfo(installed=False, error="lemonade-server not found in PATH")

        with patch("gaia.installer.init_command.LemonadeInstaller") as mock_cls:
            mock_installer = MagicMock()
            mock_installer.is_platform_supported.return_value = True
            mock_installer.system = "windows"
            mock_installer.download_installer.return_value = _P("/tmp/lemonade.msi")
            mock_installer.install.return_value = InstallResult(
                success=True, version=LEMONADE_VERSION, message="ok"
            )
            mock_installer.check_installation.side_effect = [
                info,
                LemonadeInfo(
                    installed=True,
                    version=LEMONADE_VERSION,
                    path="C:\\lemonade-server.exe",
                ),
            ]
            mock_cls.return_value = mock_installer

            cmd = InitCommand(profile="minimal", yes=True)
            result = cmd._ensure_lemonade_installed()

        self.assertTrue(result)
        mock_installer.download_installer.assert_called_once()
        mock_installer.install.assert_called_once()


class TestInstallViaPpa(unittest.TestCase):
    """Tests for _install_via_ppa — the Linux PPA-based install path."""

    def _make_linux_installer(self):
        with patch("platform.system", return_value="Linux"):
            return LemonadeInstaller(target_version="10.2.0")

    def _ok_run(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    def _fail_run(self, stdout="", stderr="error output"):
        result = MagicMock()
        result.returncode = 1
        result.stdout = stdout
        result.stderr = stderr
        return result

    @patch("os.geteuid", return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_runs_commands_in_order(
        self, mock_run, mock_which, mock_geteuid
    ):
        """Three subprocess calls in order: add-apt-repository, apt-get update, apt-get install."""
        import subprocess as _sub

        installer = self._make_linux_installer()
        mock_run.return_value = self._ok_run()

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            with patch.object(installer, "check_installation") as mock_check:
                mock_check.return_value = LemonadeInfo(
                    installed=True, version="10.2.0", path="/usr/bin/lemonade-server"
                )
                result = installer._install_via_ppa(non_interactive=False)

        self.assertTrue(result.success)
        self.assertEqual(mock_run.call_count, 3)

        calls = mock_run.call_args_list
        first_cmd = calls[0][0][0]
        self.assertIn("sudo", first_cmd)
        self.assertIn("add-apt-repository", first_cmd)

        second_cmd = calls[1][0][0]
        self.assertIn("apt-get", second_cmd)
        self.assertIn("update", second_cmd)

        third_cmd = calls[2][0][0]
        self.assertIn("apt-get", third_cmd)
        self.assertIn("install", third_cmd)
        self.assertIn("lemonade-server", third_cmd)

        for call in calls:
            self.assertEqual(call[1].get("stdin"), _sub.DEVNULL)

    @patch("os.geteuid", return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_noninteractive_sets_env_and_devnull_stdin(
        self, mock_run, mock_which, mock_geteuid
    ):
        """non_interactive=True: DEBIAN_FRONTEND=noninteractive and stdin=DEVNULL on all calls."""
        import subprocess as _sub

        installer = self._make_linux_installer()

        sudo_ok = self._ok_run()
        install_ok = self._ok_run()
        mock_run.side_effect = [sudo_ok, install_ok, install_ok, install_ok]

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            with patch.object(installer, "check_installation") as mock_check:
                mock_check.return_value = LemonadeInfo(
                    installed=True, version="10.2.0", path="/usr/bin/lemonade-server"
                )
                result = installer._install_via_ppa(non_interactive=True)

        self.assertTrue(result.success)
        for call in mock_run.call_args_list:
            self.assertEqual(call[1].get("stdin"), _sub.DEVNULL)
            env = call[1].get("env", {})
            if call[0][0] != ["sudo", "-n", "true"]:
                self.assertEqual(env.get("DEBIAN_FRONTEND"), "noninteractive")

    @patch("os.geteuid", return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_sudo_password_required_returns_clear_error(
        self, mock_run, mock_which, mock_geteuid
    ):
        """non_interactive+not-root+sudo requires password -> clear error, not timeout."""
        installer = self._make_linux_installer()
        mock_run.return_value = self._fail_run(stderr="sudo: a password is required")

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            result = installer._install_via_ppa(non_interactive=True)

        self.assertFalse(result.success)
        self.assertIn("sudo", result.error.lower())
        self.assertIn("passwordless", result.error.lower())
        mock_run.assert_called_once()

    @patch("os.geteuid", return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_apt_install_failure_returns_actionable_error(
        self, mock_run, mock_which, mock_geteuid
    ):
        """apt-get install failure: error names step, stdout+stderr, docs URL."""
        installer = self._make_linux_installer()

        ok = self._ok_run()
        fail = self._fail_run(stdout="E: Package not found", stderr="dpkg error")
        mock_run.side_effect = [ok, ok, fail]

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            result = installer._install_via_ppa(non_interactive=False)

        self.assertFalse(result.success)
        self.assertIn("lemonade-server", result.error)
        self.assertIn("amd-gaia.ai", result.error)

    @patch("os.geteuid", return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_unsupported_distro_returns_clear_error(
        self, mock_run, mock_which, mock_geteuid
    ):
        """_check_linux_version returns error string -> early return with that message."""
        installer = self._make_linux_installer()
        version_msg = "Requires Ubuntu 24.04+. Detected: Ubuntu 22.04"

        with patch.object(
            LemonadeInstaller, "_check_linux_version", return_value=version_msg
        ):
            result = installer._install_via_ppa(non_interactive=False)

        self.assertFalse(result.success)
        self.assertIn("Ubuntu 22.04", result.error)
        mock_run.assert_not_called()

    @patch("os.geteuid", return_value=1000)
    @patch("shutil.which", return_value=None)
    @patch("subprocess.run")
    def test_install_via_ppa_missing_add_apt_repository_clear_error(
        self, mock_run, mock_which, mock_geteuid
    ):
        """add-apt-repository missing -> error mentions software-properties-common."""
        installer = self._make_linux_installer()

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            result = installer._install_via_ppa(non_interactive=False)

        self.assertFalse(result.success)
        self.assertIn("software-properties-common", result.error)
        mock_run.assert_not_called()

    @patch("os.geteuid", return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_returns_real_version_from_check_installation(
        self, mock_run, mock_which, mock_geteuid
    ):
        """Returned version comes from check_installation(), not self.target_version."""
        installer = self._make_linux_installer()
        mock_run.return_value = self._ok_run()

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            with patch.object(installer, "check_installation") as mock_check:
                mock_check.return_value = LemonadeInfo(
                    installed=True, version="10.3.0", path="/usr/bin/lemonade-server"
                )
                result = installer._install_via_ppa(non_interactive=False)

        self.assertTrue(result.success)
        self.assertEqual(result.version, "10.3.0")
        self.assertNotEqual(result.version, installer.target_version)

    @patch("platform.system", return_value="Linux")
    def test_install_dispatches_to_ppa_on_linux(self, mock_system):
        """install() on Linux calls _install_via_ppa without requiring installer_path."""
        installer = LemonadeInstaller()

        expected = InstallResult(success=True, version="10.2.0", message="via ppa")
        with patch.object(
            installer, "_install_via_ppa", return_value=expected
        ) as mock_ppa:
            result = installer.install(silent=True)

        mock_ppa.assert_called_once_with(non_interactive=True)
        self.assertTrue(result.success)

    @patch("platform.system", return_value="Windows")
    def test_install_windows_still_requires_installer_path(self, mock_system):
        """install() on Windows with missing path returns failure immediately."""
        from pathlib import Path

        installer = LemonadeInstaller()
        result = installer.install(
            installer_path=Path("/nonexistent_does_not_exist.msi")
        )

        self.assertFalse(result.success)
        self.assertIn("not found", result.error.lower())


class TestUpgradeLemonadeOnLinux(unittest.TestCase):
    """Verify _upgrade_lemonade routes through _install_lemonade which uses PPA on Linux."""

    def test_upgrade_lemonade_on_linux_uses_ppa(self):
        """_upgrade_lemonade calls _install_lemonade which routes Linux through install(None)."""
        from gaia.installer.init_command import InitCommand

        with patch("gaia.installer.init_command.LemonadeInstaller") as mock_cls:
            mock_installer = MagicMock()
            mock_installer.system = "linux"
            mock_installer.uninstall.return_value = InstallResult(
                success=True, message="uninstalled"
            )
            mock_installer.wait_for_msi_mutex.return_value = True
            mock_installer.install.return_value = InstallResult(
                success=True, version=LEMONADE_VERSION, message="ok"
            )
            mock_installer.check_installation.return_value = LemonadeInfo(
                installed=True,
                version=LEMONADE_VERSION,
                path="/usr/bin/lemonade-server",
            )
            mock_cls.return_value = mock_installer

            cmd = InitCommand(profile="minimal", yes=True)
            cmd._upgrade_lemonade("10.1.0")

        mock_installer.download_installer.assert_not_called()
        mock_installer.install.assert_called_once()
        call_kwargs = mock_installer.install.call_args
        installer_path_arg = (
            call_kwargs[0][0]
            if call_kwargs[0]
            else call_kwargs[1].get("installer_path")
        )
        self.assertIsNone(installer_path_arg)


class TestWaitForMsiMutex(unittest.TestCase):
    """Test wait_for_msi_mutex."""

    @patch("platform.system")
    def test_non_windows_returns_true(self, mock_system):
        """Non-Windows platforms skip MSI check."""
        mock_system.return_value = "Linux"
        installer = LemonadeInstaller()
        self.assertTrue(installer.wait_for_msi_mutex(timeout=1))

    @patch("platform.system")
    @patch("subprocess.run")
    def test_no_msiexec_returns_true(self, mock_run, mock_system):
        """Returns True immediately when no msiexec is running."""
        mock_system.return_value = "Windows"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="INFO: No tasks are running which match the specified criteria.",
        )
        installer = LemonadeInstaller()
        self.assertTrue(installer.wait_for_msi_mutex(timeout=5))


class TestFindProductCode(unittest.TestCase):
    """Test find_product_code."""

    @patch("platform.system")
    def test_non_windows_returns_none(self, mock_system):
        """Non-Windows platforms return None."""
        mock_system.return_value = "Linux"
        installer = LemonadeInstaller()
        self.assertIsNone(installer.find_product_code())

    @patch("platform.system")
    def test_finds_product_code_in_registry(self, mock_system):
        """Test registry lookup returns valid ProductCode GUID."""
        mock_system.return_value = "Windows"
        installer = LemonadeInstaller()

        product_code = "{12345678-1234-1234-1234-123456789012}"
        mock_winreg = MagicMock()
        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.HKEY_CURRENT_USER = 0x80000001
        mock_winreg.QueryInfoKey.return_value = (1, 0, 0)
        mock_winreg.EnumKey.return_value = product_code
        mock_winreg.QueryValueEx.return_value = ("Lemonade Server", 1)

        mock_key = MagicMock()
        mock_key.__enter__ = MagicMock(return_value=mock_key)
        mock_key.__exit__ = MagicMock(return_value=False)
        mock_winreg.OpenKey.return_value = mock_key

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            result = installer.find_product_code()
        self.assertEqual(result, product_code)

    @patch("platform.system")
    def test_skips_non_guid_subkeys(self, mock_system):
        """Test that non-GUID subkeys are skipped."""
        mock_system.return_value = "Windows"
        installer = LemonadeInstaller()

        mock_winreg = MagicMock()
        mock_winreg.HKEY_LOCAL_MACHINE = 0x80000002
        mock_winreg.HKEY_CURRENT_USER = 0x80000001
        mock_winreg.QueryInfoKey.return_value = (1, 0, 0)
        mock_winreg.EnumKey.return_value = "NotAGuid"
        mock_winreg.QueryValueEx.return_value = ("Lemonade Server", 1)

        mock_key = MagicMock()
        mock_key.__enter__ = MagicMock(return_value=mock_key)
        mock_key.__exit__ = MagicMock(return_value=False)
        mock_winreg.OpenKey.return_value = mock_key

        with patch.dict("sys.modules", {"winreg": mock_winreg}):
            result = installer.find_product_code()
        self.assertIsNone(result)


class TestEnsureServerRunningAutoStartFirst(unittest.TestCase):
    """AC5/AC6: `_ensure_server_running()` must attempt auto-start BEFORE
    ever falling back to the interactive "Please start Lemonade Server"
    prompt, in BOTH interactive (yes=False) and CI (yes=True) modes — and
    must never hang waiting on `input()` when auto-start fails in CI mode.

    Today's code has an indentation bug: in interactive mode, the
    "Please start Lemonade Server" prompt block runs unconditionally
    regardless of whether auto-start might have worked. These tests pin
    the FIXED, corrected behavior — auto-start attempted first in both
    modes, prompt reached only as a genuine fallback.
    """

    def _make_cmd(self, yes: bool):
        from gaia.installer.init_command import InitCommand

        with patch("gaia.installer.init_command.LemonadeInstaller"):
            cmd = InitCommand(profile="chat", yes=yes, skip_models=True)
        cmd.console = MagicMock()
        return cmd

    @patch("time.sleep")
    @patch("subprocess.Popen")
    @patch("gaia.installer.init_command.build_start_command")
    @patch("gaia.installer.init_command.resolve_lemonade")
    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_interactive_auto_start_success_never_calls_input(
        self,
        mock_client_cls,
        mock_resolve,
        mock_build_cmd,
        mock_popen,
        mock_sleep,
    ):
        """yes=False: server down then healthy after auto-start attempt ->
        returns True, Popen called exactly once, input() NEVER called, and
        no console.print call contains 'Please start Lemonade Server'."""
        from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="/usr/bin/lemonade-server",
            server_launcher="/usr/bin/lemonade-server",
        )
        mock_build_cmd.return_value = StartSpec(
            argv=["/usr/bin/lemonade-server", "serve", "--ctx-size", "32768"],
            env={},
        )
        mock_popen.return_value = MagicMock()

        client_instance = mock_client_cls.return_value
        client_instance.health_check.side_effect = [
            None,  # initial check: server down
            {"status": "ok"},  # after auto-start attempt: healthy
        ]

        cmd = self._make_cmd(yes=False)
        with patch("builtins.input") as mock_input:
            result = cmd._ensure_server_running()

        self.assertTrue(result)
        mock_popen.assert_called_once()
        mock_input.assert_not_called()

        for call in cmd.console.print.call_args_list:
            args = [str(a) for a in call.args]
            joined = " ".join(args)
            self.assertNotIn(
                "Please start Lemonade Server",
                joined,
                "auto-start succeeded — the manual prompt must never print",
            )

    @patch("time.sleep")
    @patch("subprocess.Popen")
    @patch("gaia.installer.init_command.build_start_command")
    @patch("gaia.installer.init_command.resolve_lemonade")
    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_interactive_auto_start_failure_falls_through_to_manual_prompt(
        self,
        mock_client_cls,
        mock_resolve,
        mock_build_cmd,
        mock_popen,
        mock_sleep,
    ):
        """yes=False: auto-start attempted but health never reports ok ->
        falls through to the manual prompt path (input() IS reached)."""
        from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="/usr/bin/lemonade-server",
            server_launcher="/usr/bin/lemonade-server",
        )
        mock_build_cmd.return_value = StartSpec(
            argv=["/usr/bin/lemonade-server", "serve", "--ctx-size", "32768"],
            env={},
        )
        mock_popen.return_value = MagicMock()

        client_instance = mock_client_cls.return_value
        # Never reports "ok" — auto-start attempt fails, then the manual
        # prompt's post-input() health check also fails.
        client_instance.health_check.return_value = None

        cmd = self._make_cmd(yes=False)
        with patch("builtins.input", return_value="") as mock_input:
            cmd._ensure_server_running()

        self.assertTrue(mock_input.called, "manual fallback prompt must be reached")

    @patch("time.sleep")
    @patch("subprocess.Popen")
    @patch("gaia.installer.init_command.build_start_command")
    @patch("gaia.installer.init_command.resolve_lemonade")
    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_ci_mode_auto_start_success_never_calls_input(
        self,
        mock_client_cls,
        mock_resolve,
        mock_build_cmd,
        mock_popen,
        mock_sleep,
    ):
        """AC6: yes=True, auto-start success -> returns True, prompt/input
        never reached (mirrors the interactive success assertions above)."""
        from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="/usr/bin/lemonade-server",
            server_launcher="/usr/bin/lemonade-server",
        )
        mock_build_cmd.return_value = StartSpec(
            argv=["/usr/bin/lemonade-server", "serve", "--ctx-size", "32768"],
            env={},
        )
        mock_popen.return_value = MagicMock()

        client_instance = mock_client_cls.return_value
        client_instance.health_check.side_effect = [
            None,
            {"status": "ok"},
        ]

        cmd = self._make_cmd(yes=True)
        with patch("builtins.input") as mock_input:
            result = cmd._ensure_server_running()

        self.assertTrue(result)
        mock_input.assert_not_called()

        for call in cmd.console.print.call_args_list:
            args = [str(a) for a in call.args]
            joined = " ".join(args)
            self.assertNotIn("Please start Lemonade Server", joined)

    @patch("time.sleep")
    @patch("subprocess.Popen")
    @patch("gaia.installer.init_command.build_start_command")
    @patch("gaia.installer.init_command.resolve_lemonade")
    @patch("gaia.llm.lemonade_client.LemonadeClient")
    def test_ci_mode_auto_start_timeout_returns_false_without_hanging(
        self,
        mock_client_cls,
        mock_resolve,
        mock_build_cmd,
        mock_popen,
        mock_sleep,
    ):
        """AC6 CRITICAL: yes=True, Popen succeeds but health-check polling
        never reports ok -> returns False, input() is NEVER called (the
        must-never-hang-in-CI case)."""
        from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

        mock_resolve.return_value = LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="/usr/bin/lemonade-server",
            server_launcher="/usr/bin/lemonade-server",
        )
        mock_build_cmd.return_value = StartSpec(
            argv=["/usr/bin/lemonade-server", "serve", "--ctx-size", "32768"],
            env={},
        )
        mock_popen.return_value = MagicMock()

        client_instance = mock_client_cls.return_value
        # Server never comes up, no matter how many times polled.
        client_instance.health_check.return_value = None

        cmd = self._make_cmd(yes=True)
        with patch("builtins.input") as mock_input:
            result = cmd._ensure_server_running()

        self.assertFalse(result)
        mock_input.assert_not_called()


# ---------------------------------------------------------------------------
# #2358: `gaia init --profile chat` must install the chat agent from the Hub
# (via gaia.hub.installer.install) when it isn't already importable, so
# `gaia chat` works right after `gaia init` on a plain `pip install amd-gaia`.
#
# ``gaia.hub.installer.install`` is patched at its OWN module
# (``gaia.hub.installer.install``), not at an alias imported into
# ``init_command``, because every other collaborator in this file
# (``LemonadeClient``, ``resolve_lemonade``, ``build_start_command``) is
# imported LAZILY inside method bodies in init_command.py and this test suite
# consistently patches those at their origin module (e.g.
# ``gaia.llm.lemonade_client.LemonadeClient`` above) rather than at an
# init_command-local alias -- the hub-install wiring is expected to follow
# the same lazy-import convention.
# ---------------------------------------------------------------------------


def _fake_catalog_result(agents):
    """Build a ``gaia.hub.catalog.CatalogResult`` listing *agents* (dicts with
    at least an ``id`` key), for mocking ``gaia.hub.catalog.load_index``."""
    from gaia.hub.catalog import CatalogResult

    return CatalogResult(agents=agents, offline=False, source="network")


class _HubInstallWiringTestBase(unittest.TestCase):
    """Shared run()-reaching harness for the #2358 hub-install wiring tests.

    Patches every `InitCommand.run()` step OTHER than the (not-yet-written)
    hub-install step, so `run()` can be exercised end-to-end deterministically
    without touching the network, a real Lemonade server, real pip, or the
    user's real ``~/.gaia/config.json``.
    """

    def _make_cmd(self, profile, **kwargs):
        from gaia.installer.init_command import InitCommand

        with patch("gaia.installer.init_command.LemonadeInstaller"):
            cmd = InitCommand(profile=profile, yes=True, skip_lemonade=True, **kwargs)
        return cmd

    def _patch_common_steps(self, cmd, order=None):
        """Patch every step so run() reaches (and passes through) the
        hub-install step deterministically. Records call order in *order*
        when provided (list of step-name strings).
        """

        def _tracked(name, retval=True):
            def _fn(*_a, **_k):
                if order is not None:
                    order.append(name)
                return retval

            return _fn

        patches = [
            patch.object(cmd, "_ensure_server_running", side_effect=_tracked("server")),
            patch.object(
                cmd, "_download_models", side_effect=_tracked("download_models")
            ),
            patch.object(
                cmd, "_install_pip_extras", side_effect=_tracked("pip_extras")
            ),
            patch.object(cmd, "_verify_setup", side_effect=_tracked("verify")),
            # NPU-only steps; harmless no-ops for profiles that don't declare
            # required_device/backend (run() only calls them when declared).
            patch.object(
                cmd, "_check_device_available", side_effect=_tracked("device_check")
            ),
            patch.object(
                cmd, "_install_backend", side_effect=_tracked("install_backend")
            ),
            patch("gaia.ui.build.ensure_webui_built", return_value=True),
            # Never touch the real user's ~/.gaia/config.json during a test.
            patch("gaia.config.GaiaConfig"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestHubInstallWiringChatProfile(_HubInstallWiringTestBase):
    """AC: `gaia init --profile chat` installs chat from the Hub when it
    isn't already importable, and skips the install when it already is."""

    def test_installs_chat_agent_when_not_available_and_published(self):
        cmd = self._make_cmd("chat")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result(
                    [{"id": "chat", "latest_version": "0.1.0"}]
                ),
            ),
            patch("gaia.hub.installer.install") as mock_install,
        ):
            mock_install.return_value = MagicMock(hot_registered=True)
            rc = cmd.run()

        self.assertEqual(rc, 0)
        mock_install.assert_called_once()

    def test_skips_install_when_chat_agent_already_available(self):
        cmd = self._make_cmd("chat")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=MagicMock(),  # a real find_spec() result -> importable
            ),
            patch("gaia.hub.installer.install") as mock_install,
        ):
            rc = cmd.run()

        self.assertEqual(rc, 0)
        mock_install.assert_not_called()

    def test_hub_install_runs_after_download_models_and_pip_extras_still_runs(self):
        """Ordering: the hub-install attempt happens AFTER `_download_models()`
        (models must exist before the agent that uses them is wired in), and
        the `[rag]` pip-extras step still runs independently -- the hub
        install targets `~/.gaia/agents/chat/site-packages` while the extras
        step targets the ACTIVE interpreter's site-packages; one must not
        replace or block the other (#2358 plan amendment A9).
        """
        cmd = self._make_cmd("chat")
        order = []
        self._patch_common_steps(cmd, order)

        def _track_install(*_a, **_k):
            order.append("hub_install")
            return MagicMock(hot_registered=True)

        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result([{"id": "chat"}]),
            ),
            patch("gaia.hub.installer.install", side_effect=_track_install),
        ):
            rc = cmd.run()

        self.assertEqual(rc, 0)
        self.assertIn("download_models", order)
        self.assertIn("hub_install", order)
        self.assertIn("pip_extras", order)
        self.assertLess(
            order.index("download_models"),
            order.index("hub_install"),
            f"hub install must happen after model downloads; order was {order}",
        )


class TestHubInstallWiringFailsLoudly(_HubInstallWiringTestBase):
    """AC: unlike `_install_pip_extras` (warn-but-continue), a genuine hub
    install failure for a PUBLISHED chat agent must hard-fail `init` --
    silently continuing would recreate the exact "chat isn't installed"
    state this issue closes. But chat isn't in the live Hub catalog yet
    (only `email` is, as of #2358) -- so `init --profile chat` must NOT
    hard-fail merely because chat isn't published yet; it must only fail
    loud once chat IS published and the install itself genuinely fails.
    """

    def test_returns_nonzero_when_published_chat_install_genuinely_fails(self):
        from gaia.hub.installer import InstallError

        cmd = self._make_cmd("chat")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result(
                    [{"id": "chat", "latest_version": "0.1.0"}]
                ),
            ),
            patch(
                "gaia.hub.installer.install",
                side_effect=InstallError("simulated genuine hub install failure"),
            ),
        ):
            rc = cmd.run()

        self.assertNotEqual(
            rc,
            0,
            "a genuine hub-install failure for a published agent must fail "
            "`gaia init` loudly, not warn-and-continue like the pip-extras "
            "step does",
        )

    def test_returns_zero_when_chat_not_yet_published_in_catalog(self):
        """Regression guard: chat isn't in the live catalog yet (only
        `email` is) -- `init --profile chat` must still exit 0 today, not
        hard-fail on every user's `gaia init` before chat is ever published.
        This may currently pass "by accident" (no hub-install call exists at
        all yet) -- that's fine; it pins the not-yet-published case so a
        later "install unconditionally" implementation doesn't regress it.
        """
        cmd = self._make_cmd("chat")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result([]),  # chat not yet published
            ),
            patch("gaia.hub.installer.install") as mock_install,
        ):
            rc = cmd.run()

        self.assertEqual(rc, 0)
        mock_install.assert_not_called()


class TestHubInstallWiringChatOnlyScope(_HubInstallWiringTestBase):
    """AC: only profiles whose declared agent is "chat" trigger the hub
    install. A generic "install the profile's agent" would make
    `gaia init --profile sd/code/rag/vlm/minimal/all` hard-fail today, since
    none of those agents are in the hub index (#2358 review finding).

    Scope decision (documented, since the plan text left this ambiguous):
    ``INIT_PROFILES["npu"]["agent"] == "chat"`` too (both profiles resolve to
    the same standalone chat wheel), so `npu` is treated as IN-SCOPE for the
    hub-install wiring -- same as `chat` -- and is deliberately excluded from
    the "must never call install" list below. See
    ``TestHubInstallWiringNpuProfile`` for the positive case.
    """

    NON_CHAT_PROFILES = ("sd", "code", "rag", "vlm", "minimal", "all")

    def test_non_chat_profiles_never_call_hub_install_and_still_exit_zero(self):
        for profile in self.NON_CHAT_PROFILES:
            with self.subTest(profile=profile):
                cmd = self._make_cmd(profile)
                self._patch_common_steps(cmd)
                with patch("gaia.hub.installer.install") as mock_install:
                    rc = cmd.run()
                self.assertEqual(rc, 0, f"profile={profile}")
                mock_install.assert_not_called()


class TestHubInstallWiringNpuProfile(_HubInstallWiringTestBase):
    """Positive case for the npu-profile scope decision above: `npu`
    declares ``"agent": "chat"`` just like `chat` does, so it must ALSO
    trigger the hub install when chat isn't already available.
    """

    def test_npu_profile_also_installs_chat_agent_when_not_available(self):
        cmd = self._make_cmd("npu")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result(
                    [{"id": "chat", "latest_version": "0.1.0"}]
                ),
            ),
            patch("gaia.hub.installer.install") as mock_install,
        ):
            mock_install.return_value = MagicMock(hot_registered=True)
            rc = cmd.run()

        self.assertEqual(rc, 0)
        mock_install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
