# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Contract tests for gaia.llm.lemonade_launcher (issue #316).

Modern Lemonade Server (10.7/10.8) removed the ``lemonade-server`` CLI:
Windows ships ``LemonadeServer.exe`` (server) + ``lemonade.exe`` (client),
context size is set via the ``LEMONADE_CTX_SIZE`` env var (not a
``--ctx-size`` flag), and Linux ships ``/usr/bin/lemonade`` +
``/usr/bin/lemond`` started via the ``lemond`` systemd unit. Legacy
Lemonade still uses ``lemonade-server ... serve --ctx-size N``.

This module does not exist yet — every test here is expected to fail at
collection (ImportError) or on the first call into the not-yet-implemented
primitives, until the fix lands.
"""

import os
from pathlib import Path

import pytest

from gaia.llm.lemonade_launcher import (
    build_start_command,
    get_installed_version,
    resolve_lemonade,
)

# Real captured modern-client output (from `lemonade --version` on Windows
# 10.7.0) — must parse to exactly "10.7.0" via re.search(r"(\d+\.\d+\.\d+)").
MODERN_VERSION_OUTPUT = "lemonade version 10.7.0"


def only_these_paths_exist(mocker, *present):
    """Make Path.exists() report True for exactly *present* and nothing else.

    Lets the Windows / Linux / macOS answers all be proven on any runner: the
    host's real filesystem never leaks into the probe, so a macOS test passes
    on a Linux CI box and vice versa.
    """
    wanted = {str(Path(p).expanduser()) for p in present}
    mocker.patch(
        "pathlib.Path.exists",
        autospec=True,
        side_effect=lambda self: str(self.expanduser()) in wanted,
    )


# ---------------------------------------------------------------------------
# resolve_lemonade() precedence (AC3)
# ---------------------------------------------------------------------------


def test_lemonade_server_path_env_var_used_verbatim_and_which_not_called(mocker):
    """LEMONADE_SERVER_PATH set -> used verbatim; shutil.which never called."""
    mock_which = mocker.patch("shutil.which")
    mocker.patch.dict(
        os.environ, {"LEMONADE_SERVER_PATH": "/custom/path/to/lemonade-server"}
    )

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.client_path == "/custom/path/to/lemonade-server"
    mock_which.assert_not_called()


def test_modern_canonical_path_wins_over_legacy_on_path(mocker):
    """Modern binary at its canonical path wins even when a legacy
    lemonade-server binary is ALSO discoverable via shutil.which.

    This guards against a stale legacy binary sitting earlier on PATH than
    the modern install — modern must win regardless of PATH order.
    """
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Linux")
    # Legacy IS on PATH...
    mocker.patch("shutil.which", return_value="/usr/local/bin/lemonade-server")
    # ...but the modern canonical path also exists.
    mocker.patch("pathlib.Path.exists", return_value=True)

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.kind == "modern"


def test_legacy_lemonade_server_dev_treated_as_legacy_hit(mocker):
    """shutil.which("lemonade-server-dev") is an equivalent legacy hit
    (the pip/CI variant of the legacy CLI)."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Linux")
    # No modern canonical binary present.
    mocker.patch("pathlib.Path.exists", return_value=False)

    def which_side_effect(name):
        if name == "lemonade-server-dev":
            return "/usr/local/bin/lemonade-server-dev"
        return None

    mocker.patch("shutil.which", side_effect=which_side_effect)

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.kind == "legacy"


def test_legacy_only_no_modern_present(mocker):
    """Only legacy lemonade-server on PATH, no modern canonical binary ->
    kind == 'legacy'."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("pathlib.Path.exists", return_value=False)
    mocker.patch("shutil.which", return_value="/usr/bin/lemonade-server")

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.kind == "legacy"


def test_nothing_found_returns_not_found(mocker):
    """No env var, no modern canonical path, no legacy on PATH -> not found."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("pathlib.Path.exists", return_value=False)
    mocker.patch("shutil.which", return_value=None)

    tooling = resolve_lemonade()

    assert tooling.found is False


