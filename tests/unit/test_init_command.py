# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for the gaia init command.

These tests use mocking to avoid actual network calls and installations.
"""

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gaia.installer.lemonade_installer import (
    InstallResult,
    LemonadeInfo,
    LemonadeInstaller,
)
from gaia.ui.build import WebuiBuildResult, WebuiBuildStatus
from gaia.version import LEMONADE_VERSION

# Lemonade /system-info for a PC too small for the large default (16 GB Mac), so
# `gaia init` keeps choosing Gemma 4 E4B as these tests assume.
SMALL_MACHINE = {
    "Physical Memory": "16 GB",
    "devices": {"metal": {"available": True, "vram_gb": 11.8}},
    "model_storage": {"free_bytes": 200e9},
}
# A 16 GB Ryzen AI laptop: small GPU carve-out, an XDNA2 NPU.
NPU_LAPTOP = {
    "Physical Memory": "16 GB",
    "devices": {
        "amd_gpu": [{"available": True, "integrated": True, "vram_gb": 0.5}],
        "amd_npu": {"available": True},
    },
    "model_storage": {"free_bytes": 200e9},
}


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
        self.assertIn("only to uninstall it on Windows", str(ctx.exception))

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

        Paths are compared as POSIX: a Windows host renders `Path("/usr/...")`
        with backslashes, which would otherwise match nothing.
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
                Path, "exists", lambda self: self.expanduser().as_posix() in present
            ),
        ):
            info = LemonadeInstaller().check_installation()

        self.assertIs(Path.exists, real_exists, "Path.exists must be restored")
        self.assertTrue(info.installed, f"macOS install missed: {info.error}")
        self.assertEqual(info.version, "10.10.0")
        self.assertEqual(Path(info.path).as_posix(), "/usr/local/bin/lemonade")
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


class TestInitProfilePersistence(unittest.TestCase):
    """`gaia init` exits non-zero when it can't save the profile (#3954)."""

    def _run(self, update):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(
            profile="minimal", yes=True, skip_models=True, skip_webui_build=True
        )
        with (
            patch.object(InitCommand, "_print_header"),
            patch.object(InitCommand, "_print"),
            patch.object(InitCommand, "_print_step"),
            patch.object(InitCommand, "_choose_lemonade", return_value=True),
            patch.object(InitCommand, "_ensure_lemonade_ready", return_value=True),
            patch.object(InitCommand, "_ensure_llamacpp_backend", return_value=True),
            patch.object(InitCommand, "_verify_setup", return_value=True),
            patch.object(
                InitCommand, "_print_completion", return_value=True
            ) as completion,
            patch.object(InitCommand, "_print_error") as print_error,
            # Never touch the real ~/.gaia/config.json during a test.
            patch("gaia.config.GaiaConfig.update", update),
        ):
            rc = cmd.run()
        return rc, print_error, completion

    def test_saved_profile_exits_zero(self):
        update = MagicMock()
        rc, print_error, completion = self._run(update)

        self.assertEqual(rc, 0)
        update.assert_called_once()
        print_error.assert_not_called()
        completion.assert_called_once()

    def test_unsaved_profile_exits_one_and_says_why(self):
        update = MagicMock(side_effect=OSError("disk full"))
        rc, print_error, completion = self._run(update)

        self.assertEqual(rc, 1)
        print_error.assert_called_once()
        message = print_error.call_args.args[0]
        self.assertIn("Failed to save profile 'minimal'", message)
        self.assertIn("disk full", message)
        self.assertIn("gaia config set profile minimal", message)
        completion.assert_not_called()


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

    def test_email_min_version_locksteps_with_core(self):
        """The email agent's runtime minimum must match GAIA's floor —
        readiness (/v1/email/init) and the installer must agree on what
        'compatible' means."""
        from gaia.version import LEMONADE_MIN_VERSION

        try:
            from gaia_agent_email.version import MIN_LEMONADE_VERSION
        except ImportError:
            self.skipTest("gaia_agent_email (standalone email wheel) not installed")
        self.assertEqual(LEMONADE_MIN_VERSION, MIN_LEMONADE_VERSION)

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

    def _run_embedded(self, embedded, replace=None, **kwargs):
        cmd, buf = self._cmd(**kwargs)
        if replace is None:
            replace = MagicMock(return_value=None)
        with (
            patch("gaia.llm.lemonade_embedded.EmbeddedLemonade", return_value=embedded),
            patch("gaia.daemon.client.replace_outdated_daemon", replace),
            patch.object(cmd, "_hand_server_to_daemon") as hand_off,
        ):
            ok = cmd._ensure_lemonade_ready()
        self.hand_off = hand_off
        self.replace = replace
        return ok, buf.getvalue()

    def test_the_started_server_is_handed_to_the_daemon(self):
        """Otherwise the first-launch server belongs to nobody and outlives GAIA."""
        embedded = self._embedded(self._status())
        ok, _ = self._run_embedded(embedded)

        self.assertTrue(ok)
        self.hand_off.assert_called_once_with()

    def test_hand_off_asks_the_daemon_and_holds_the_server_through_init(self):
        cmd, _ = self._cmd()
        with (
            patch("gaia.daemon.client.ensure_lemonade") as ensure,
            patch("gaia.llm.lemonade_service.hold_lemonade_open") as hold,
        ):
            cmd._hand_server_to_daemon()
        ensure.assert_called_once_with()
        hold.assert_called_once_with("cli", "gaia init")

    def test_no_daemon_to_hand_to_says_how_to_stop_it(self):
        from gaia.daemon.errors import DaemonStartError

        cmd, buf = self._cmd()
        with patch(
            "gaia.daemon.client.ensure_lemonade",
            side_effect=DaemonStartError("needs amd-gaia[api]"),
        ):
            cmd._hand_server_to_daemon()
        out = buf.getvalue()
        self.assertIn("gaia lemonade embedded stop", out)
        self.assertIn("needs amd-gaia[api]", out)

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

    def test_older_running_version_installs_the_new_one_before_starting(self):
        """The old binary on disk must not satisfy the install check."""
        import tempfile
        from pathlib import Path

        from gaia.llm.lemonade_embedded import EmbeddedLemonade

        with tempfile.TemporaryDirectory() as home:
            embedded = EmbeddedLemonade(home=Path(home))
            old_binary = embedded.root / "dist" / "0.0.1" / embedded.daemon_path.name
            old_binary.parent.mkdir(parents=True)
            old_binary.touch()
            old = self._status(running=True, version="0.0.1", port=1, pid=1)
            new = self._status(running=True, port=51234, pid=2)
            with (
                patch.object(embedded, "status", return_value=old),
                patch.object(embedded, "stop"),
                patch.object(embedded, "install") as install,
                patch.object(embedded, "start", return_value=new),
            ):
                ok, _ = self._run_embedded(embedded)

        self.assertTrue(ok)
        install.assert_called_once_with(force=False)

    def test_an_outdated_daemon_is_replaced_before_the_server_is_swapped(self):
        """Left running, it restarts its own release's server from its dist."""
        old = self._status(running=True, version="0.0.1", port=1, pid=1)
        embedded = self._embedded(old, installed=False)
        calls = MagicMock()
        calls.attach_mock(embedded.install, "install")
        calls.attach_mock(embedded.stop, "stop")
        calls.attach_mock(embedded.start, "start")

        def replace_daemon():
            # The old daemon stops the server it owns on its way out.
            embedded.status.return_value = self._status()
            return "it runs GAIA 0.25.1, and 0.25.2 is installed"

        replace = MagicMock(side_effect=replace_daemon)
        calls.attach_mock(replace, "replace")

        ok, out = self._run_embedded(embedded, replace=replace)

        self.assertTrue(ok)
        self.assertEqual(
            [c[0] for c in calls.mock_calls], ["install", "replace", "start"]
        )
        self.assertIn("0.25.1", out)

    def test_a_stopped_server_also_replaces_an_outdated_daemon(self):
        embedded = self._embedded(self._status())
        ok, _ = self._run_embedded(embedded)

        self.assertTrue(ok)
        self.replace.assert_called_once_with()

    def test_a_current_running_server_leaves_the_daemon_to_hand_off(self):
        """The hand-off restarts an outdated daemon once it is free."""
        embedded = self._embedded(self._status(running=True, port=51234, pid=1))
        ok, _ = self._run_embedded(embedded)

        self.assertTrue(ok)
        self.replace.assert_not_called()

    def test_an_outdated_daemon_that_will_not_stop_fails_the_step(self):
        from gaia.daemon.errors import DaemonError

        old = self._status(running=True, version="0.0.1", port=1, pid=1)
        embedded = self._embedded(old)
        replace = MagicMock(side_effect=DaemonError("did not stop when asked"))
        ok, out = self._run_embedded(embedded, replace=replace)

        self.assertFalse(ok)
        self.assertIn("did not stop when asked", out)
        embedded.stop.assert_not_called()
        embedded.start.assert_not_called()

    def test_a_failed_download_leaves_the_old_server_running(self):
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        old = self._status(running=True, version="0.0.1", port=1, pid=1)
        embedded = self._embedded(old, installed=False)
        embedded.install.side_effect = EmbeddedLemonadeError("download failed")
        ok, out = self._run_embedded(embedded)

        self.assertFalse(ok)
        self.assertIn("download failed", out)
        embedded.stop.assert_not_called()
        self.replace.assert_not_called()

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
            "http://localhost:13305", health={"status": "ok", "version": "9999.0.0"}
        )

        self.assertTrue(ok)
        self.assertEqual(
            client_class.call_args.kwargs["base_url"], "http://localhost:13305/api/v1"
        )
        self.assertIn("v9999.0.0", out)

    def test_unreachable_configured_server_fails_and_says_how_to_recover(self):
        ok, out, _ = self._run_configured("http://gpu-box:13305", error="refused")

        self.assertFalse(ok)
        self.assertIn("refused", out)
        self.assertIn("unset LEMONADE_BASE_URL", out)

    def test_gaias_own_env_file_is_managed_not_merely_checked(self):
        import os

        os.environ["LEMONADE_BASE_URL"] = "http://localhost:50601/api/v1"
        os.environ["GAIA_LEMONADE_EMBEDDED"] = "1"
        embedded = self._embedded(self._status())
        ok, _ = self._run_embedded(embedded)

        self.assertTrue(ok)
        embedded.start.assert_called_once()

    def test_configured_server_below_the_minimum_is_refused(self):
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
            mock_client.get_system_info.return_value = SMALL_MACHINE
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
            mock_client.get_system_info.return_value = SMALL_MACHINE
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
            mock_client.get_system_info.return_value = SMALL_MACHINE
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
            mock_client.get_system_info.return_value = SMALL_MACHINE
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
            mock_client.get_system_info.return_value = NPU_LAPTOP
            mock_client.ensure_model_downloaded.return_value = True
            mock_client_class.return_value = mock_client

            result = cmd._download_models()
            self.assertTrue(result)
            pulled = {
                c.args[0] for c in mock_client.ensure_model_downloaded.call_args_list
            }
            self.assertEqual(pulled, {"gemma4-it-e4b-FLM", "embed-gemma-300m-FLM"})
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
            mock_client.get_system_info.return_value = SMALL_MACHINE
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
            mock_client.get_system_info.return_value = SMALL_MACHINE
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
            mock_client.get_system_info.return_value = SMALL_MACHINE
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

    def test_skip_models_verify_never_loads_the_chat_model(self):
        """--skip-models must not load the chat model to check its context:
        Lemonade pulls a model it is asked to load, so on a fresh PC that load
        silently downloaded the whole chat model (~21 GB for Qwen3.6)."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="gaia", yes=True, skip_models=True)

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_system_info.return_value = SMALL_MACHINE
            mock_client.health_check.return_value = True
            mock_client.check_model_available.return_value = False
            mock_client_class.return_value = mock_client

            with patch(
                "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
                return_value=True,
            ) as ensure_ready:
                result = cmd._verify_setup()
            ensure_ready.assert_not_called()
            mock_client.load_model.assert_not_called()
            self.assertTrue(result)

    def test_skip_models_says_the_named_chat_model_was_not_downloaded(self):
        """Naming the chat model with no caveat read as "this is installed"."""
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="gaia", yes=True, skip_models=True)
        printed = []
        cmd._print = lambda msg="", *a, **k: printed.append(str(msg))

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get_system_info.return_value = SMALL_MACHINE
            mock_client.health_check.return_value = True
            mock_client.check_model_available.return_value = False
            mock_client_class.return_value = mock_client
            with patch(
                "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
                return_value=True,
            ):
                cmd._verify_setup()

        out = "\n".join(printed)
        self.assertIn("Chat model:", out)
        self.assertIn("Not downloaded (--skip-models)", out)


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
        # The daemon boundary: a real call would start a real daemon and server.
        start = patch(
            "gaia.llm.lemonade_manager.LemonadeManager.start_embedded_if_stopped",
            return_value=False,
        )
        self.start_embedded = start.start()
        self.addCleanup(start.stop)
        # An installed flagship, recorded where `gaia init` records it; the
        # agent-missing tests delete it.
        import tempfile

        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        os.environ["GAIA_HOME"] = home.name
        os.environ.pop("GAIA_CONFIG_DIR", None)
        # A gaia-agent on this machine's PATH would count as installed.
        path_dir = tempfile.TemporaryDirectory()
        self.addCleanup(path_dir.cleanup)
        self.path_dir = Path(path_dir.name)
        os.environ["PATH"] = path_dir.name
        self._install_flagship()
        # The hub, as `gaia init` reads it: a build for this machine unless a
        # test serves a different one.
        self.hub_manifest = self._flagship_manifest(
            ("win32-x64", "linux-x64", "darwin-arm64")
        )
        self.hub_fetches = []
        self.host_key = "win32-x64"
        from gaia.hub import catalog as hub_catalog

        self.real_fetch_bytes = hub_catalog.fetch_bytes
        hub = patch("gaia.hub.catalog.fetch_bytes", side_effect=self._serve_hub)
        hub.start()
        self.addCleanup(hub.stop)
        for target in (
            "gaia.hub.installer.current_platform_key",
            "gaia.hub.compatibility.current_platform_key",
        ):
            host = patch(target, side_effect=lambda *a: self.host_key)
            host.start()
            self.addCleanup(host.stop)

    @staticmethod
    def _flagship_manifest(platform_keys):
        names = {
            "win32-x64": "gaia-agent-win32-x64.exe",
            "linux-x64": "gaia-agent-linux-x64",
            "darwin-arm64": "gaia-agent-darwin-arm64",
        }
        artifacts = [
            {
                "filename": names[k],
                "path": f"agents/gaia/0.3.2/{names[k]}",
                "size_bytes": 1,
                "sha256": "ab" * 32,
            }
            for k in platform_keys
        ]
        return {
            "id": "gaia",
            "language": "python",
            "security_tier": "verified",
            "latest_version": "0.3.2",
            "requirements": {"platforms": []},
            "versions": {
                "0.3.2": {
                    "version": "0.3.2",
                    "artifact": artifacts[0],
                    "artifacts": artifacts,
                }
            },
        }

    def _serve_hub(self, url, *args, **kwargs):
        import json

        self.hub_fetches.append(url)
        if url.startswith("file://"):
            return self.real_fetch_bytes(url)
        if isinstance(self.hub_manifest, bytes):
            return self.hub_manifest
        if self.hub_manifest is None:
            from gaia.hub.catalog import CatalogError

            raise CatalogError(f"could not reach {url}")
        if url.endswith("/agents/gaia/manifest.json"):
            return json.dumps(self.hub_manifest).encode("utf-8")
        raise AssertionError(f"the check must download nothing else: {url}")

    @staticmethod
    def _install_flagship():
        from gaia.hub import installer as hub_installer

        install_dir = hub_installer.agent_install_dir("gaia")
        install_dir.mkdir(parents=True, exist_ok=True)
        hub_installer._write_sentinel(
            "gaia",
            "0.3.2",
            "python",
            "ab" * 32,
            install_dir,
            artifact_kind=hub_installer.ARTIFACT_KIND_BINARY,
            executable="gaia-agent.exe",
        )
        return install_dir / hub_installer.SENTINEL_NAME

    @staticmethod
    def _remove_flagship():
        from gaia.hub import installer as hub_installer

        hub_installer._sentinel_path("gaia").unlink()

    def test_stopped_server_is_started_through_the_daemon_first(self):
        from gaia.installer.init_command import check_setup_status

        with self._embedded(self._status(installed=False)):
            check_setup_status(profile="chat")

        self.start_embedded.assert_called_once()

    def test_daemon_start_failure_is_the_reason(self):
        from gaia.daemon.errors import DaemonError
        from gaia.installer.init_command import check_setup_status

        self.start_embedded.side_effect = DaemonError("port taken")
        with self._embedded(self._status()):
            status = check_setup_status(profile="chat")

        self.assertEqual(
            status.reasons,
            ["GAIA's Lemonade Server could not be started: port taken"],
        )

    @staticmethod
    def _embedded(status, older=None):
        from gaia.version import LEMONADE_VERSION

        embedded = MagicMock()
        embedded.version = LEMONADE_VERSION
        embedded.status.return_value = status
        embedded.other_installed_version.return_value = older
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

    def test_older_embedded_version_requests_an_upgrade_not_first_run_setup(self):
        """An app upgrade replaces the embedded server but keeps model data."""
        from gaia.installer.init_command import check_setup_status

        running = self._status(
            running=True, version="0.0.1", port=1, pid=2, base_url="http://x/api/v1"
        )
        with self._embedded(running):
            status = check_setup_status(profile="chat")

        self.assertFalse(status.ready)
        self.assertEqual(status.stage, "upgrade")
        self.assertIn("v0.0.1", status.reasons[0])

    def test_new_pin_not_yet_unpacked_while_the_old_one_runs_is_an_upgrade(self):
        """After a GAIA upgrade the new version's dist dir does not exist yet."""
        from gaia.installer.init_command import check_setup_status

        running = self._status(
            installed=False,
            running=True,
            version="0.0.1",
            port=1,
            pid=2,
            base_url="http://x/api/v1",
        )
        with self._embedded(running):
            status = check_setup_status(profile="chat")

        self.assertEqual(status.stage, "upgrade")
        self.assertIn("v0.0.1", status.reasons[0])

    def test_new_pin_not_unpacked_with_only_an_older_one_on_disk_is_an_upgrade(self):
        from gaia.installer.init_command import check_setup_status

        with self._embedded(self._status(installed=False), older="0.0.1"):
            status = check_setup_status(profile="chat")

        self.assertEqual(status.stage, "upgrade")
        self.assertIn("v0.0.1", status.reasons[0])

    def test_nothing_on_disk_is_first_run_setup(self):
        from gaia.installer.init_command import check_setup_status

        with self._embedded(self._status(installed=False), older=None):
            status = check_setup_status(profile="chat")

        self.assertEqual(status.stage, "setup")

    def test_other_installed_version_reads_other_unpacked_versions(self):
        import platform
        import tempfile
        from pathlib import Path

        from gaia.llm.lemonade_embedded import EmbeddedLemonade

        with tempfile.TemporaryDirectory() as home:
            embedded = EmbeddedLemonade(home=Path(home))
            self.assertIsNone(embedded.other_installed_version())
            daemon = "lemond.exe" if platform.system() == "Windows" else "lemond"
            for version in ("9.2.0", "2026.45.3", embedded.version):
                d = embedded.root / "dist" / version
                d.mkdir(parents=True)
                (d / daemon).write_bytes(b"")
            # A half-unpacked dir with no daemon does not count.
            (embedded.root / "dist" / "99.0.0").mkdir()
            (embedded.root / "dist" / "not-a-version").mkdir()
            self.assertEqual(embedded.other_installed_version(), "2026.45.3")

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

    def test_unreadable_model_list_is_a_server_problem_not_missing_models(self):
        """#8058: a model list in the wrong shape made a finished install read
        as first-run setup, with every downloaded model "not downloaded"."""
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import LemonadeClient

        with (
            self._embedded(self._running()),
            patch.object(LemonadeClient, "health_check", return_value={}),
            patch.object(
                LemonadeClient,
                "_send_request",
                return_value={"object": "list", "data": "x"},
            ) as send,
        ):
            status = check_setup_status(profile="chat")

        self.assertFalse(status.ready)
        self.assertEqual(status.stage, "server")
        self.assertTrue(status.model_list_unread)
        self.assertFalse(any("not downloaded" in r for r in status.reasons))
        self.assertIn("expected shape", status.reasons[0])
        self.assertEqual(status.to_json()["model_list_unread"], True)
        self.assertTrue(send.call_args.args[1].endswith("/models?show_all=true"))

    def test_a_server_gone_before_its_model_list_is_not_a_bad_list(self):
        """Dying between /health and /models is an outage, not a bad list."""
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

        with (
            self._embedded(self._running()),
            patch.object(LemonadeClient, "health_check", return_value={}),
            patch.object(
                LemonadeClient,
                "_send_request",
                side_effect=LemonadeClientError("connection refused"),
            ),
        ):
            status = check_setup_status(profile="chat")

        self.assertEqual(status.stage, "server")
        self.assertFalse(status.model_list_unread)

    AGENT_MISSING = "The GAIA agent is not installed -- run: gaia init"

    def _check_with_models(self, profile="gaia", have=True, **kwargs):
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            client = self._loading_client(mock_client_class)
            client.check_model_available.return_value = have
            status = check_setup_status(profile=profile, **kwargs)
        return status, client

    def test_flagship_models_present_but_agent_missing_is_not_ready(self):
        """#6571: an existing Lemonade with the models skipped setup, so the
        agent was never installed and every chat failed."""
        self._remove_flagship()
        status, _ = self._check_with_models()

        self.assertFalse(status.ready)
        self.assertEqual(status.stage, "setup")
        self.assertEqual(status.reasons, [self.AGENT_MISSING])
        self.assertTrue(status.agent_missing)

    def test_installed_flagship_with_models_present_is_ready(self):
        """An existing user who has the agent is never sent back into setup."""
        status, _ = self._check_with_models()

        self.assertTrue(status.ready)
        self.assertEqual(status.reasons, [])

    def test_load_check_reports_the_missing_agent_before_loading_anything(self):
        """The apps verify with --load; a missing agent must not pass there, and
        setup is still owed, so minutes of model loads are skipped."""
        self._remove_flagship()
        status, client = self._check_with_models(load=True)

        self.assertFalse(status.ready)
        self.assertEqual(status.stage, "setup")
        self.assertEqual(status.reasons, [self.AGENT_MISSING])
        self.assertTrue(all(m.downloaded for m in status.models))
        client._ensure_model_loaded.assert_not_called()
        client.embeddings.assert_not_called()

    def test_skip_chat_model_still_needs_the_agent(self):
        """A Claude or cloud chat still runs through the flagship agent."""
        self._remove_flagship()
        status, _ = self._check_with_models(skip_chat_model=True)

        self.assertEqual(status.stage, "setup")
        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_missing_models_and_agent_are_both_reasons(self):
        self._remove_flagship()
        status, _ = self._check_with_models(have=False)

        self.assertEqual(status.stage, "setup")
        self.assertIn(self.AGENT_MISSING, status.reasons)
        self.assertTrue(any("is not downloaded" in r for r in status.reasons))

    def test_npu_profile_names_its_own_init_command(self):
        """A bare `gaia init` would switch an npu machine to the default models."""
        self._remove_flagship()
        status, _ = self._check_with_models(profile="npu")

        self.assertEqual(
            status.reasons,
            ["The GAIA agent is not installed -- run: gaia init --profile npu"],
        )

    def test_profiles_without_the_flagship_ignore_it(self):
        self._remove_flagship()
        for profile in ("chat", "minimal", "rag"):
            with self.subTest(profile=profile):
                status, _ = self._check_with_models(profile=profile)
                self.assertTrue(status.ready, status.reasons)

    def test_an_importable_checkout_package_is_not_an_installed_agent(self):
        """The apps run the flagship binary; a source checkout's importable
        gaia_agent package must not stand in for it."""
        import importlib.util

        self._remove_flagship()
        real_find_spec = importlib.util.find_spec
        with patch(
            "importlib.util.find_spec",
            side_effect=lambda name, *a: (
                MagicMock() if name == "gaia_agent" else real_find_spec(name, *a)
            ),
        ):
            status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_a_corrupt_sentinel_is_not_an_installed_agent(self):
        sentinel = self._install_flagship()
        sentinel.write_text("{not json", encoding="utf-8")

        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_a_sentinel_with_an_unsafe_executable_is_not_an_installed_agent(self):
        import json

        sentinel = self._install_flagship()
        data = json.loads(sentinel.read_text(encoding="utf-8"))
        data["executable"] = "..\\..\\evil.exe"
        sentinel.write_text(json.dumps(data), encoding="utf-8")

        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_sentinel_is_read_from_gaia_home_not_the_profile_home(self):
        """The check and `gaia init` resolve one agents dir, honouring GAIA_HOME."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as elsewhere:
            os.environ["GAIA_HOME"] = elsewhere
            status, _ = self._check_with_models()
            self.assertEqual(status.reasons, [self.AGENT_MISSING])

            self._install_flagship()
            status, _ = self._check_with_models()
            self.assertTrue(status.ready, status.reasons)

    def test_an_older_server_is_an_upgrade_even_with_no_agent(self):
        """#6477: an update is not first-run setup, whatever else is missing."""
        from gaia.installer.init_command import check_setup_status

        self._remove_flagship()
        older = self._status(running=True, version="10.0.0", port=51234, pid=42)
        with self._embedded(older):
            status = check_setup_status(profile="gaia")

        self.assertEqual(status.stage, "upgrade")

    def test_installed_agent_never_asks_the_hub(self):
        status, _ = self._check_with_models()

        self.assertTrue(status.ready)
        self.assertEqual(self.hub_fetches, [])

    @staticmethod
    def _terminal_agent_path():
        from gaia.home import gaia_home

        name = "gaia-agent.exe" if sys.platform == "win32" else "gaia-agent"
        return gaia_home() / "bin" / name

    def _install_terminal_agent(self, mode=0o755, record=True):
        """What install.sh / install.ps1 leave: the agent in GAIA's bin dir and
        its install record beside it, no sentinel."""
        import hashlib
        import json

        path = self._terminal_agent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x7fELF")
        path.chmod(mode)
        if record:
            (path.parent / "gaia-agent.install.json").write_text(
                json.dumps(
                    {
                        "id": "gaia",
                        "version": "0.3.9",
                        "sha256": hashlib.sha256(b"\x7fELF").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
        return path

    def test_terminal_installer_agent_is_ready(self):
        """#6642: install.sh / install.ps1 put the agent in ~/.gaia/bin, not
        under ~/.gaia/agents, and the TUI runs it from there."""
        self._remove_flagship()
        self._install_terminal_agent()
        status, _ = self._check_with_models()

        self.assertTrue(status.ready)
        self.assertFalse(status.agent_missing)
        self.assertEqual(status.reasons, [])
        self.assertEqual(status.warnings, [])

    def test_terminal_installer_agent_without_a_record_is_not_ready(self):
        """#7187: the daemon cannot verify a copy with no install record, so the
        check must not count it either."""
        self._remove_flagship()
        self._install_terminal_agent(record=False)
        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])
        self.assertTrue(status.agent_missing)

    def test_a_tampered_terminal_installer_agent_is_not_ready(self):
        self._remove_flagship()
        self._install_terminal_agent().write_bytes(b"\x7fELF-replaced")
        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])
        self.assertTrue(status.agent_missing)

    def test_a_corrupt_terminal_install_record_is_not_ready(self):
        self._remove_flagship()
        path = self._install_terminal_agent()
        (path.parent / "gaia-agent.install.json").write_text("{not json")
        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_terminal_installer_agent_never_asks_the_hub(self):
        self._remove_flagship()
        self._install_terminal_agent()
        status, _ = self._check_with_models()

        self.assertTrue(status.ready)
        self.assertEqual(self.hub_fetches, [])

    def test_terminal_installer_agent_still_needs_the_models(self):
        self._remove_flagship()
        self._install_terminal_agent()
        status, _ = self._check_with_models(have=False)

        self.assertEqual(status.stage, "setup")
        self.assertFalse(status.agent_missing)
        self.assertNotIn(self.AGENT_MISSING, status.reasons)

    def _write_agent(self, directory, mode=0o755):
        name = "gaia-agent.exe" if sys.platform == "win32" else "gaia-agent"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(b"\x7fELF")
        path.chmod(mode)
        return path

    def _frozen_at(self, directory):
        """Run the check as the frozen gaia in *directory*."""
        name = "gaia.exe" if sys.platform == "win32" else "gaia"
        frozen = patch("gaia.frozen.is_frozen", return_value=True)
        exe = patch.object(sys, "executable", str(directory / name))
        frozen.start()
        exe.start()
        self.addCleanup(frozen.stop)
        self.addCleanup(exe.stop)

    def test_agent_beside_the_frozen_gaia_is_ready(self):
        """The Windows setup's terminal component installs gaia.exe and
        gaia-agent.exe side by side, with no sentinel and nothing in ~/.gaia/bin."""
        import tempfile

        self._remove_flagship()
        install = tempfile.TemporaryDirectory()
        self.addCleanup(install.cleanup)
        self._write_agent(Path(install.name))
        self._frozen_at(Path(install.name))

        status, _ = self._check_with_models()

        self.assertTrue(status.ready, status.reasons)
        self.assertFalse(status.agent_missing)
        self.assertEqual(self.hub_fetches, [])

    def test_agent_on_path_is_ready(self):
        """The .deb, .rpm and .pkg put gaia-agent in /usr/bin or /usr/local/bin
        and the frozen gaia under lib/gaia, so only PATH finds the agent."""
        import tempfile

        self._remove_flagship()
        self._write_agent(self.path_dir)
        core = tempfile.TemporaryDirectory()
        self.addCleanup(core.cleanup)
        self._frozen_at(Path(core.name))

        status, _ = self._check_with_models()

        self.assertTrue(status.ready, status.reasons)
        self.assertFalse(status.agent_missing)

    def test_agent_on_a_pip_install_path_is_not_the_agent(self):
        """A pip or source gaia: a gaia-agent console script on PATH is not
        something a GAIA installer shipped."""
        self._remove_flagship()
        self._write_agent(self.path_dir)

        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_frozen_gaia_with_no_agent_anywhere_still_needs_it(self):
        """A terminal install whose agent is gone must still send the user to setup."""
        import tempfile

        self._remove_flagship()
        install = tempfile.TemporaryDirectory()
        self.addCleanup(install.cleanup)
        self._frozen_at(Path(install.name))

        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])
        self.assertTrue(status.agent_missing)

    def test_a_directory_named_gaia_agent_beside_the_frozen_gaia_is_not_the_agent(self):
        import tempfile

        self._remove_flagship()
        install = tempfile.TemporaryDirectory()
        self.addCleanup(install.cleanup)
        name = "gaia-agent.exe" if sys.platform == "win32" else "gaia-agent"
        (Path(install.name) / name).mkdir()
        self._frozen_at(Path(install.name))

        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_agent_beside_a_source_interpreter_is_not_the_agent(self):
        """From a checkout sys.executable is Python; a stray gaia-agent in its
        folder is not something an installer shipped."""
        import tempfile

        self._remove_flagship()
        venv = tempfile.TemporaryDirectory()
        self.addCleanup(venv.cleanup)
        self._write_agent(Path(venv.name))
        with patch.object(sys, "executable", str(Path(venv.name) / "python")):
            status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    @unittest.skipIf(sys.platform == "win32", "Windows has no exec bit")
    def test_non_executable_agent_beside_the_frozen_gaia_is_not_the_agent(self):
        import tempfile

        self._remove_flagship()
        install = tempfile.TemporaryDirectory()
        self.addCleanup(install.cleanup)
        self._write_agent(Path(install.name), mode=0o644)
        self._frozen_at(Path(install.name))

        status, _ = self._check_with_models()

        self.assertEqual(status.reasons, [self.AGENT_MISSING])

    def test_only_the_tui_in_bin_still_needs_the_agent(self):
        """A terminal install whose agent download failed leaves gaia-tui alone."""
        from gaia.home import gaia_home

        self._remove_flagship()
        bin_dir = gaia_home() / "bin"
        bin_dir.mkdir(parents=True)
        tui = "gaia-tui.exe" if sys.platform == "win32" else "gaia-tui"
        (bin_dir / tui).write_bytes(b"x")
        self.host_key = "darwin-arm64"
        status, _ = self._check_with_models()

        self.assertEqual(status.stage, "setup")
        self.assertTrue(status.agent_missing)

    def test_a_directory_named_like_the_agent_is_not_the_agent(self):
        self._remove_flagship()
        self._terminal_agent_path().mkdir(parents=True)
        status, _ = self._check_with_models()

        self.assertEqual(status.stage, "setup")
        self.assertTrue(status.agent_missing)

    @unittest.skipIf(sys.platform == "win32", "Windows has no exec bit")
    def test_a_non_executable_terminal_agent_is_not_the_agent(self):
        """A half-written download (no exec bit yet) must not pass as installed."""
        self._remove_flagship()
        self._install_terminal_agent(mode=0o644)
        status, _ = self._check_with_models()

        self.assertEqual(status.stage, "setup")
        self.assertTrue(status.agent_missing)

    def test_terminal_agent_outside_this_gaia_home_does_not_count(self):
        """An isolated GAIA_HOME never borrows another home's agent."""
        import os
        import tempfile

        self._remove_flagship()
        self._install_terminal_agent()
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        os.environ["GAIA_HOME"] = other.name
        status, _ = self._check_with_models()

        self.assertEqual(status.stage, "setup")
        self.assertTrue(status.agent_missing)

    def _on_a_pc_without_a_build(self):
        self._remove_flagship()
        self.host_key = "linux-arm64"

    def test_no_agent_build_for_this_pc_is_not_a_setup_loop(self):
        """init finishes without the agent here, so setup must not be offered
        again on every launch; the check says why chat is unavailable."""
        self._on_a_pc_without_a_build()
        status, _ = self._check_with_models()

        self.assertTrue(status.ready)
        self.assertIsNone(status.stage)
        self.assertEqual(status.reasons, [])
        self.assertFalse(status.agent_missing)
        self.assertEqual(len(status.warnings), 1)
        self.assertIn(
            "No GAIA agent build for this PC (linux-arm64)", status.warnings[0]
        )
        self.assertIn("chat isn't available here", status.warnings[0])

    def test_no_agent_build_still_proves_the_models_load(self):
        self._on_a_pc_without_a_build()
        status, client = self._check_with_models(load=True)

        self.assertTrue(status.ready)
        self.assertEqual(len(status.warnings), 1)
        client._ensure_model_loaded.assert_called_once()

    def test_no_agent_build_does_not_hide_missing_models(self):
        self._on_a_pc_without_a_build()
        status, _ = self._check_with_models(have=False)

        self.assertEqual(status.stage, "setup")
        self.assertFalse(status.agent_missing)
        self.assertNotIn(self.AGENT_MISSING, status.reasons)
        self.assertEqual(len(status.warnings), 1)

    def test_a_supported_pc_without_the_agent_still_needs_setup(self):
        self._remove_flagship()
        self.host_key = "darwin-arm64"
        status, _ = self._check_with_models()

        self.assertEqual(status.stage, "setup")
        self.assertTrue(status.agent_missing)
        self.assertEqual(status.warnings, [])

    def test_an_unreachable_hub_reports_the_agent_missing(self):
        """Not knowing is not "no build": setup then names the hub error."""
        self._remove_flagship()
        self.hub_manifest = None
        status, _ = self._check_with_models()

        self.assertEqual(status.stage, "setup")
        self.assertTrue(status.agent_missing)
        self.assertEqual(len(status.reasons), 1)
        self.assertTrue(status.reasons[0].startswith(self.AGENT_MISSING))
        self.assertIn("GAIA_HUB_URL", status.reasons[0])

    def _assert_unreadable_hub_names_its_url(self, status, url):
        self.assertFalse(status.ready)
        self.assertEqual(status.stage, "setup")
        self.assertTrue(status.agent_missing)
        self.assertFalse(status.no_agent_build)
        self.assertEqual(len(status.reasons), 1)
        reason = status.reasons[0]
        self.assertTrue(reason.startswith(self.AGENT_MISSING), reason)
        self.assertIn(url, reason)
        self.assertIn("GAIA_HUB_URL", reason)

    def test_a_captive_portal_page_is_not_a_crash(self):
        """A 200 that is HTML, not the manifest, must not crash the check."""
        import os

        os.environ["GAIA_HUB_URL"] = "https://hub.example.test"
        self._remove_flagship()
        self.hub_manifest = b"<html><body>Sign in to the Wi-Fi</body></html>"
        status, _ = self._check_with_models()

        self._assert_unreadable_hub_names_its_url(status, "https://hub.example.test")

    def test_a_file_hub_that_is_not_there_is_not_a_crash(self):
        import os
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as root:
            url = (Path(root) / "no-such-hub").as_uri()
            os.environ["GAIA_HUB_URL"] = url
            self._remove_flagship()
            status, _ = self._check_with_models()

        self._assert_unreadable_hub_names_its_url(status, url)

    def test_a_malformed_manifest_is_not_a_crash(self):
        """Valid JSON of the wrong shape (artifacts as a dict) used to crash."""
        import os

        os.environ["GAIA_HUB_URL"] = "https://hub.example.test"
        self._remove_flagship()
        entry = self.hub_manifest["versions"]["0.3.2"]
        entry["artifacts"] = {a["filename"]: a for a in entry["artifacts"]}
        status, _ = self._check_with_models()

        self._assert_unreadable_hub_names_its_url(
            status, "https://hub.example.test/agents/gaia/manifest.json"
        )
        self.assertIn("artifacts is dict, not a list", status.reasons[0])

    def test_a_full_disk_is_reported_not_read_as_no_build(self):
        from gaia.hub import installer as hub_installer

        self._remove_flagship()
        with patch.object(
            hub_installer,
            "no_build_reason",
            side_effect=hub_installer.DiskSpaceError("Not enough disk space"),
        ):
            status, _ = self._check_with_models()

        self.assertEqual(status.stage, "setup")
        self.assertIn("Not enough disk space", status.reasons[0])

    def test_no_agent_build_is_flagged_for_the_apps(self):
        self._on_a_pc_without_a_build()
        status, _ = self._check_with_models()

        self.assertTrue(status.no_agent_build)
        self.assertTrue(status.to_json()["no_agent_build"])

    def test_no_agent_build_json_keeps_the_exit_code_ready(self):
        import json

        self._on_a_pc_without_a_build()
        status, _ = self._check_with_models()
        payload = json.loads(json.dumps(status.to_json()))

        self.assertTrue(payload["ready"])
        self.assertFalse(payload["agent_missing"])
        self.assertEqual(len(payload["warnings"]), 1)

    def test_a_server_that_will_not_answer_outranks_a_missing_agent(self):
        from gaia.installer.init_command import check_setup_status

        self._remove_flagship()
        with self._embedded(self._status()):
            status = check_setup_status(profile="gaia")

        self.assertEqual(status.stage, "server")

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

    def test_configured_server_below_the_profile_minimum_is_not_ready(self):
        """`gaia init` refuses it, so `--check` must not call it ready."""
        import os

        from gaia.installer.init_command import check_setup_status

        os.environ["LEMONADE_BASE_URL"] = "http://gpu-box:13305"
        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class:
            mock_client = mock_client_class.return_value
            mock_client.health_check.return_value = {"version": "1.0.0"}
            mock_client.check_model_available.return_value = True
            status = check_setup_status(profile="chat")

        self.assertFalse(status.ready)
        self.assertIn("v1.0.0", status.reasons[0])
        mock_client.check_model_available.assert_not_called()

    def test_gaias_own_env_file_is_not_a_configured_server(self):
        """Sourcing GAIA's credentials file must not opt out of its management."""
        import os

        from gaia.installer.init_command import check_setup_status

        os.environ["LEMONADE_BASE_URL"] = "http://localhost:50601/api/v1"
        os.environ["GAIA_LEMONADE_EMBEDDED"] = "1"
        with self._embedded(self._status()):
            status = check_setup_status(profile="chat")

        self.assertEqual(
            status.reasons, ["GAIA's Lemonade Server is installed but not running"]
        )

    def test_unlistable_profile_models_are_a_reason_not_a_traceback(self):
        from gaia.installer.init_command import INIT_PROFILES, check_setup_status
        from gaia.llm.lemonade_client import LemonadeClientError

        profile = next(n for n, c in INIT_PROFILES.items() if not c["models"])
        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            mock_client_class.return_value.get_required_models.side_effect = (
                LemonadeClientError("500")
            )
            status = check_setup_status(profile=profile)

        self.assertFalse(status.ready)
        self.assertIn("500", status.reasons[0])

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
            mock_client.get_system_info.return_value = SMALL_MACHINE
            mock_client.check_model_available.return_value = False
            status = check_setup_status(profile="chat", skip_chat_model=True)

        self.assertFalse(status.ready)
        self.assertEqual(
            status.reasons,
            ["Model 'user.embeddinggemma-300m-GGUF' is not downloaded"],
        )
        checked = {c.args[0] for c in mock_client.check_model_available.call_args_list}
        self.assertEqual(checked, {"user.embeddinggemma-300m-GGUF"})

    def _loading_client(self, mock_client_class, embed_error=None):
        from gaia.llm.lemonade_client import LemonadeClientError

        client = mock_client_class.return_value
        client.check_model_available.return_value = True
        client.get_model_info.side_effect = lambda m: {"id": m, "size_gb": 0.3}
        if embed_error:
            client.embeddings.side_effect = LemonadeClientError(embed_error)
        else:
            client.embeddings.return_value = {"data": [{"embedding": [0.1] * 768}]}
        return client

    def test_load_reports_a_downloaded_embedder_that_will_not_load(self):
        """Downloaded is not working: the #4449 embedder passed --check and
        then failed on the first chat turn."""
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            client = self._loading_client(
                mock_client_class, embed_error="model_load_error"
            )
            status = check_setup_status(profile="gaia", skip_chat_model=True, load=True)

        self.assertFalse(status.ready)
        self.assertEqual(status.stage, "load")
        self.assertEqual(
            [m.id for m in status.models], ["user.embeddinggemma-300m-GGUF"]
        )
        self.assertFalse(status.models[0].loaded)
        self.assertIn("model_load_error", status.models[0].error)
        self.assertIn("would not load", status.reasons[0])
        client._ensure_model_loaded.assert_not_called()

    def test_load_loads_the_chat_model_and_embeds_one_word(self):
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            client = self._loading_client(mock_client_class)
            status = check_setup_status(profile="gaia", load=True)

        self.assertTrue(status.ready)
        self.assertEqual(
            {m.id: (m.role, m.loaded) for m in status.models},
            {
                "user.embeddinggemma-300m-GGUF": ("embedding", True),
                "Gemma-4-E4B-it-GGUF": ("chat", True),
            },
        )
        client._ensure_model_loaded.assert_called_once_with("Gemma-4-E4B-it-GGUF")
        client.embeddings.assert_called_once()
        self.assertEqual(
            client.embeddings.call_args.kwargs["model"], "user.embeddinggemma-300m-GGUF"
        )

    def test_chat_model_replaces_the_default_in_what_is_loaded(self):
        """Presence stays the profile's — setup can only download those — while
        the load proves the model the session will actually use."""
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            client = self._loading_client(mock_client_class)
            status = check_setup_status(
                profile="gaia", load=True, chat_model="Qwen3-4B-Instruct-GGUF"
            )

        self.assertTrue(status.ready)
        checked = {c.args[0] for c in client.check_model_available.call_args_list}
        self.assertEqual(
            checked, {"user.embeddinggemma-300m-GGUF", "Gemma-4-E4B-it-GGUF"}
        )
        client._ensure_model_loaded.assert_called_once_with("Qwen3-4B-Instruct-GGUF")

    def test_a_probe_crash_is_reported_as_that_models_failure(self):
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            client = self._loading_client(mock_client_class)
            client.embeddings.side_effect = KeyError("data")
            status = check_setup_status(profile="gaia", skip_chat_model=True, load=True)

        self.assertEqual(status.stage, "load")
        self.assertIn("KeyError", status.models[0].error)

    def test_a_server_that_will_not_answer_is_not_a_setup_step(self):
        from gaia.installer.init_command import check_setup_status

        with self._embedded(self._status()):
            status = check_setup_status(profile="chat")

        self.assertEqual(status.stage, "server")

    def test_missing_models_carry_their_download_size_when_asked(self):
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
        ):
            client = self._loading_client(mock_client_class)
            client.check_model_available.return_value = False
            client.list_models.return_value = {
                "data": [{"id": "user.embeddinggemma-300m-GGUF", "size": 0.3}]
            }
            status = check_setup_status(profile="gaia", skip_chat_model=True, load=True)

        self.assertEqual(status.stage, "setup")
        self.assertEqual(status.models[0].size_gb, 0.3)
        self.assertFalse(status.models[0].loaded)
        client.embeddings.assert_not_called()

    def test_missing_model_sizes_match_the_setup_plan(self):
        """The first-run step and "Choose your models" show one download size:
        the catalog's size less what an interrupted pull left on disk."""
        from gaia.installer import init_command
        from gaia.installer.init_command import check_setup_status

        with (
            self._embedded(self._running()),
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_client_class,
            patch.object(
                init_command,
                "bytes_already_on_disk",
                side_effect=lambda _c, ids, store: {m: 100_000_000 for m in ids},
            ) as on_disk,
        ):
            client = self._loading_client(mock_client_class)
            client.check_model_available.return_value = False
            client.list_models.return_value = {
                "data": [{"id": "user.embeddinggemma-300m-GGUF", "size": 0.33}]
            }
            client.get_system_info.return_value = {
                "model_storage": {"path": "/models", "free_bytes": 10**12}
            }
            status = check_setup_status(profile="gaia", skip_chat_model=True, load=True)

        self.assertEqual(status.models[0].size_gb, 0.23)
        self.assertEqual(on_disk.call_args.args[2], "/models")
        self.assertEqual(
            init_command.remaining_download_gb(0.33, 100_000_000),
            status.models[0].size_gb,
        )


