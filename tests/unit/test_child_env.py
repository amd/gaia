# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shell steps and MCP servers never inherit GAIA's internal credentials.

Shell steps and MCP servers run with a copy of GAIA's environment. GAIA's own
credentials (sidecar and broker tokens, the custody secret) are for GAIA, not for
the programs it launches, so ``gaia.env.child_env`` leaves them out — and an
embedder can name more variables to withhold via ``GAIA_CHILD_ENV_DENY``.

``GAIA_NO_DOTENV`` turns ``.env`` loading off. It is read from the startup
environment, so a ``.env`` file cannot decide whether ``.env`` files load.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import gaia
from gaia import env as gaia_env
from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools.shell_tools import ShellToolsMixin, _segment_env
from gaia.mcp.client.transports.stdio import StdioTransport
from gaia.mcp.external_services import ExternalMCPService

REPO_ROOT = Path(__file__).resolve().parents[2]

INTERNAL = {
    "GAIA_GAIA_SIDECAR_TOKEN": "tok-gaia",
    "GAIA_GAIA_SIDECAR_TOKEN_FILE": "/tmp/tok-gaia-file",
    "GAIA_EMAIL_SIDECAR_TOKEN": "tok-email",
    "GAIA_EMAIL_SIDECAR_TOKEN_FILE": "/tmp/tok-email-file",
    "GAIA_MODEL_BROKER_TOKEN": "tok-broker",
    "GAIA_MODEL_BROKER_TOKEN_FILE": "/tmp/tok-broker-file",
    "GAIA_HOST_CUSTODY_SECRET": "tok-custody",
    "GAIA_ENGINEERING_TOKEN": "tok-engineering",
}

PYTHON = shutil.which("python3") or shutil.which("python")
# Windows steps run through cmd.exe, whose quoting this one-liner does not target.
needs_python = pytest.mark.skipif(
    PYTHON is None or sys.platform == "win32",
    reason="needs a POSIX host with python on PATH",
)
DUMP_ENV = (
    f"{Path(PYTHON or 'python').stem} -c "
    "\"import os; print(chr(10).join(k + '=' + v for k, v in os.environ.items()))\""
)


@pytest.fixture
def internal_secrets(monkeypatch):
    for name, value in INTERNAL.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GH_TOKEN", "user-gh-token")
    monkeypatch.delenv(gaia_env.CHILD_ENV_DENY_ENV_VAR, raising=False)
    return INTERNAL


def _assert_scrubbed(env):
    leaked = sorted(set(INTERNAL) & set(env))
    assert not leaked, f"internal credentials reached the child: {leaked}"


# --------------------------------------------------------------------------
# child_env itself
# --------------------------------------------------------------------------


def test_internal_credentials_are_dropped_user_ones_kept(internal_secrets):
    env = gaia_env.child_env()

    _assert_scrubbed(env)
    assert env["GH_TOKEN"] == "user-gh-token"
    assert env["PATH"] == os.environ["PATH"]


def test_the_names_match_the_daemon_constants():
    from gaia.daemon.constants import BROKER_TOKEN_ENV_VAR, BROKER_TOKEN_FILE_ENV_VAR
    from gaia.daemon.custody.constants import CUSTODY_SECRET_ENV_VAR
    from gaia.daemon.sidecars import spec
    from gaia.engineering.handoff import ENGINEERING_TOKEN_ENV_VAR

    names = [
        ENGINEERING_TOKEN_ENV_VAR,
        BROKER_TOKEN_ENV_VAR,
        BROKER_TOKEN_FILE_ENV_VAR,
        CUSTODY_SECRET_ENV_VAR,
        spec._EMAIL_TOKEN_ENV_VAR,
        spec._EMAIL_TOKEN_FILE_ENV_VAR,
        spec._GAIA_TOKEN_ENV_VAR,
        spec._GAIA_TOKEN_FILE_ENV_VAR,
    ]
    assert all(gaia_env.is_internal_secret(name) for name in names), names


def test_os_environ_is_not_modified(internal_secrets):
    before = dict(os.environ)

    gaia_env.child_env({"EXTRA": "1"}, deny=["GH_TOKEN"])

    assert dict(os.environ) == before


def test_configured_deny_list_is_honored(internal_secrets, monkeypatch):
    monkeypatch.setenv("EMBEDDER_SECRET", "a")
    monkeypatch.setenv("OTHER_SECRET", "b")
    monkeypatch.setenv(
        gaia_env.CHILD_ENV_DENY_ENV_VAR, "EMBEDDER_SECRET, other_secret GH_TOKEN"
    )

    env = gaia_env.child_env()

    assert "EMBEDDER_SECRET" not in env
    assert "OTHER_SECRET" not in env
    assert "GH_TOKEN" not in env
    assert "PATH" in env


def test_deny_argument_and_extra_overlay(internal_secrets):
    env = gaia_env.child_env({"SERVER_KEY": "k"}, deny=["GH_TOKEN"])

    assert "GH_TOKEN" not in env
    assert env["SERVER_KEY"] == "k"


# --------------------------------------------------------------------------
# Shell steps
# --------------------------------------------------------------------------


class _FullAccessHost(ShellToolsMixin):
    """Full access so a Python one-liner that dumps the environment may run."""

    class console:
        full_access = True
        auto_approve_gated_tools = True


def _run(command, cwd):
    host = _FullAccessHost()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd), timeout=30
    )


def test_segment_env_is_scrubbed(internal_secrets):
    env = _segment_env({"FOO": "bar"})

    _assert_scrubbed(env)
    assert env["FOO"] == "bar"