# ---------------------------------------------------------------------------
# get_installed_version() (AC1 — indirectly, via the primitive)
# ---------------------------------------------------------------------------


def test_get_installed_version_parses_modern_client_output(mocker):
    """Modern client's `--version` output parses to exactly '10.7.0'."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Windows")
    mocker.patch("pathlib.Path.exists", return_value=True)

    tooling = resolve_lemonade()
    assert tooling.kind == "modern"

    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = MODERN_VERSION_OUTPUT
    mock_run.return_value.stderr = ""

    version = get_installed_version(tooling)

    assert version == "10.7.0"


def test_get_installed_version_parses_legacy_client_output(mocker):
    """Legacy `lemonade-server --version` output still parses correctly
    (regression guard — AC2)."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("pathlib.Path.exists", return_value=False)
    mocker.patch("shutil.which", return_value="/usr/bin/lemonade-server")

    tooling = resolve_lemonade()
    assert tooling.kind == "legacy"

    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "lemonade-server 9.1.4"
    mock_run.return_value.stderr = ""

    version = get_installed_version(tooling)

    assert version == "9.1.4"


# ---------------------------------------------------------------------------
# build_start_command() (AC4)
# ---------------------------------------------------------------------------


def test_build_start_command_modern_windows():
    """Modern Windows: argv=[<LemonadeServer.exe path>, '--silent'],
    env={'LEMONADE_CTX_SIZE': '32768'} — ctx size travels via env, not argv."""
    from gaia.llm.lemonade_launcher import LemonadeTooling

    tooling = LemonadeTooling(
        found=True,
        kind="modern",
        client_path=r"C:\Users\test\AppData\Local\lemonade_server\bin\lemonade.exe",
        server_launcher=(
            r"C:\Users\test\AppData\Local\lemonade_server\bin\LemonadeServer.exe"
        ),
    )

    spec = build_start_command(tooling, ctx_size=32768)

    assert spec.argv == [
        r"C:\Users\test\AppData\Local\lemonade_server\bin\LemonadeServer.exe",
        "--silent",
    ]
    assert spec.env == {"LEMONADE_CTX_SIZE": "32768"}


def test_build_start_command_legacy(mocker):
    """Legacy (non-Windows): argv=['lemonade-server', 'serve', '--ctx-size',
    '32768'], env={} — unchanged from today's behavior."""
    from gaia.llm.lemonade_launcher import LemonadeTooling

    mocker.patch("platform.system", return_value="Linux")
    tooling = LemonadeTooling(
        found=True,
        kind="legacy",
        client_path="lemonade-server",
        server_launcher="lemonade-server",
    )

    spec = build_start_command(tooling, ctx_size=32768)

    assert spec.argv == ["lemonade-server", "serve", "--ctx-size", "32768"]
    assert spec.env == {}


def test_build_start_command_legacy_windows_includes_no_tray(mocker):
    """Legacy on Windows: argv gains '--no-tray' alongside '--ctx-size'
    (preserves today's Windows auto-start argv byte-for-byte)."""
    from gaia.llm.lemonade_launcher import LemonadeTooling

    mocker.patch("platform.system", return_value="Windows")
    tooling = LemonadeTooling(
        found=True,
        kind="legacy",
        client_path=r"C:\lemonade-server.exe",
        server_launcher=r"C:\lemonade-server.exe",
    )

    spec = build_start_command(tooling, ctx_size=32768)

    assert spec.argv[0] == r"C:\lemonade-server.exe"
    assert spec.argv[1] == "serve"
    assert "--no-tray" in spec.argv
    idx = spec.argv.index("--ctx-size")
    assert spec.argv[idx + 1] == "32768"
    assert spec.env == {}