class TestInstallPipExtras(unittest.TestCase):
    """Profiles without extras never shell out."""

    def _make_cmd(self, profile):
        from gaia.installer.init_command import InitCommand

        return InitCommand(profile=profile, yes=True)

    def test_no_extras_skips_install(self):
        """Profiles without pip_extras short-circuit without shelling out."""
        cmd = self._make_cmd("minimal")  # minimal declares no pip_extras
        with patch("subprocess.run") as mock_run:
            self.assertTrue(cmd._install_pip_extras())
            mock_run.assert_not_called()


class TestDiagnoseLaunchpadOutage(unittest.TestCase):
    """diagnose_launchpad_outage names a Launchpad outage in apt's output.

    CI's Linux runners install Lemonade from the PPA and use this to tell an
    upstream outage from a real failure.
    """

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

    # Trimmed from CI on 2026-11-11: add-apt.log, then apt-update.log.
    ADD_APT_API_TIMEOUT = (
        "Traceback (most recent call last):\n"
        '  File "/usr/bin/add-apt-repository", line 452, in <module>\n'
        "    sys.exit(0 if addaptrepo.main() else 1)\n"
        '  File "/usr/lib/python3/dist-packages/softwareproperties/ppa.py", '
        "line 111, in lp\n"
        '  File "/usr/lib/python3/dist-packages/launchpadlib/launchpad.py", '
        "line 494, in login_anonymously\n"
        '  File "/usr/lib/python3/dist-packages/httplib2/__init__.py", '
        "line 1139, in connect\n"
        "    sock.connect((self.host, self.port))\n"
        "TimeoutError: [Errno 110] Connection timed out\n"
    )
    APT_UPDATE_ONLINE = (
        "Get:1 file:/etc/apt/apt-mirrors.txt Mirrorlist [144 B]\n"
        "Hit:2 http://azure.archive.ubuntu.com/ubuntu noble InRelease\n"
        "Hit:6 https://packages.microsoft.com/ubuntu/24.04/prod noble InRelease\n"
        "Reading package lists...\n"
    )

    def test_launchpad_api_timeout_is_named_as_an_outage(self):
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        message = diagnose_launchpad_outage(
            self.ADD_APT_API_TIMEOUT + self.APT_UPDATE_ONLINE
        )
        self.assertIn("could not reach Launchpad", message)
        self.assertIn("TimeoutError: [Errno 110] Connection timed out", message)
        self.assertNotIn("Traceback", message)

    def test_dropped_connection_names_the_last_error(self):
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        dropped_then_timed_out = (
            "http.client.RemoteDisconnected: Remote end closed connection "
            "without response\n"
            "During handling of the above exception, another exception occurred:\n"
            + self.ADD_APT_API_TIMEOUT
            + self.APT_UPDATE_ONLINE
        )
        message = diagnose_launchpad_outage(dropped_then_timed_out)
        self.assertIn("Connection timed out", message)
        self.assertNotIn("RemoteDisconnected", message)

    def test_timeout_on_an_offline_machine_is_not_blamed_on_launchpad(self):
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        offline = (
            "Get:1 file:/etc/apt/apt-mirrors.txt Mirrorlist [144 B]\n"
            "Err:2 http://azure.archive.ubuntu.com/ubuntu noble InRelease\n"
            "  Temporary failure resolving 'azure.archive.ubuntu.com'\n"
        )
        self.assertIsNone(diagnose_launchpad_outage(self.ADD_APT_API_TIMEOUT + offline))

    def test_timeout_outside_launchpadlib_is_not_blamed_on_launchpad(self):
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        elsewhere = (
            "Traceback (most recent call last):\n"
            '  File "/usr/lib/python3/dist-packages/urllib3/connection.py", '
            "line 199, in _new_conn\n"
            "TimeoutError: [Errno 110] Connection timed out\n"
        )
        self.assertIsNone(diagnose_launchpad_outage(elsewhere + self.APT_UPDATE_ONLINE))

    def test_index_outage_blames_launchpad_not_apt(self):
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        message = diagnose_launchpad_outage(self.APT_UPDATE_503)
        self.assertIn("Launchpad", message)
        self.assertIn("503", message)
        self.assertNotIn("dpkg --configure", message)

    def test_key_outage_is_named_without_the_traceback(self):
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        message = diagnose_launchpad_outage(self.ADD_APT_GPG_500)
        self.assertIn("Launchpad", message)
        self.assertIn("signing key", message)
        self.assertNotIn("Traceback", message)

    def test_unrelated_repositories_are_not_blamed_on_launchpad(self):
        from gaia.installer.lemonade_installer import diagnose_launchpad_outage

        unrelated = (
            "W: Failed to fetch https://ppa.launchpadcontent.net/deadsnakes/ppa/"
            "ubuntu/dists/noble/InRelease  404  Not Found\n"
        )
        self.assertIsNone(diagnose_launchpad_outage(unrelated))
        self.assertIsNone(diagnose_launchpad_outage("Reading package lists... Done\n"))


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


