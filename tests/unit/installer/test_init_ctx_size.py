# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests verifying `gaia init` auto-starts Lemonade with --ctx-size.

Issue #839: when GAIA itself spawns the Lemonade server in CI/auto mode,
the subprocess.Popen argv must include `--ctx-size <profile.min_context_size>`
so the server doesn't come up with a default (small) context window that GAIA
later has to fight to correct.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import INIT_PROFILES, InitCommand


@pytest.fixture(autouse=True)
def _isolate_remote_env(monkeypatch):
    """Strip LEMONADE_BASE_URL so InitCommand doesn't auto-flip to remote mode."""
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)


def _make_cmd(profile: str = "chat") -> InitCommand:
    """Build an InitCommand wired for CI/auto mode."""
    return InitCommand(profile=profile, yes=True, skip_models=True)


def _patch_health_unreachable(client_mock):
    """Configure the lazily-instantiated LemonadeClient to look 'not running'.

    health_check() returns falsy on the first call so _ensure_server_running
    falls through to the auto-start branch, then returns 'ok' once Popen has
    been "called" so the wait-loop exits.
    """
    instance = client_mock.return_value
    instance.health_check.side_effect = [
        None,  # first call: server not running
        {"status": "ok"},  # post-Popen wait-loop: server up
    ]
    return instance


# ---------------------------------------------------------------------------
# Linux/macOS branch
# ---------------------------------------------------------------------------


@patch("sys.platform", "linux")
@patch("subprocess.Popen")
@patch("gaia.llm.lemonade_client.LemonadeClient")
def test_linux_auto_start_includes_ctx_size(mock_client_cls, mock_popen):
    """Linux Popen argv must contain `--ctx-size 32768` for profile=chat."""
    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()

    cmd = _make_cmd(profile="chat")
    with patch.object(
        cmd, "_find_lemonade_server", return_value="/usr/bin/lemonade-server"
    ):
        result = cmd._ensure_server_running()

    assert result is True, "expected auto-start to report success"
    assert mock_popen.called, "expected subprocess.Popen to be invoked"
    argv = mock_popen.call_args.args[0]
    # Literal 32768 — testing-against-the-imported-constant is circular and
    # would not catch a future regression that flips the profile default.
    assert "--ctx-size" in argv, f"--ctx-size missing from argv: {argv}"
    idx = argv.index("--ctx-size")
    assert (
        argv[idx + 1] == "32768"
    ), f"expected ctx-size value 32768, got {argv[idx + 1]}"
    # And `serve` must come before `--ctx-size`
    assert argv.index("serve") < idx


# ---------------------------------------------------------------------------
# Windows branch
# ---------------------------------------------------------------------------


@patch("sys.platform", "win32")
@patch("subprocess.Popen")
@patch("gaia.llm.lemonade_client.LemonadeClient")
def test_windows_auto_start_includes_ctx_size(mock_client_cls, mock_popen):
    """Windows Popen argv must contain --no-tray AND --ctx-size 32768."""
    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()

    cmd = _make_cmd(profile="chat")
    with patch.object(cmd, "_find_lemonade_server", return_value=r"C:\lemonade.exe"):
        result = cmd._ensure_server_running()

    assert result is True
    argv = mock_popen.call_args.args[0]
    assert "--no-tray" in argv, f"--no-tray missing from Windows argv: {argv}"
    assert "--ctx-size" in argv, f"--ctx-size missing from argv: {argv}"
    idx = argv.index("--ctx-size")
    assert argv[idx + 1] == "32768"


# ---------------------------------------------------------------------------
# Profile selection — value comes from profile, not hardcoded
# ---------------------------------------------------------------------------


def test_all_profiles_define_min_context_size():
    """Every shipped profile must declare min_context_size so init never spawns
    Lemonade without an explicit --ctx-size value."""
    for name, profile in INIT_PROFILES.items():
        assert (
            "min_context_size" in profile
        ), f"profile {name!r} missing min_context_size"
        assert isinstance(profile["min_context_size"], int)
        assert profile["min_context_size"] > 0


@patch("sys.platform", "linux")
@patch("subprocess.Popen")
@patch("gaia.llm.lemonade_client.LemonadeClient")
@pytest.mark.parametrize("profile_name", sorted(INIT_PROFILES.keys()))
def test_profile_min_context_size_is_passed(mock_client_cls, mock_popen, profile_name):
    """argv ctx-size value must equal INIT_PROFILES[profile]['min_context_size']."""
    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()

    cmd = _make_cmd(profile=profile_name)
    with patch.object(
        cmd, "_find_lemonade_server", return_value="/usr/bin/lemonade-server"
    ):
        cmd._ensure_server_running()

    expected = str(INIT_PROFILES[profile_name]["min_context_size"])
    argv = mock_popen.call_args.args[0]
    idx = argv.index("--ctx-size")
    assert (
        argv[idx + 1] == expected
    ), f"profile={profile_name}: expected ctx-size {expected}, got {argv[idx + 1]}"
