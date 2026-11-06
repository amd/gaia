# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The warm-up does the first turn's one-time work, and changes nothing else.

A local model's first answer used to pay for loading the model, embedding every
tool description and reading ~10K tokens of system prompt while the user
watched. The warm-up moves that ahead of the chat. The guarantee that makes it
safe: the first real turn behaves exactly as it would have without it.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.tool_loader import ToolLoader
from gaia.chat.sdk import LLMCallCancelled
from gaia.daemon import broker_client
from gaia.llm.lemonade_client import FULL_TOOLS_SLOT
from gaia.llm.retry import current_cancel_check

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
        def _prime_second_prefix(self, say, cancel):
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


# ── Stopping a warm-up for a turn on another model (#6582) ─────────────────


def test_a_stopped_warm_up_never_reaches_the_model():
    agent = _Agent()
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(LLMCallCancelled):
        agent.warm_up(cancel=cancel)
    assert agent.chat.calls == []


def test_a_stoppable_warm_up_can_be_stopped_mid_prompt():
    """The prompt read sends nothing back, so only the stream's Stop hook and
    the lease wait's cancel scope can end it there."""
    agent = _Agent()
    cancel = threading.Event()
    seen = {}

    def send(messages, system_prompt=None, tools=None, **kwargs):
        seen["cancel"] = kwargs.get("cancel")
        seen["stop_hook"] = current_cancel_check()
        seen["lease_scope"] = broker_client._cancel_scope.get()

    agent.chat.send_messages = send
    agent.warm_up(cancel=cancel)

    assert seen["cancel"] is cancel
    assert seen["lease_scope"] is cancel
    assert seen["stop_hook"]() is False
    cancel.set()
    assert seen["stop_hook"]() is True


def test_a_request_cut_off_by_the_stop_reads_as_stopped():
    agent = _Agent()
    cancel = threading.Event()

    def send(*_a, **_k):
        cancel.set()
        raise RuntimeError("Stopped while waiting for the model to respond.")

    agent.chat.send_messages = send
    with pytest.raises(LLMCallCancelled):
        agent.warm_up(cancel=cancel)


def test_a_warm_up_failure_is_not_mistaken_for_a_stop():
    agent = _Agent()

    def send(*_a, **_k):
        raise ConnectionError("Lemonade not reachable")

    agent.chat.send_messages = send
    with pytest.raises(ConnectionError):
        agent.warm_up(cancel=threading.Event())


def test_a_stopped_warm_up_skips_the_memory_upkeep():
    class _MemoryAgent(MemoryMixin, _Agent):
        runs = 0

        def _run_memory_post_init(self):
            type(self).runs += 1

    agent = _MemoryAgent()
    agent._memory_post_init_pending = True  # pylint: disable=protected-access
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(LLMCallCancelled):
        agent.warm_up(cancel=cancel)
    assert _MemoryAgent.runs == 0
    assert agent._memory_post_init_pending is True  # pylint: disable=protected-access


# ── FastFlowLM: load only, no prompt read ─────────────────────────────────
#
# FLM v1.0.7 reuses a cached prompt only when the next request continues the
# last one, so the first turn's own user message discards a primed prefix:
# priming cost ~35 s on the NPU and saved that turn nothing.

FLM = "gemma4-it-e4b-FLM"


class _LemonadeClient:
    def __init__(self, on_load=None):
        self.loaded = []
        self.on_load = on_load

    def ensure_model_loaded(self, model):
        if self.on_load is not None:
            self.on_load()
        self.loaded.append(model)


def _flm_agent(on_load=None):
    agent = _Agent()
    agent.model_id = FLM
    agent.chat.llm_client = _LemonadeClient(on_load)
    return agent


def test_flm_warm_up_loads_the_model_and_sends_no_prompt():
    agent = _flm_agent()
    steps: list = []

    result = agent.warm_up(progress=steps.append)

    assert agent.chat.calls == []
    assert agent.chat.llm_client.loaded == [FLM]
    assert result["primed"] is False
    assert steps == [f"Loading {FLM}"]


def test_flm_warm_up_gives_a_turn_no_prompt_read_to_wait_behind():
    """A turn on the warmed model only ever waits for the load it needs anyway."""
    agent = _flm_agent()
    agent.warm_up(cancel=threading.Event())
    assert agent.chat.calls == []
    assert agent.chat.llm_client.loaded == [FLM]


def test_a_stopped_flm_warm_up_loads_nothing():
    agent = _flm_agent()
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(LLMCallCancelled):
        agent.warm_up(cancel=cancel)
    assert agent.chat.llm_client.loaded == []


def test_an_flm_warm_up_load_wait_can_be_stopped():
    seen = {}
    agent = _flm_agent(
        on_load=lambda: seen.update(lease_scope=broker_client._cancel_scope.get())
    )
    cancel = threading.Event()

    agent.warm_up(cancel=cancel)

    assert seen["lease_scope"] is cancel


def test_an_flm_load_failure_is_raised_not_hidden():
    def down():
        raise ConnectionError("Lemonade not reachable")

    agent = _flm_agent(on_load=down)
    with pytest.raises(ConnectionError):
        agent.warm_up(cancel=threading.Event())


def test_an_flm_warm_up_without_a_lemonade_client_says_so():
    agent = _Agent()
    agent.model_id = FLM
    with pytest.raises(RuntimeError, match="Lemonade"):
        agent.warm_up()


def test_the_lemonade_provider_loads_through_the_turns_own_preflight(monkeypatch):
    """Same window and lease as a turn's load, so the turn does not reload."""
    from gaia.llm.providers.lemonade import LemonadeProvider

    base = "http://127.0.0.1:59999/api/v1"
    monkeypatch.setenv("LEMONADE_BASE_URL", base)
    provider = LemonadeProvider(model=FLM, base_url=base)
    calls = []
    monkeypatch.setattr(
        provider._backend,  # pylint: disable=protected-access
        "_ensure_model_loaded",
        lambda model, **kw: calls.append((model, kw)),
    )

    provider.ensure_model_loaded(FLM)

    assert calls == [(FLM, {})]


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


def test_a_stop_during_the_full_list_read_ends_the_warm_up(chat_agent):
    """A turn on another model must not wait behind the second prime either."""
    cancel = threading.Event()
    real_send = chat_agent.chat.send_messages

    def stop_on_slot_two(messages, **kwargs):
        if kwargs.get("id_slot") == FULL_TOOLS_SLOT:
            assert kwargs.get("cancel") is cancel
            cancel.set()
            raise RuntimeError("Stopped while waiting for the model to respond.")
        real_send(messages, **kwargs)

    chat_agent.chat.send_messages = stop_on_slot_two

    with pytest.raises(LLMCallCancelled):
        chat_agent.warm_up(cancel=cancel)

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