# What a pip-install-from-a-git-checkout remedy looks like. Setup must never
# print one: it needs git and is not the supported install path (#2972).
_SOURCE_INSTALL_MARKER = "git+https://"


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


class TestHubInstallWiringFlagshipProfile(_HubInstallWiringTestBase):
    """AC: `gaia init` (the flagship profile) installs its agent from the Hub
    when it isn't already present, and skips the install when it is.

    Was the `chat` profile until `chat` left the catalog: the wheel is
    source-install only, so `gaia` is now the only id `gaia init` can fetch.
    """

    def _sentinel_after_install(self, install_side_effect=None):
        """A hub install writes the agent's sentinel; the completion check reads
        it back, so a stubbed install has to leave one behind too."""
        installed = []

        def _install(*args, **kwargs):
            installed.append(args[0])
            if install_side_effect is not None:
                install_side_effect(*args, **kwargs)
            return MagicMock(hot_registered=True)

        sentinel = patch(
            "gaia.hub.installer.read_sentinel",
            side_effect=lambda agent_id: {"id": agent_id} if installed else None,
        )
        return _install, sentinel

    def test_installs_flagship_agent_when_not_available_and_published(self):
        cmd = self._make_cmd("gaia")
        self._patch_common_steps(cmd)
        install, sentinel = self._sentinel_after_install()
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result(
                    [{"id": "gaia", "latest_version": "0.1.0"}]
                ),
            ),
            patch("gaia.hub.installer.install", side_effect=install) as mock_install,
            sentinel,
        ):
            rc = cmd.run()

        self.assertEqual(rc, 0)
        mock_install.assert_called_once()

    def test_npu_profile_installs_the_flagship_too(self):
        """#2972: the NPU profile left the Agent UI with zero agents because it
        installed none. It now takes the flagship through the same hub path."""
        cmd = self._make_cmd("npu")
        self._patch_common_steps(cmd)
        install, sentinel = self._sentinel_after_install()
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result(
                    [{"id": "gaia", "latest_version": "0.1.0"}]
                ),
            ),
            patch("gaia.hub.installer.install", side_effect=install) as mock_install,
            sentinel,
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            rc = cmd.run()

        self.assertEqual(rc, 0)
        mock_install.assert_called_once_with("gaia", trusted=True)
        self.assertNotIn(_SOURCE_INSTALL_MARKER, mock_stdout.getvalue())

    def test_npu_profile_with_unreachable_hub_is_incomplete(self):
        """Same contract as the default profile: no agent, non-zero exit."""
        cmd = self._make_cmd("npu")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                side_effect=OSError("network is unreachable"),
            ),
            patch("gaia.hub.installer.install") as mock_install,
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            rc = cmd.run()

        self.assertNotEqual(rc, 0)
        mock_install.assert_not_called()
        out = mock_stdout.getvalue()
        self.assertIn("not installed yet -- run: gaia init --profile npu", out)
        self.assertNotIn("gaia hub", out)
        self.assertNotIn(_SOURCE_INSTALL_MARKER, out)

    def test_skips_install_when_flagship_agent_already_available(self):
        cmd = self._make_cmd("gaia")
        self._patch_common_steps(cmd)
        with (
            # Only the installed binary counts for the flagship.
            patch("gaia.hub.installer.read_sentinel", return_value=MagicMock()),
            patch.object(cmd, "_installed_agent_is_stale", return_value=False),
            patch("gaia.hub.prerelease.flagship_source", return_value=None),
            patch("gaia.hub.installer.install") as mock_install,
        ):
            rc = cmd.run()

        self.assertEqual(rc, 0)
        mock_install.assert_not_called()

    def test_hub_install_runs_after_download_models_and_pip_extras_still_runs(self):
        """Ordering: the hub-install attempt happens AFTER `_download_models()`
        (models must exist before the agent that uses them is wired in), and
        the `[rag]` pip-extras step still runs independently -- the hub
        install targets `~/.gaia/agents/gaia/` while the extras step targets
        the ACTIVE interpreter's site-packages; one must not replace or block
        the other (#2358 plan amendment A9).
        """
        cmd = self._make_cmd("gaia")
        order = []
        self._patch_common_steps(cmd, order)

        install, sentinel = self._sentinel_after_install(
            lambda *_a, **_k: order.append("hub_install")
        )
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result([{"id": "gaia"}]),
            ),
            patch("gaia.hub.installer.install", side_effect=install),
            sentinel,
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
    """AC: `gaia init` never reports success without the profile's hub agent.

    A failed install, an unreachable catalog and a catalog that does not list
    the agent all leave it missing, so all three exit non-zero. The last two
    still finish the other steps, so the models are not downloaded twice.
    """

    def test_returns_nonzero_when_published_install_genuinely_fails(self):
        from gaia.hub.installer import InstallError

        cmd = self._make_cmd("gaia")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result(
                    [{"id": "gaia", "latest_version": "0.1.0"}]
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

    def _run_with_catalog(self, **load_index):
        cmd = self._make_cmd("gaia")
        order = []
        self._patch_common_steps(cmd, order)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch("gaia.hub.catalog.load_index", **load_index),
            patch("gaia.hub.installer.install") as mock_install,
            patch.object(cmd, "_print_error") as error,
            patch("gaia.installer.init_command.RICH_AVAILABLE", False),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = cmd.run()
        mock_install.assert_not_called()
        errors = " ".join(str(c.args[0]) for c in error.call_args_list)
        return rc, errors, order, stdout.getvalue()

    def test_agent_missing_from_catalog_fails(self):
        rc, errors, _, out = self._run_with_catalog(
            return_value=_fake_catalog_result([])
        )

        self.assertEqual(rc, 1)
        self.assertIn("does not list the 'gaia' agent", errors)
        self.assertIn("run `gaia init` again", errors)
        self.assertNotIn("gaia hub", errors)
        self.assertIn("incomplete", out)

    def test_unreachable_catalog_fails_with_an_actionable_error(self):
        """Before: "Setup continues without it" and exit 0, so the Agent UI
        wizard said "GAIA is ready" with no agent to run."""
        from gaia.hub.catalog import DEFAULT_HUB_URL

        rc, errors, order, out = self._run_with_catalog(
            side_effect=ConnectionError("hub unreachable")
        )

        self.assertEqual(rc, 1)
        self.assertIn(f"download server at {DEFAULT_HUB_URL}", errors)
        self.assertIn("hub unreachable", errors)
        self.assertIn("run `gaia init` again", errors)
        self.assertNotIn("gaia hub", errors)
        self.assertIn("incomplete", out)
        self.assertNotIn("initialization complete", out)
        # The remaining steps still ran; only the agent is missing.
        self.assertIn("verify", order)

    def test_untrusted_proxy_certificate_names_the_fix(self):
        from gaia.hub.catalog import CatalogError

        ssl_error = OSError(
            "HTTPSConnectionPool(host='hub.amd-gaia.ai', port=443): Max retries "
            "exceeded (Caused by SSLError(SSLCertVerificationError(1, '[SSL: "
            "CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
            "local issuer certificate')))"
        )
        wrapped = CatalogError("Could not reach the GAIA Agent Hub")
        wrapped.__cause__ = ssl_error

        rc, errors, _, _ = self._run_with_catalog(side_effect=wrapped)

        self.assertEqual(rc, 1)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", errors)
        self.assertIn("root certificate in the system certificate store", errors)
        self.assertIn("https://amd-gaia.ai/docs/reference/troubleshooting", errors)

    def test_no_build_for_this_machine_still_exits_zero(self):
        """Nothing the user can run would add the agent, so this is not a
        failure -- but the headline must not claim the agent is there."""
        from gaia.hub.installer import UnsupportedPlatformError

        cmd = self._make_cmd("gaia")
        self._patch_common_steps(cmd)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result([{"id": "gaia"}]),
            ),
            patch(
                "gaia.hub.installer.install",
                side_effect=UnsupportedPlatformError("no artifact for win-arm64"),
            ),
            patch("gaia.installer.init_command.RICH_AVAILABLE", False),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            rc = cmd.run()

        self.assertEqual(rc, 0)
        self.assertIn("has no build for this machine", stdout.getvalue())
        self.assertNotIn("GAIA initialization complete!", stdout.getvalue())


