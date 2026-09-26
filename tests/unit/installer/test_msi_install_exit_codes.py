# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""msiexec exit codes from a silent Windows Lemonade install."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import InitCommand
from gaia.installer.lemonade_installer import (
    InstallResult,
    LemonadeInfo,
    LemonadeInstaller,
)


def _install_with_exit_code(code: int, tmp_path: Path) -> InstallResult:
    msi = tmp_path / "lemonade.msi"
    msi.write_bytes(b"")
    with patch("platform.system", return_value="Windows"):
        installer = LemonadeInstaller()
    with (
        patch.object(installer, "wait_for_msi_mutex", return_value=True),
        patch(
            "gaia.installer.lemonade_installer.subprocess.run",
            return_value=MagicMock(returncode=code, stdout="", stderr=""),
        ) as run,
    ):
        result = installer._install_windows(msi, silent=True)
    assert run.call_args.args[0][:2] == ["msiexec", "/i"]
    return result


@pytest.mark.parametrize("code", [3010, 1641])
def test_success_reboot_required_is_success(code, mock_home, tmp_path):
    result = _install_with_exit_code(code, tmp_path)

    assert result.success, result.error
    assert result.restart_required
    assert "Restart Windows" in result.message


def test_plain_success_needs_no_restart(mock_home, tmp_path):
    result = _install_with_exit_code(0, tmp_path)

    assert result.success
    assert not result.restart_required


@pytest.mark.parametrize("code", [1603, 1620])
def test_failure_names_the_msi_log(code, mock_home, tmp_path):
    result = _install_with_exit_code(code, tmp_path)

    assert not result.success
    expected_log = mock_home / ".cache" / "gaia" / "installer" / "msi_install.log"
    assert str(expected_log) in result.error


def test_init_tells_the_user_to_restart(mock_home):
    cmd = InitCommand(profile="minimal", yes=True)
    cmd.installer = MagicMock()
    cmd.installer.system = "windows"
    cmd.installer.install.return_value = InstallResult(
        success=True,
        version="1.0.0",
        message="Installed Lemonade v1.0.0. Restart Windows to finish the installation.",
        restart_required=True,
    )
    cmd.installer.check_installation.return_value = LemonadeInfo(installed=False)
    warnings = []

    with (
        patch.object(cmd, "_print_warning", side_effect=warnings.append),
        patch.object(cmd, "_refresh_path_environment"),
    ):
        assert cmd._install_lemonade()

    assert any("Restart Windows" in w for w in warnings)