def test_build_start_command_modern_linux_uses_systemctl(mocker):
    """Modern Linux best-effort path: systemctl --user start lemond,
    ctx size via LEMONADE_CTX_SIZE env var.

    Pins the host platform: the systemd form is Linux-only, and this assertion
    silently depended on the runner not being macOS before the Darwin branch
    existed."""
    from gaia.llm.lemonade_launcher import LemonadeTooling

    mocker.patch("platform.system", return_value="Linux")
    tooling = LemonadeTooling(
        found=True,
        kind="modern",
        client_path="/usr/bin/lemonade",
        server_launcher="/usr/bin/lemond",
    )

    spec = build_start_command(tooling, ctx_size=32768)

    assert spec.argv == ["systemctl", "--user", "start", "lemond"]
    assert spec.env == {"LEMONADE_CTX_SIZE": "32768"}


def test_env_override_modern_non_exe_launched_verbatim(mocker):
    """A modern-classified LEMONADE_SERVER_PATH override that is not a
    Windows .exe (e.g. an explicit Linux daemon path) is launched verbatim —
    never silently rerouted to systemctl."""
    mocker.patch.dict(os.environ, {"LEMONADE_SERVER_PATH": "/opt/lemonade/lemond"})

    tooling = resolve_lemonade()
    assert tooling.source == "env"
    assert tooling.kind == "modern"

    spec = build_start_command(tooling, ctx_size=32768)

    assert spec.argv == ["/opt/lemonade/lemond"]
    assert spec.env == {"LEMONADE_CTX_SIZE": "32768"}


def test_probe_resolved_modern_linux_still_uses_systemctl(mocker):
    """Guard: a probe-resolved modern Linux tooling (no env override) keeps
    the best-effort systemctl start — the override fast-path must not leak."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("pathlib.Path.exists", return_value=True)

    tooling = resolve_lemonade()
    assert tooling.source == "probe"
    assert tooling.kind == "modern"

    spec = build_start_command(tooling, ctx_size=32768)

    assert spec.argv == ["systemctl", "--user", "start", "lemond"]


# ---------------------------------------------------------------------------
# Env-merge semantics the caller is expected to apply (AC4)
# ---------------------------------------------------------------------------


def test_caller_env_merge_pattern_preserves_existing_path(mocker):
    """The caller applies {**os.environ, **spec.env} — verify this merge
    pattern is additive and does not clobber an existing PATH value."""
    from gaia.llm.lemonade_launcher import StartSpec

    mocker.patch.dict(
        os.environ, {"PATH": "/usr/bin:/bin", "SOME_OTHER_VAR": "keep-me"}
    )
    spec = StartSpec(argv=["irrelevant"], env={"LEMONADE_CTX_SIZE": "32768"})

    merged = {**os.environ, **spec.env}

    assert merged["PATH"] == "/usr/bin:/bin"
    assert merged["SOME_OTHER_VAR"] == "keep-me"
    assert merged["LEMONADE_CTX_SIZE"] == "32768"


# ---------------------------------------------------------------------------
# resolve_lemonade() on macOS — the resolver reported "not installed" about a
# Lemonade that was answering requests on the port GAIA itself probes, because
# there was no Darwin branch at all (issue #1867).
#
# Every test here injects BOTH platform.system() and Path.exists(), so the
# macOS answer is provable on a Linux/Windows runner and vice versa.
# ---------------------------------------------------------------------------

MACOS_DAEMON = "/usr/local/bin/lemond"
MACOS_CLIENT = "/usr/local/bin/lemonade"


def test_darwin_canonical_daemon_resolves_as_modern(mocker):
    """The regression: macOS with lemond + lemonade installed must resolve as
    a found, modern install — not fall through to the legacy probe."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value=None)
    only_these_paths_exist(mocker, MACOS_DAEMON, MACOS_CLIENT)

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.kind == "modern"
    assert tooling.server_launcher == MACOS_DAEMON
    assert tooling.client_path == MACOS_CLIENT
    assert tooling.source == "probe"


def test_darwin_resolves_even_when_only_the_daemon_is_present(mocker):
    """The daemon is what gets started; the client only serves the version
    query. A missing client must not make the install invisible."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value=None)
    only_these_paths_exist(mocker, MACOS_DAEMON)

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.kind == "modern"
    assert tooling.server_launcher == MACOS_DAEMON
    assert tooling.client_path is None


def test_darwin_homebrew_prefix_is_probed(mocker):
    """Apple-Silicon Homebrew installs land under /opt/homebrew/bin."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value=None)
    only_these_paths_exist(mocker, "/opt/homebrew/bin/lemond")

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.server_launcher == "/opt/homebrew/bin/lemond"