class TestHubInstallWiringFlagshipOnlyScope(_HubInstallWiringTestBase):
    """AC: only profiles whose declared agent is in ``HUB_INSTALL_AGENTS``
    trigger the hub install. A generic "install the profile's agent" would
    make `gaia init --profile sd/rag/vlm/minimal/all` hard-fail, since none
    of those agents are in the hub index (#2358 review finding).

    `chat` is on this list: there is no chat agent package (#6103), so
    asking the Hub for it could only ever no-op. Its completion
    message says no chat agent is installed and names `gaia hub install gaia`
    -- see ``TestPrintCompletionHeadlineGate``. `npu` is not: it installs the
    flagship (#2972).
    """

    NON_HUB_PROFILES = ("sd", "rag", "vlm", "minimal", "all", "chat")

    def test_non_hub_profiles_never_call_hub_install_and_still_exit_zero(self):
        """A profile that installs no agent says so in the completion, not
        through its exit code."""
        for profile in self.NON_HUB_PROFILES:
            with self.subTest(profile=profile):
                cmd = self._make_cmd(profile)
                self._patch_common_steps(cmd)
                with (
                    patch(
                        "gaia.installer.init_command.importlib.util.find_spec",
                        return_value=None,  # nothing importable: worst case
                    ),
                    patch("gaia.hub.installer.install") as mock_install,
                ):
                    rc = cmd.run()
                self.assertEqual(rc, 0, f"profile={profile}")
                mock_install.assert_not_called()

    def test_chat_is_not_a_hub_install_target(self):
        """There is no chat agent package to publish (#6103), so a profile
        that asked the Hub for `chat` would wait on a publish that can never
        land."""
        from gaia.installer.init_command import HUB_INSTALL_AGENTS, INIT_PROFILES

        self.assertNotIn("chat", HUB_INSTALL_AGENTS)
        self.assertNotIn(INIT_PROFILES["chat"]["agent"], HUB_INSTALL_AGENTS)

    def test_profile_to_agent_mapping(self):
        """Which profiles install the flagship. The Agent UI lists no agent
        without it, so the NPU profile must be on this list (#2972)."""
        from gaia.installer.init_command import HUB_INSTALL_AGENTS, INIT_PROFILES

        installs_flagship = {
            name for name, cfg in INIT_PROFILES.items() if cfg["agent"] == "gaia"
        }
        self.assertEqual(installs_flagship, {"gaia", "npu"})
        self.assertIn("gaia", HUB_INSTALL_AGENTS)
        # The NPU profile keeps its own FLM models and device keys.
        npu = INIT_PROFILES["npu"]
        self.assertEqual(npu["models"], ["gemma4-it-e4b-FLM", "embed-gemma-300m-FLM"])
        self.assertEqual(npu["backend"], "flm:npu")
        # The flagship runs in the daemon / Agent UI, which [ui] installs.
        self.assertEqual(npu["pip_extras"], INIT_PROFILES["gaia"]["pip_extras"])


