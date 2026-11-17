# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An MCP connector changed in Settings reaches a chat that is already open.

An agent registers its MCP tools as it is built, so a connector turned off,
turned on, or (de)activated for GAIA must rebuild the chat's agent on its next
turn. A real flagship runs here with the repo's toy stdio MCP server; only the
model server is a stand-in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from gaia import config as config_mod
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.llm.lemonade_manager import LemonadeManager
from tests.fixtures.fake_lemonade import FakeLemonade

REPO = Path(__file__).resolve().parents[5]
TOY = REPO / "tests" / "fixtures" / "toy_mcp_server.py"
FLAGSHIP = "installed:gaia"
TOOL = "mcp_toy_echo"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A GAIA home with the toy server configured; ``home.set(...)`` edits it."""
    import gaia.connectors.activations as activations

    gaia_dir = tmp_path / "home"
    gaia_dir.mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    monkeypatch.setenv("GAIA_HOME", str(gaia_dir))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(gaia_dir / "memory.db"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", gaia_dir / "config.json")
    ledger = gaia_dir / "connectors" / "activations.json"
    monkeypatch.setattr(activations, "_activations_path", lambda: ledger)

    class Home:
        work = str(tmp_path / "work")

        def set(self, *, enabled: bool = True, servers=("toy",)) -> None:
            entries = {
                name: {
                    "command": sys.executable,
                    "args": [str(TOY), "--log", str(tmp_path / f"{name}.log")],
                    "disabled": not enabled,
                }
                for name in servers
            }
            (gaia_dir / "mcp_servers.json").write_text(
                json.dumps({"mcpServers": entries}), encoding="utf-8"
            )

    LemonadeManager.reset()
    with FakeLemonade(
        downloaded=(DEFAULT_MODEL_NAME, "user.embeddinggemma-300m-GGUF")
    ) as fake:
        monkeypatch.setenv("LEMONADE_BASE_URL", fake.base_url)
        yield Home()
    LemonadeManager.reset()


@pytest.fixture
def chat(home):
    """An open chat in the sidecar's session registry, with the toy server on."""
    from gaia_agent import session_registry as sr

    from gaia.connectors.activations import activate_agent

    home.set()
    activate_agent("toy", FLAGSHIP)
    registry = sr._SessionRegistry()
    kwargs = {"allowed_paths": [home.work]}

    def turn():
        """The chat's next message: take its turn and follow Settings."""
        session, took = registry.checkout("chat-1", **kwargs)
        assert took
        rebuilt = session.follow_settings(**kwargs)
        session.run_lock.release()
        return session.agent, rebuilt

    yield turn
    registry.clear()


def _mcp_tools(agent):
    return sorted(n for n in agent.get_tools_info() if n.startswith("mcp_"))


def test_a_connector_turned_off_leaves_an_open_chat(chat, home):
    first, _ = chat()
    assert _mcp_tools(first) == [TOOL]
    first.conversation_history = [{"role": "user", "content": "use echo"}]

    home.set(enabled=False)
    agent, rebuilt = chat()
    assert rebuilt and agent is not first
    assert _mcp_tools(agent) == []
    # The conversation carries over; the old agent's server process stops.
    assert agent.conversation_history == first.conversation_history
    assert first._mcp_manager.list_servers() == []


def test_a_connector_turned_on_reaches_an_open_chat(chat, home):
    home.set(enabled=False)
    first, _ = chat()
    assert _mcp_tools(first) == []
    home.set(enabled=True)
    agent, rebuilt = chat()
    assert rebuilt and _mcp_tools(agent) == [TOOL]


def test_deactivating_a_connector_for_gaia_leaves_an_open_chat(chat):
    from gaia.connectors.activations import deactivate_agent

    first, _ = chat()
    assert _mcp_tools(first) == [TOOL]
    deactivate_agent("toy", FLAGSHIP)
    agent, rebuilt = chat()
    assert rebuilt and _mcp_tools(agent) == []


def test_an_unchanged_setup_keeps_the_agent(chat):
    first, _ = chat()
    agent, rebuilt = chat()
    assert not rebuilt and agent is first


def test_a_server_not_active_for_gaia_offers_no_tools(home):
    """Every way of building the flagship honours activation, as Settings shows it."""
    from gaia_agent.session_registry import build_session_agent, close_agent

    home.set()  # enabled, never activated for GAIA
    agent = build_session_agent(allowed_paths=[home.work])
    try:
        assert agent._namespaced_agent_id() == FLAGSHIP
        assert _mcp_tools(agent) == []
    finally:
        close_agent(agent)


def test_a_new_agent_does_not_inherit_another_agents_mcp_tools(home):
    """MCP tools sit in a process-wide registry; an agent offers only its own."""
    from gaia_agent.session_registry import build_session_agent, close_agent

    from gaia.connectors.activations import activate_agent

    home.set()
    activate_agent("toy", FLAGSHIP)
    first = build_session_agent(allowed_paths=[home.work])
    home.set(enabled=False)
    second = build_session_agent(allowed_paths=[home.work])
    try:
        assert _mcp_tools(first) == [TOOL]
        assert _mcp_tools(second) == []
    finally:
        close_agent(second)
        close_agent(first)


def test_a_rebuilt_chat_keeps_what_the_user_set_in_it(chat, home, tmp_path):
    """An offline or private chat stays so; granted folders stay granted."""
    first, _ = chat()
    granted = tmp_path / "granted"
    granted.mkdir()
    first.internet_off = True
    first._incognito = True
    first._incognito_reason = "private"
    first.path_validator.add_allowed_path(str(granted))
    first.config.working_directory = str(granted)

    home.set(enabled=False)
    agent, rebuilt = chat()
    assert rebuilt and agent is not first
    assert agent.internet_off is True
    assert (agent._incognito, agent._incognito_reason) == (True, "private")
    assert agent.path_validator.is_path_allowed(str(granted), prompt_user=False)
    assert agent.config.working_directory == str(granted)
