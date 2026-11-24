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
    from gaiapy import session_registry as sr

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
    from gaiapy.session_registry import build_session_agent, close_agent

    home.set()  # enabled, never activated for GAIA
    agent = build_session_agent(allowed_paths=[home.work])
    try:
        assert agent._namespaced_agent_id() == FLAGSHIP
        assert _mcp_tools(agent) == []
    finally:
        close_agent(agent)


def test_a_new_agent_does_not_inherit_another_agents_mcp_tools(home):
    """MCP tools sit in a process-wide registry; an agent offers only its own."""
    from gaiapy.session_registry import build_session_agent, close_agent

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


def test_a_document_taken_off_after_a_rebuild_is_refused(chat, home, tmp_path):
    """The rebuilt agent knows which documents were attached, so a removed one
    loses its grant on the turn it is removed."""
    from gaiapy import server

    first, _ = chat()
    doc = tmp_path / "notes.txt"
    doc.write_text("The pump logs 42 litres.", encoding="utf-8")
    first.path_validator.add_allowed_path(str(doc))
    first._attached_documents = {str(doc)}

    home.set(enabled=False)
    agent, rebuilt = chat()
    assert rebuilt and agent._attached_documents == {str(doc)}
    assert agent.path_validator.is_path_allowed(str(doc), prompt_user=False)

    server._drop_detached_documents(agent, [])  # next turn, sent without it
    assert not agent.path_validator.is_path_allowed(str(doc), prompt_user=False)


def test_a_withheld_path_stays_refused_after_a_rebuild(chat, home, tmp_path):
    """A file withheld inside a folder that stays granted, as GAIA's own copy of
    a removed document sits in the Agent UI's granted documents folder."""
    first, _ = chat()
    folder = tmp_path / "documents"
    folder.mkdir()
    removed = folder / "old.pdf"
    removed.write_text("x", encoding="utf-8")
    first.path_validator.add_allowed_path(str(folder))
    first.path_validator.withhold_path(str(removed))
    assert first.path_validator.is_path_allowed(str(folder), prompt_user=False)

    home.set(enabled=False)
    agent, rebuilt = chat()
    assert rebuilt
    assert agent.path_validator.is_path_allowed(str(folder), prompt_user=False)
    assert not agent.path_validator.is_path_allowed(str(removed), prompt_user=False)


def test_a_terminal_ui_chat_follows_a_connector_change(home, tmp_path):
    """The TUI keeps one agent per session; it is rebuilt with its transcript."""
    from gaiapy import stdio
    from gaiapy.agent import GaiaAgent, GaiaAgentConfig
    from gaiapy.session_registry import close_agent

    from gaia.agents.base.history import SessionHistory
    from gaia.connectors.activations import activate_agent

    home.set()
    activate_agent("toy", FLAGSHIP)
    config_kwargs = {"silent_mode": True, "streaming": True}
    agent = GaiaAgent(config=GaiaAgentConfig(**config_kwargs))
    history = SessionHistory(str(tmp_path / "history.sqlite3"))
    agent.conversation_history = history
    history.append({"role": "user", "content": "use echo"})
    agent.internet_off = True
    try:
        assert stdio.follow_settings(agent, config_kwargs) is agent
        assert _mcp_tools(agent) == [TOOL]

        home.set(enabled=False)
        new = stdio.follow_settings(agent, config_kwargs)
        assert new is not agent
        assert _mcp_tools(new) == []
        assert new.conversation_history is history
        assert list(history)[-1]["content"] == "use echo"
        assert new.internet_off is True
        assert new.model_id == agent.model_id
        agent = new
    finally:
        close_agent(agent)


def test_the_sidecar_query_route_follows_a_connector_change(home, monkeypatch):
    """End to end through /v1/gaia/query: two real turns on one session."""
    import uuid

    from fastapi.testclient import TestClient
    from gaiapy import caller_auth
    from gaiapy import server as server_mod
    from gaiapy import session_registry as sr

    from gaia.connectors.activations import activate_agent

    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setenv(caller_auth.ALLOW_NO_TOKEN_ENV_VAR, "1")
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    home.set()
    activate_agent("toy", FLAGSHIP)
    client = TestClient(server_mod.build_app(), base_url="http://127.0.0.1:8141")
    session_id = f"chat-{uuid.uuid4().hex[:8]}"

    def turn(query):
        resp = client.post(
            "/v1/gaia/query",
            json={
                "query": query,
                "run_id": str(uuid.uuid4()),
                "session_id": session_id,
                "allowed_paths": [home.work],
                "context": [],
            },
        )
        assert resp.status_code == 200, resp.text
        return sr.registry.get(session_id).agent

    try:
        first = turn("hello")
        assert _mcp_tools(first) == [TOOL]
        home.set(enabled=False)  # Settings › Connectors, between messages
        second = turn("and again")
        assert second is not first
        assert _mcp_tools(second) == []
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


@pytest.mark.parametrize(
    "router, model, expected",
    [
        (object(), "Gemma-4-E4B-it-GGUF", "hybrid"),
        (None, "Gemma-4-E4B-it-GGUF", "Gemma-4-E4B-it-GGUF"),
    ],
)
def test_a_terminal_ui_rebuild_keeps_a_hybrid_session_hybrid(
    monkeypatch, router, model, expected
):
    """The routed turn's model is not the session's: a hybrid chat stays hybrid."""
    from types import SimpleNamespace

    import gaiapy.agent as agent_mod
    import gaiapy.session_registry as sr
    from gaiapy import stdio

    built = {}

    class _Built:
        def __init__(self, config):
            built["model_id"] = config.model_id

    monkeypatch.setattr(agent_mod, "GaiaAgent", _Built)
    monkeypatch.setattr(sr, "_carry_session_state", lambda old, new, sid: None)
    monkeypatch.setattr(sr, "close_agent", lambda agent: None)
    old = SimpleNamespace(
        built_settings_changed=lambda: True,
        hybrid_router=router,
        model_id=model,
        conversation_history=[],
    )
    stdio.follow_settings(old, {"silent_mode": True})
    assert built["model_id"] == expected