class TestUnverifiedChatModelIsNotPinned(_HubInstallWiringTestBase):
    """A hardware-picked model setup could not load must not become everyone's
    ``default_model`` (#5582); the profile is still saved."""

    def _run(self, verify_ok):
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import ChatModelChoice
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME

        cmd = self._make_cmd("minimal")
        self._patch_common_steps(cmd)
        cmd._chat_choice = ChatModelChoice(LARGE_DEFAULT_MODEL_NAME, False, [])
        cmd._chat_model_ready = True
        cfg = GaiaConfig()
        config_cls = MagicMock()
        config_cls.update.side_effect = lambda fn, **_: fn(cfg)
        with (
            patch.object(cmd, "_verify_setup", return_value=verify_ok),
            patch("gaia.config.GaiaConfig", config_cls),
        ):
            rc = cmd.run()
        return rc, cfg

    def test_verify_failed_leaves_default_model_unset(self):
        rc, cfg = self._run(verify_ok=False)
        self.assertEqual(rc, 1)
        self.assertIsNone(cfg.default_model)
        self.assertEqual(cfg.profile, "minimal")

    def test_verified_model_is_recorded(self):
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME

        _rc, cfg = self._run(verify_ok=True)
        self.assertEqual(cfg.default_model, LARGE_DEFAULT_MODEL_NAME)


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
        mock_config_cls.update.assert_called_once()
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
            patch("gaia.utils.terminal.is_windows_console", return_value=True),
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
            patch("gaia.utils.terminal.is_windows_console", return_value=True),
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
    """The completion headline and the "no agent" remedy, per profile.

    Profiles that install the flagship (`gaia`, `npu`) report setup
    incomplete without it. Every other profile still completes, but says
    plainly that no chat agent is installed. Every remedy is
    `gaia hub install gaia` -- never a pip install from a git checkout
    (#2972). Each scenario runs against both the Rich and plain-text paths,
    since a fix applied to only one copy is the obvious regression.
    """

    def _make_cmd(self, profile, agent_available):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile=profile, yes=True)
        cmd._is_hub_agent_available = MagicMock(return_value=agent_available)
        cmd._tui_installed = MagicMock(return_value=True)
        return cmd

    def _render(self, cmd, rich):
        """Run _print_completion; return (its result, everything it printed)."""
        from gaia.installer import init_command as ic

        if rich:
            if not ic.RICH_AVAILABLE:
                self.skipTest("rich not installed")
            buf = io.StringIO()
            cmd.console = ic.Console(file=buf, force_terminal=False, width=300)
            return cmd._print_completion(), buf.getvalue()
        with (
            patch("gaia.installer.init_command.RICH_AVAILABLE", False),
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            complete = cmd._print_completion()
        return complete, mock_stdout.getvalue()

    def test_flagship_profiles_without_the_agent_are_incomplete(self):
        # The npu remedy keeps its profile: a bare `gaia init` would pull the
        # default profile's GGUF models onto an NPU machine.
        for profile, remedy in (
            ("gaia", "run: gaia init"),
            ("npu", "run: gaia init --profile npu"),
        ):
            for rich in (True, False):
                with self.subTest(profile=profile, rich=rich):
                    cmd = self._make_cmd(profile, agent_available=False)
                    complete, out = self._render(cmd, rich)
                    self.assertFalse(complete)
                    self.assertIn("GAIA initialization incomplete", out)
                    self.assertIn(remedy, out)
                    self.assertNotIn("gaia hub", out)
                    self.assertNotIn(_SOURCE_INSTALL_MARKER, out)

    def test_flagship_profiles_with_the_agent_report_complete(self):
        for profile in ("gaia", "npu"):
            for rich in (True, False):
                with self.subTest(profile=profile, rich=rich):
                    cmd = self._make_cmd(profile, agent_available=True)
                    complete, out = self._render(cmd, rich)
                    self.assertTrue(complete)
                    self.assertIn("GAIA initialization complete!", out)
                    self.assertIn("gaia-tui", out)
                    self.assertNotIn("not installed yet", out)

    def test_npu_profile_leads_with_the_flagship_not_the_chat_wheels(self):
        """`gaia chat --device npu` needs wheels init cannot install; the
        flagship's own entry points are what the NPU profile now sets up."""
        for rich in (True, False):
            with self.subTest(rich=rich):
                cmd = self._make_cmd("npu", agent_available=True)
                _, out = self._render(cmd, rich)
                self.assertIn("gaia chat --ui", out)
                self.assertNotIn("gaia chat --device npu", out)
                self.assertIn("gaia config set default_model <id>", out)

    def test_no_build_for_this_machine_is_not_a_failed_setup(self):
        cmd = self._make_cmd("npu", agent_available=False)
        cmd._no_hub_build_for_machine = True
        complete, out = self._render(cmd, rich=False)
        self.assertTrue(complete)
        self.assertIn("it has no build for this machine yet", out)
        self.assertNotIn("Agent Hub", out)

    def test_profiles_without_an_agent_say_so_plainly(self):
        """A profile that installs no agent completes, but names the gap and
        the supported fix instead of a git source install."""
        for profile in ("chat", "sd", "rag", "vlm", "minimal", "email", "all"):
            for rich in (True, False):
                with self.subTest(profile=profile, rich=rich):
                    cmd = self._make_cmd(profile, agent_available=False)
                    complete, out = self._render(cmd, rich)
                    self.assertTrue(complete)
                    self.assertIn("no chat agent installed", out)
                    self.assertIn("No chat agent is installed", out)
                    self.assertIn("Install the GAIA agent with: gaia init", out)
                    self.assertNotIn("gaia hub", out)
                    self.assertNotIn(_SOURCE_INSTALL_MARKER, out)

    def test_profiles_without_an_agent_are_quiet_once_it_is_installed(self):
        for profile in ("chat", "sd", "minimal"):
            for rich in (True, False):
                with self.subTest(profile=profile, rich=rich):
                    cmd = self._make_cmd(profile, agent_available=True)
                    complete, out = self._render(cmd, rich)
                    self.assertTrue(complete)
                    self.assertIn("GAIA initialization complete!", out)
                    self.assertNotIn("No chat agent is installed", out)


class TestAgentInstallNote(unittest.TestCase):
    """The flagship counts as installed whether it came from the hub (a
    binary, seen only by its sentinel) or a source checkout (importable)."""

    def test_hub_sentinel_counts_as_installed(self):
        from gaia.installer.init_command import InitCommand

        cmd = InitCommand(profile="chat", yes=True)
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch("gaia.hub.installer.read_sentinel", return_value=object()),
        ):
            self.assertIsNone(cmd._agent_install_note())

    def test_nothing_installed_names_gaia_init(self):
        from gaia.installer.init_command import InitCommand

        for profile, expected in (
            ("gaia", "GAIA agent not installed yet -- run: gaia init"),
            ("npu", "GAIA agent not installed yet -- run: gaia init --profile npu"),
            ("chat", "Install the GAIA agent with: gaia init"),
        ):
            with self.subTest(profile=profile):
                cmd = InitCommand(profile=profile, yes=True)
                with (
                    patch(
                        "gaia.installer.init_command.importlib.util.find_spec",
                        return_value=None,
                    ),
                    patch("gaia.hub.installer.read_sentinel", return_value=None),
                ):
                    note = cmd._agent_install_note()
                self.assertIn(expected, note)
                self.assertNotIn("gaia hub", note)


