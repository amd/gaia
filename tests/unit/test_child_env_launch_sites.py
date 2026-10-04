# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every program GAIA starts for skills and tools gets the trimmed environment.

Each test drives one launch site with the process launch mocked, then checks
the ``env`` it was handed: GAIA's internal credentials are gone, and ordinary
user variables (``PATH``, ``GH_TOKEN``) are still there.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia import env as gaia_env

# Captured at import, before the unit conftest swaps it for a refusal: the test
# below mocks Popen, so no daemon is ever started.
from gaia.daemon.client import _spawn_and_wait as _real_spawn_and_wait
from gaia.daemon.custody.constants import CUSTODY_URL_ENV_VAR

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


@pytest.fixture(autouse=True)
def internal_secrets(monkeypatch):
    for name, value in INTERNAL.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GH_TOKEN", "user-gh-token")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv(gaia_env.CHILD_ENV_DENY_ENV_VAR, raising=False)


def assert_trimmed(env):
    assert env is not None, "launch inherited the full environment (env=None)"
    leaked = sorted(set(INTERNAL) & set(env))
    assert not leaked, f"internal credentials reached the child: {leaked}"
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["GH_TOKEN"] == "user-gh-token"


class Recorder:
    """Stands in for ``subprocess.run`` / ``Popen``; records each call's kwargs."""

    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result
        self._raises = raises

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self._raises is not None:
            raise self._raises
        return self._result

    @property
    def env(self):
        assert self.calls, "the launch was never reached"
        return self.calls[-1][1].get("env")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# ---------------------------------------------------------------------------
# Skill CLI setup (cli_setup mixin → gaia.skills.binary_setup)
# ---------------------------------------------------------------------------


def test_cli_setup_install_and_status_runs_are_trimmed(monkeypatch):
    from gaia.skills import binary_setup

    run = Recorder(result=_completed())
    monkeypatch.setattr(binary_setup.subprocess, "run", run)

    binary_setup._run(["gh", "auth", "status"], timeout=1)

    assert_trimmed(run.env)


def test_cli_setup_sign_in_is_trimmed_and_drops_browser_hooks(monkeypatch):
    from gaia.skills import binary_setup
    from gaia.skills.binaries import BINARY_POLICIES

    monkeypatch.setenv("BROWSER", "calc.exe")
    monkeypatch.setenv("GH_BROWSER", "calc.exe")
    popen = Recorder(raises=OSError("stop here"))

    with pytest.raises(binary_setup.SetupError):
        binary_setup.start_device_login(BINARY_POLICIES["gh"], popen=popen)

    assert_trimmed(popen.env)
    assert "BROWSER" not in popen.env
    assert "GH_BROWSER" not in popen.env


# ---------------------------------------------------------------------------
# Native (C++) hub agents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [None, {"AGENT_MODE": "test"}])
def test_native_agent_launch_is_trimmed(monkeypatch, tmp_path, extra):
    from gaia.hub import native_launcher

    binary = tmp_path / "agent-bin"
    binary.write_text("", encoding="utf-8")
    binary.chmod(0o755)
    popen = Recorder(raises=OSError("stop here"))
    monkeypatch.setattr(native_launcher.subprocess, "Popen", popen)

    with pytest.raises(native_launcher.NativeAgentError):
        native_launcher.NativeAgentLauncher().start(tmp_path, str(binary), env=extra)

    assert_trimmed(popen.env)
    if extra:
        assert popen.env["AGENT_MODE"] == "test"


# ---------------------------------------------------------------------------
# Audio: ffmpeg / ffprobe / installers
# ---------------------------------------------------------------------------


def test_ffmpeg_decode_is_trimmed(monkeypatch):
    from gaia.audio import media

    run = Recorder(result=_completed())
    monkeypatch.setattr(media.subprocess, "run", run)

    media._run_ffmpeg(["ffmpeg", "-i", "in.mp3"], Path("in.mp3"))

    assert_trimmed(run.env)


def test_ffmpeg_decode_with_progress_is_trimmed(monkeypatch):
    from gaia.audio import media

    popen = Recorder(raises=OSError("stop here"))
    monkeypatch.setattr(media.subprocess, "Popen", popen)

    with pytest.raises(media.MediaError):
        media._run_ffmpeg_with_progress(
            ["ffmpeg", "-i", "in.mp3"], Path("in.mp3"), 1.0, lambda _f: None
        )

    assert_trimmed(popen.env)