def test_darwin_daemon_found_on_path_outside_standard_prefixes(mocker):
    """A non-standard install prefix still counts when lemond is on PATH."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    only_these_paths_exist(mocker)  # nothing at any canonical path
    mocker.patch(
        "shutil.which",
        side_effect=lambda name: {
            "lemond": "/opt/custom/lemond",
            "lemonade": "/opt/custom/lemonade",
        }.get(name),
    )

    tooling = resolve_lemonade()

    assert tooling.found is True
    assert tooling.kind == "modern"
    assert tooling.server_launcher == "/opt/custom/lemond"
    assert tooling.client_path == "/opt/custom/lemonade"


def test_darwin_modern_wins_over_a_stale_legacy_binary_on_path(mocker):
    """Same precedence rule as Linux/Windows: modern beats a legacy CLI that
    happens to be on PATH."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value="/usr/local/bin/lemonade-server")
    only_these_paths_exist(mocker, MACOS_DAEMON, MACOS_CLIENT)

    tooling = resolve_lemonade()

    assert tooling.kind == "modern"
    assert tooling.server_launcher == MACOS_DAEMON


def test_darwin_with_nothing_installed_is_still_not_found(mocker):
    """The Darwin branch must not manufacture a phantom install."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    only_these_paths_exist(mocker)
    mocker.patch("shutil.which", return_value=None)

    tooling = resolve_lemonade()

    assert tooling.found is False
    assert tooling.kind == "none"


def test_darwin_env_override_still_wins_and_is_used_verbatim(mocker):
    """A user who names a binary means THAT binary — the new Darwin probe
    must not preempt LEMONADE_SERVER_PATH."""
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch.dict(os.environ, {"LEMONADE_SERVER_PATH": "/opt/mine/lemond"})
    mock_which = mocker.patch("shutil.which")
    only_these_paths_exist(mocker, MACOS_DAEMON, MACOS_CLIENT)

    tooling = resolve_lemonade()

    assert tooling.source == "env"
    assert tooling.server_launcher == "/opt/mine/lemond"
    mock_which.assert_not_called()


def test_darwin_probe_does_not_leak_into_linux_or_windows(mocker):
    """The macOS bin dirs must not be probed on other platforms — a Linux box
    with /usr/local/bin/lemond present is still resolved the Linux way."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Linux")
    mocker.patch("shutil.which", return_value=None)
    # ONLY the macOS-style path exists; the Linux canonical client does not.
    only_these_paths_exist(mocker, MACOS_DAEMON)

    tooling = resolve_lemonade()

    assert tooling.found is False, "macOS prefix must not satisfy the Linux probe"


# ---------------------------------------------------------------------------
# build_start_command() on macOS — systemctl is the Linux form and must never
# leak to a platform that has no systemd
# ---------------------------------------------------------------------------


def test_build_start_command_modern_macos_starts_the_daemon_not_systemctl(mocker):
    from gaia.llm.lemonade_launcher import LemonadeTooling

    mocker.patch("platform.system", return_value="Darwin")
    tooling = LemonadeTooling(
        found=True,
        kind="modern",
        client_path=MACOS_CLIENT,
        server_launcher=MACOS_DAEMON,
    )

    spec = build_start_command(tooling, ctx_size=32768)

    assert spec.argv == [MACOS_DAEMON]
    assert "systemctl" not in spec.argv
    assert spec.env == {"LEMONADE_CTX_SIZE": "32768"}


def test_build_start_command_modern_macos_without_launcher_fails_loudly(mocker):
    """No silent fallback: a modern macOS tooling object with no daemon path
    is a programming error, not something to guess at."""
    from gaia.llm.lemonade_launcher import LemonadeTooling

    mocker.patch("platform.system", return_value="Darwin")
    tooling = LemonadeTooling(
        found=True, kind="modern", client_path=MACOS_CLIENT, server_launcher=None
    )

    with pytest.raises(ValueError, match="lemond"):
        build_start_command(tooling, ctx_size=None)


