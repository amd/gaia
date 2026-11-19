# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP tools go through the tool loader, with a menu naming the ones left out (#1972).

Two things must hold. The prompt cache stays warm: the menu sits at the end of
the system prompt and changes only when the tool list does, so a warm-up primes
the exact bytes the first turn and a jump send. And the agent's own tools are
never held back for MCP tools' sake.
"""

from __future__ import annotations

import logging

import pytest
from gaia_agent.chat_agent import ChatAgent
from test_prompt_prefix_stability import (
    _assert_each_extends_the_last,
    _assert_same_prefix,
    _prefix,
    _tool_names,
    _turn,
    _warm_up,
)

from gaia.agents.base.memory import MemoryMixin
from gaia.eval import tool_cost
from gaia.eval.mcp_fixture import (
    build_mock_servers,
    convert_mock_servers,
    lexical_embedder,
)
from gaia.mcp.mixin import MCP_MENU_HEADER, MCP_MENU_MAX_TOOLS


def _connect(agent, servers: int = 3, tools: int = 5) -> dict:
    """Give *agent* mock MCP servers' tools, as ``_load_mcp_tools`` would."""
    entries = convert_mock_servers(agent, build_mock_servers(servers, tools))
    agent._instance_tools.update(entries)
    agent.rebuild_system_prompt()
    return entries


def _system(request) -> str:
    return request[0][0]["content"]


def _menu_names(system: str) -> list[str]:
    """Full registry names the menu shows, rebuilt from its per-server lines."""
    menu = system[system.index(MCP_MENU_HEADER) :]
    names = []
    for line in menu.splitlines()[2:]:
        server, tools = line[2:].split(": ", 1)
        for tool in tools.split(", "):
            if not tool.startswith("and ") and not tool.endswith(" tools"):
                names.append(f"mcp_{server}_{tool}")
    return names


# ── KV cache: the menu never costs a warm prefix ──────────────────────────


def test_the_warm_up_primes_the_first_turn_menu_and_all(flagship):
    agent, model, _doc = flagship
    entries = _connect(agent)
    core, full = _warm_up(agent, model)

    assert _system(core).endswith(agent._unlisted_tools_menu())
    assert _menu_names(_system(core)) == list(entries)
    assert not set(_tool_names(core)) & set(entries)
    # The full list leaves nothing out, so the slot a jump lands on has no menu.
    assert MCP_MENU_HEADER not in _system(full)

    _turn(agent, model, "hello", ["Hi."])
    _assert_same_prefix(core, model.requests[-1])


def test_stable_turns_send_the_same_bytes(flagship):
    agent, model, _doc = flagship
    _connect(agent)
    agent.tool_loader._threshold = 2.0  # the fake embedder is no jump signal

    for query in ("hello", "how are you?", "thanks!"):
        _turn(agent, model, query, ["Hi."])

    assert agent.tool_loader.jumped is False
    assert len({_prefix(r) for r in model.requests}) == 1
    assert MCP_MENU_HEADER in _system(model.requests[0])
    _assert_each_extends_the_last(model.requests)


def test_a_jump_for_an_mcp_tool_lands_on_the_primed_full_prefix(flagship):
    agent, model, _doc = flagship
    entries = _connect(agent)
    _core, full = _warm_up(agent, model)

    _turn(agent, model, "hello", ["Hi."])
    _turn(agent, model, "Call mcp_files_move_file on draft.md.", ["Done."])

    assert agent.tool_loader.jumped is True
    assert set(entries) <= set(_tool_names(model.requests[-1]))
    _assert_same_prefix(full, model.requests[-1])


def test_search_tools_reaches_an_mcp_tool_and_the_menu_goes(flagship):
    agent, model, _doc = flagship
    _connect(agent)
    _turn(agent, model, "hello", ["Hi."])
    assert "search_tools" in agent._active_tool_filter

    # The fixture's hashed bag of words is no ranker; its own doc text scores 1.0.
    doc = agent.tool_loader.tool_docs(agent._tools_registry)["mcp_files_move_file"]
    found = agent._search_tools(doc)

    assert found["status"] == "success", found
    assert "mcp_files_move_file" in found["tools"]
    assert agent.tool_loader.jumped is True
    assert MCP_MENU_HEADER not in agent.system_prompt


# ── the agent's own tools are never held back ────────────────────────────