if __name__ == "__main__":
    unittest.main()


class TestModelSmokeTest:
    """The verification chat proves the model runs; a thinking model's short
    reply is all reasoning, which is still generated text."""

    def _cmd(self):
        from gaia.installer.init_command import InitCommand

        return InitCommand(profile="minimal", yes=True)

    def _client(self, message):
        client = MagicMock()
        client.check_model_loaded.return_value = False
        client.health_check.return_value = {
            "all_models_loaded": [
                {
                    "model_name": "Qwen3.6-35B-A3B-GGUF",
                    "type": "llm",
                    "recipe_options": {"ctx_size": 1 << 20},
                }
            ]
        }
        client.chat_completions.return_value = {"choices": [{"message": message}]}
        return client

    def test_reasoning_only_reply_passes(self):
        ok, error = self._cmd()._test_model_inference(
            self._client({"content": "", "reasoning_content": "The user wants"}),
            "Qwen3.6-35B-A3B-GGUF",
        )
        assert (ok, error) == (True, None)

    def test_an_empty_reply_still_fails(self):
        ok, error = self._cmd()._test_model_inference(
            self._client({"content": "", "reasoning_content": ""}),
            "Qwen3.6-35B-A3B-GGUF",
        )
        assert (ok, error) == (False, "Empty response")


