# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The silent opening of a model call reports what the model is doing.

A thinking model on a local GPU sat for 16 s with nothing on the wire: the
prompt was being read, then a one-paragraph thought was held back until it was
finished. These pin each phase report to the moment the code actually knows it.
"""

from unittest.mock import MagicMock, patch

from gaia.agents.base.agent import Agent
from gaia.llm.providers import lemonade as lemonade_provider
from gaia.llm.providers.lemonade import LemonadeProvider
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


def _reasoning_frames(pieces, answer="Done."):
    for piece in pieces:
        yield {"choices": [{"delta": {"reasoning_content": piece}}]}
    yield {"choices": [{"delta": {"content": answer}}]}
    yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}


def _provider(stream):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = stream
        return LemonadeProvider(model="Gemma-4-E4B-it-GGUF")


def test_reasoning_reports_its_word_count_while_it_is_held_back(monkeypatch):
    monkeypatch.setattr(lemonade_provider, "REASONING_PROGRESS_INTERVAL_S", 0.0)
    provider = _provider(
        _reasoning_frames(["The user ", "wants revenue ", "by region"])
    )
    seen = []
    provider.reasoning_progress = seen.append

    out = "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    # The first delta starts the clock; each later one reports the total so far.
    assert seen == [4, 6]
    # The text itself is unchanged: still released whole, still wrapped.
    assert out == "<think>The user wants revenue by region</think>Done."


def test_reasoning_reports_are_spaced_out():
    provider = _provider(_reasoning_frames(["a "] * 20))
    seen = []
    provider.reasoning_progress = seen.append

    "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert seen == []  # all inside the first second


def test_entering_a_think_block_reports_the_reasoning_phase():
    handler = SSEOutputHandler()
    handler.print_streaming_text("<think>")

    assert handler.event_queue.get_nowait() == {
        "type": "status",
        "status": "working",
        "message": "Reasoning",
        "phase": "reasoning",
    }


def test_the_translator_keeps_the_phase_and_its_count():
    translator = CanonicalTranslator(run_id=None, agent_id="gaia", debug=False)

    out = translator.translate(
        {
            "type": "status",
            "status": "working",
            "message": "Reasoning — 40 words so far",
            "phase": "reasoning",
            "words": 40,
            "elapsed": 3.2,
        }
    )

    assert out == [
        {
            "type": "status",
            "message": "Reasoning — 40 words so far",
            "phase": "reasoning",
            "words": 40,
        }
    ]


def test_a_plain_status_gains_no_phase():
    translator = CanonicalTranslator(run_id=None, agent_id="gaia", debug=False)

    assert translator.translate({"type": "status", "message": "Searching files"}) == [
        {"type": "status", "message": "Searching files"}
    ]


class _Agent(Agent):
    def _get_system_prompt(self):
        return ""

    def _register_tools(self):
        pass


def _agent_with_backend():
    agent = _Agent.__new__(_Agent)
    agent.console = MagicMock()
    backend = MagicMock(spec=["model_load_listener"])
    provider = MagicMock(spec=["tool_call_progress", "reasoning_progress", "_backend"])
    provider._backend = backend
    agent.chat = MagicMock(spec=["llm_client"])
    agent.chat.llm_client = provider
    return agent, backend


def test_a_call_opens_by_reading_the_request_and_a_load_interrupts_it():
    agent, backend = _agent_with_backend()

    agent._announce_model_call(after_tools=False)
    backend.model_load_listener("Qwen3.6-35B-A3B-GGUF", "loading")
    backend.model_load_listener("Qwen3.6-35B-A3B-GGUF", "loaded")

    assert [c.args for c in agent.console.report_phase.call_args_list] == [
        ("reading", "Reading your request"),
        ("loading_model", "Loading Qwen3.6-35B-A3B-GGUF into memory"),
        ("reading", "Reading your request"),
    ]


def test_after_a_tool_the_model_reads_its_result():
    agent, _ = _agent_with_backend()

    agent._announce_model_call(after_tools=True)

    agent.console.report_phase.assert_called_once_with(
        "reading", "Reading the tool results"
    )


def test_reasoning_progress_is_a_phase_with_a_count():
    agent, _ = _agent_with_backend()

    agent._report_reasoning_progress(1234)

    agent.console.report_phase.assert_called_once_with(
        "reasoning", "Reasoning — 1,234 words so far", words=1234
    )