def test_ffprobe_is_trimmed(monkeypatch, tmp_path):
    from gaia.audio import media

    src = tmp_path / "clip.mp3"
    src.write_bytes(b"")
    run = Recorder(result=_completed(stdout="1.5\n"))
    monkeypatch.setattr(media, "_ensure_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr(media.subprocess, "run", run)

    media.probe_duration(src)

    assert_trimmed(run.env)


def test_ffmpeg_install_is_trimmed(monkeypatch):
    from gaia.audio import media

    run = Recorder(result=_completed())
    monkeypatch.setattr(media.platform, "system", lambda: "Linux")
    monkeypatch.setitem(
        media._INSTALL_COMMANDS, "Linux", (("apt-get", ("apt-get", "install")),)
    )
    monkeypatch.setattr(media, "_which", lambda _tool: "/usr/bin/apt-get")
    monkeypatch.setattr(media.subprocess, "run", run)

    media._install_ffmpeg()

    assert_trimmed(run.env)


def test_diarization_engine_install_is_trimmed(monkeypatch):
    from gaia.audio import diarize

    run = Recorder(raises=subprocess.CalledProcessError(1, ["pip"]))
    monkeypatch.setattr(diarize, "_package_installed", lambda: False)
    monkeypatch.delattr(diarize.sys, "frozen", raising=False)
    monkeypatch.setattr(diarize.subprocess, "run", run)

    with pytest.raises(diarize.DiarizationError):
        diarize._ensure_package(lambda _msg: None)

    assert_trimmed(run.env)


# ---------------------------------------------------------------------------
# File-edit impact scan, PPTX conversion, engineering handoff
# ---------------------------------------------------------------------------


def test_edit_impact_git_grep_is_trimmed(monkeypatch, tmp_path):
    from gaia.agents.tools import edit_impact

    run = Recorder(result=_completed(returncode=1))
    monkeypatch.setattr(edit_impact.subprocess, "run", run)

    edit_impact._git_grep(tmp_path, "needle")

    assert_trimmed(run.env)


def test_pptx_conversion_is_trimmed_and_keeps_its_paths(monkeypatch, tmp_path):
    from gaia.rag import pptx_utils

    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"")
    run = Recorder(result=_completed(returncode=1))
    monkeypatch.setattr(pptx_utils.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pptx_utils.subprocess, "run", run)

    assert pptx_utils.convert_pptx_to_pdf(str(deck), str(tmp_path)) is None

    assert_trimmed(run.env)
    assert run.env[pptx_utils.PPTX_IN_ENV_VAR] == str(deck.resolve())


def test_engineering_app_handoff_is_trimmed(monkeypatch, tmp_path):
    from gaia.engineering import handoff

    run = Recorder(result=_completed())
    monkeypatch.setattr(handoff.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        handoff, "detect_apps", lambda: {"claude": {"app": "/Applications/X.app"}}
    )
    monkeypatch.setattr(handoff.subprocess, "run", run)

    handoff.open_app("claude", "a" * 32, tmp_path)

    assert_trimmed(run.env)


def test_engineering_pairing_token_reaches_the_coding_app_explicitly(tmp_path):
    """The coding app starts the engineering MCP server from this config, so the
    pairing token rides in its ``env`` block rather than through inheritance —
    and the StdioTransport that would launch it overlays that block on
    ``child_env``, so an explicit value still arrives."""
    from gaia.engineering import handoff

    config = handoff.connection_config("claude", "python", tmp_path, "tok-pair")
    server_env = config["mcpServers"]["gaia-engineering"]["env"]

    assert server_env[handoff.ENGINEERING_TOKEN_ENV_VAR] == "tok-pair"
    child = gaia_env.child_env(server_env)
    assert child[handoff.ENGINEERING_TOKEN_ENV_VAR] == "tok-pair"
    assert handoff.ENGINEERING_TOKEN_ENV_VAR not in gaia_env.child_env()


def test_engineering_repository_git_is_trimmed(monkeypatch, tmp_path):
    from gaia.engineering import repository

    run = Recorder(result=_completed())
    monkeypatch.setattr(repository.subprocess, "run", run)

    repository.Repository(tmp_path / "cache").git("status")

    assert_trimmed(run.env)
    assert run.env["GIT_TERMINAL_PROMPT"] == "0"


# ---------------------------------------------------------------------------
# Lemonade server
# ---------------------------------------------------------------------------


