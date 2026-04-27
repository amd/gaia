# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for gaia.ui.build.ensure_webui_built and the gaia init
frontend build step.

Tests use real temp directories for path logic and patch only subprocess
and shutil.which so no actual npm/node invocations happen.
"""

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestEnsureWebuiBuilt(unittest.TestCase):
    """Tests for gaia.ui.build.ensure_webui_built."""

    def _call(
        self, webui_dir, which_return="/usr/bin/node", run_side_effect=None, log=None
    ):
        """Helper: call ensure_webui_built with controlled environment."""
        from gaia.ui.build import ensure_webui_built

        msgs = []
        log_fn = log if log is not None else msgs.append

        with (
            patch("gaia.ui.build.shutil.which", return_value=which_return),
            patch(
                "gaia.ui.build.subprocess.run",
                side_effect=run_side_effect,
            ) as mock_run,
        ):
            result = ensure_webui_built(log_fn=log_fn, _webui_dir=webui_dir)

        return msgs, mock_run, result

    # ------------------------------------------------------------------
    # Test 1: skip when src/ is absent (pip install, no source tree)
    # ------------------------------------------------------------------

    def test_skips_pip_install(self):
        """ensure_webui_built returns early when src/ directory is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            # src/ deliberately NOT created

            msgs, mock_run, result = self._call(webui_dir)

        mock_run.assert_not_called()
        self.assertFalse(result, "Expected False (silent skip) when src/ is absent")

    # ------------------------------------------------------------------
    # Test 2: skip when dist is fresh (staleness check)
    # ------------------------------------------------------------------

    def test_staleness_skip(self):
        """No build when dist/index.html is newer than all source files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            src_dir = webui_dir / "src"
            src_dir.mkdir()
            # Write a source file with an old mtime
            src_file = src_dir / "app.ts"
            src_file.write_text("const x = 1;")

            # Create dist/index.html with a NEWER mtime
            dist_dir = webui_dir / "dist"
            dist_dir.mkdir()
            dist_index = dist_dir / "index.html"
            dist_index.write_text("<html/>")

            # Force dist to appear newer than src
            old_time = time.time() - 60
            import os

            os.utime(str(src_file), (old_time, old_time))
            new_time = time.time()
            os.utime(str(dist_index), (new_time, new_time))

            msgs, mock_run, result = self._call(webui_dir)

        mock_run.assert_not_called()
        self.assertTrue(result, "Expected True when dist is already up-to-date")

    # ------------------------------------------------------------------
    # Test 3: node missing — logs warning, no exception
    # ------------------------------------------------------------------

    def test_node_missing(self):
        """Log a warning and return gracefully when Node.js is not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            # No dist/index.html — build is needed

            msgs, mock_run, result = self._call(webui_dir, which_return=None)

        mock_run.assert_not_called()
        self.assertFalse(result, "Expected False when Node.js is missing")
        self.assertTrue(
            any("Node.js not found" in m for m in msgs),
            f"Expected 'Node.js not found' in log output, got: {msgs}",
        )

    # ------------------------------------------------------------------
    # Test 4: happy path — builds when dist is absent, node/npm available
    # ------------------------------------------------------------------

    def test_builds_frontend(self):
        """subprocess.run called with ['npm', 'run', 'build'] when dist absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            src_dir = webui_dir / "src"
            src_dir.mkdir()
            src_file = src_dir / "app.ts"
            src_file.write_text("const x = 1;")
            # node_modules present so npm install is skipped
            (webui_dir / "node_modules").mkdir()
            # No dist/index.html — build needed

            msgs, mock_run, result = self._call(webui_dir)

        called_cmds = [c.args[0] for c in mock_run.call_args_list]
        self.assertTrue(
            any(c == ["npm", "run", "build"] for c in called_cmds),
            f"Expected ['npm', 'run', 'build'] call, got: {called_cmds}",
        )
        self.assertTrue(result, "Expected True when build succeeds")

    # ------------------------------------------------------------------
    # Test 5: npm install failure — no exception propagated
    # ------------------------------------------------------------------

    def test_npm_install_failure_continues(self):
        """CalledProcessError from npm install does not propagate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            # node_modules absent — triggers npm install
            # No dist/index.html

            def fail_install(cmd, **kwargs):
                if "install" in cmd:
                    raise subprocess.CalledProcessError(1, cmd, stderr="ERR")
                return MagicMock(returncode=0)

            try:
                msgs, mock_run, result = self._call(
                    webui_dir, run_side_effect=fail_install
                )
            except Exception as e:
                self.fail(f"ensure_webui_built raised unexpectedly: {e}")

        self.assertFalse(result, "Expected False when npm install fails")

    # ------------------------------------------------------------------
    # Test 6: npm run build failure — caught, returns False, no exception
    # ------------------------------------------------------------------

    def test_build_step_failure_continues(self):
        """npm run build CalledProcessError is caught; returns False without raising."""
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            # node_modules present so npm install is skipped
            (webui_dir / "node_modules").mkdir()
            # No dist/index.html — build is needed

            def fail_build(cmd, **kwargs):
                if "build" in cmd:
                    raise subprocess.CalledProcessError(1, cmd)
                return MagicMock(returncode=0)

            warnings = []
            result = None
            try:
                with (
                    patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                    patch("gaia.ui.build.subprocess.run", side_effect=fail_build),
                ):
                    result = ensure_webui_built(
                        _webui_dir=webui_dir, warn_fn=warnings.append
                    )
            except Exception as e:
                self.fail(f"ensure_webui_built raised unexpectedly: {e}")

        self.assertFalse(result, "Expected False when build step fails")
        self.assertTrue(
            any(
                "build failed" in w.lower() or "Frontend build failed" in w
                for w in warnings
            ),
            f"Expected build-failure warning, got: {warnings}",
        )

    # ------------------------------------------------------------------
    # Test 7: node found, npm missing — warns and skips build
    # ------------------------------------------------------------------

    def test_npm_missing_warns_and_skips(self):
        """If node is present but npm is missing, warn_fn is called and build is skipped."""
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            # No dist/index.html — build would be needed

            warnings = []

            def fake_which(cmd):
                return "/usr/bin/node" if cmd == "node" else None

            with (
                patch("gaia.ui.build.shutil.which", side_effect=fake_which),
                patch("gaia.ui.build.subprocess.run") as mock_run,
            ):
                result = ensure_webui_built(
                    _webui_dir=webui_dir, warn_fn=warnings.append
                )

        mock_run.assert_not_called()
        self.assertFalse(result, "Expected False when npm is missing")
        self.assertTrue(
            any("npm" in w.lower() for w in warnings),
            f"Expected npm warning in warn_fn output, got: {warnings}",
        )