def test_mcp_tools_leave_the_core_list_alone(flagship):
    agent, _model, _doc = flagship
    agent._refresh_active_tool_filter("hello")
    before = list(agent._active_tool_filter)
    entries = _connect(agent)
    agent.tool_loader.reset_session()

    agent._refresh_active_tool_filter("hello")

    assert agent._active_tool_filter == before
    assert not set(before) & set(entries)


# ── off-states and edge cases ─────────────────────────────────────────────


def test_memory_off_no_longer_sends_every_mcp_tool(flagship):
    agent, _model, _doc = flagship
    agent._memory_store = None
    assert agent._select_tools_for_turn("hello") is None, "no MCP tools: as before"

    entries = _connect(agent)
    agent.tool_loader.reset_session()
    agent._refresh_active_tool_filter("hello")

    assert agent.tool_loader.mcp_only is True
    own = [n for n in agent._tools_registry if n not in entries]
    assert agent._active_tool_filter == own, "memory off must not hide own tools"
    assert _menu_names(agent.system_prompt) == list(entries)


def test_memory_off_warm_up_primes_the_first_turn_after_the_switch(flagship):
    agent, model, _doc = flagship
    agent._memory_store = None
    entries = _connect(agent)
    core, full = _warm_up(agent, model)

    assert agent.tool_loader.mcp_only is True
    assert _tool_names(core) == [n for n in agent._tools_registry if n not in entries]
    assert MCP_MENU_HEADER not in _system(full)

    _turn(agent, model, "hello", ["Hi."])
    _assert_same_prefix(core, model.requests[-1])


@pytest.mark.parametrize(
    "reason", ["model_not_pulled", "model_load_failed", "service_unreachable"]
)
def test_memory_off_for_a_dead_embedder_sends_every_tool(flagship, reason):
    """Nothing to select with, so as on main: every tool, no menu."""
    agent, _model, _doc = flagship
    agent._memory_store = None
    agent._memory_unavailable_reason = reason
    _connect(agent)

    agent._refresh_active_tool_filter("hello")

    assert agent._active_tool_filter is None
    assert agent.tool_loader.mcp_only is False
    assert MCP_MENU_HEADER not in agent.system_prompt


def test_an_agent_built_later_without_the_loader_drops_the_first_ones_search_tools(
    make_flagship, monkeypatch
):
    first, _model, _doc = make_flagship()
    assert "search_tools" in first._tools_registry
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS", "0")

    second, _model, _doc = make_flagship()

    assert second.tool_loader is None
    assert "search_tools" not in second._tools_registry


def test_no_mcp_tools_means_no_menu(flagship):
    agent, _model, _doc = flagship
    agent._refresh_active_tool_filter("hello")
    assert agent._unlisted_tools_menu() == ""
    assert MCP_MENU_HEADER not in agent.system_prompt


def test_an_embedder_that_dies_offers_every_tool_and_no_menu(flagship, monkeypatch):
    agent, model, _doc = flagship
    _connect(agent)

    def dead(*_args):
        raise ConnectionError("embedder down")

    monkeypatch.setattr(ChatAgent, "_embed_texts_batch", dead)
    monkeypatch.setattr(MemoryMixin, "_embed_text", dead)
    agent.tool_loader._embed_fn = dead
    _turn(agent, model, "hello", ["Hi."])

    assert agent.tool_loader.session_disabled is True
    assert _tool_names(model.requests[-1]) == list(agent._tools_registry)
    assert MCP_MENU_HEADER not in _system(model.requests[-1])


def test_a_hosted_model_gets_every_mcp_tool_and_no_menu(flagship, monkeypatch):
    agent, _model, _doc = flagship
    _connect(agent)
    monkeypatch.setattr(agent, "_hosted_model", lambda: True)

    agent._refresh_active_tool_filter("hello")

    assert agent._active_tool_filter is None
    assert MCP_MENU_HEADER not in agent.system_prompt


def test_a_withheld_search_tools_leaves_no_menu_and_warns_once(flagship, caplog):
    agent, _model, _doc = flagship
    _connect(agent)
    agent.deny_tools(["search_tools"])

    with caplog.at_level(logging.WARNING):
        agent._refresh_active_tool_filter("hello")
        assert agent._unlisted_tools_menu() == ""

    assert agent._active_tool_filter is not None
    warned = [r for r in caplog.records if "search_tools is withheld" in r.getMessage()]
    assert len(warned) == 1


