# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The warm-up does the first turn's one-time work, and changes nothing else.

A local model's first answer used to pay for loading the model, embedding every
tool description and reading ~10K tokens of system prompt while the user
watched. The warm-up moves that ahead of the chat. The guarantee that makes it
safe: the first real turn behaves exactly as it would have without it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.tool_loader import ToolLoader
from gaia.llm.lemonade_client import FULL_TOOLS_SLOT

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
    assert loader.jumped is False

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


def test_warm_on_a_disabled_session_embeds_nothing_and_offers_no_list():
    embedded: list = []
    loader = _loader(embedded)
    loader._session_disabled = True  # pylint: disable=protected-access

    assert loader.warm(_registry()) == []
    assert embedded == []


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

    def __init__(self):  # pylint: disable=super-init-not-called
        self.chat = _Chat()
        self.model_id = "Qwen3.6-35B-A3B-GGUF"
        self.tool_loader = None
        self.applied = []
        self._active_tool_filter = None

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
    agent = _Agent()
    steps: list = []

    result = agent.warm_up(progress=steps.append)

    # The first turn's exact tool list, so its prefix is what gets primed.
    assert agent.applied == []
    # Skills render through the per-turn filter, exactly as a turn renders them.
    assert agent.skill_filter_input == Agent.WARM_UP_PROMPT
    [call] = agent.chat.calls
    assert call == {
        "messages": [{"role": "user", "content": Agent.WARM_UP_PROMPT}],
        "system_prompt": "SYSTEM PROMPT",
        "tools": [{"name": "read_file"}],
        "max_tokens": 1,
    }
    assert steps == ["Loading Qwen3.6-35B-A3B-GGUF and reading its instructions"]
    assert "seconds" in result


def test_warm_up_applies_the_list_a_new_session_starts_on():
    class _Narrowing(_Agent):
        def _warm_tool_filter(self):
            return ["read_file"]

    agent = _Narrowing()
    agent.warm_up()
    assert agent.applied == [["read_file"]]


def test_warm_up_keeps_the_filter_it_already_has():
    class _Same(_Agent):
        def _warm_tool_filter(self):
            return self._active_tool_filter

    agent = _Same()
    agent._active_tool_filter = ["read_file"]  # pylint: disable=protected-access
    agent.warm_up()
    assert agent.applied == []


def test_warm_up_reports_the_first_requests_token_counts():
    agent = _Agent()
    agent.chat.llm_client = SimpleNamespace(
        get_last_usage=lambda: {"prompt_tokens": 9000, "cached_tokens": 12}
    )
    result = agent.warm_up()
    assert result["prompt_tokens"] == 9000
    assert result["cached_tokens"] == 12


def test_warm_up_primes_the_second_prefix_after_the_first():
    order: list = []

    class _Second(_Agent):
        def _prime_second_prefix(self, say):
            order.append(len(self.chat.calls))
            say("second prefix")

    agent = _Second()
    steps: list = []
    agent.warm_up(progress=steps.append)
    assert order == [1]
    assert steps[-1] == "second prefix"


def test_a_base_agent_primes_no_second_prefix():
    agent = _Agent()
    agent.warm_up()
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


def test_warm_up_leaves_memory_upkeep_pending_for_a_private_agent():
    class _MemoryAgent(MemoryMixin, _Agent):
        runs = 0

        def _run_memory_post_init(self):
            type(self).runs += 1

    agent = _MemoryAgent()
    agent._incognito = True  # pylint: disable=protected-access
    agent._memory_post_init_pending = True  # pylint: disable=protected-access

    agent.warm_up()

    assert _MemoryAgent.runs == 0
    assert agent._memory_post_init_pending is True  # pylint: disable=protected-access


# ── ChatAgent: core on slot 0, full list on slot 2 ────────────────────────


pytest.importorskip("gaia_agent")

from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402


def _names(call):
    return [schema["function"]["name"] for schema in call["tools"]]


