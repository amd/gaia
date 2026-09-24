# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for the gaia init command.

These tests use mocking to avoid actual network calls and installations.
"""

import io
import sys
import unittest
from unittest.mock import MagicMock, patch

from gaia.installer.lemonade_installer import (
    InstallResult,
    LemonadeInfo,
    LemonadeInstaller,
)
from gaia.ui.build import WebuiBuildResult, WebuiBuildStatus
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
        """Test platform support on macOS."""
        mock_system.return_value = "Darwin"
        installer = LemonadeInstaller()
        self.assertTrue(installer.is_platform_supported())

    @patch("platform.system")
    def test_is_platform_supported_unknown(self, mock_system):
        """Test platform support on an unsupported OS."""
        mock_system.return_value = "FreeBSD"
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
        mock_system.return_value = "FreeBSD"
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

    @patch("gaia.installer.lemonade_installer.get_installed_version")
    def test_check_installation_finds_macos_install(self, mock_get_version):
        """`gaia init` reported "not installed" on a macOS box where lemond was
        answering requests on the port GAIA itself probes (issue #1867).

        Drives the REAL resolver through injected platform + filesystem probes
        — patching resolve_lemonade here would only prove it was called, not
        that macOS detection works.
        """
        from pathlib import Path

        real_exists = Path.exists
        present = {"/usr/local/bin/lemond", "/usr/local/bin/lemonade"}

        mock_get_version.return_value = "10.10.0"
        with (
            patch("platform.system", return_value="Darwin"),
            patch.dict("os.environ", {}, clear=True),
            patch("shutil.which", return_value=None),
            patch.object(
                Path, "exists", lambda self: str(self.expanduser()) in present
            ),
        ):
            info = LemonadeInstaller().check_installation()

        self.assertIs(Path.exists, real_exists, "Path.exists must be restored")
        self.assertTrue(info.installed, f"macOS install missed: {info.error}")
        self.assertEqual(info.version, "10.10.0")
        self.assertEqual(info.path, "/usr/local/bin/lemonade")
        self.assertIsNone(info.error)


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

        valid_profiles = ["minimal", "chat", "rag", "all"]
        for profile in valid_profiles:
            cmd = InitCommand(profile=profile, yes=True)
            self.assertEqual(cmd.profile, profile)

    def test_bracketed_text_not_eaten_by_rich_markup(self):
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

    def test_hub_agent_bootstrap_installs_with_trust(self):
        """`gaia init` installs its curated profile agent WITH the trust opt-in.

        Every non-verified agent now needs an explicit trust acknowledgement to
        install. The curated first-run profile agent is a hardcoded INIT_PROFILES
        id (trusted by GAIA's own curation), so the bootstrap must pass the flag
        — otherwise `gaia init` hard-fails for exactly the new users it targets.
        """
        from gaia.hub import catalog as hub_catalog
        from gaia.hub import installer as hub_installer
        from gaia.installer.init_command import INIT_PROFILES, InitCommand

        agent_id = INIT_PROFILES["chat"]["agent"]
        cmd = InitCommand(profile="chat", yes=True)

        index = MagicMock()
        index.agents = [{"id": agent_id}]

        with (
            patch.object(cmd, "_is_hub_agent_available", return_value=False),
            patch.object(hub_catalog, "load_index", return_value=index),
            patch.object(hub_installer, "install") as mock_install,
        ):
            mock_install.return_value = MagicMock(path="not-a-real-path")
            cmd._ensure_hub_agent_installed()

        mock_install.assert_called_once()
        self.assertEqual(mock_install.call_args.args[0], agent_id)
        self.assertIs(mock_install.call_args.kwargs.get("trusted"), True)


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

        expected = ["minimal", "chat", "rag", "all"]
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

    @patch.dict("os.environ", {}, clear=False)
    def test_remote_flag_without_a_url_is_refused(self):
        """--remote with nothing to point at must not quietly set up embedded."""
        import os

        from gaia.installer.init_command import InitCommand

        os.environ.pop("LEMONADE_BASE_URL", None)
        with self.assertRaises(ValueError) as ctx:
            InitCommand(profile="minimal", yes=True, remote=True)
        self.assertIn("LEMONADE_BASE_URL", str(ctx.exception))

    @patch.dict("os.environ", {"LEMONADE_BASE_URL": "  "})
    def test_blank_url_counts_as_unset(self):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=True)
        self.assertIsNone(cmd._lemonade_base_url)


class TestLemonadeReady(unittest.TestCase):
    """Step 1: GAIA's embedded server by default, a configured one only when
    LEMONADE_BASE_URL names it."""

    def setUp(self):
        env = patch.dict("os.environ")
        env.start()
        self.addCleanup(env.stop)
        import os

        os.environ.pop("LEMONADE_BASE_URL", None)

    def _cmd(self, **kwargs):
        from gaia.installer import init_command as ic

        cmd = ic.InitCommand(profile="chat", yes=True, **kwargs)
        buf = io.StringIO()
        if ic.RICH_AVAILABLE:
            cmd.console = ic.Console(file=buf, force_terminal=False, width=300)
        return cmd, buf

    @staticmethod
    def _status(**kwargs):
        from gaia.llm.lemonade_embedded import EmbeddedStatus

        fields = {"installed": True, "running": False, "version": LEMONADE_VERSION}
        fields.update(kwargs)
        return EmbeddedStatus(**fields)

    def _embedded(self, status, installed=True):
        embedded = MagicMock()
        embedded.version = LEMONADE_VERSION
        embedded.dist_dir = "/gaia/lemonade/dist"
        embedded.status.return_value = status
        embedded.is_installed.return_value = installed
        embedded.start.return_value = self._status(
            running=True, port=51234, pid=1, base_url="http://localhost:51234/api/v1"
        )
        return embedded

    def _run_embedded(self, embedded, **kwargs):
        cmd, buf = self._cmd(**kwargs)
        with patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade", return_value=embedded
        ):
            ok = cmd._ensure_lemonade_ready()
        return ok, buf.getvalue()

    def test_fresh_machine_installs_then_starts(self):
        embedded = self._embedded(self._status(installed=False), installed=False)
        ok, out = self._run_embedded(embedded)

        self.assertTrue(ok)
        embedded.install.assert_called_once_with(force=False)
        embedded.start.assert_called_once_with(install_if_missing=False)
        embedded.stop.assert_not_called()
        self.assertIn("running on port 51234", out)

    def test_installed_and_running_is_reused(self):
        running = self._status(running=True, port=51234, pid=1)
        embedded = self._embedded(running)
        ok, _ = self._run_embedded(embedded)

        self.assertTrue(ok)
        embedded.install.assert_not_called()
        embedded.stop.assert_not_called()

    def test_older_running_version_is_replaced(self):
        old = self._status(running=True, version="0.0.1", port=1, pid=1)
        embedded = self._embedded(old)
        ok, out = self._run_embedded(embedded)

        self.assertTrue(ok)
        embedded.stop.assert_called_once()
        embedded.start.assert_called_once()
        self.assertIn("v0.0.1", out)

    def test_unresponsive_instance_is_stopped_before_starting(self):
        embedded = self._embedded(self._status(port=1, unresponsive_pid=77))
        ok, out = self._run_embedded(embedded)

        self.assertTrue(ok)
        embedded.stop.assert_called_once()
        self.assertIn("pid 77", out)

    def test_force_reinstall_stops_and_reinstalls(self):
        running = self._status(running=True, port=1, pid=1)
        embedded = self._embedded(running)
        ok, _ = self._run_embedded(embedded, force_reinstall=True)

        self.assertTrue(ok)
        embedded.stop.assert_called_once()
        embedded.install.assert_called_once_with(force=True)

    def test_embedded_failure_fails_the_step_with_its_message(self):
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        embedded = self._embedded(self._status())
        embedded.start.side_effect = EmbeddedLemonadeError(
            "port taken; read lemond.log"
        )
        ok, out = self._run_embedded(embedded)

        self.assertFalse(ok)
        self.assertIn("port taken; read lemond.log", out)

    def test_unsupported_platform_fails_the_step(self):
        from gaia.llm.lemonade_embedded import UnsupportedPlatformError

        embedded = self._embedded(self._status(installed=False), installed=False)
        embedded.install.side_effect = UnsupportedPlatformError("not published")
        ok, out = self._run_embedded(embedded)

        self.assertFalse(ok)
        self.assertIn("not published", out)
        embedded.start.assert_not_called()

    def _run_configured(self, url, health=None, error=None):
        import os

        from gaia.llm.lemonade_client import LemonadeClientError

        os.environ["LEMONADE_BASE_URL"] = url
        cmd, buf = self._cmd()
        with (
            patch("gaia.llm.lemonade_embedded.EmbeddedLemonade") as mock_embedded,
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            if error:
                mock_client_class.return_value.health_check.side_effect = (
                    LemonadeClientError(error)
                )
            else:
                mock_client_class.return_value.health_check.return_value = health
            ok = cmd._ensure_lemonade_ready()
        mock_embedded.assert_not_called()
        return ok, buf.getvalue(), mock_client_class

    def test_configured_server_is_used_without_installing_anything(self):
        ok, out, client_class = self._run_configured(
            "http://localhost:13305", health={"status": "ok", "version": "99.0.0"}
        )

        self.assertTrue(ok)
        self.assertEqual(
            client_class.call_args.kwargs["base_url"], "http://localhost:13305/api/v1"
        )
        self.assertIn("v99.0.0", out)

    def test_unreachable_configured_server_fails_and_says_how_to_recover(self):
        ok, out, _ = self._run_configured("http://gpu-box:13305", error="refused")

        self.assertFalse(ok)
        self.assertIn("refused", out)
        self.assertIn("unset LEMONADE_BASE_URL", out)

    def test_configured_server_below_profile_minimum_is_refused(self):
        ok, out, _ = self._run_configured(
            "http://gpu-box:13305", health={"status": "ok", "version": "1.0.0"}
        )

        self.assertFalse(ok)
        self.assertIn("v1.0.0", out)


class TestDownloadModels(unittest.TestCase):
    """Test _download_models delegates to LemonadeClient."""

    def test_calls_ensure_model_downloaded_per_model(self):
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

    def test_returns_false_on_download_failure(self):
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

    @patch.dict(
        "os.environ",
        {"LEMONADE_BASE_URL": "http://192.168.1.100:13305/api/v1"},
    )
    def test_remote_mode_uses_ensure_model_downloaded(self):
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

    def test_force_models_deletes_before_download(self):
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

    def test_npu_profile_pulls_builtin_model_without_recipe(self):
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


class TestSkipChatModel(unittest.TestCase):
    """skip_chat_model (the TUI's --use-claude path) must skip the chat LLM
    while still pulling the embedder RAG/memory need — and this must be true
    of the REAL `_download_models()` filtering, not a stub that only proves
    the method was invoked (see CLAUDE.md's hidden-state/mock-validity note
    and the #1655 case it cites)."""

    def test_without_skip_chat_model_downloads_both(self):
        """Control case: the chat profile's normal behavior pulls the chat
        LLM AND the embedder, so the skip test below is a real change in
        behavior, not just an assertion that happens to always pass."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="chat", yes=True)
        self.assertFalse(cmd.skip_chat_model)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.ensure_model_downloaded.return_value = True
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            pulled = {
                c.args[0] for c in mock_client.ensure_model_downloaded.call_args_list
            }
            self.assertEqual(
                pulled, {"Gemma-4-E4B-it-GGUF", "user.embeddinggemma-300m-GGUF"}
            )

    def test_skip_chat_model_downloads_only_the_embedder(self):
        """A Claude-backed session never calls the local chat LLM — pulling
        it would waste several GB of bandwidth/disk for a model that is
        never loaded. The embedder is still required: RAG/memory/code-index
        embeddings have no Claude equivalent (Anthropic has no embeddings
        API — see hub/agents/gaia/python/gaia_agent/stdio.py)."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="chat", yes=True, skip_chat_model=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.ensure_model_downloaded.return_value = True
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            pulled = {
                c.args[0] for c in mock_client.ensure_model_downloaded.call_args_list
            }
            self.assertEqual(pulled, {"user.embeddinggemma-300m-GGUF"})
            # Never even asked about the chat LLM's availability, let alone
            # downloaded it.
            checked = {
                c.args[0] for c in mock_client.check_model_available.call_args_list
            }
            self.assertNotIn("Gemma-4-E4B-it-GGUF", checked)

    def test_skip_chat_model_verify_only_checks_the_embedder(self):
        """_verify_setup must apply the same filter, or a Claude session
        reports the chat LLM as "not downloaded" for a model it deliberately
        never pulled."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="chat", yes=True, skip_chat_model=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.health_check.return_value = True
            mock_client.check_model_available.return_value = False
            mock_client_class.return_value = mock_client

            with patch(
                "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
                return_value=True,
            ) as ensure_ready:
                result = cmd._verify_setup()
            ensure_ready.assert_not_called()
            self.assertTrue(result)
            checked = {
                c.args[0] for c in mock_client.check_model_available.call_args_list
            }
            self.assertEqual(checked, {"user.embeddinggemma-300m-GGUF"})


class TestCheckSetupStatus(unittest.TestCase):
    """gaia init --check: a read-only readiness probe the TUI polls on every
    launch instead of trusting a marker file the user cannot see or clear."""

    def test_invalid_profile_raises(self):
        from gaia.installer.init_command import check_setup_status

        with self.assertRaises(ValueError):
            check_setup_status(profile="not-a-real-profile")

    def setUp(self):
        env = patch.dict("os.environ")
        env.start()
        self.addCleanup(env.stop)
        import os

        os.environ.pop("LEMONADE_BASE_URL", None)

    @staticmethod
    def _embedded(status):
        from gaia.version import LEMONADE_VERSION

        embedded = MagicMock()
        embedded.version = LEMONADE_VERSION
        embedded.status.return_value = status
        return patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade", return_value=embedded
        )

    @staticmethod
    def _status(**kwargs):
        from gaia.llm.lemonade_embedded import EmbeddedStatus
        from gaia.version import LEMONADE_VERSION

        fields = {"installed": True, "running": False, "version": LEMONADE_VERSION}
        fields.update(kwargs)
        return EmbeddedStatus(**fields)

    def _running(self):
        return self._status(
            running=True, port=51234, pid=42, base_url="http://localhost:51234/api/v1"
        )

    def test_not_installed_reports_not_ready_without_a_client(self):
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._status(installed=False)),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            status = check_setup_status(profile="chat")

        self.assertFalse(status.ready)
        self.assertEqual(status.reasons, ["GAIA's Lemonade Server is not installed"])
        mock_client_class.assert_not_called()

    def test_installed_but_stopped_reports_not_ready(self):
        from gaia.installer.init_command import check_setup_status

        with self._embedded(self._status()):
            status = check_setup_status(profile="chat")

        self.assertEqual(
            status.reasons, ["GAIA's Lemonade Server is installed but not running"]
        )

    def test_unresponsive_server_is_named(self):
        from gaia.installer.init_command import check_setup_status

        with self._embedded(self._status(port=51234, unresponsive_pid=77)):
            status = check_setup_status(profile="chat")

        self.assertFalse(status.ready)
        self.assertIn("pid 77", status.reasons[0])

    def test_older_embedded_version_is_not_ready(self):
        """init would replace it, so --check must not call it ready."""
        from gaia.installer.init_command import check_setup_status

        running = self._status(
            running=True, version="0.0.1", port=1, pid=2, base_url="http://x/api/v1"
        )
        with self._embedded(running):
            status = check_setup_status(profile="chat")

        self.assertFalse(status.ready)
        self.assertIn("v0.0.1", status.reasons[0])

    def test_ready_when_server_up_and_required_models_present(self):
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            mock_client_class.return_value.check_model_available.return_value = True
            status = check_setup_status(profile="chat")

        self.assertTrue(status.ready)
        self.assertEqual(status.reasons, [])
        # The embedded server, never whatever answers on Lemonade's default port.
        self.assertEqual(
            mock_client_class.call_args.kwargs["base_url"],
            "http://localhost:51234/api/v1",
        )

    def test_configured_url_is_checked_instead_of_the_embedded_server(self):
        import os

        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import LemonadeClientError

        os.environ["LEMONADE_BASE_URL"] = "http://gpu-box:13305"
        with (
            patch("gaia.llm.lemonade_embedded.EmbeddedLemonade") as mock_embedded,
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            mock_client_class.return_value.health_check.side_effect = (
                LemonadeClientError("refused")
            )
            status = check_setup_status(profile="chat")

        mock_embedded.assert_not_called()
        self.assertFalse(status.ready)
        self.assertIn("http://gpu-box:13305/api/v1", status.reasons[0])
        self.assertIn("refused", status.reasons[0])

    def test_remote_without_a_configured_url_is_an_error(self):
        from gaia.installer.init_command import check_setup_status

        with self.assertRaises(ValueError):
            check_setup_status(profile="chat", remote=True)

    def test_model_probe_error_is_reported_not_called_missing(self):
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import LemonadeClientError

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            mock_client_class.return_value.check_model_available.side_effect = (
                LemonadeClientError("500")
            )
            status = check_setup_status(profile="chat", skip_chat_model=True)

        self.assertEqual(
            status.reasons,
            ["Could not check model 'user.embeddinggemma-300m-GGUF': 500"],
        )

    def test_skip_chat_model_never_asks_about_the_chat_llm(self):
        """Same real-state check the TUI's --use-claude launch makes before
        deciding whether to auto-run setup: the chat LLM must not even be
        probed, let alone reported missing."""
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            mock_client = mock_client_class.return_value
            mock_client.check_model_available.return_value = False
            status = check_setup_status(profile="chat", skip_chat_model=True)

        self.assertFalse(status.ready)
        self.assertEqual(
            status.reasons,
            ["Model 'user.embeddinggemma-300m-GGUF' is not downloaded"],
        )
        checked = {c.args[0] for c in mock_client.check_model_available.call_args_list}
        self.assertEqual(checked, {"user.embeddinggemma-300m-GGUF"})


class TestInstallPipExtras(unittest.TestCase):
    """Test _install_pip_extras frontend selection and messaging."""

    def _make_cmd(self, profile):
        from gaia.installer.init_command import InitCommand

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

    # Log text below is copied verbatim from the GAIA CLI Tests (Linux) runs that
    # went red during the 2026-09-21 Launchpad outage.
    APT_UPDATE_503 = (
        "Get:1 http://azure.archive.ubuntu.com/ubuntu noble InRelease [256 kB]\n"
        "Err:8 https://ppa.launchpadcontent.net/lemonade-team/stable/ubuntu "
        "noble InRelease\n"
        "  503  Service Unavailable [IP: 185.125.189.187 443]\n"
        "Reading package lists...\n"
        "W: Failed to fetch https://ppa.launchpadcontent.net/lemonade-team/stable/"
        "ubuntu/dists/noble/InRelease  503  Service Unavailable "
        "[IP: 185.125.189.187 443]\n"
        "W: Some index files failed to download. They have been ignored, "
        "or old ones used instead.\n"
    )

    ADD_APT_GPG_500 = (
        "Traceback (most recent call last):\n"
        '  File "/usr/bin/add-apt-repository", line 367, in <module>\n'
        "    sys.exit(0 if addaptrepo.main() else 1)\n"
        "urllib.error.HTTPError: HTTP Error 500: Internal Server Error\n"
        "ERROR: b'GPGKeyTemporarilyNotFoundError'\n"
    )

    # create=True: os.geteuid does not exist on Windows dev machines.
    @patch("os.geteuid", create=True, return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_launchpad_index_outage_is_named(
        self, mock_run, mock_which, mock_geteuid
    ):
        """A 503 on the PPA index stops the install and blames Launchpad, not apt."""
        installer = self._make_linux_installer()

        # apt-get update exits 0 when only *some* indexes fail -- that is the trap.
        update = self._ok_run()
        update.stdout = self.APT_UPDATE_503
        mock_run.side_effect = [self._ok_run(), update]

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            result = installer._install_via_ppa(non_interactive=False)

        self.assertFalse(result.success)
        self.assertIn("Launchpad", result.error)
        self.assertIn("503", result.error)
        # The old message sent users to repair a dpkg state that was never broken.
        self.assertNotIn("dpkg --configure", result.error)
        # Negative control: without the check, apt-get install would have run and
        # failed with "Unable to locate package lemonade-server".
        self.assertEqual(mock_run.call_count, 2)

    # create=True: os.geteuid does not exist on Windows dev machines.
    @patch("os.geteuid", create=True, return_value=1000)
    @patch("shutil.which", return_value="/usr/bin/add-apt-repository")
    @patch("subprocess.run")
    def test_install_via_ppa_launchpad_key_outage_is_named(
        self, mock_run, mock_which, mock_geteuid
    ):
        """A failed signing-key fetch reports the outage, not a raw Python traceback."""
        installer = self._make_linux_installer()
        mock_run.side_effect = [self._fail_run(stderr=self.ADD_APT_GPG_500)]

        with patch.object(LemonadeInstaller, "_check_linux_version", return_value=None):
            result = installer._install_via_ppa(non_interactive=False)

        self.assertFalse(result.success)
        self.assertIn("Launchpad", result.error)
        self.assertIn("signing key", result.error)
        self.assertNotIn("Traceback", result.error)

    def test_diagnose_launchpad_outage_ignores_unrelated_repositories(self):
        """Another PPA failing must not be blamed on Launchpad's Lemonade archive."""
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        unrelated = (
            "W: Failed to fetch https://ppa.launchpadcontent.net/deadsnakes/ppa/"
            "ubuntu/dists/noble/InRelease  404  Not Found\n"
        )
        self.assertIsNone(diagnose_launchpad_outage(unrelated))
        self.assertIsNone(diagnose_launchpad_outage("Reading package lists... Done\n"))
        self.assertIsNotNone(diagnose_launchpad_outage(self.APT_UPDATE_503))

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

        cmd = InitCommand(profile=profile, yes=True, **kwargs)
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
            patch.object(cmd, "_ensure_lemonade_ready", side_effect=_tracked("server")),
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
            patch(
                "gaia.ui.build.ensure_webui_built",
                return_value=WebuiBuildResult(status=WebuiBuildStatus.OK),
            ),
            # Never touch the real user's ~/.gaia/config.json during a test.
            patch("gaia.config.GaiaConfig"),
            # Same reason: _is_hub_agent_available falls back to the install
            # sentinel under the real ~/.gaia/agents, so without this whether
            # a developer happens to have run `gaia hub install` would decide
            # the result of these tests.
            patch("gaia.hub.installer.read_sentinel", return_value=None),
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

    NON_CHAT_PROFILES = ("sd", "rag", "vlm", "minimal", "all")

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


class TestWebuiBuildGatesInitCompletion(_HubInstallWiringTestBase):
    """AC4/4b/5 (#2880): a hard Agent UI build failure (too-old Node, or a
    build failure with no usable dist/) must not let `gaia init` report
    plain success -- but verify_setup and config persistence, which don't
    depend on the frontend build, must still run unconditionally. A merely
    absent toolchain (no node/npm at all) stays a warn-and-continue outcome,
    since a backend-only dev install is legitimate.

    Uses the "minimal" profile so only the webui-build step's own
    `gaia.ui.build.ensure_webui_built` patch (installed by
    `_patch_common_steps`, overridden per test below) is in play --
    "minimal" doesn't trigger the hub-agent-install or device/backend
    branches, which need their own separate patches.
    """

    def _run_with_webui_result(self, result):
        cmd = self._make_cmd("minimal")
        self._patch_common_steps(cmd)
        # Override the "OK" stub _patch_common_steps installed above.
        p = patch("gaia.ui.build.ensure_webui_built", return_value=result)
        p.start()
        self.addCleanup(p.stop)
        rc = cmd.run()
        return cmd, rc

    def test_node_too_old_does_not_report_plain_success(self):
        result = WebuiBuildResult(
            status=WebuiBuildStatus.NODE_TOO_OLD,
            message="Agent UI frontend requires Node >=20.19.0, but "
            "/usr/bin/node reports v18.19.0.",
            found_version="18.19.0",
            required_range=">=20.19.0",
            node_path="/usr/bin/node",
        )
        cmd = self._make_cmd("minimal")
        self._patch_common_steps(cmd)
        p = patch("gaia.ui.build.ensure_webui_built", return_value=result)
        p.start()
        self.addCleanup(p.stop)

        with patch.object(cmd, "_print_completion") as mock_completion:
            rc = cmd.run()

        self.assertNotEqual(rc, 0, "NODE_TOO_OLD must not exit 0")
        mock_completion.assert_not_called()

    def test_build_failed_with_no_dist_does_not_report_plain_success(self):
        result = WebuiBuildResult(
            status=WebuiBuildStatus.BUILD_FAILED,
            message="Warning: Frontend build failed (exit code 1).",
        )
        _cmd, rc = self._run_with_webui_result(result)

        self.assertNotEqual(rc, 0, "BUILD_FAILED (no usable dist) must not exit 0")

    def test_node_too_old_still_runs_verify_and_persists_config(self):
        result = WebuiBuildResult(
            status=WebuiBuildStatus.NODE_TOO_OLD, message="too old"
        )
        cmd = self._make_cmd("minimal")
        self._patch_common_steps(cmd)
        p = patch("gaia.ui.build.ensure_webui_built", return_value=result)
        p.start()
        self.addCleanup(p.stop)

        mock_config_cls = MagicMock()
        with (
            patch.object(cmd, "_verify_setup", return_value=True) as mock_verify,
            patch("gaia.config.GaiaConfig", mock_config_cls),
        ):
            rc = cmd.run()

        mock_verify.assert_called_once()
        mock_config_cls.load.return_value.save.assert_called_once()
        self.assertNotEqual(rc, 0)

    def test_toolchain_absent_still_returns_zero(self):
        """node/npm missing entirely is tolerable -- a backend-only dev
        install is legitimate, so this must stay a warn-and-continue exit 0,
        unlike NODE_TOO_OLD/BUILD_FAILED above."""
        result = WebuiBuildResult(
            status=WebuiBuildStatus.TOOLCHAIN_ABSENT,
            message="Warning: Node.js not found. Cannot auto-rebuild Agent UI frontend.",
        )
        _cmd, rc = self._run_with_webui_result(result)

        self.assertEqual(rc, 0)

    def test_stale_dist_still_usable_returns_zero(self):
        """A build failure with a stale-but-working dist/ is status OK (the
        5th outcome) -- it must not fail init."""
        result = WebuiBuildResult(
            status=WebuiBuildStatus.OK,
            message="Warning: npm install failed: ERR",
        )
        _cmd, rc = self._run_with_webui_result(result)

        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# #2882: `gaia init` must not report success it did not deliver. Covers the
# non-interactive pre-flight refusal (AC1/AC2/AC3/AC5b), the Ctrl-C exit-130
# contract (AC4/AC4b/AC4c/AC5), and the profile-scoped completion-banner gate
# (AC6/AC7/AC7b/AC8). See the issue for the full acceptance-criteria list.
# ---------------------------------------------------------------------------


def _assert_refusal_message_shape(testcase, message, profile):
    """Shared AC1/AC5b assertions for the refusal message: it must name the
    profile and both flags that unblock it."""
    testcase.assertIn(profile, message)
    testcase.assertIn("--yes", message)
    testcase.assertIn("--skip-models", message)


class TestInitPreflightRefusal(unittest.TestCase):
    """AC1/AC2/AC3/AC5b: `gaia init` must refuse to run non-interactively
    without --yes -- on stderr, before Step 1 -- and must do so cleanly
    even when sys.stdin.isatty() itself raises (closed stdin).

    Every test that drives run() here defensively neutralizes the two real
    side effects run() unconditionally reaches once it falls through to
    completion (a live Hub-catalog network call and a real `tsc && vite
    build` that writes ~/.gaia/config.json): required regardless of
    whether THIS test expects the refusal to fire, because prior to the
    fix `run()` has no gate at all and genuinely falls through to doing
    that work.
    """

    def _make_cmd(self, yes: bool, profile: str = "minimal"):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile=profile, yes=yes)
        return cmd

    def test_ac1_non_tty_no_yes_refuses_before_step1(self):
        """Non-TTY + no --yes -> refuse before Step 1; the three real-work
        methods are never called; exit 1; message lands on stderr."""
        cmd = self._make_cmd(yes=False)

        with (
            patch.object(sys.stdin, "isatty", return_value=False),
            patch.object(cmd, "_ensure_lemonade_ready") as mock_lemonade,
            patch.object(cmd, "_download_models") as mock_download,
            patch.object(cmd, "_verify_setup", return_value=True),
            patch("gaia.ui.build.ensure_webui_built", return_value=False),
            patch("gaia.config.GaiaConfig"),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            rc = cmd.run()

        mock_lemonade.assert_not_called()
        mock_download.assert_not_called()
        self.assertEqual(rc, 1)
        self.assertEqual(mock_stdout.getvalue(), "", "refusal must not print to stdout")
        _assert_refusal_message_shape(self, mock_stderr.getvalue(), cmd.profile)

    def test_ac2_non_tty_with_yes_gate_noops(self):
        """Non-TTY + --yes -> the gate no-ops; Step 1 is genuinely reached.

        Isolated: does NOT replay a full successful run() end to end --
        tests/installer/test_installer_scenarios.py:167 already covers that.
        """
        cmd = self._make_cmd(yes=True)
        cmd.console = MagicMock()

        with (
            patch.object(sys.stdin, "isatty", return_value=False),
            patch.object(cmd, "_print_header") as mock_header,
            patch.object(
                cmd, "_ensure_lemonade_ready", return_value=False
            ) as mock_lemonade,
        ):
            rc = cmd.run()

        mock_header.assert_called_once()
        mock_lemonade.assert_called_once()
        self.assertEqual(rc, 1)

    def test_ac3_tty_no_yes_no_refusal(self):
        """TTY + no --yes -> no refusal; Step 1 is genuinely reached
        (prompts render as today)."""
        cmd = self._make_cmd(yes=False)
        cmd.console = MagicMock()

        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.object(cmd, "_print_header") as mock_header,
            patch.object(
                cmd, "_ensure_lemonade_ready", return_value=False
            ) as mock_lemonade,
        ):
            rc = cmd.run()

        mock_header.assert_called_once()
        mock_lemonade.assert_called_once()
        self.assertEqual(rc, 1)

    def test_ac5b_closed_stdin_isatty_raises_still_refuses_cleanly(self):
        """sys.stdin.isatty() raising ValueError (closed stdin -- the
        literal scenario in the issue title) must not escape as a
        traceback; run() still returns 1 with the AC1-shaped message."""
        cmd = self._make_cmd(yes=False)

        with (
            patch.object(
                sys.stdin,
                "isatty",
                side_effect=ValueError("I/O operation on closed file"),
            ),
            patch.object(cmd, "_ensure_lemonade_ready") as mock_lemonade,
            patch.object(cmd, "_download_models") as mock_download,
            patch.object(cmd, "_verify_setup", return_value=True),
            patch("gaia.ui.build.ensure_webui_built", return_value=False),
            patch("gaia.config.GaiaConfig"),
            patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            rc = cmd.run()

        mock_lemonade.assert_not_called()
        mock_download.assert_not_called()
        self.assertEqual(rc, 1)
        self.assertEqual(mock_stdout.getvalue(), "", "refusal must not print to stdout")
        _assert_refusal_message_shape(self, mock_stderr.getvalue(), cmd.profile)


class TestPromptYesNoKeyboardInterrupt(unittest.TestCase):
    """AC4b/AC5: `_prompt_yes_no` in isolation, with no run() plumbing --
    KeyboardInterrupt must propagate uncaught (D4's core edit) while
    EOFError must still return False (unchanged; pins that D4 touched
    only the KeyboardInterrupt half of the old combined handler)."""

    def _make_cmd(self):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=False)
        cmd.console = MagicMock()
        return cmd

    def test_ac4b_keyboard_interrupt_propagates(self):
        cmd = self._make_cmd()
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                cmd._prompt_yes_no("Continue?", default=True)

    def test_ac5_eof_error_still_returns_false(self):
        cmd = self._make_cmd()
        with patch("builtins.input", side_effect=EOFError):
            result = cmd._prompt_yes_no("Continue?", default=True)
        self.assertFalse(result)


class TestRunKeyboardInterruptExitCode(unittest.TestCase):
    """AC4/AC4c: a KeyboardInterrupt raised while run() is mid-flight must
    reach run()'s own top-level `except KeyboardInterrupt` handler and
    produce the SAME end-to-end contract everywhere it can fire: exit 130
    and "Initialization cancelled by user." on stdout -- and, critically
    for AC4c, NOT the "GAIA initialization complete!" banner (today's
    second instance of the issue's headline bug, fact 4).

    Both tests defensively neutralize the Agent UI build and config-persist
    side effects (see TestInitPreflightRefusal's docstring) since today's
    unfixed code falls all the way through to them.
    """

    def _make_cmd(self, yes: bool = False, **kwargs):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="minimal", yes=yes, **kwargs)
        return cmd

    def test_ac4_ctrl_c_at_download_prompt_returns_130(self):
        """Ctrl-C at Step 5's 'Continue?' download-confirmation prompt.

        Uses yes=False + isatty->True (mechanics item 2): yes=True would
        make _prompt_yes_no return before input() is ever called, and the
        new AC1 refusal would fire first -- either way silently proving
        nothing.
        """
        cmd = self._make_cmd(yes=False)

        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.object(cmd, "_ensure_lemonade_ready", return_value=True),
            patch.object(cmd, "_verify_setup", return_value=True),
            patch("gaia.ui.build.ensure_webui_built", return_value=False),
            patch("gaia.config.GaiaConfig"),
            patch("gaia.llm.lemonade_client.LemonadeClient"),
            patch("builtins.input", side_effect=KeyboardInterrupt),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            rc = cmd.run()

        self.assertEqual(rc, 130)
        self.assertIn("Initialization cancelled by user.", mock_stdout.getvalue())

    def test_ac4c_ctrl_c_during_verification_loop_returns_130_no_banner(self):
        """Ctrl-C during Step 8's per-model verification loop.

        Today (fact 4) this is swallowed by _verify_setup's own
        KeyboardInterrupt handler, which falls through to `return True` --
        the fix must not let either that or the completion banner happen.
        Uses yes=True to sail past _verify_setup's OWN "Run model
        verification?" prompt without a real input() call; the interrupt
        under test fires inside the loop itself, not at a prompt, so
        mechanics item 2 does not apply to this particular call site.
        """
        cmd = self._make_cmd(yes=True)

        mock_client = MagicMock()
        mock_client.health_check.return_value = {"status": "ok"}
        mock_client.check_model_available.return_value = True

        with (
            patch.object(cmd, "_ensure_lemonade_ready", return_value=True),
            patch.object(cmd, "_download_models", return_value=True),
            patch.object(cmd, "_test_model_inference", side_effect=KeyboardInterrupt),
            patch(
                "gaia.ui.build.ensure_webui_built",
                return_value=WebuiBuildResult(status=WebuiBuildStatus.SKIPPED),
            ),
            patch("gaia.config.GaiaConfig"),
            patch(
                "gaia.llm.lemonade_client.LemonadeClient",
                return_value=mock_client,
            ),
            patch(
                "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
                return_value=True,
            ),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            rc = cmd.run()

        out = mock_stdout.getvalue()
        self.assertEqual(rc, 130)
        self.assertIn("Initialization cancelled by user.", out)
        self.assertNotIn("GAIA initialization complete!", out)


class TestPrintCompletionHeadlineGate(unittest.TestCase):
    """AC6/AC7/AC7b/AC8: the pre-branch "GAIA initialization complete!"
    headline (fact 5 -- printed BEFORE any profile branching, in two
    independent Rich/non-Rich copies) must be suppressed exactly when the
    profile installs a hub agent (the `gaia` flagship, or the `chat` wheel
    that `chat` and `npu` both declare) AND that wheel isn't importable --
    and must stay byte-identical to today in every other case. Each scenario
    is exercised against BOTH _print_completion code paths per AC8, since a
    fix applied to only one copy is the obvious regression.
    """

    def _make_cmd(self, profile, chat_available):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile=profile, yes=True)
        # Two seams, one scenario ("is the agent this profile needs there?").
        # _chat_agent_available drives the `gaia chat` hint; the headline goes
        # through _profile_agent_available, which is stubbed at its underlying
        # probe so its real "does this profile install an agent at all?"
        # scoping still runs (sd/vlm/minimal must stay unaffected).
        cmd._chat_agent_available = MagicMock(return_value=chat_available)
        cmd._is_hub_agent_available = MagicMock(return_value=chat_available)
        return cmd

    def test_flagship_profile_unavailable_rich_suppresses_headline(self):
        """The default profile gets the same gate: `gaia init` must not claim
        success while the flagship wheel the TUI spawns is still missing."""
        from gaia.installer import init_command as ic

        if not ic.RICH_AVAILABLE:
            self.skipTest("rich not installed")
        cmd = self._make_cmd("gaia", chat_available=False)
        buf = io.StringIO()
        cmd.console = ic.Console(file=buf, force_terminal=False, width=300)

        cmd._print_completion()

        self.assertNotIn("GAIA initialization complete!", buf.getvalue())

    def test_flagship_profile_available_rich_reports_complete(self):
        from gaia.installer import init_command as ic

        if not ic.RICH_AVAILABLE:
            self.skipTest("rich not installed")
        cmd = self._make_cmd("gaia", chat_available=True)
        buf = io.StringIO()
        cmd.console = ic.Console(file=buf, force_terminal=False, width=300)

        cmd._print_completion()

        out = buf.getvalue()
        self.assertIn("GAIA initialization complete!", out)
        # The flagship's own next step -- `gaia chat` runs a different agent
        # through a wheel this profile never installs.
        self.assertIn("gaia-tui", out)

    # -- AC6: chat/npu profile, chat agent unavailable -> no headline --

    def test_ac6_chat_profile_unavailable_rich_suppresses_headline(self):
        from gaia.installer import init_command as ic

        if not ic.RICH_AVAILABLE:
            self.skipTest("rich not installed")
        cmd = self._make_cmd("chat", chat_available=False)
        buf = io.StringIO()
        cmd.console = ic.Console(file=buf, force_terminal=False, width=300)

        cmd._print_completion()

        out = buf.getvalue()
        self.assertNotIn("GAIA initialization complete!", out)
        self.assertIn("Chat agent not installed yet -- run:", out)

    def test_ac6_npu_profile_unavailable_rich_suppresses_headline(self):
        from gaia.installer import init_command as ic

        if not ic.RICH_AVAILABLE:
            self.skipTest("rich not installed")
        cmd = self._make_cmd("npu", chat_available=False)
        buf = io.StringIO()
        cmd.console = ic.Console(file=buf, force_terminal=False, width=300)

        cmd._print_completion()

        out = buf.getvalue()
        self.assertNotIn("GAIA initialization complete!", out)
        self.assertIn("Chat agent not installed yet -- run:", out)

    def test_ac6_chat_profile_unavailable_non_rich_suppresses_headline(self):
        cmd = self._make_cmd("chat", chat_available=False)

        with (
            patch("gaia.installer.init_command.RICH_AVAILABLE", False),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            cmd._print_completion()

        out = mock_stdout.getvalue()
        self.assertNotIn("GAIA initialization complete!", out)
        self.assertIn("Chat agent not installed yet -- run:", out)

    # -- AC7: chat/npu profile, chat agent available -> unchanged --

    def test_ac7_chat_profile_available_rich_unchanged(self):
        from gaia.installer import init_command as ic

        if not ic.RICH_AVAILABLE:
            self.skipTest("rich not installed")
        cmd = self._make_cmd("chat", chat_available=True)
        buf = io.StringIO()
        cmd.console = ic.Console(file=buf, force_terminal=False, width=300)

        cmd._print_completion()

        out = buf.getvalue()
        self.assertIn("GAIA initialization complete!", out)
        self.assertNotIn("Chat agent not installed yet -- run:", out)

    def test_ac7_chat_profile_available_non_rich_unchanged(self):
        cmd = self._make_cmd("chat", chat_available=True)

        with (
            patch("gaia.installer.init_command.RICH_AVAILABLE", False),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            cmd._print_completion()

        out = mock_stdout.getvalue()
        self.assertIn("GAIA initialization complete!", out)
        self.assertNotIn("Chat agent not installed yet -- run:", out)

    # -- AC7b: non-chat profile (sd) -> headline always present --

    def test_ac7b_sd_profile_rich_headline_present_regardless_of_availability(
        self,
    ):
        from gaia.installer import init_command as ic

        if not ic.RICH_AVAILABLE:
            self.skipTest("rich not installed")
        for chat_available in (False, True):
            with self.subTest(chat_available=chat_available):
                cmd = self._make_cmd("sd", chat_available=chat_available)
                buf = io.StringIO()
                cmd.console = ic.Console(file=buf, force_terminal=False, width=300)

                cmd._print_completion()

                self.assertIn("GAIA initialization complete!", buf.getvalue())

    def test_ac7b_sd_profile_non_rich_headline_present_regardless_of_availability(
        self,
    ):
        for chat_available in (False, True):
            with self.subTest(chat_available=chat_available):
                cmd = self._make_cmd("sd", chat_available=chat_available)

                with (
                    patch("gaia.installer.init_command.RICH_AVAILABLE", False),
                    patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
                ):
                    cmd._print_completion()

                self.assertIn("GAIA initialization complete!", mock_stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