@needs_python
@pytest.mark.parametrize("command", ["{}", "{} | sort", "FOO=bar {}"])
def test_shell_step_dumping_its_environment_shows_no_internal_token(
    command, internal_secrets, tmp_path
):
    """Plain, piped and env-assignment steps take separate spawn paths."""
    result = _run(command.format(DUMP_ENV), tmp_path)

    assert result["status"] == "success", result
    out = result["stdout"]
    assert "PATH=" in out, "the dump did not run; the test would be vacuous"
    for name, value in INTERNAL.items():
        assert name not in out and value not in out, name
    assert "GH_TOKEN=user-gh-token" in out


@needs_python
def test_shell_step_honors_the_deny_list(internal_secrets, monkeypatch, tmp_path):
    monkeypatch.setenv(gaia_env.CHILD_ENV_DENY_ENV_VAR, "GH_TOKEN")

    result = _run(DUMP_ENV, tmp_path)

    assert result["status"] == "success", result
    assert "PATH=" in result["stdout"]
    assert "user-gh-token" not in result["stdout"]


# --------------------------------------------------------------------------
# MCP stdio servers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("server_env", [None, {"SERVER_KEY": "k"}])
def test_mcp_stdio_child_env_lacks_internal_tokens(internal_secrets, server_env):
    """Both branches — with and without configured env — get a scrubbed copy."""
    process = Mock()
    process.poll.return_value = None
    with patch(
        "gaia.mcp.client.transports.stdio.subprocess.Popen", return_value=process
    ) as popen:
        StdioTransport(sys.executable, args=["-V"], env=server_env).connect()

    passed = popen.call_args.kwargs["env"]
    assert passed is not None, "None would inherit the full environment"
    _assert_scrubbed(passed)
    assert "PATH" in passed
    if server_env:
        assert passed["SERVER_KEY"] == "k"


def test_mcp_stdio_server_process_sees_no_internal_token(internal_secrets):
    """A real child: what the spawned program can read, not just what we passed."""
    # Waits on stdin so the transport's early-exit check sees a live server.
    probe = (
        "import os, sys; sys.stdin.readline(); "
        f"sys.stdout.write(','.join(n for n in {sorted(INTERNAL)!r} "
        "if n in os.environ) or 'clean')"
    )
    transport = StdioTransport(sys.executable, args=["-c", probe])
    assert transport.connect() is True
    try:
        out, _ = transport._process.communicate(input="\n", timeout=30)
    finally:
        transport.disconnect()
    assert out.strip() == "clean", out


def test_external_service_env_lacks_internal_tokens(internal_secrets):
    service = ExternalMCPService(command=["npx"], env={"PERPLEXITY_API_KEY": "p"})

    _assert_scrubbed(service.env)
    assert service.env["PERPLEXITY_API_KEY"] == "p"


# --------------------------------------------------------------------------
# GAIA_NO_DOTENV
# --------------------------------------------------------------------------


def _import_gaia_in(cwd, extra_env):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GAIA_NO_DOTENV", "FROM_DOTENV")
    }
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.update(extra_env)
    code = (
        "import os, gaia.cli, gaia.env; "
        "print(os.environ.get('FROM_DOTENV', '<unset>'), gaia.env.dotenv_disabled())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


@pytest.fixture
def dotenv_dir(tmp_path):
    (tmp_path / ".env").write_text("FROM_DOTENV=loaded\n")
    return tmp_path


def test_dotenv_loads_by_default(dotenv_dir):
    """The control: without the opt-out the cwd ``.env`` is picked up."""
    assert _import_gaia_in(dotenv_dir, {}) == "loaded False"


def test_gaia_no_dotenv_skips_dotenv(dotenv_dir):
    assert _import_gaia_in(dotenv_dir, {"GAIA_NO_DOTENV": "1"}) == "<unset> True"


def test_a_dotenv_file_cannot_set_gaia_no_dotenv(tmp_path):
    (tmp_path / ".env").write_text("GAIA_NO_DOTENV=1\nFROM_DOTENV=loaded\n")

    assert _import_gaia_in(tmp_path, {}) == "loaded False"


def test_a_dotenv_file_cannot_clear_gaia_no_dotenv(tmp_path):
    (tmp_path / ".env").write_text("GAIA_NO_DOTENV=0\nFROM_DOTENV=loaded\n")

    assert _import_gaia_in(tmp_path, {"GAIA_NO_DOTENV": "1"}) == "<unset> True"


def test_dotenv_disabled_reads_the_startup_snapshot(monkeypatch):
    monkeypatch.setenv("GAIA_NO_DOTENV", "1")
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, "GAIA_NO_DOTENV", raising=False)
    assert gaia_env.dotenv_disabled() is False

    monkeypatch.setitem(gaia._PRE_DOTENV_ENVIRON, "GAIA_NO_DOTENV", "true")
    assert gaia_env.dotenv_disabled() is True


# --------------------------------------------------------------------------
# Guard: one place loads .env
# --------------------------------------------------------------------------


def test_load_dotenv_is_only_called_from_gaia_env():
    roots = [REPO_ROOT / "src" / "gaia"] + sorted(
        (REPO_ROOT / "hub" / "agents").glob("*/python")
    )
    allowed = REPO_ROOT / "src" / "gaia" / "env.py"
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == allowed:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "load_dotenv(" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "Call gaia.env.load_env() instead of load_dotenv() so GAIA_NO_DOTENV "
        f"is honored: {offenders}"
    )
