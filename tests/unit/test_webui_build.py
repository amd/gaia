# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for gaia.ui.build.ensure_webui_built and the gaia init
frontend build step.

Tests use real temp directories for path logic and patch only subprocess
and shutil.which so no actual npm/node invocations happen.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gaia.ui.build import WebuiBuildResult, WebuiBuildStatus


class _ClearsSkipWebuiBuildEnv(unittest.TestCase):
    """Base class that isolates tests from an ambient GAIA_SKIP_WEBUI_BUILD.

    ensure_webui_built() checks this env var unconditionally at the top of
    every call -- a developer who has it exported for their own shell must
    not silently break every other test here.
    """

    def setUp(self):
        super().setUp()
        self._prior_skip_env = os.environ.pop("GAIA_SKIP_WEBUI_BUILD", None)
        self.addCleanup(self._restore_skip_env)

    def _restore_skip_env(self):
        if self._prior_skip_env is not None:
            os.environ["GAIA_SKIP_WEBUI_BUILD"] = self._prior_skip_env
        else:
            os.environ.pop("GAIA_SKIP_WEBUI_BUILD", None)


class TestEnsureWebuiBuilt(_ClearsSkipWebuiBuildEnv):
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

        # On Windows npm is invoked via `cmd /c npm ...` (avoids shell=True);
        # on other platforms it's the bare `npm ...`. Match the npm suffix and
        # assert every call runs shell=False.
        called_cmds = [c.args[0] for c in mock_run.call_args_list]
        self.assertTrue(
            any(c[-3:] == ["npm", "run", "build"] for c in called_cmds),
            f"Expected an 'npm run build' call, got: {called_cmds}",
        )
        self.assertTrue(
            all(c.kwargs.get("shell") is False for c in mock_run.call_args_list),
            "All npm calls must run with shell=False",
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


class TestNodeVersionPreflight(_ClearsSkipWebuiBuildEnv):
    """Tests for the engines.node preflight (#2880) -- Gate 3b in
    ensure_webui_built, which runs after node/npm are confirmed present but
    before any npm invocation.
    """

    @staticmethod
    def _write_package_json(webui_dir, node_range):
        (webui_dir / "package.json").write_text(
            json.dumps({"engines": {"node": node_range}})
        )

    # ------------------------------------------------------------------
    # AC1/2/3: below-floor Node blocks the build with an actionable
    # message naming requirement, found version, and node path -- and
    # npm is never invoked (only the version probe runs).
    # ------------------------------------------------------------------

    def test_node_too_old_blocks_build_with_actionable_message(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            self._write_package_json(webui_dir, ">=20.19.0")
            (webui_dir / "node_modules").mkdir()  # would skip npm install if reached

            warnings = []

            def fake_run(cmd, **kwargs):
                self.assertEqual(cmd, ["/usr/bin/node", "--version"])
                return MagicMock(stdout="v18.19.0\n", stderr="", returncode=0)

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch("gaia.ui.build.subprocess.run", side_effect=fake_run) as mock_run,
            ):
                result = ensure_webui_built(
                    _webui_dir=webui_dir, warn_fn=warnings.append
                )

        # Only the version probe ran -- npm install/build never invoked.
        mock_run.assert_called_once()
        self.assertEqual(result.status, WebuiBuildStatus.NODE_TOO_OLD)
        self.assertFalse(result)
        combined = " ".join(warnings)
        self.assertIn(">=20.19.0", combined, "requirement must be named")
        self.assertIn("18.19.0", combined, "found version must be named")
        self.assertIn("/usr/bin/node", combined, "absolute node path must be named")

    def test_node_too_old_result_fields_are_populated(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            self._write_package_json(webui_dir, ">=20.19.0")

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch(
                    "gaia.ui.build.subprocess.run",
                    return_value=MagicMock(
                        stdout="v18.19.0\n", stderr="", returncode=0
                    ),
                ),
            ):
                result = ensure_webui_built(_webui_dir=webui_dir)

        self.assertEqual(result.status, WebuiBuildStatus.NODE_TOO_OLD)
        self.assertEqual(result.found_version, "18.19.0")
        self.assertEqual(result.required_range, ">=20.19.0")
        self.assertEqual(result.node_path, "/usr/bin/node")

    # ------------------------------------------------------------------
    # AC6: engines.node is read from package.json at runtime, never
    # hardcoded -- the verdict must flip purely based on the file's
    # declared floor, including today's literal `>=18` shape.
    # ------------------------------------------------------------------

    def test_engines_node_floor_is_read_from_package_json_not_hardcoded(self):
        from gaia.ui.build import ensure_webui_built

        cases = (
            (">=18", WebuiBuildStatus.OK),
            (">=20.19.0", WebuiBuildStatus.NODE_TOO_OLD),
        )
        for node_range, expected_status in cases:
            with self.subTest(node_range=node_range):
                with tempfile.TemporaryDirectory() as tmpdir:
                    webui_dir = Path(tmpdir)
                    (webui_dir / "src").mkdir()
                    self._write_package_json(webui_dir, node_range)
                    (webui_dir / "node_modules").mkdir()

                    def fake_run(cmd, **kwargs):
                        if cmd[-1] == "--version":
                            return MagicMock(
                                stdout="v18.19.0\n", stderr="", returncode=0
                            )
                        return MagicMock(returncode=0, stdout="", stderr="")

                    with (
                        patch(
                            "gaia.ui.build.shutil.which", return_value="/usr/bin/node"
                        ),
                        patch("gaia.ui.build.subprocess.run", side_effect=fake_run),
                    ):
                        result = ensure_webui_built(_webui_dir=webui_dir)

                    self.assertEqual(result.status, expected_status)

    # ------------------------------------------------------------------
    # AC4/5th-outcome: npm install failure with an existing (stale) dist
    # is still OK; without a dist it's a hard BUILD_FAILED. Also proves
    # the build.py:101-104 bug (unconditional "continuing" message) is
    # fixed -- the status now branches on dist_index.exists().
    # ------------------------------------------------------------------

    def test_npm_install_failure_without_dist_is_build_failed(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            # No dist/index.html present.

            def fail_install(cmd, **kwargs):
                if "install" in cmd:
                    raise subprocess.CalledProcessError(
                        1, cmd, stderr="EACCES permission denied"
                    )
                return MagicMock(returncode=0)

            warnings = []
            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch("gaia.ui.build.subprocess.run", side_effect=fail_install),
            ):
                result = ensure_webui_built(
                    _webui_dir=webui_dir, warn_fn=warnings.append
                )

        self.assertEqual(result.status, WebuiBuildStatus.BUILD_FAILED)
        self.assertFalse(result)
        # AC7: the underlying npm stderr must reach the user.
        self.assertTrue(
            any("EACCES permission denied" in w for w in warnings),
            f"Expected npm install stderr in warn output, got: {warnings}",
        )

    def test_npm_install_failure_with_existing_dist_stays_ok(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            src_dir = webui_dir / "src"
            src_dir.mkdir()
            (src_dir / "app.ts").write_text("const x = 1;")
            dist_dir = webui_dir / "dist"
            dist_dir.mkdir()
            dist_index = dist_dir / "index.html"
            dist_index.write_text("<html/>")
            # Force a rebuild attempt by making dist look stale.
            old_time = time.time() - 60
            import os

            os.utime(str(dist_index), (old_time, old_time))

            def fail_install(cmd, **kwargs):
                if "install" in cmd:
                    raise subprocess.CalledProcessError(1, cmd, stderr="ERR")
                return MagicMock(returncode=0)

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch("gaia.ui.build.subprocess.run", side_effect=fail_install),
            ):
                result = ensure_webui_built(_webui_dir=webui_dir)

        self.assertEqual(result.status, WebuiBuildStatus.OK)
        self.assertTrue(result, "a stale-but-usable dist/ must not fail init")

    # ------------------------------------------------------------------
    # AC8: an unparseable/absent engines.node, or an unparseable/timed-out
    # node --version, must skip the preflight (never block, never crash)
    # and let the build proceed as before.
    # ------------------------------------------------------------------

    def test_malformed_package_json_skips_preflight_without_crashing(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            (webui_dir / "package.json").write_text("{not valid json")
            (webui_dir / "node_modules").mkdir()

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch(
                    "gaia.ui.build.subprocess.run",
                    return_value=MagicMock(returncode=0),
                ),
            ):
                result = ensure_webui_built(_webui_dir=webui_dir)

        self.assertEqual(result.status, WebuiBuildStatus.OK)

    def test_missing_engines_node_key_skips_preflight_without_crashing(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            (webui_dir / "package.json").write_text(json.dumps({"name": "webui"}))
            (webui_dir / "node_modules").mkdir()

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch(
                    "gaia.ui.build.subprocess.run",
                    return_value=MagicMock(returncode=0),
                ),
            ):
                result = ensure_webui_built(_webui_dir=webui_dir)

        self.assertEqual(result.status, WebuiBuildStatus.OK)

    def test_unparseable_node_version_skips_preflight_without_crashing(self):
        """Real shims hit this: asdf `No version is set` / nvm `N/A`."""
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            self._write_package_json(webui_dir, ">=18")
            (webui_dir / "node_modules").mkdir()

            def fake_run(cmd, **kwargs):
                if cmd[-1] == "--version":
                    return MagicMock(stdout="N/A\n", stderr="", returncode=126)
                return MagicMock(returncode=0)

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch("gaia.ui.build.subprocess.run", side_effect=fake_run),
            ):
                result = ensure_webui_built(_webui_dir=webui_dir)

        self.assertEqual(result.status, WebuiBuildStatus.OK)

    def test_node_version_probe_timeout_skips_preflight_without_crashing(self):
        """A hanging node (nvm hook, asdf shim, corporate wrapper, WSL
        interop) must not turn the preflight into an indefinite hang."""
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            self._write_package_json(webui_dir, ">=18")
            (webui_dir / "node_modules").mkdir()

            def fake_run(cmd, **kwargs):
                if cmd[-1] == "--version":
                    raise subprocess.TimeoutExpired(cmd, 10)
                return MagicMock(returncode=0)

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch("gaia.ui.build.subprocess.run", side_effect=fake_run),
            ):
                result = ensure_webui_built(_webui_dir=webui_dir)

        self.assertEqual(result.status, WebuiBuildStatus.OK)

    # ------------------------------------------------------------------
    # AC4 (subprocess convention): the version probe uses timeout=10,
    # matching repo convention elsewhere (lemonade_launcher.py:220).
    # ------------------------------------------------------------------

    def test_version_probe_uses_10s_timeout(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()
            self._write_package_json(webui_dir, ">=18")
            (webui_dir / "node_modules").mkdir()

            with (
                patch("gaia.ui.build.shutil.which", return_value="/usr/bin/node"),
                patch(
                    "gaia.ui.build.subprocess.run",
                    return_value=MagicMock(stdout="v20.0.0\n", stderr="", returncode=0),
                ) as mock_run,
            ):
                ensure_webui_built(_webui_dir=webui_dir)

        version_call = mock_run.call_args_list[0]
        self.assertEqual(version_call.args[0], ["/usr/bin/node", "--version"])
        self.assertEqual(version_call.kwargs.get("timeout"), 10)

    # ------------------------------------------------------------------
    # Escape hatch: GAIA_SKIP_WEBUI_BUILD short-circuits before any check.
    # ------------------------------------------------------------------

    def test_gaia_skip_webui_build_env_var_short_circuits(self):
        from gaia.ui.build import ensure_webui_built

        with tempfile.TemporaryDirectory() as tmpdir:
            webui_dir = Path(tmpdir)
            (webui_dir / "src").mkdir()  # would otherwise trigger a build

            with (
                patch.dict("os.environ", {"GAIA_SKIP_WEBUI_BUILD": "1"}),
                patch("gaia.ui.build.subprocess.run") as mock_run,
            ):
                result = ensure_webui_built(_webui_dir=webui_dir)

        mock_run.assert_not_called()
        self.assertEqual(result.status, WebuiBuildStatus.SKIPPED)


class TestCliEnsureWebuiBuiltDegrades(unittest.TestCase):
    """AC9 (#2880): gaia.cli._ensure_webui_built / _launch_agent_ui must
    degrade -- keep going -- on a hard build-failure outcome, never crash.
    cli.py's callers (bare `gaia`, `gaia chat --ui`, `gaia --ui`, the
    interactive menu) have no try/except around this call.

    ensure_webui_built() itself already reports a hard failure through the
    warn_fn it's given (proven in TestNodeVersionPreflight); these tests
    check that _ensure_webui_built() wires log.warning through as warn_fn
    -- not that it re-prints the message a second time, which would double
    the output the user sees.
    """

    def test_node_too_old_wires_warn_fn_and_does_not_raise(self):
        from gaia.cli import _ensure_webui_built

        fake_result = WebuiBuildResult(
            status=WebuiBuildStatus.NODE_TOO_OLD,
            message="Agent UI frontend requires Node >=20.19.0, but found 18.19.0.",
        )
        mock_log = MagicMock()

        with patch(
            "gaia.ui.build.ensure_webui_built", return_value=fake_result
        ) as mock_ensure:
            result = _ensure_webui_built(log=mock_log)  # must not raise

        self.assertEqual(result.status, WebuiBuildStatus.NODE_TOO_OLD)
        mock_ensure.assert_called_once_with(
            log_fn=mock_log.info, warn_fn=mock_log.warning
        )

    def test_build_failed_wires_warn_fn_and_does_not_raise(self):
        from gaia.cli import _ensure_webui_built

        fake_result = WebuiBuildResult(
            status=WebuiBuildStatus.BUILD_FAILED,
            message="Warning: Frontend build failed (exit code 1).",
        )
        mock_log = MagicMock()

        with patch(
            "gaia.ui.build.ensure_webui_built", return_value=fake_result
        ) as mock_ensure:
            result = _ensure_webui_built(log=mock_log)  # must not raise

        self.assertEqual(result.status, WebuiBuildStatus.BUILD_FAILED)
        mock_ensure.assert_called_once_with(
            log_fn=mock_log.info, warn_fn=mock_log.warning
        )

    def test_launch_agent_ui_proceeds_past_a_hard_build_failure(self):
        """_launch_agent_ui must still attempt to start the server after a
        hard webui-build failure -- it degrades, it never aborts the
        launch."""
        import gaia.cli as gaia_cli

        fake_result = WebuiBuildResult(
            status=WebuiBuildStatus.NODE_TOO_OLD, message="too old"
        )

        with (
            patch("gaia.ui.build.ensure_webui_built", return_value=fake_result),
            patch("gaia.ui.server.create_app") as mock_create_app,
            patch("uvicorn.run"),
        ):
            try:
                gaia_cli._launch_agent_ui(port=0, log=MagicMock())
            except Exception as e:  # pragma: no cover - failure path
                self.fail(f"_launch_agent_ui raised on a hard build failure: {e}")

        mock_create_app.assert_called_once()


class TestSkipWebuiBuildFlag(unittest.TestCase):
    """The --skip-webui-build escape hatch (#2880): `gaia init` must expose
    it as a CLI flag and forward it through to run_init/InitCommand, and
    InitCommand must honor it by never calling ensure_webui_built at all.
    """

    def test_cli_parser_defines_skip_webui_build_flag(self):
        from gaia.cli import build_parser

        parser = build_parser()

        args_off = parser.parse_args(["init"])
        self.assertFalse(getattr(args_off, "skip_webui_build", True))

        args_on = parser.parse_args(["init", "--skip-webui-build"])
        self.assertTrue(args_on.skip_webui_build)

    def test_init_command_skip_webui_build_never_calls_ensure_webui_built(self):
        from gaia.installer.init_command import InitCommand

        with patch("gaia.installer.init_command.LemonadeInstaller"):
            cmd = InitCommand(
                profile="minimal",
                yes=True,
                skip_lemonade=True,
                skip_models=True,
                skip_webui_build=True,
            )

        with (
            patch("gaia.ui.build.ensure_webui_built") as mock_ensure,
            patch.object(cmd, "_ensure_server_running", return_value=True),
            patch.object(cmd, "_verify_setup", return_value=True),
            patch("gaia.config.GaiaConfig"),
        ):
            cmd.run()

        mock_ensure.assert_not_called()


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
            cmd.skip_webui_build = False
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
