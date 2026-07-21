# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AC-6 (issue #2358): the clean/core-only `init -> chat` integration test.

Proves the wheel-install -> cross-process-import MECHANISM end-to-end using
REAL subprocesses, from a `$HOME`-redirected temp dir standing in for "the
real user's initial state" (a plain `pip install amd-gaia`, no hub-agent
wheel editable-installed anywhere) -- see CLAUDE.md's "test from the user's
real initial state" rule and the #1655 postmortem it's named for.

Journey:

1. Install a REAL, self-contained fixture wheel (built by
   ``tests/fixtures/wheel_builder.py``) via ``gaia.hub.installer.install()``
   -- real ``run_pip`` subprocess, no mocks -- standing in for what the Hub
   R2 channel would serve for `chat` once it's published. It does NOT need
   to be the literal, production ``gaia_agent_chat`` wheel from
   ``hub/agents/chat/python`` -- but it DOES need to be importable as the
   literal ``gaia_agent_chat`` module, because `gaia chat`'s CLI handler
   hardcodes ``from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig``
   (``src/gaia/cli.py:650``) rather than resolving through
   ``AgentRegistry`` -- unlike `gaia browse`/`gaia analyze`, which DO go
   through the registry (``cli.py:832-855``). This is a real, load-bearing
   finding for whoever implements the fix: making
   ``AgentRegistry.discover()`` aware of hub installs (the crux fix in
   ``tests/unit/test_registry_installed_import.py``) is necessary but NOT
   sufficient to make `gaia chat` work -- something must also make
   `gaia_agent_chat` importable before that hardcoded import line runs in a
   fresh process.
2. Run `gaia init --profile chat --skip-models --yes --remote` as a REAL
   subprocess, against a tiny local fake HTTP `/health` responder (so the
   test needs neither a real Lemonade Server nor network access) -- proves
   `init` completes cleanly in this environment without disturbing the
   pre-installed fixture.
3. Run `gaia chat -q "hello"` as a REAL subprocess, same redirected `$HOME`.
   Asserts it does NOT show the CURRENT (broken) "chat agent is not
   installed" / ModuleNotFoundError signature. It also asserts the process
   got all the way to the agent's real logic (our fixture's deliberate stub
   marker) -- not just "didn't crash on import" -- which is a stronger
   proof than an absence check alone. Reaching an unrelated, expected
   downstream failure (no real LLM backend is running) is fine and is NOT
   the crux bug; only the "not installed" signature is.

