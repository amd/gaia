# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Word Count reference agent.

These tests mock the LLM and never contact a live Lemonade server. They show
the two layers worth testing in a tool-using agent:

- The pure tool logic (``count_text_stats``) — tested directly, no LLM.
- The agent wiring — the tool registers and the registration metadata is right.
"""

from unittest.mock import MagicMock, patch

import gaia_agent_word_count
from gaia_agent_word_count.agent import WordCountAgent, count_text_stats


def test_count_text_stats_basic():
    stats = count_text_stats("The quick brown fox.")
    assert stats["words"] == 4
    assert stats["sentences"] == 1
    assert stats["characters"] == len("The quick brown fox.")


def test_count_text_stats_multiline_and_sentences():
    stats = count_text_stats("Hello there!\nHow are you? I'm fine.")
    assert stats["words"] == 7
    assert stats["sentences"] == 3  # ! ? .
    assert stats["lines"] == 2


def test_count_text_stats_empty():
    stats = count_text_stats("")
    assert stats == {
        "words": 0,
        "characters": 0,
        "characters_no_spaces": 0,
        "sentences": 0,
        "lines": 0,
    }


def test_build_registration_metadata():
    reg = gaia_agent_word_count.build_registration()
    assert reg.id == "word-count"
    assert reg.category == "examples"
    assert reg.tools_count == 1
    assert reg.namespaced_agent_id == "installed:word-count"


def test_tool_registers_on_agent():
    """The @tool wrapper is snapshotted onto the instance."""
    with patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()):
        agent = WordCountAgent(skip_lemonade=True)

    assert "count_text" in agent._tools_registry
    # The system prompt advertises the tool to the model.
    assert "count_text" in agent.system_prompt