def test_a_list_without_search_tools_fails_loudly(flagship):
    agent, _model, _doc = flagship
    _connect(agent)
    agent._refresh_active_tool_filter("hello")
    agent._active_tool_filter = [
        n for n in agent._active_tool_filter if n != "search_tools"
    ]
    with pytest.raises(RuntimeError, match="search_tools"):
        agent._unlisted_tools_menu()


def test_a_server_that_disconnects_mid_session_leaves_the_menu(flagship):
    agent, model, _doc = flagship
    entries = _connect(agent)
    agent.tool_loader._threshold = 2.0  # the fake embedder is no jump signal
    _turn(agent, model, "hello", ["Hi."])
    gone = [n for n in entries if n.startswith("mcp_files_")]
    assert gone

    for name in gone:
        del agent._instance_tools[name]
    agent.rebuild_system_prompt()
    _turn(agent, model, "and now?", ["Hi."])

    assert agent.tool_loader.jumped is False
    names = _menu_names(_system(model.requests[-1]))
    assert names == [n for n in entries if n not in gone]


def test_the_cap_holds_on_a_real_session_and_is_logged(flagship, caplog):
    agent, _model, _doc = flagship
    entries = _connect(agent, servers=9, tools=5)
    assert len(entries) > MCP_MENU_MAX_TOOLS

    with caplog.at_level(logging.INFO, logger="gaia.mcp.mixin"):
        agent._refresh_active_tool_filter("hello")

    assert len(_menu_names(agent.system_prompt)) == MCP_MENU_MAX_TOOLS
    menu = agent.system_prompt[agent.system_prompt.index(MCP_MENU_HEADER) :]
    assert " more" in menu or " tools" in menu.splitlines()[-1]
    assert any("MCP tool menu names" in r.getMessage() for r in caplog.records)


# ── the doc profile, and profiles without a loader config ─────────────────


def test_the_doc_profile_gates_mcp_tools_with_its_own_core():
    agent = tool_cost.build_doc_agent_skeleton(
        "doc", dynamic_tools=True, embed_fn=lexical_embedder()
    )
    assert agent.tool_loader.mcp_only is False
    entries = convert_mock_servers(agent, build_mock_servers(3, 5))
    with tool_cost.with_mcp_tools(agent, entries):
        names = tool_cost.first_turn_tools(agent, "hello")
        menu = tool_cost.unlisted_tools_menu(agent, names)
    assert names == agent.tool_loader.offered(agent._tools_registry)
    assert not set(names) & set(entries)
    assert _menu_names(menu) == list(entries)


def test_without_mcp_tools_an_mcp_only_profile_is_byte_identical():
    off = tool_cost.build_doc_agent_skeleton("web", dynamic_tools=False)
    on = tool_cost.build_doc_agent_skeleton("web", dynamic_tools=True)
    assert on.tool_loader.mcp_only is True
    assert list(on._tools_registry) == list(off._tools_registry)
    assert on._select_tools_for_turn("hello") is None
    assert on._thin_tool_schemas() is False
    assert on._openai_tools == off._openai_tools
    assert on._format_tools_for_prompt() == off._format_tools_for_prompt()


def test_loading_mcp_tools_registers_search_tools_on_an_mcp_only_profile(
    monkeypatch,
):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    def load(agent):
        _TOOL_REGISTRY["mcp_files_move_file"] = {
            "name": "mcp_files_move_file",
            "description": "[MCP:files] Move a file.",
            "parameters": {},
            "_mcp_server": "files",
        }
        agent._own_mcp_tools = {"mcp_files_move_file"}

    monkeypatch.setattr(ChatAgent, "_load_mcp_tools", load)
    on = tool_cost.build_doc_agent_skeleton(
        "web", dynamic_tools=True, embed_fn=lexical_embedder()
    )
    assert "search_tools" in on._tools_registry
    # Only MCP tools wait; the agent's own tools go out as without a loader.
    assert on._local_loader() is True
    assert on._thin_tool_schemas() is False
    names = on._select_tools_for_turn("hello")
    assert "mcp_files_move_file" not in names
    assert names == [n for n in on._tools_registry if n != "mcp_files_move_file"]