This test is expected to be SLOW/heavy (real subprocess pip installs, a real
`gaia` subprocess) -- marked ``@pytest.mark.integration`` per this repo's
slow-test convention (see ``tests/integration/*`` and
``tests/conftest.py``/``pyproject.toml`` markers) so it stays out of the
default fast unit run.

MUST currently fail: no wiring installs `chat` during `gaia init`, and (more
fundamentally) nothing makes a hub-installed wheel importable in the fresh
`gaia chat` subprocess -- see the assertion's failure message for exactly
which signature shows up today.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager

import pytest

from gaia.hub import installer as hub_installer
from tests.fixtures.wheel_builder import (
    build_fixture_wheel_bytes,
    build_wheel_fetcher,
    build_wheel_manifest,
)

BASE_URL = "https://hub.test"
AGENT_ID = "chat"

# The crux bug's exact failure signature on unfixed `main` (verified by
# running `gaia chat -q hello` against a fresh $HOME with nothing installed;
# see cli.py:650-659 / gaia.agents.install_hints.agent_not_installed_message).
_CRUX_NOT_INSTALLED_SIGNATURE = "chat agent is not installed"

# Our fixture ChatAgent deliberately raises this from process_query() so the
# test can positively confirm execution reached real agent logic, not just
# "didn't raise ImportError".
_FIXTURE_STUB_MARKER = "GAIA_TEST_FIXTURE_CHAT_STUB_REACHED"

_FIXTURE_AGENT_MODULE_SOURCE = f'''\
"""Fixture stand-in for gaia_agent_chat.agent (#2358 AC-6 mechanism proof).

Provides just enough of the real gaia_agent_chat.agent surface (ChatAgent,
ChatAgentConfig) for `gaia chat`'s hardcoded
`from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig` (cli.py:650)
to succeed, plus a `build_registration()` entry point for AgentRegistry-based
discovery. Deliberately NOT a faithful re-implementation -- the point is
proving the wheel-install -> cross-process-import mechanism, not chat
functionality.
"""

from gaia.agents.registry import AgentRegistration

FIXTURE_STUB_MARKER = {_FIXTURE_STUB_MARKER!r}


class ChatAgentConfig:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _DummySession:
    session_id = "fixture-session"


class _DummySessionManager:
    def create_session(self):
        return _DummySession()


class ChatAgent:
    def __init__(self, config):
        self.config = config
        self.current_session = None
        self.session_manager = _DummySessionManager()

    def process_query(self, query, trace=False):
        # Deliberately fails PAST the import/construction gate -- proves the
        # cross-process import + construction worked, distinct from the
        # crux "chat agent is not installed" signature.
        raise RuntimeError(FIXTURE_STUB_MARKER)

    def stop_watching(self):
        pass


def _factory(**kwargs):
    return ChatAgent(ChatAgentConfig(**kwargs))


def build_registration():
    return AgentRegistration(
        id={AGENT_ID!r},
        name="Fixture Chat Agent",
        description="AC-6 mechanism-proof fixture (#2358)",
        source="installed",
        conversation_starters=[],
        factory=_factory,
        agent_dir=None,
        models=[],
    )
'''

_FIXTURE_APP_MODULE_SOURCE = """\
def interactive_mode(agent):
    raise RuntimeError("fixture stub: interactive_mode not exercised by -q tests")
"""


def _build_chat_fixture_wheel() -> bytes:
    """A wheel importable as the literal ``gaia_agent_chat`` module."""
    return build_fixture_wheel_bytes(
        dist_name="gaia-agent-chat",
        version="0.1.0",
        module_name="gaia_agent_chat",
        entry_point_group="gaia.agent",
        entry_point_name=AGENT_ID,
        entry_point_target="gaia_agent_chat.agent:build_registration",
        module_source=_FIXTURE_AGENT_MODULE_SOURCE,
        extra_modules={"app.py": _FIXTURE_APP_MODULE_SOURCE},
    )


def _real_pip_run_pip(python_exe):
    def run_pip(args):
        subprocess.run(
            [python_exe, "-m", "pip", "install", *args],
            check=True,
            capture_output=True,
            text=True,
        )

    return run_pip


# `gaia init`'s final "Verifying setup..." step (init_command.py:1696,
# _verify_setup) unconditionally calls `LemonadeManager.ensure_ready()` for
# the profile's min_context_size, even with --skip-models. ensure_ready()
# reads `client.get_status()`, which is built entirely from this `/health`
# payload's `all_models_loaded[].{type,recipe_options.ctx_size}`
# (lemonade_client.py:3777-3801). Reporting an LLM model already loaded with
# ctx_size >= the chat profile's min_context_size (32768) makes
# ensure_ready() take its "already sufficient" fast path and return True
# WITHOUT ever issuing the real model-load POST -- which this fake server
# deliberately does not implement (avoids re-implementing Lemonade's load
# API just to satisfy a health probe).
_FAKE_HEALTH_BODY = json.dumps(
    {
        "status": "ok",
        "version": "10.9.0",
        "all_models_loaded": [
            {
                "model_name": "Gemma-4-E4B-it-GGUF",
                "type": "llm",
                "recipe_options": {"ctx_size": 32768},
            }
        ],
    }
).encode("utf-8")


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Answers exactly the one endpoint `gaia init`'s remote-mode server
    check needs (`GET {base_url}/health`) -- avoids any dependency on a real
    Lemonade Server or the network for the `gaia init` step."""

    def do_GET(self):  # noqa: N802 - stdlib method name
        if self.path == "/api/v1/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_FAKE_HEALTH_BODY)))
            self.end_headers()
            self.wfile.write(_FAKE_HEALTH_BODY)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 - silence stderr spam
        pass


@contextmanager
def _fake_lemonade_health_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/api/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _gaia_executable() -> str:
    """Resolve the `gaia` console script next to the active interpreter --
    the same venv `.venv-wt/bin/python` runs from has `.venv-wt/bin/gaia`,
    guaranteed present for any editable/real install (unlike relying on
    inherited shell PATH, which pytest's own invocation may not carry)."""
    from pathlib import Path

    suffix = ".exe" if sys.platform == "win32" else ""
    return str(Path(sys.executable).parent / f"gaia{suffix}")


def _minimal_path_env() -> str:
    """A PATH with `node`/`npm` deliberately excluded.

    `gaia init` unconditionally attempts to rebuild the Agent UI frontend in
    a dev/source checkout (`_is_dev_install`, init_command.py:507) when
    `apps/webui/dist` doesn't exist yet -- true for a fresh worktree. If
    node/npm ARE on PATH, `ensure_webui_built` (gaia/ui/build.py) will
    happily kick off a REAL `npm install && npm run build`, which is slow,
    unrelated to this test, and not hermetic. Stripping node/npm from PATH
    makes that gate no-op cleanly (it already handles "node not found" as a
    graceful non-failure, gaia/ui/build.py:76-80) -- so keep the venv's own
    bin dir (for `gaia`/`python`) plus basic system dirs, nothing else.
    """
    from pathlib import Path

    venv_bin = str(Path(sys.executable).parent)
    if sys.platform == "win32":
        return venv_bin
    return os.pathsep.join([venv_bin, "/usr/bin", "/bin", "/usr/sbin", "/sbin"])


@pytest.mark.integration
def test_clean_core_only_init_then_chat_journey(tmp_path):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    install_root = tmp_home / ".gaia" / "agents"

    # --- 1. Install a REAL fixture wheel, standing in for chat (#2358) ---
    wheel_bytes = _build_chat_fixture_wheel()
    manifest, artifact_path = build_wheel_manifest(
        AGENT_ID, "0.1.0", wheel_bytes, dist_name="gaia-agent-chat"
    )
    fetcher = build_wheel_fetcher(BASE_URL, artifact_path, wheel_bytes, AGENT_ID)

    result = hub_installer.install(
        AGENT_ID,
        manifest=manifest,
        base_url=BASE_URL,
        fetcher=fetcher,
        run_pip=_real_pip_run_pip(sys.executable),
        install_root=install_root,
    )
    site_packages = install_root / AGENT_ID / "site-packages"
    assert (site_packages / "gaia_agent_chat" / "agent.py").exists(), (
        "fixture wheel did not actually install -- test setup is broken, "
        "not the crux bug"
    )
    assert (
        result.hot_registered is False
    )  # no registry= passed -- fresh-process proof only

    gaia_exe = _gaia_executable()
    base_env = {**os.environ, "HOME": str(tmp_home), "PATH": _minimal_path_env()}

    # --- 2. `gaia init --profile chat` as a REAL subprocess ---
    with _fake_lemonade_health_server() as lemonade_base_url:
        init_env = {**base_env, "LEMONADE_BASE_URL": lemonade_base_url}
        init_proc = subprocess.run(
            [
                gaia_exe,
                "init",
                "--profile",
                "chat",
                "--skip-models",
                "--yes",
                "--remote",
            ],
            env=init_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert init_proc.returncode == 0, (
        f"`gaia init --profile chat` failed unexpectedly.\n"
        f"stdout={init_proc.stdout}\nstderr={init_proc.stderr}"
    )
    # The pre-installed fixture must still be there -- init must not clobber it.
    assert (site_packages / "gaia_agent_chat" / "agent.py").exists()

    # --- 3. `gaia chat -q "hello"` as a REAL subprocess ---
    # `--no-lemonade-check` skips the CLI's own pre-flight
    # `initialize_lemonade_for_agent()` gate (cli.py:583-596, which would
    # otherwise hard-exit(1) with "Lemonade server is not running" before
    # ever reaching the `elif action == "chat":` handler this test cares
    # about). LEMONADE_BASE_URL still points at a guaranteed-closed port so
    # the handler's OWN device probe (cli.py:690-731) hits its documented
    # "connection refused -> soft pass" path deterministically, without
    # needing a second fake server.
    chat_env = {**base_env, "LEMONADE_BASE_URL": "http://127.0.0.1:1/api/v1"}
    chat_proc = subprocess.run(
        [gaia_exe, "chat", "-q", "hello", "--no-lemonade-check"],
        env=chat_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined_output = chat_proc.stdout + chat_proc.stderr

    assert _CRUX_NOT_INSTALLED_SIGNATURE not in combined_output, (
        "`gaia chat -q hello` still shows the #2358 crux bug's exact failure "
        "signature ('chat agent is not installed') even though a chat-shaped "
        "wheel WAS installed under ~/.gaia/agents/chat/ -- nothing made the "
        "hub-installed gaia_agent_chat importable in this fresh subprocess.\n"
        f"stdout={chat_proc.stdout}\nstderr={chat_proc.stderr}"
    )
    assert "ModuleNotFoundError" not in combined_output, (
        f"`gaia chat -q hello` raised ModuleNotFoundError -- the crux bug is "
        f"still present.\nstdout={chat_proc.stdout}\nstderr={chat_proc.stderr}"
    )
    # Positive proof, not just an absence check: execution must have reached
    # our fixture ChatAgent's real logic (process_query), which is only
    # possible if `from gaia_agent_chat.agent import ChatAgent,
    # ChatAgentConfig` (cli.py:650) actually succeeded in this fresh process.
    assert _FIXTURE_STUB_MARKER in combined_output, (
        "`gaia chat -q hello` did not reach the fixture agent's process_query "
        "-- it neither hit the crux 'not installed' signature nor our stub "
        "marker, so something else entirely went wrong before proving (or "
        f"disproving) the crux mechanism.\nstdout={chat_proc.stdout}\n"
        f"stderr={chat_proc.stderr}"
    )
