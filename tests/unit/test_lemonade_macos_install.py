# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Control-flow tests for the macOS Lemonade install path.

Nobody can run `installer -pkg … -target /` in CI, so these pin the parts a
reviewer would otherwise have to take on faith: the exact argv, when sudo is
prefixed, that the elevation warning is printed *before* sudo is invoked, and
that every failure mode returns a loud InstallResult instead of a half-install.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.lemonade_installer import LemonadeInfo
from gaia.version import LEMONADE_VERSION
from tests.fixtures.lemonade_assets import make_installer

PKG = Path(f"/tmp/Lemonade-{LEMONADE_VERSION}-Darwin.pkg")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def installer():
    return make_installer("Darwin", "arm64")


@pytest.fixture(autouse=True)
def _non_root():
    """Pin euid — a root CI container would otherwise skip every sudo branch."""
    with patch("os.geteuid", return_value=501):
        yield


@pytest.fixture
def installed_ok():
    """check_installation() reporting a healthy install."""
    return LemonadeInfo(installed=True, version="11.5.0", path="/usr/local/bin/lemond")


class TestSudoHandling:
    def test_non_interactive_without_cached_sudo_refuses_before_installing(
        self, installer
    ):
        """--yes must not hang on a hidden password prompt."""
        with patch("subprocess.run", return_value=_completed(returncode=1)) as run:
            result = installer._install_macos(PKG, non_interactive=True)

        assert result.success is False
        assert "sudo -v" in result.error
        # Only the `sudo -n true` probe ran; installer was never invoked.
        assert run.call_count == 1
        assert run.call_args_list[0].args[0] == ["sudo", "-n", "true"]

    def test_interactive_announces_elevation_before_invoking_sudo(
        self, installer, installed_ok
    ):
        """The user is told sudo is coming — not surprised by a bare prompt."""
        events = []

        def _run(cmd, **_kwargs):
            events.append(("run", cmd[0]))
            return _completed(returncode=1 if cmd[:2] == ["sudo", "-n"] else 0)

        with (
            patch("subprocess.run", side_effect=_run),
            patch.object(
                type(installer),
                "_announce",
                lambda _self, msg: events.append(("announce", msg)),
            ),
            patch.object(
                type(installer), "check_installation", return_value=installed_ok
            ),
        ):
            result = installer._install_macos(PKG, non_interactive=False)

        assert result.success is True
        kinds = [kind for kind, _ in events]
        announce_at = kinds.index("announce")
        install_at = [
            i for i, (k, v) in enumerate(events) if k == "run" and v == "sudo"
        ][-1]
        assert announce_at < install_at, f"warning must precede sudo: {events}"

    def test_cached_sudo_skips_the_warning(self, installer, installed_ok):
        """No prompt is coming, so don't cry wolf."""
        announced = []
        with (
            patch("subprocess.run", return_value=_completed(0)),
            patch.object(
                type(installer), "_announce", lambda _self, msg: announced.append(msg)
            ),
            patch.object(
                type(installer), "check_installation", return_value=installed_ok
            ),
        ):
            installer._install_macos(PKG, non_interactive=False)

        assert announced == []

    def test_root_does_not_prefix_sudo(self, installer, installed_ok):
        with (
            patch("os.geteuid", return_value=0),
            patch("subprocess.run", return_value=_completed(0)) as run,
            patch.object(
                type(installer), "check_installation", return_value=installed_ok
            ),
        ):
            installer._install_macos(PKG, non_interactive=False)

        assert run.call_count == 1  # no sudo probe needed
        assert run.call_args_list[0].args[0] == [
            "installer",
            "-pkg",
            str(PKG),
            "-target",
            "/",
        ]


class TestInstallerInvocation:
    def test_argv_shape_matches_installer_8(self, installer, installed_ok):
        """`installer -pkg <path> -target /` — order matters to installer(8)."""
        with (
            patch("subprocess.run", return_value=_completed(0)) as run,
            patch.object(
                type(installer), "check_installation", return_value=installed_ok
            ),
        ):
            installer._install_macos(PKG, non_interactive=False)

        argv = run.call_args_list[-1].args[0]
        assert argv == ["sudo", "installer", "-pkg", str(PKG), "-target", "/"]

    def test_nonzero_exit_surfaces_output_and_manual_command(self, installer):
        def _run(cmd, **_kwargs):
            if cmd[:2] == ["sudo", "-n"]:
                return _completed(0)
            return _completed(returncode=1, stderr="installer: Package Authoring Error")

        with patch("subprocess.run", side_effect=_run):
            result = installer._install_macos(PKG, non_interactive=False)

        assert result.success is False
        assert "Package Authoring Error" in result.error
        assert "sudo installer -pkg" in result.error

    def test_exit_zero_but_nothing_installed_is_a_failure(self, installer):
        """installer(8) can exit 0 under MDM while installing nothing."""
        missing = LemonadeInfo(installed=False, error="not found")
        with (
            patch("subprocess.run", return_value=_completed(0)),
            patch.object(type(installer), "check_installation", return_value=missing),
        ):
            result = installer._install_macos(PKG, non_interactive=False)

        assert result.success is False
        assert "installer reported success but Lemonade was not found" in result.error

    def test_success_reports_the_probed_version_not_the_target(self, installer):
        probed = LemonadeInfo(
            installed=True, version="11.4.0", path="/usr/local/bin/lemond"
        )
        with (
            patch("subprocess.run", return_value=_completed(0)),
            patch.object(type(installer), "check_installation", return_value=probed),
        ):
            result = installer._install_macos(PKG, non_interactive=False)

        assert result.success is True
        assert result.version == "11.4.0"

    def test_ctrl_c_at_the_prompt_is_a_clean_cancel(self, installer):
        """KeyboardInterrupt is BaseException — it must not escape as a traceback."""
        with patch("subprocess.run", side_effect=KeyboardInterrupt()):
            result = installer._install_macos(PKG, non_interactive=False)

        assert result.success is False
        assert "cancelled" in result.error.lower()

    def test_timeout_is_reported_with_a_retry_command(self, installer):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="installer", timeout=600),
        ):
            result = installer._install_macos(PKG, non_interactive=False)

        assert result.success is False
        assert "timed out" in result.error
        assert "sudo installer -pkg" in result.error


class TestInstallRouting:
    def test_darwin_routes_to_macos_installer(self, installer):
        with (
            patch.object(
                type(installer), "_install_macos", return_value=MagicMock(success=True)
            ) as macos,
            patch.object(Path, "exists", return_value=True),
        ):
            installer.install(PKG, silent=False)
        macos.assert_called_once()

    def test_missing_pkg_fails_before_invoking_installer(self, installer):
        with patch("subprocess.run") as run:
            result = installer.install(Path("/tmp/nope.pkg"), silent=True)

        assert result.success is False
        assert "Installer not found" in result.error
        run.assert_not_called()