@pytest.fixture
def chat_agent():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    agent.chat = _Chat()
    agent.chat.llm_client = SimpleNamespace(_backend=MagicMock())
    agent.chat.llm_client._backend.slot_count.return_value = 3
    agent._active_tool_filter = None
    agent._active_skill_filter = None
    agent._refresh_active_skill_filter = lambda _q: None
    agent._compose_system_prompt = lambda: "PROMPT"
    agent._system_prompt_cache = None
    agent.conversation_history = []
    agent._tool_notice_due = False
    return agent


def test_a_warm_up_primes_core_then_the_full_list(chat_agent):
    steps: list = []

    chat_agent.warm_up(progress=steps.append)

    registry = list(chat_agent._tools_registry)
    core = chat_agent.tool_loader.offered(chat_agent._tools_registry)
    assert len(core) < len(registry)
    first, second = chat_agent.chat.calls
    assert _names(first) == core
    assert "id_slot" not in first
    assert _names(second) == registry
    assert second["id_slot"] == FULL_TOOLS_SLOT == 2
    assert second["max_tokens"] == 1
    assert second["messages"] == first["messages"]
    assert steps == [
        f"Loading {chat_agent.model_id} and reading its instructions",
        "Reading the full tool list",
    ]


def test_both_primed_lists_are_thin(chat_agent):
    chat_agent.warm_up()
    for call in chat_agent.chat.calls:
        for schema in call["tools"]:
            for prop in schema["function"]["parameters"]["properties"].values():
                assert "description" not in prop


def test_a_warm_up_leaves_the_session_on_core(chat_agent):
    chat_agent.warm_up()

    loader = chat_agent.tool_loader
    assert chat_agent._active_tool_filter == loader.offered(chat_agent._tools_registry)
    assert loader.jumped is False
    assert chat_agent._conversation_request_kwargs() == {}


def test_a_warm_up_leaves_no_tool_list_notice_for_the_first_turn(chat_agent):
    chat_agent.warm_up()
    assert chat_agent._tool_list_notice() is False


@pytest.mark.parametrize("slots", [1, 2])
def test_a_model_without_a_third_slot_primes_only_core(chat_agent, slots):
    """On two slots the full list would overwrite a side job's, or be overwritten."""
    chat_agent.chat.llm_client._backend.slot_count.return_value = slots

    chat_agent.warm_up()

    [call] = chat_agent.chat.calls
    assert "id_slot" not in call
    assert _names(call) == chat_agent.tool_loader.offered(chat_agent._tools_registry)


def test_a_client_without_a_local_backend_primes_only_core(chat_agent):
    chat_agent.chat.llm_client = SimpleNamespace()
    chat_agent.warm_up()
    assert len(chat_agent.chat.calls) == 1


def test_a_failed_second_prime_raises_and_still_restores_core(chat_agent):
    real_send = chat_agent.chat.send_messages

    def fail_on_slot_two(messages, **kwargs):
        if kwargs.get("id_slot") == FULL_TOOLS_SLOT:
            raise ConnectionError("model evicted")
        real_send(messages, **kwargs)

    chat_agent.chat.send_messages = fail_on_slot_two

    with pytest.raises(ConnectionError):
        chat_agent.warm_up()

    assert chat_agent._active_tool_filter == chat_agent.tool_loader.offered(
        chat_agent._tools_registry
    )


def test_a_jumped_session_primes_nothing_extra(chat_agent):
    """The full list is already what the conversation slot would read."""
    chat_agent.tool_loader.jump("search")

    chat_agent.warm_up()

    [call] = chat_agent.chat.calls
    assert _names(call) == list(chat_agent._tools_registry)


def test_a_session_with_no_loader_primes_only_its_list(chat_agent):
    chat_agent.tool_loader = None

    chat_agent.warm_up()

    [call] = chat_agent.chat.calls
    assert _names(call) == list(chat_agent._tools_registry)


def test_the_warm_filter_validates_the_config_first(chat_agent):
    chat_agent.tool_loader._core = frozenset({"not_a_tool"})
    with pytest.raises(ValueError, match="not_a_tool"):
        chat_agent._warm_tool_filter()