def test_macos_end_to_end_resolve_then_start_command(mocker):
    """The whole chain the bug broke: probe -> resolve -> start command, with
    no mocking of resolve_lemonade itself (which would prove nothing)."""
    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value=None)
    only_these_paths_exist(mocker, MACOS_DAEMON, MACOS_CLIENT)

    spec = build_start_command(resolve_lemonade(), ctx_size=65536)

    assert spec.argv == [MACOS_DAEMON]
    assert spec.env == {"LEMONADE_CTX_SIZE": "65536"}


# ---------------------------------------------------------------------------
# describe_start_hint() — the user-facing remedy must never name a command
# that does not exist on the host (issue #1867)
# ---------------------------------------------------------------------------


def test_start_hint_modern_linux_names_systemctl_not_lemonade_server(mocker):
    """Modern Linux remedy is the systemd unit, never `lemonade-server serve`."""
    from gaia.llm.lemonade_launcher import LemonadeTooling, describe_start_hint

    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(
            found=True,
            kind="modern",
            client_path="/usr/bin/lemonade",
            server_launcher="/usr/bin/lemond",
        ),
    )

    hint = describe_start_hint(ctx_size=32768)

    assert hint.command == "LEMONADE_CTX_SIZE=32768 systemctl --user start lemond"
    assert "lemonade-server" not in hint.instruction
    # systemctl returns immediately — callers must not append " &".
    assert hint.foreground is False


def test_start_hint_legacy_still_names_lemonade_server_serve(mocker):
    """A real legacy install DOES have the CLI — keep telling it the truth."""
    from gaia.llm.lemonade_launcher import LemonadeTooling, describe_start_hint

    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(
            found=True,
            kind="legacy",
            client_path="/usr/local/bin/lemonade-server",
            server_launcher="/usr/local/bin/lemonade-server",
        ),
    )

    hint = describe_start_hint(ctx_size=8192)

    assert hint.command == "/usr/local/bin/lemonade-server serve --ctx-size 8192"
    assert hint.foreground is True


def test_start_hint_modern_windows_points_at_the_tray_not_a_shell_command(mocker):
    """Modern Windows has no start command a user would run — the server is
    launched from the tray icon, so emit prose and NO command."""
    from gaia.llm.lemonade_launcher import LemonadeTooling, describe_start_hint

    mocker.patch("platform.system", return_value="Windows")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(
            found=True,
            kind="modern",
            client_path=r"C:\lemonade_server\bin\lemonade.exe",
            server_launcher=r"C:\lemonade_server\bin\LemonadeServer.exe",
        ),
    )

    hint = describe_start_hint(ctx_size=32768)

    assert hint.command is None
    assert "lemonade-server" not in hint.instruction
    assert "tray" in hint.instruction.lower()


def test_start_hint_legacy_windows_path_is_joined_for_cmd_not_posix(mocker):
    """A Windows path must not come back wrapped in POSIX single quotes —
    `'C:\\x\\lemonade-server.exe' serve` is not runnable in cmd.exe."""
    from gaia.llm.lemonade_launcher import LemonadeTooling, describe_start_hint

    mocker.patch("platform.system", return_value="Windows")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(
            found=True,
            kind="legacy",
            client_path=r"C:\Program Files\lemonade\lemonade-server.exe",
            server_launcher=r"C:\Program Files\lemonade\lemonade-server.exe",
        ),
    )

    hint = describe_start_hint(ctx_size=None)

    assert hint.command is not None
    assert "'" not in hint.command
    assert hint.command.startswith('"C:\\Program Files')


