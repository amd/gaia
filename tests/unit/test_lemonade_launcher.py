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

import pytest

from gaia.llm.lemonade_launcher import (
    build_start_command,
    get_installed_version,
    resolve_lemonade,
)

# Real captured modern-client output (from `lemonade --version` on Windows
# 10.7.0) — must parse to exactly "10.7.0" via re.search(r"(\d+\.\d+\.\d+)").
MODERN_VERSION_OUTPUT = "lemonade version 10.7.0"


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


def test_build_start_command_modern_linux_uses_systemctl():
    """Modern Linux best-effort path: systemctl --user start lemond,
    ctx size via LEMONADE_CTX_SIZE env var."""
    from gaia.llm.lemonade_launcher import LemonadeTooling

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