class TestHfHubCache:
    def test_hf_hub_cache_wins(self, monkeypatch):
        from gaia.installer.init_command import _hf_hub_cache

        monkeypatch.setenv("HF_HUB_CACHE", "/x/hub-cache")
        monkeypatch.setenv("HF_HOME", "/y")
        assert _hf_hub_cache() == "/x/hub-cache"

    def test_hf_home_is_honoured(self, monkeypatch):
        import os

        from gaia.installer.init_command import _hf_hub_cache

        monkeypatch.delenv("HF_HUB_CACHE", raising=False)
        monkeypatch.setenv("HF_HOME", "/y")
        assert _hf_hub_cache() == os.path.join("/y", "hub")


# Lemonade /system-info for a 128 GB Strix Halo (96 GB carve-out + shared GTT).
STRIX_HALO_128 = {
    "Physical Memory": "128 GB",
    "devices": {
        "amd_gpu": [
            {
                "available": True,
                "integrated": True,
                "vram_gb": 96.0,
                "virtual_mem_gb": 15.8,
            }
        ]
    },
    "model_storage": {"free_bytes": 900e9},
}
STRIX_HALO_64 = {
    "Physical Memory": "64 GB",
    "devices": {
        "amd_gpu": [
            {
                "available": True,
                "integrated": True,
                "vram_gb": 48.0,
                "virtual_mem_gb": 7.9,
            }
        ]
    },
    "model_storage": {"free_bytes": 900e9},
}