def test_start_hint_windows_renders_env_the_cmd_way_never_drops_it(mocker):
    """A ctx-carrying env var must survive into the rendered command on
    Windows too — as `set VAR=...`, not the POSIX `VAR=... cmd` form (which
    cmd.exe would treat as the program name)."""
    from gaia.llm.lemonade_launcher import LemonadeTooling, describe_start_hint

    mocker.patch("platform.system", return_value="Windows")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(
            found=True,
            kind="modern",
            client_path=r"C:\lemonade\lemonade.exe",
            server_launcher=r"C:\lemonade\lemond",  # non-.exe modern launcher
            source="env",
        ),
    )

    hint = describe_start_hint(ctx_size=32768)

    assert hint.command is not None
    assert "LEMONADE_CTX_SIZE=32768" in hint.command
    assert not hint.command.startswith("LEMONADE_CTX_SIZE")
    assert hint.command.startswith("set LEMONADE_CTX_SIZE=32768 && ")


def test_start_hint_macos_names_the_daemon_via_real_detection(mocker):
    """macOS remedy on a working install, driven by the real probe — no
    mocking of resolve_lemonade, which would only prove we called it."""
    from gaia.llm.lemonade_launcher import describe_start_hint

    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value=None)
    only_these_paths_exist(mocker, MACOS_DAEMON, MACOS_CLIENT)

    hint = describe_start_hint(ctx_size=32768)

    assert hint.command == f"LEMONADE_CTX_SIZE=32768 {MACOS_DAEMON}"
    assert "lemonade-server" not in hint.instruction
    assert "systemctl" not in hint.instruction
    # The app is the normal macOS entry point, so name it alongside the CLI.
    assert "Lemonade app" in hint.instruction
    assert hint.foreground is True


def test_start_hint_macos_app_installed_but_no_binaries_describes_the_app(mocker):
    """App bundle present but no daemon on disk: describe the app, do NOT
    invent a shell command."""
    from gaia.llm.lemonade_launcher import StartHint, describe_start_hint

    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value=None)
    only_these_paths_exist(mocker, "/Applications/lemonade-app.app")

    hint = describe_start_hint()

    assert hint == StartHint(
        instruction="Start the Lemonade app from Applications, then retry."
    )


def test_start_hint_macos_nothing_installed_points_at_gaia_init(mocker):
    """Nothing installed on macOS: `gaia init` is the supported remedy."""
    from gaia.llm.lemonade_launcher import StartHint, describe_start_hint

    mocker.patch.dict(os.environ, {}, clear=True)
    mocker.patch("platform.system", return_value="Darwin")
    mocker.patch("shutil.which", return_value=None)
    only_these_paths_exist(mocker)

    hint = describe_start_hint()

    assert hint == StartHint(
        instruction=(
            "Lemonade Server is not installed. Run `gaia init` to install it, "
            "or set LEMONADE_SERVER_PATH to an existing install."
        )
    )


@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_start_hint_not_installed_on_supported_platform_points_at_gaia_init(
    mocker, system
):
    """Nothing installed on a supported platform: `gaia init` is the remedy,
    not a start command for software that isn't there."""
    from gaia.llm.lemonade_launcher import (
        LemonadeTooling,
        StartHint,
        describe_start_hint,
    )

    mocker.patch("platform.system", return_value=system)
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(found=False, kind="none"),
    )

    hint = describe_start_hint()

    assert hint == StartHint(
        instruction=(
            "Lemonade Server is not installed. Run `gaia init` to install it, "
            "or set LEMONADE_SERVER_PATH to an existing install."
        )
    )


def test_start_hint_instruction_embeds_the_command_verbatim(mocker):
    """`instruction` is the single string call sites print — when a command
    exists it must contain it, so no call site has to re-render it."""
    from gaia.llm.lemonade_launcher import LemonadeTooling, describe_start_hint

    mocker.patch("platform.system", return_value="Linux")
    mocker.patch(
        "gaia.llm.lemonade_launcher.resolve_lemonade",
        return_value=LemonadeTooling(
            found=True,
            kind="modern",
            client_path="/usr/bin/lemonade",
            server_launcher="/usr/bin/lemond",
        ),
    )

    hint = describe_start_hint(ctx_size=65536)

    assert hint.command in hint.instruction


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
