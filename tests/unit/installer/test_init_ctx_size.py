# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests verifying `gaia init` auto-starts Lemonade with the right context
size, across both legacy and modern Lemonade CLI tooling (issue #316/#839).

Issue #839: when GAIA itself spawns the Lemonade server in CI/auto mode, the
launched server must come up with `<profile.min_context_size>` so it doesn't
come up with a default (small) context window that GAIA later has to fight
to correct.

Issue #316: modern Lemonade Server (10.7/10.8) removed the `lemonade-server`
CLI. Legacy tooling passes ctx size via `--ctx-size N` on the Popen argv;
modern tooling passes it via the `LEMONADE_CTX_SIZE` env var instead. Both
must be exercised so a future regression can't silently drop the modern path
while only the legacy argv assertions keep passing.
"""

import os
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


def _legacy_tooling(client_path="/usr/bin/lemonade-server"):
    from gaia.llm.lemonade_launcher import LemonadeTooling

    return LemonadeTooling(
        found=True, kind="legacy", client_path=client_path, server_launcher=client_path
    )


def _modern_tooling_windows():
    from gaia.llm.lemonade_launcher import LemonadeTooling

    return LemonadeTooling(
        found=True,
        kind="modern",
        client_path=r"C:\Users\test\AppData\Local\lemonade_server\bin\lemonade.exe",
        server_launcher=(
            r"C:\Users\test\AppData\Local\lemonade_server\bin\LemonadeServer.exe"
        ),
    )


def _modern_tooling_linux():
    from gaia.llm.lemonade_launcher import LemonadeTooling

    return LemonadeTooling(
        found=True,
        kind="modern",
        client_path="/usr/bin/lemonade",
        server_launcher="/usr/bin/lemond",
    )


# ---------------------------------------------------------------------------
# Legacy tooling — argv-based --ctx-size (regression guard, re-plumbed
# through resolve_lemonade/build_start_command per the new contract)
# ---------------------------------------------------------------------------


@patch("sys.platform", "linux")
@patch("subprocess.Popen")
@patch("gaia.installer.init_command.build_start_command")
@patch("gaia.installer.init_command.resolve_lemonade")
@patch("gaia.llm.lemonade_client.LemonadeClient")
def test_linux_legacy_auto_start_includes_ctx_size(
    mock_client_cls, mock_resolve, mock_build_cmd, mock_popen
):
    """Linux legacy Popen argv must contain `--ctx-size 32768` for profile=chat."""
    from gaia.llm.lemonade_launcher import StartSpec

    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()
    mock_resolve.return_value = _legacy_tooling()
    mock_build_cmd.return_value = StartSpec(
        argv=["/usr/bin/lemonade-server", "serve", "--ctx-size", "32768"],
        env={},
    )

    cmd = _make_cmd(profile="chat")
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
    # build_start_command must have been called WITH the profile's ctx size
    mock_build_cmd.assert_called_once()
    _, kwargs = mock_build_cmd.call_args
    call_ctx = kwargs.get("ctx_size", mock_build_cmd.call_args.args[-1:])
    assert 32768 in (call_ctx if isinstance(call_ctx, (list, tuple)) else [call_ctx])


@patch("sys.platform", "win32")
@patch("subprocess.Popen")
@patch("gaia.installer.init_command.build_start_command")
@patch("gaia.installer.init_command.resolve_lemonade")
@patch("gaia.llm.lemonade_client.LemonadeClient")
def test_windows_legacy_auto_start_includes_no_tray_and_ctx_size(
    mock_client_cls, mock_resolve, mock_build_cmd, mock_popen
):
    """Windows legacy Popen argv must contain --no-tray AND --ctx-size 32768."""
    from gaia.llm.lemonade_launcher import StartSpec

    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()
    mock_resolve.return_value = _legacy_tooling(client_path=r"C:\lemonade-server.exe")
    mock_build_cmd.return_value = StartSpec(
        argv=[
            r"C:\lemonade-server.exe",
            "serve",
            "--no-tray",
            "--ctx-size",
            "32768",
        ],
        env={},
    )

    cmd = _make_cmd(profile="chat")
    result = cmd._ensure_server_running()

    assert result is True
    argv = mock_popen.call_args.args[0]
    assert "--no-tray" in argv, f"--no-tray missing from Windows argv: {argv}"
    assert "--ctx-size" in argv, f"--ctx-size missing from argv: {argv}"
    idx = argv.index("--ctx-size")
    assert argv[idx + 1] == "32768"


# ---------------------------------------------------------------------------
# Modern tooling — LEMONADE_CTX_SIZE env var, NOT argv (AC4 / issue #316)
# ---------------------------------------------------------------------------


@patch("sys.platform", "win32")
@patch("subprocess.Popen")
@patch("gaia.installer.init_command.build_start_command")
@patch("gaia.installer.init_command.resolve_lemonade")
@patch("gaia.llm.lemonade_client.LemonadeClient")
def test_windows_modern_auto_start_passes_ctx_size_via_env_not_argv(
    mock_client_cls, mock_resolve, mock_build_cmd, mock_popen
):
    """Modern Windows: LemonadeServer.exe --silent launched with
    env['LEMONADE_CTX_SIZE'] == '32768' — ctx size travels via env, NOT argv.
    This is the #839 guard for the modern (post-#316) CLI path."""
    from gaia.llm.lemonade_launcher import StartSpec

    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()
    mock_resolve.return_value = _modern_tooling_windows()
    mock_build_cmd.return_value = StartSpec(
        argv=[
            r"C:\Users\test\AppData\Local\lemonade_server\bin\LemonadeServer.exe",
            "--silent",
        ],
        env={"LEMONADE_CTX_SIZE": "32768"},
    )

    cmd = _make_cmd(profile="chat")
    with patch.dict(os.environ, {"GAIA_TEST_SENTINEL": "1"}):
        result = cmd._ensure_server_running()

    assert result is True
    argv = mock_popen.call_args.args[0]
    assert "--ctx-size" not in argv, (
        "modern tooling must not pass --ctx-size on argv — "
        f"got: {argv}"
    )
    assert argv[-1] == "--silent"

    _, popen_kwargs = mock_popen.call_args
    env = popen_kwargs.get("env", {})
    assert (
        env.get("LEMONADE_CTX_SIZE") == "32768"
    ), f"expected LEMONADE_CTX_SIZE=32768 in Popen env, got: {env}"
    # The spec env must be MERGED into the parent environment, never replace
    # it — Popen(argv, env=spec.env) alone would drop PATH/LOCALAPPDATA and
    # break LemonadeServer.exe.
    assert env.get("GAIA_TEST_SENTINEL") == "1", (
        "Popen env must retain the parent environment "
        f"({{**os.environ, **spec.env}}), got keys: {sorted(env)[:10]}..."
    )
    assert "PATH" in env, "Popen env must retain PATH from the parent environment"


@patch("sys.platform", "linux")
@patch("subprocess.Popen")
@patch("gaia.installer.init_command.build_start_command")
@patch("gaia.installer.init_command.resolve_lemonade")
@patch("gaia.llm.lemonade_client.LemonadeClient")
def test_linux_modern_auto_start_passes_ctx_size_via_env(
    mock_client_cls, mock_resolve, mock_build_cmd, mock_popen
):
    """Modern Linux best-effort path (systemctl --user start lemond) also
    carries ctx size via LEMONADE_CTX_SIZE env, not argv."""
    from gaia.llm.lemonade_launcher import StartSpec

    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()
    mock_resolve.return_value = _modern_tooling_linux()
    mock_build_cmd.return_value = StartSpec(
        argv=["systemctl", "--user", "start", "lemond"],
        env={"LEMONADE_CTX_SIZE": "32768"},
    )

    cmd = _make_cmd(profile="chat")
    with patch.dict(os.environ, {"GAIA_TEST_SENTINEL": "1"}):
        result = cmd._ensure_server_running()

    assert result is True
    argv = mock_popen.call_args.args[0]
    assert "--ctx-size" not in argv

    _, popen_kwargs = mock_popen.call_args
    env = popen_kwargs.get("env", {})
    assert env.get("LEMONADE_CTX_SIZE") == "32768"
    # Merged into parent env, never a replacement (see Windows test above).
    assert env.get("GAIA_TEST_SENTINEL") == "1"
    assert "PATH" in env


# ---------------------------------------------------------------------------
# Profile selection — value comes from profile, not hardcoded
# ---------------------------------------------------------------------------


def test_all_profiles_define_min_context_size():
    """Every shipped profile must declare min_context_size so init never spawns
    Lemonade without an explicit ctx size value."""
    for name, profile in INIT_PROFILES.items():
        assert (
            "min_context_size" in profile
        ), f"profile {name!r} missing min_context_size"
        assert isinstance(profile["min_context_size"], int)
        assert profile["min_context_size"] > 0


@patch("sys.platform", "linux")
@patch("subprocess.Popen")
@patch("gaia.installer.init_command.build_start_command")
@patch("gaia.installer.init_command.resolve_lemonade")
@patch("gaia.llm.lemonade_client.LemonadeClient")
@pytest.mark.parametrize("profile_name", sorted(INIT_PROFILES.keys()))
def test_profile_min_context_size_is_passed(
    mock_client_cls, mock_resolve, mock_build_cmd, mock_popen, profile_name
):
    """The ctx-size value passed to build_start_command() must equal
    INIT_PROFILES[profile]['min_context_size'] (legacy argv shape used here
    for concreteness; the value itself is what's under test)."""
    from gaia.llm.lemonade_launcher import StartSpec

    _patch_health_unreachable(mock_client_cls)
    mock_popen.return_value = MagicMock()
    mock_resolve.return_value = _legacy_tooling()

    expected = str(INIT_PROFILES[profile_name]["min_context_size"])

    def _build_cmd_side_effect(tooling, ctx_size):
        return StartSpec(
            argv=["/usr/bin/lemonade-server", "serve", "--ctx-size", str(ctx_size)],
            env={},
        )

    mock_build_cmd.side_effect = _build_cmd_side_effect

    cmd = _make_cmd(profile=profile_name)
    cmd._ensure_server_running()

    argv = mock_popen.call_args.args[0]
    idx = argv.index("--ctx-size")
    assert (
        argv[idx + 1] == expected
    ), f"profile={profile_name}: expected ctx-size {expected}, got {argv[idx + 1]}"
