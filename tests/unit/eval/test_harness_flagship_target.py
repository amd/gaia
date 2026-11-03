# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The shared eval harnesses measure the flagship, and stay comparable.

Retrieval binds the flagship's search-key generator — the very function
ChatAgent defines, so its outputs are unchanged. ``tool_cost`` reports on the
flagship by default and still reproduces the ChatAgent doc-profile numbers
behind ``--agent chat``.
"""

import pytest

pytest.importorskip("gaia_agent")

from gaia.eval import tool_cost  # noqa: E402


def test_retrieval_binds_the_flagships_search_keys_unchanged():
    from unittest.mock import MagicMock

    from gaia_agent.agent import GaiaAgent
    from gaia_agent.chat_agent import ChatAgent

    from gaia.eval.retrieval.harness import agent_query_tool

    # Same function object: switching the harness cannot move a retrieval score.
    assert GaiaAgent._generate_search_keys is ChatAgent._generate_search_keys
    assert callable(agent_query_tool(MagicMock()))


def test_the_default_report_measures_the_flagship_with_the_new_mail_tools():
    agent = tool_cost.build_agent_skeleton()
    assert type(agent).__name__ == "GaiaAgent"
    assert {"get_thread", "list_waiting_on_you", "list_connected_mailboxes"} <= set(
        agent._tools_registry
    )
    # Unfiltered, like the doc baseline: no load_tools meta-tool.
    assert "load_tools" not in agent._tools_registry


def test_the_flagship_report_is_deterministic():
    first = tool_cost.measure_tool_prompt_cost(tool_cost.build_agent_skeleton())
    second = tool_cost.measure_tool_prompt_cost(tool_cost.build_agent_skeleton())
    assert first["tool_count"] == second["tool_count"]
    assert first["native_chars"] == second["native_chars"]


def test_chat_doc_reproduces_the_original_skeleton_exactly():
    via_flag = tool_cost.build_agent_skeleton(tool_cost.CHAT_AGENT, "doc")
    original = tool_cost.build_doc_agent_skeleton("doc")
    assert set(via_flag._tools_registry) == set(original._tools_registry)
    assert tool_cost.measure_tool_prompt_cost(
        via_flag
    ) == tool_cost.measure_tool_prompt_cost(original)


def test_a_chat_profile_on_the_flagship_is_refused_not_silently_swapped():
    with pytest.raises(ValueError, match="--agent chat"):
        tool_cost.build_agent_skeleton(tool_cost.GAIA_AGENT, "doc")
    with pytest.raises(SystemExit):
        tool_cost.main(["--profile", "doc"])


def test_an_unknown_agent_is_refused():
    with pytest.raises(ValueError, match="unknown agent"):
        tool_cost.build_agent_skeleton("email")


def test_the_report_names_what_it_measured():
    report = tool_cost.format_markdown_report()
    assert "gaia agent, full" in report.splitlines()[0]
    chat = tool_cost.format_markdown_report("doc", agent_name=tool_cost.CHAT_AGENT)
    assert "chat agent, doc" in chat.splitlines()[0]
