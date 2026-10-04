# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A long tool call reports its progress while its arguments stream in.

A local model writing a whole file into edit_file streamed for seven minutes;
the arguments arrive as silent deltas, so the TUI showed only a timer.
"""

import json
from unittest.mock import MagicMock, patch

from gaia.agents.base.agent import Agent
from gaia.llm.providers import lemonade as lemonade_provider
from gaia.llm.providers.lemonade import LemonadeProvider
from gaia.ui.sse_handler import SSEOutputHandler


def _frames(name, pieces):
    yield {
        "choices": [
            {
                "delta": {
                    "tool_calls": [{"index": 0, "id": "c1", "function": {"name": name}}]
                }
            }
        ]
    }
    for piece in pieces:
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": piece}}]
                    }
                }
            ]
        }
    yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}


def _provider(stream):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = stream
        return LemonadeProvider(model="Gemma-4-E4B-it-GGUF")


def test_a_long_call_reports_its_size_as_it_streams(monkeypatch):
    monkeypatch.setattr(lemonade_provider, "TOOL_CALL_PROGRESS_INTERVAL_S", 0.0)
    provider = _provider(_frames("edit_file", ["x" * 1000] * 4))
    seen = []
    provider.tool_call_progress = lambda tool, chars: seen.append((tool, chars))

    out = "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert seen == [("edit_file", 2000), ("edit_file", 3000), ("edit_file", 4000)]
    # The envelope the agent parses is unchanged.
    call = json.loads(out)["__tool_calls__"][0]
    assert call["function"]["arguments"] == "x" * 4000


def test_a_short_call_says_nothing(monkeypatch):
    monkeypatch.setattr(lemonade_provider, "TOOL_CALL_PROGRESS_INTERVAL_S", 0.0)
    provider = _provider(_frames("read_file", ['{"file_path": "a.py"}']))
    seen = []
    provider.tool_call_progress = lambda tool, chars: seen.append((tool, chars))

    "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert seen == []


def test_reports_are_spaced_out():
    provider = _provider(_frames("write_file", ["y" * 1000] * 5))
    seen = []
    provider.tool_call_progress = lambda tool, chars: seen.append(chars)

    "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert seen == [2000]  # the rest arrived inside the 3s interval


class _Agent(Agent):
    def _get_system_prompt(self):
        return ""

    def _register_tools(self):
        pass


def test_the_agent_names_the_work_in_the_users_words():
    agent = _Agent.__new__(_Agent)
    agent.console = MagicMock()

    agent._report_tool_call_progress("edit_file", 3200)
    agent._report_tool_call_progress("custom_tool", 1600)

    assert [c.args for c in agent.console.report_phase.call_args_list] == [
        ("tool_call", "Writing a file edit — 3,200 characters so far"),
        ("tool_call", "Preparing custom_tool — 1,600 characters so far"),
    ]
    assert [c.kwargs for c in agent.console.report_phase.call_args_list] == [
        {"chars": 3200},
        {"chars": 1600},
    ]


def test_the_ui_console_emits_it_as_a_status():
    handler = SSEOutputHandler()
    handler.report_progress("Writing a file edit — 3,200 characters so far")

    event = handler.event_queue.get_nowait()
    assert event == {
        "type": "status",
        "status": "working",
        "message": "Writing a file edit — 3,200 characters so far",
    }