class TestInitCommandWebuiBuild(unittest.TestCase):
    """Tests for the gaia init frontend build integration."""

    def _run_init_with_src_dir_mock(self, src_is_dir: bool):
        """
        Run InitCommand.run() with all heavy operations mocked.

        Returns the mock for ensure_webui_built so caller can assert on it.
        """
        from gaia.installer.init_command import InitCommand
        from gaia.installer.lemonade_installer import LemonadeInstaller

        # Fake src path whose .is_dir() is controlled by the caller
        fake_src = MagicMock()
        fake_src.is_dir.return_value = src_is_dir

        # Build Path chain via MagicMock's auto-chaining of __truediv__.return_value.
        # Path(__file__).resolve().parent.parent / "apps" / "webui" / "src" = fake_src
        # Each / uses __truediv__.return_value on the previous mock.
        mock_path = MagicMock()
        (
            mock_path.return_value.resolve.return_value.parent.parent.__truediv__.return_value.__truediv__.return_value.__truediv__.return_value  # / "apps"  # / "webui"  # / "src"
        )
        # Now override the final .return_value to be fake_src
        (
            mock_path.return_value.resolve.return_value.parent.parent.__truediv__.return_value.__truediv__.return_value.__truediv__
        ).return_value = fake_src

        mock_installer = MagicMock(spec=LemonadeInstaller)

        with (
            patch("gaia.installer.init_command.Path", mock_path),
            patch("gaia.ui.build.ensure_webui_built") as mock_ensure_built,
            patch.object(InitCommand, "_print_header"),
            patch.object(InitCommand, "_print"),
            patch.object(InitCommand, "_print_step"),
            patch.object(InitCommand, "_print_success"),
            patch.object(InitCommand, "_print_completion"),
            patch.object(InitCommand, "_ensure_lemonade_installed", return_value=True),
            patch.object(InitCommand, "_ensure_server_running", return_value=True),
            patch.object(InitCommand, "_verify_setup", return_value=True),
        ):
            cmd = InitCommand.__new__(InitCommand)
            cmd.profile = "minimal"
            cmd.skip_models = True
            cmd.skip_lemonade = True
            cmd.remote = False
            cmd.verbose = False
            cmd.force_reinstall = False
            cmd._lemonade_base_url = None
            cmd.installer = mock_installer
            cmd.console = MagicMock()
            cmd.run()

        return mock_ensure_built

    # ------------------------------------------------------------------
    # Test 6: init calls ensure_webui_built in dev mode
    # ------------------------------------------------------------------

    def test_init_calls_build_in_dev_mode(self):
        """ensure_webui_built is called when webui src/ exists (dev install)."""
        mock_ensure_built = self._run_init_with_src_dir_mock(src_is_dir=True)
        mock_ensure_built.assert_called_once()

    # ------------------------------------------------------------------
    # Test 7: init skips build for pip installs (no src/)
    # ------------------------------------------------------------------

    def test_init_skips_build_for_pip(self):
        """ensure_webui_built is NOT called when webui src/ is absent (pip install)."""
        mock_ensure_built = self._run_init_with_src_dir_mock(src_is_dir=False)
        mock_ensure_built.assert_not_called()


if __name__ == "__main__":
    unittest.main()
