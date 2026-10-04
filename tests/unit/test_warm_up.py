# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The warm-up does the first turn's one-time work, and changes nothing else.

A local model's first answer used to pay for loading the model, embedding every
tool description and reading ~10K tokens of system prompt while the user
watched. The warm-up moves that ahead of the chat. The guarantee that makes it
safe: the first real turn behaves exactly as it would have without it.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.tool_loader import ToolLoader

TOOLS = ["read_file", "write_file", "web_search", "send_email"]
CORE = ["write_file", "read_file"]


def _registry():
    return {name: {"description": f"{name} tool", "parameters": {}} for name in TOOLS}


def _loader(embedded: list):
    def embed(text):
        embedded.append(text)
        vec = np.zeros(len(TOOLS))
        vec[len(embedded) % len(TOOLS)] = 1.0
        return vec

    return ToolLoader(core_tools=CORE, bundles=[], embed_fn=embed)


def test_warm_builds_the_embeddings_and_returns_core_in_registry_order():
    embedded: list = []
    loader = _loader(embedded)

    core = loader.warm(_registry())

    assert core == ["read_file", "write_file"]  # registry order, not config order
    assert len(embedded) == len(TOOLS)  # every tool doc embedded once


def test_warm_leaves_the_turn_state_untouched():
    embedded: list = []
    loader = _loader(embedded)
    loader.warm(_registry())

    assert loader._turn == 0  # pylint: disable=protected-access
    assert loader._loaded == {}  # pylint: disable=protected-access

    # The embeddings are cached: the first turn embeds only its query.
    before = len(embedded)
    loader.select("find a file", _registry())
    assert len(embedded) == before + 1


def test_warm_raises_when_the_embedder_is_down():
    def down(_text):
        raise ConnectionError("embedding server unreachable")

    loader = ToolLoader(core_tools=CORE, bundles=[], embed_fn=down)
    with pytest.raises(ConnectionError):
        loader.warm(_registry())


class _Chat:
    def __init__(self):
        self.calls = []

    def send_messages(self, messages, system_prompt=None, tools=None, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "system_prompt": system_prompt,
                "tools": tools,
                **kwargs,
            }
        )


class _Agent(Agent):
    """Just the pieces warm_up reads, without building a real agent."""

    def __init__(self, loader=None):  # pylint: disable=super-init-not-called
        self.chat = _Chat()
        self.model_id = "Qwen3.6-35B-A3B-GGUF"
        self.tool_loader = loader
        self.applied = []

    @property
    def _tools_registry(self):
        return _registry()

    def _apply_tool_filter(self, new_filter):
        self.applied.append(new_filter)

    def _refresh_active_skill_filter(self, user_input):
        self.skill_filter_input = user_input

    @property
    def system_prompt(self):
        return "SYSTEM PROMPT"

    @property
    def _openai_tools(self):
        return [{"name": "read_file"}]

    def _get_system_prompt(self):
        return ""

    def _register_tools(self):
        pass


def test_warm_up_primes_with_the_real_prompt_and_a_one_token_budget():
    embedded: list = []
    agent = _Agent(loader=_loader(embedded))
    steps: list = []

    result = agent.warm_up(progress=steps.append)

    assert agent.applied == [["read_file", "write_file"]]
    # Skills render through the per-turn filter, exactly as a turn renders them.
    assert agent.skill_filter_input == Agent.WARM_UP_PROMPT
    [call] = agent.chat.calls
    assert call["system_prompt"] == "SYSTEM PROMPT"
    assert call["tools"] == [{"name": "read_file"}]
    assert call["max_tokens"] == 1
    assert steps == [
        "Indexing tools",
        "Loading Qwen3.6-35B-A3B-GGUF and reading its instructions",
    ]
    assert "seconds" in result


def test_warm_up_without_a_tool_loader_still_primes_the_model():
    agent = _Agent(loader=None)
    agent.warm_up()
    assert agent.applied == []
    assert len(agent.chat.calls) == 1


def test_memory_upkeep_moves_into_the_warm_up_and_runs_once():
    class _MemoryAgent(MemoryMixin, _Agent):
        runs = 0

        def _run_memory_post_init(self):
            type(self).runs += 1

    agent = _MemoryAgent()
    agent._memory_post_init_pending = True  # pylint: disable=protected-access
    steps: list = []

    agent.warm_up(progress=steps.append)

    assert _MemoryAgent.runs == 1
    assert steps[0] == "Tidying memory"
    # The first real query must not run it again.
    assert agent._memory_post_init_pending is False  # pylint: disable=protected-access