class TestHardwareChatModel(unittest.TestCase):
    """The chat model follows the hardware: Qwen3.6 35B A3B where a GPU holds
    it, Gemma 4 E4B everywhere else, and a user's default_model always wins."""

    def setUp(self):
        from gaia.llm.lemonade_embedded import EmbeddedStatus
        from gaia.version import LEMONADE_VERSION

        embedded = MagicMock()
        embedded.version = LEMONADE_VERSION
        embedded.status.return_value = EmbeddedStatus(
            installed=True,
            running=True,
            version=LEMONADE_VERSION,
            port=51234,
            pid=42,
            base_url="http://localhost:51234/api/v1",
        )
        running = patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade", return_value=embedded
        )
        running.start()
        self.addCleanup(running.stop)

    def _client(self, system_info, have=()):
        client = MagicMock()
        client.health_check.return_value = {"status": "ok", "version": "2026.44.1"}
        client.get_system_info.return_value = system_info
        client.check_model_available.side_effect = lambda m: m in have
        client.ensure_model_downloaded.return_value = True
        return client

    def test_check_asks_for_qwen_on_a_big_machine_not_gemma(self):
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import (
            DEFAULT_MODEL_NAME,
            LARGE_DEFAULT_MODEL_NAME,
        )

        client = self._client(STRIX_HALO_128, have={DEFAULT_MODEL_NAME})
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            status = check_setup_status(profile="gaia")
        self.assertFalse(status.ready)
        self.assertTrue(any(LARGE_DEFAULT_MODEL_NAME in r for r in status.reasons))
        # Gemma stays required: the vision paths still load it by name.
        probed = {c.args[0] for c in client.check_model_available.call_args_list}
        self.assertIn(DEFAULT_MODEL_NAME, probed)

    def test_check_keeps_gemma_on_a_small_machine(self):
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        client = self._client(SMALL_MACHINE)
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            status = check_setup_status(profile="gaia")
        self.assertTrue(any(DEFAULT_MODEL_NAME in r for r in status.reasons))

    def test_user_default_model_wins_without_probing_hardware(self):
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import check_setup_status

        cfg = GaiaConfig()
        cfg.default_model = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
        cfg.save()
        client = self._client(STRIX_HALO_128)
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            status = check_setup_status(profile="gaia")
        client.get_system_info.assert_not_called()
        self.assertTrue(any("Qwen3-Coder" in r for r in status.reasons))

    def test_a_chosen_default_model_replaces_gemma_in_the_download(self):
        """default_model=Qwen3-0.6B must not pull the 6 GB Gemma as well."""
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import InitCommand
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        cfg = GaiaConfig()
        cfg.default_model = "Qwen3-0.6B-GGUF"
        cfg.save()
        cmd = InitCommand(profile="gaia", yes=True)
        cmd._print = MagicMock()
        client = self._client(SMALL_MACHINE)
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            self.assertTrue(cmd._download_models())
        pulled = [c.args[0] for c in client.ensure_model_downloaded.call_args_list]
        self.assertIn("Qwen3-0.6B-GGUF", pulled)
        self.assertNotIn(DEFAULT_MODEL_NAME, pulled)
        said = " ".join(str(c.args[0]) for c in cmd._print.call_args_list)
        self.assertIn(f"Not downloading {DEFAULT_MODEL_NAME}", said)
        self.assertIn("images", said)

    def test_check_does_not_require_gemma_beside_a_chosen_default(self):
        """Otherwise the Agent UI's setup check would rerun setup forever."""
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import DEFAULT_EMBEDDING_MODEL

        cfg = GaiaConfig()
        cfg.default_model = "Qwen3-0.6B-GGUF"
        cfg.save()
        client = self._client(
            SMALL_MACHINE, have={"Qwen3-0.6B-GGUF", DEFAULT_EMBEDDING_MODEL}
        )
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            status = check_setup_status(profile="gaia")
        self.assertFalse(any("Gemma" in r for r in status.reasons), status.reasons)

    def test_choosing_gemma_itself_still_downloads_it(self):
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import InitCommand
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        cfg = GaiaConfig()
        cfg.default_model = DEFAULT_MODEL_NAME
        cfg.save()
        cmd = InitCommand(profile="gaia", yes=True)
        client = self._client(SMALL_MACHINE)
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            self.assertTrue(cmd._download_models())
        pulled = [c.args[0] for c in client.ensure_model_downloaded.call_args_list]
        self.assertEqual(pulled.count(DEFAULT_MODEL_NAME), 1)

    def test_download_registers_qwen_with_a_size_scaled_timeout(self):
        from gaia.installer.init_command import InitCommand
        from gaia.llm.lemonade_client import (
            DEFAULT_MODEL_NAME,
            LARGE_DEFAULT_MODEL_NAME,
        )

        cmd = InitCommand(profile="gaia", yes=True)
        client = self._client(STRIX_HALO_128)
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            self.assertTrue(cmd._download_models())
        self.assertTrue(cmd._chat_model_ready)
        calls = {
            c.args[0]: c.kwargs for c in client.ensure_model_downloaded.call_args_list
        }
        self.assertIn(DEFAULT_MODEL_NAME, calls)
        # The default is a Lemonade built-in: no registration kwargs, just a
        # size-scaled timeout for the download.
        qwen = calls[LARGE_DEFAULT_MODEL_NAME]
        self.assertEqual(qwen.get("checkpoint"), None)
        self.assertEqual(qwen.get("recipe"), None)
        self.assertGreater(qwen["timeout"], 1200)

    def test_download_registers_flash_with_its_checkpoint_when_configured(self):
        """Flash is no longer the auto-picked default, but a user who switched
        to it with `gaia config set default_model` must still download it
        with its registration kwargs, not by name alone (#1655)."""
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import InitCommand
        from gaia.llm.lemonade_client import FLASH_OPTION_MODEL_NAME

        cfg = GaiaConfig()
        cfg.default_model = FLASH_OPTION_MODEL_NAME
        cfg.save()
        cmd = InitCommand(profile="gaia", yes=True)
        client = self._client(STRIX_HALO_128)
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            self.assertTrue(cmd._download_models())
        self.assertTrue(cmd._chat_model_ready)
        calls = {
            c.args[0]: c.kwargs for c in client.ensure_model_downloaded.call_args_list
        }
        flash = calls[FLASH_OPTION_MODEL_NAME]
        self.assertTrue(
            flash["checkpoint"].startswith("unsloth/Qwen3.8-Flash-Next-GGUF:")
        )
        self.assertEqual(flash["recipe"], "llamacpp")
        self.assertEqual(flash["mmproj"], "mmproj-F16.gguf")
        self.assertGreater(flash["timeout"], 7200)

    def test_vlm_profile_keeps_gemma_and_never_probes(self):
        from gaia.installer.init_command import with_chat_model
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        def fail():
            raise AssertionError("vlm must not resolve a hardware chat model")

        self.assertEqual(
            with_chat_model("vlm", [DEFAULT_MODEL_NAME], fail), [DEFAULT_MODEL_NAME]
        )

    def test_recorded_choice_never_overrides_the_user(self):
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import ChatModelChoice, InitCommand
        from gaia.llm.lemonade_client import (
            DEFAULT_MODEL_NAME,
            LARGE_DEFAULT_MODEL_NAME,
        )

        cmd = InitCommand(profile="gaia", yes=True)

        cmd._chat_choice = ChatModelChoice(LARGE_DEFAULT_MODEL_NAME, False, [])
        cfg = GaiaConfig()
        cmd._record_chat_choice(cfg)
        self.assertIsNone(
            cfg.default_model, "recorded a default that was never downloaded"
        )

        cmd._chat_model_ready = True
        cmd._record_chat_choice(cfg)
        self.assertEqual(cfg.default_model, LARGE_DEFAULT_MODEL_NAME)

        cfg = GaiaConfig(default_model="mine")
        cmd._record_chat_choice(cfg)
        self.assertEqual(cfg.default_model, "mine")

        cmd._chat_choice = ChatModelChoice(DEFAULT_MODEL_NAME, False, [])
        cfg = GaiaConfig()
        cmd._record_chat_choice(cfg)
        self.assertIsNone(cfg.default_model)

    def test_a_cloud_or_claude_default_is_not_a_local_download(self):
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME

        for configured in ("claude-sonnet-5", "fireworks.deepseek-v4-flash-0731"):
            cfg = GaiaConfig()
            cfg.default_model = configured
            cfg.save()
            client = self._client(STRIX_HALO_128)
            with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
                status = check_setup_status(profile="gaia")
            self.assertFalse(any(configured in r for r in status.reasons))
            self.assertTrue(any(LARGE_DEFAULT_MODEL_NAME in r for r in status.reasons))

    def test_check_refuses_a_corrupt_config_instead_of_guessing(self):
        from gaia import config as config_mod
        from gaia.config import GaiaConfigError
        from gaia.installer.init_command import check_setup_status

        config_mod.GAIA_CONFIG_FILE.write_text("{not json", encoding="utf-8")
        client = self._client(STRIX_HALO_128)
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            with self.assertRaises(GaiaConfigError):
                check_setup_status(profile="gaia")

    def test_a_user_default_that_does_not_fit_is_refused(self):
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME
        from gaia.llm.model_fit import ModelFitError

        cfg = GaiaConfig()
        cfg.default_model = LARGE_DEFAULT_MODEL_NAME
        cfg.save()
        client = self._client(SMALL_MACHINE)
        client.list_models.return_value = {"data": []}
        from gaia.installer.init_command import resolve_init_chat_model

        with self.assertRaisesRegex(ModelFitError, "gaia config set default_model"):
            resolve_init_chat_model(client, reset_corrupt=True, enforce_fit=True)
        # --check only reports it missing, so the TUI still offers setup.
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            status = check_setup_status(profile="gaia")
        self.assertTrue(any(LARGE_DEFAULT_MODEL_NAME in r for r in status.reasons))

    def test_minimal_profile_stays_small_on_a_big_machine(self):
        from gaia.installer.init_command import with_chat_model
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        def fail():
            raise AssertionError("minimal must not resolve a hardware chat model")

        self.assertEqual(
            with_chat_model("minimal", [DEFAULT_MODEL_NAME], fail),
            [DEFAULT_MODEL_NAME],
        )

    def test_flash_chosen_on_a_64gb_strix_halo_is_refused_naming_its_memory(self):
        from gaia.config import GaiaConfig
        from gaia.installer.init_command import resolve_init_chat_model
        from gaia.llm.lemonade_client import FLASH_OPTION_MODEL_NAME
        from gaia.llm.model_fit import ModelFitError

        cfg = GaiaConfig()
        cfg.default_model = FLASH_OPTION_MODEL_NAME
        cfg.save()
        client = self._client(STRIX_HALO_64)
        client.list_models.return_value = {"data": []}
        with self.assertRaisesRegex(ModelFitError, r"needs ~90 GB of memory") as ctx:
            resolve_init_chat_model(client, reset_corrupt=True, enforce_fit=True)
        self.assertIn("gaia config set default_model", str(ctx.exception))
        client.ensure_model_downloaded.assert_not_called()

    def test_a_failed_hardware_request_keeps_gemma_and_still_answers_check(self):
        from gaia.installer.init_command import check_setup_status
        from gaia.llm.lemonade_client import (
            DEFAULT_MODEL_NAME,
            LARGE_DEFAULT_MODEL_NAME,
            LemonadeClientError,
        )

        client = self._client(STRIX_HALO_128)
        client.get_system_info.side_effect = LemonadeClientError("timed out")
        with patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client):
            status = check_setup_status(profile="gaia")
        self.assertFalse(status.ready)
        self.assertTrue(any(DEFAULT_MODEL_NAME in r for r in status.reasons))
        self.assertFalse(any(LARGE_DEFAULT_MODEL_NAME in r for r in status.reasons))


class TestNpuProfileMemlockOffer(_HubInstallWiringTestBase):
    """`gaia init --profile npu` only asks Lemonade about memlock when it can
    prompt on Linux, and a Lemonade that doesn't answer never aborts setup."""

    def _run_npu(self, *, yes, tty, on_linux, system_info):
        cmd = self._make_cmd("npu")
        cmd.yes = yes
        self._patch_common_steps(cmd)
        install, sentinel = TestHubInstallWiringFlagshipProfile._sentinel_after_install(
            self
        )
        client = MagicMock()
        client.get_system_info.side_effect = system_info
        with (
            patch(
                "gaia.installer.init_command.importlib.util.find_spec",
                return_value=None,
            ),
            patch(
                "gaia.hub.catalog.load_index",
                return_value=_fake_catalog_result(
                    [{"id": "gaia", "latest_version": "0.1.0"}]
                ),
            ),
            patch("gaia.hub.installer.install", side_effect=install) as mock_install,
            sentinel,
            patch("gaia.installer.init_command.stdin_is_tty", return_value=tty),
            patch("gaia.llm.npu_linux._on_linux", return_value=on_linux),
            patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
            patch.object(cmd, "_prompt_yes_no", return_value=True),
            patch("gaia.llm.npu_linux.apply_memlock_fix") as applied,
            patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
        ):
            rc = cmd.run()
        return rc, client, mock_install, applied, mock_stdout.getvalue()

    def test_non_linux_never_asks_lemonade(self):
        rc, client, mock_install, applied, _ = self._run_npu(
            yes=False, tty=True, on_linux=False, system_info=AssertionError
        )
        self.assertEqual(rc, 0)
        client.get_system_info.assert_not_called()
        applied.assert_not_called()
        mock_install.assert_called_once_with("gaia", trusted=True)

    def test_yes_never_asks_lemonade(self):
        rc, client, _, applied, _ = self._run_npu(
            yes=True, tty=True, on_linux=True, system_info=AssertionError
        )
        self.assertEqual(rc, 0)
        client.get_system_info.assert_not_called()
        applied.assert_not_called()

    def test_unreachable_lemonade_warns_and_setup_continues(self):
        from gaia.llm.lemonade_client import LemonadeClientError

        rc, client, mock_install, applied, out = self._run_npu(
            yes=False,
            tty=True,
            on_linux=True,
            system_info=LemonadeClientError("Request failed: connection refused"),
        )
        out = " ".join(out.split())  # Rich wraps long warnings
        self.assertEqual(rc, 0)
        client.get_system_info.assert_called_once()
        applied.assert_not_called()
        mock_install.assert_called_once_with("gaia", trusted=True)
        self.assertIn("locked-memory check is skipped", out)
        self.assertIn("connection refused", out)
        self.assertNotIn("Unexpected error", out)
