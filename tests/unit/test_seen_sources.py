# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What an agent keeps of user messages and tool results for skill fact checks."""

import logging
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import SEEN_SOURCE_MAX_CHARS, SEEN_SOURCES_MAX, Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _Agent(Agent):
    def _register_tools(self):
        pass


def _make_agent() -> _Agent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _Agent(skip_lemonade=True, silent_mode=True)


def _cycle():
    loop = {"rows": 3}
    loop["self"] = loop
    return loop


@pytest.mark.parametrize("make", [lambda: {("a", 1): 2}, _cycle])
def test_a_result_json_cannot_hold_is_still_a_successful_tool_call(make, caplog):
    result = make()

    @tool
    def odd_result() -> dict:
        """Return something json.dumps refuses."""
        return result

    agent = _make_agent()
    with caplog.at_level(logging.WARNING):
        assert agent._execute_tool("odd_result", {}) is result
    source, _args, text = agent.seen_sources()[-1]
    assert source == "odd_result" and text == str(result)
    assert "Could not serialise" in caplog.text


def test_each_entry_and_the_list_are_capped():
    agent = _make_agent()
    agent.note_seen("read_file", {}, "x" * (SEEN_SOURCE_MAX_CHARS + 500))
    assert len(agent.seen_sources()[-1][2]) == SEEN_SOURCE_MAX_CHARS
    for i in range(SEEN_SOURCES_MAX + 25):
        agent.note_seen("read_file", {"n": i}, {"n": i})
    seen = agent.seen_sources()
    assert len(seen) == SEEN_SOURCES_MAX
    assert seen[-1][1] == {"n": SEEN_SOURCES_MAX + 24}


def test_clearing_forgets_what_was_seen():
    agent = _make_agent()
    agent.note_seen("user", {}, "Revenue was 12,431,220")
    agent.clear_seen_sources()
    assert agent.seen_sources() == []