def test_daemon_supervised_lemonade_is_trimmed(monkeypatch, tmp_path):
    from gaia.llm import lemonade_supervisor
    from gaia.llm.lemonade_launcher import StartSpec

    popen = Recorder(result=SimpleNamespace(pid=1))
    monkeypatch.setattr(lemonade_supervisor.subprocess, "Popen", popen)
    sup = lemonade_supervisor.LemonadeSupervisor(log_dir=tmp_path)

    try:
        sup._spawn(StartSpec(argv=["lemond"], env={"LEMONADE_CTX_SIZE": "65536"}))
    finally:
        sup._close_log()

    assert_trimmed(popen.env)
    assert popen.env["LEMONADE_CTX_SIZE"] == "65536"


def test_client_launched_lemonade_is_trimmed(monkeypatch):
    from gaia.llm import lemonade_client
    from gaia.llm.lemonade_launcher import LemonadeTooling, StartSpec

    client = lemonade_client.LemonadeClient(host="localhost", port=1, verbose=False)
    popen = Recorder(raises=OSError("stop here"))
    monkeypatch.setattr(client, "health_check", lambda: None)
    monkeypatch.setattr(client, "_classify_port_listeners", lambda: ([], []))
    monkeypatch.setattr(
        lemonade_client,
        "resolve_lemonade",
        lambda: LemonadeTooling(found=True, kind="modern", client_path="lemonade"),
    )
    monkeypatch.setattr(
        lemonade_client,
        "build_start_command",
        lambda _t, _c: StartSpec(argv=["lemond"], env={"LEMONADE_CTX_SIZE": "1"}),
    )
    monkeypatch.setattr(lemonade_client.subprocess, "Popen", popen)

    with pytest.raises(OSError, match="stop here"):
        client.launch_server(background="terminal")

    assert_trimmed(popen.env)
    assert popen.env["LEMONADE_CTX_SIZE"] == "1"


def test_embedded_lemonade_start_is_trimmed(monkeypatch, tmp_path):
    from gaia.llm import lemonade_embedded

    manager = lemonade_embedded.EmbeddedLemonade(home=tmp_path)
    popen = Recorder(result=SimpleNamespace(pid=1, poll=lambda: None))
    monkeypatch.setattr(manager, "is_installed", lambda: True)
    monkeypatch.setattr(manager, "write_config", lambda: None)
    monkeypatch.setattr(manager, "_health", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(lemonade_embedded.subprocess, "Popen", popen)

    manager.start(port=65530)

    assert_trimmed(popen.env)
    assert popen.env["LEMONADE_API_KEY"]


def test_embedded_lemonade_backend_install_is_trimmed(monkeypatch, tmp_path):
    from gaia.llm import lemonade_embedded

    manager = lemonade_embedded.EmbeddedLemonade(home=tmp_path)
    run = Recorder(result=_completed())
    monkeypatch.setattr(
        manager, "status", lambda: SimpleNamespace(running=True, port=65530)
    )
    monkeypatch.setattr(manager, "current_api_key", lambda: "k")
    monkeypatch.setattr(lemonade_embedded.subprocess, "run", run)

    manager.install_backend("llamacpp:cpu")

    assert_trimmed(run.env)
    assert run.env["LEMONADE_API_KEY"] == "k"


# ---------------------------------------------------------------------------
# GAIA's own processes
# ---------------------------------------------------------------------------


def test_daemon_spawn_drops_internal_credentials_but_keeps_the_deny_listed(
    monkeypatch,
):
    """The daemon mints its own credentials, so a sidecar's must not reach it —
    but it is GAIA, so ``GAIA_CHILD_ENV_DENY`` (meant for tool children) does
    not apply."""
    from gaia.daemon import client
    from gaia.daemon.errors import DaemonStartError

    monkeypatch.setenv("APP_SECRET", "kept-for-gaia")
    monkeypatch.setenv(gaia_env.CHILD_ENV_DENY_ENV_VAR, "APP_SECRET")
    monkeypatch.setenv(CUSTODY_URL_ENV_VAR, "http://127.0.0.1:1")
    popen = Recorder(raises=OSError("stop here"))
    monkeypatch.setattr(client.subprocess, "Popen", popen)

    with pytest.raises(DaemonStartError):
        _real_spawn_and_wait(timeout=0.1)

    assert_trimmed(popen.env)
    assert popen.env["APP_SECRET"] == "kept-for-gaia"
    # The daemon serves its own custody endpoint; a stale URL here would reach
    # its sidecars without the matching secret, and they refuse to start.
    assert CUSTODY_URL_ENV_VAR not in popen.env
