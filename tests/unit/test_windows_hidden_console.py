# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Background processes GAIA starts must never open a console window on Windows.

A process started with ``DETACHED_PROCESS`` has no console, so every console
program it starts later gets a new, visible, blank one. Each spawn site
therefore passes ``CREATE_NO_WINDOW`` — a console of its own with no window,
which its children inherit — and never ``DETACHED_PROCESS``.
"""

import ast
import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.daemon.client import _spawn_and_wait as _REAL_DAEMON_SPAWN
from gaia.utils import terminal
from gaia.utils.terminal import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    background_popen_kwargs,
    windows_hidden_popen_kwargs,
)

DETACHED_PROCESS = 0x00000008
SRC = Path(__file__).resolve().parents[2] / "src" / "gaia"


class _Spawned(OSError):
    """Raised by the fake Popen so a spawn site stops right after the call."""


@pytest.fixture
def on_windows(monkeypatch):
    """Make the helpers answer as on Windows, whatever OS runs the test."""
    monkeypatch.setattr(terminal, "sys", SimpleNamespace(platform="win32"))


@pytest.fixture
def spawned(monkeypatch):
    """Capture the kwargs of the next Popen/run call, then abort it."""
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        raise _Spawned("spawn captured")

    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(subprocess, "run", fake)
    return calls


def _assert_hidden(kwargs, *, group: bool):
    flags = kwargs.get("creationflags", 0)
    assert flags & CREATE_NO_WINDOW, f"no CREATE_NO_WINDOW in {kwargs}"
    assert not flags & DETACHED_PROCESS, "DETACHED_PROCESS gives children windows"
    assert bool(flags & CREATE_NEW_PROCESS_GROUP) is group
    assert "start_new_session" not in kwargs


# -- the helpers -------------------------------------------------------------


def test_hidden_kwargs_on_windows(on_windows):
    assert windows_hidden_popen_kwargs() == {"creationflags": CREATE_NO_WINDOW}


def test_background_kwargs_on_windows_never_detach(on_windows):
    kwargs = background_popen_kwargs()
    assert kwargs == {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    _assert_hidden(kwargs, group=True)


def test_helpers_off_windows(monkeypatch):
    monkeypatch.setattr(terminal, "sys", SimpleNamespace(platform="linux"))
    assert windows_hidden_popen_kwargs() == {}
    assert background_popen_kwargs() == {"start_new_session": True}


@pytest.mark.skipif(
    not hasattr(subprocess, "CREATE_NO_WINDOW"), reason="Windows-only constants"
)
def test_literal_flags_match_the_windows_constants():
    assert CREATE_NO_WINDOW == subprocess.CREATE_NO_WINDOW
    assert CREATE_NEW_PROCESS_GROUP == subprocess.CREATE_NEW_PROCESS_GROUP


# -- long-running servers GAIA owns ------------------------------------------


def test_embedded_lemonade_gets_a_hidden_console(on_windows, spawned, tmp_path):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    emb = EmbeddedLemonade(home=tmp_path)
    emb.status = lambda: SimpleNamespace(running=False, unresponsive_pid=None)
    emb.is_installed = lambda: True
    emb.write_config = lambda: None

    with pytest.raises(_Spawned):
        emb.start(install_if_missing=False)
    _assert_hidden(spawned[0], group=True)


def test_daemon_gets_a_hidden_console(on_windows, spawned, tmp_path, monkeypatch):
    from gaia.daemon.errors import DaemonStartError

    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path))
    # The real function: conftest stubs it out, and Popen is faked here.
    with pytest.raises(DaemonStartError):
        _REAL_DAEMON_SPAWN(timeout=1)
    _assert_hidden(spawned[0], group=True)


def test_sidecar_gets_a_hidden_console(on_windows, spawned, tmp_path, monkeypatch):
    from gaia.daemon.sidecars import manager as mgr
    from gaia.daemon.sidecars.errors import SidecarSpawnError
    from gaia.daemon.sidecars.spec import builtin_specs

    # Dev mode delivers the secret by file, so no installed binary is needed.
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.AgentSidecarManager(
        builtin_specs()["email"], cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    with pytest.raises(SidecarSpawnError):
        m._spawn_process(["sidecar"], {}, 9127)
    _assert_hidden(spawned[0], group=True)


def test_supervised_lemonade_gets_a_hidden_console(on_windows, spawned, tmp_path):
    from gaia.llm.lemonade_supervisor import LemonadeSupervisor

    supervisor = LemonadeSupervisor(log_dir=tmp_path)
    with pytest.raises(_Spawned):
        supervisor._spawn(SimpleNamespace(argv=["lemond"], env={}))
    _assert_hidden(spawned[0], group=True)


# -- one-shot and stdio children ---------------------------------------------


def test_mcp_stdio_server_gets_a_hidden_console(on_windows, spawned):
    from gaia.mcp.client.transports.stdio import StdioTransport

    assert StdioTransport(command="npx", args=["-y", "server"]).connect() is False
    _assert_hidden(spawned[0], group=False)


def test_native_agent_gets_a_hidden_console(on_windows, spawned, tmp_path):
    from gaia.hub.native_launcher import NativeAgentError, NativeAgentLauncher

    binary = tmp_path / "agent.exe"
    binary.touch()
    with pytest.raises(NativeAgentError):
        NativeAgentLauncher().start(tmp_path, binary.name, handshake=False)
    _assert_hidden(spawned[0], group=False)


def test_setup_check_gets_a_hidden_console(on_windows, spawned):
    from gaia.ui import setup_runner

    with pytest.raises(_Spawned):
        setup_runner.check()
    _assert_hidden(spawned[0], group=False)


def test_setup_run_gets_a_hidden_console(on_windows, spawned):
    from gaia.ui.setup_runner import SetupError, SetupRunner

    with pytest.raises(SetupError):
        SetupRunner().start(skip_chat_model=True)
    _assert_hidden(spawned[0], group=False)


def test_agent_ui_mcp_server_gets_a_hidden_console(on_windows, spawned, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    from gaia.ui.routers import mcp as router

    async def port_free(_port):
        return False

    monkeypatch.setattr(router, "_probe_mcp_port", port_free)
    monkeypatch.setattr(router, "_agent_mcp_process", None)
    with pytest.raises(HTTPException):
        asyncio.run(router.start_agent_mcp_server())
    _assert_hidden(spawned[0], group=False)


# -- every spawn in the background paths carries a helper --------------------

_HELPERS = {"windows_hidden_popen_kwargs", "background_popen_kwargs"}
_SPAWNS = {"Popen", "run", "check_output", "call", "check_call", "popen_outliving_job"}

# Modules whose spawns can run under a console-less parent (the daemon, the
# Agent UI backend, a sidecar). A spawn meant to be seen opts out explicitly.
_GUARDED = [
    "daemon/client.py",
    "daemon/sidecars/manager.py",
    "hub/native_launcher.py",
    "llm/lemonade_client.py",
    "llm/lemonade_embedded.py",
    "llm/lemonade_supervisor.py",
    "mcp/client/transports/stdio.py",
    "ui/routers/mcp.py",
    "ui/setup_runner.py",
    "ui/tunnel.py",
]


def _helper_names(func: ast.AST) -> set:
    """Local names bound to a helper call inside *func*."""
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            callee = node.value.func
            if isinstance(callee, ast.Name) and callee.id in _HELPERS:
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


_POSIX_ONLY = {"ps", "pkill"}


def _carries_helper(call: ast.Call, local_names: set) -> bool:
    argv = call.args[0] if call.args else None
    if isinstance(argv, ast.List) and argv.elts:
        first = argv.elts[0]
        if isinstance(first, ast.Constant) and first.value in _POSIX_ONLY:
            return True
    for kw in call.keywords:
        if kw.arg is None:  # **mapping
            value = kw.value
            if (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", None) in _HELPERS
            ):
                return True
            if isinstance(value, ast.Name) and value.id in local_names:
                return True
        if kw.arg == "creationflags" and "CREATE_NEW_CONSOLE" in ast.unparse(kw.value):
            return True  # a console the user asked to see
    return False


@pytest.mark.parametrize("rel", _GUARDED)
def test_every_spawn_hides_its_console(rel):
    tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
    missing = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_names = _helper_names(func)
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _SPAWNS
                and getattr(node.func.value, "id", None) == "subprocess"
                and not _carries_helper(node, local_names)
            ):
                missing.append(f"{rel}:{node.lineno}")
    assert not missing, (
        "spawn without windows_hidden_popen_kwargs()/background_popen_kwargs() — "
        f"it opens a blank console window on Windows: {sorted(set(missing))}"
    )
