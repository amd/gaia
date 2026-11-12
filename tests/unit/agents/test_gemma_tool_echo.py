# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""FastFlowLM does not stop Gemma at the end of a native tool call, so Gemma
invents the tool's response in its own syntax and answers from it. That text
must never reach history: replayed, it makes the next turn come back empty."""

import json
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_markup import has_gemma_tool_markup

# Captured from gemma4-it-e4b-FLM (FLM v1.0.7) during the tool_selection eval.
ECHO = (
    'response:analyze_data_file{column_count:7,columns:[<|"|>month<|"|>,'
    '<|"|>revenue<|"|>],top_1:{revenue_total:70000.0,salesperson:<|"|>Sarah Chen<|"|>}}'
    "<tool_call|>Sarah Chen was the top salesperson by revenue in Q1."
)


def _envelope(content):
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "analyze_data_file",
                        "arguments": json.dumps({"file_path": "sales.csv"}),
                    },
                }
            ],
            "content": content,
            "finish_reason": "tool_calls",
        }
    )


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a


@pytest.mark.parametrize(
    "text, expected",
    [
        (ECHO, True),
        ('<|tool_call>call:get_weather{city:<|"|>Toronto<|"|>}<tool_call|>', True),
        ("<|tool_response>response:x{}<tool_response|>", True),
        ("Sarah Chen led Q1 with $70,000.", False),
        ('Gemma quotes strings as `<|"|>` in its template.', False),
        ("", False),
        (None, False),
    ],
)
def test_detects_gemma_tool_syntax_outside_code(text, expected):
    assert has_gemma_tool_markup(text) is expected


def test_invented_tool_response_is_dropped_but_the_call_runs(agent):
    parsed = agent._parse_llm_response(_envelope(ECHO))
    assert parsed["tool"] == "analyze_data_file"
    assert parsed["content"] is None


def test_ordinary_narration_beside_a_call_is_kept(agent):
    parsed = agent._parse_llm_response(_envelope("Let me check the sales file."))
    assert parsed["content"] == "Let me check the sales file."


class _Stream:
    """A server stream that records whether GAIA hung up on it."""

    def __init__(self, chunks):
        self._it = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)

    def close(self):
        self.closed = True


def _provider():
    from gaia.llm.providers.lemonade import LemonadeProvider

    provider = LemonadeProvider.__new__(LemonadeProvider)
    provider._last_ttft_seconds = None
    provider.tool_call_progress = None
    provider.reasoning_progress = None
    provider._last_model = "gemma4-it-e4b-FLM"
    return provider


def _call_delta(**extra):
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "analyze_data_file",
                                "arguments": "{}",
                            },
                        }
                    ],
                    **extra,
                },
                "finish_reason": None,
            }
        ]
    }


def test_stream_stops_at_the_call_and_never_shows_the_run_on():
    """FLM streams the call first, then hundreds of invented-result chunks."""
    import time

    run_on = [
        {"choices": [{"delta": {"content": piece}, "finish_reason": None}]}
        for piece in (
            "response:analyze_data_file{",
            'columns:[<|"|>month<|"|>]}',
            "Sarah Chen",
        )
    ]
    stream = _Stream([_call_delta(), *run_on])
    out = list(_provider()._handle_stream(stream, time.perf_counter()))
    assert len(out) == 1, f"the run-on reached the user: {out}"
    envelope = json.loads(out[0])
    assert envelope["__tool_calls__"][0]["function"]["name"] == "analyze_data_file"
    assert envelope["content"] is None
    assert envelope["finish_reason"] == "tool_calls"
    assert stream.closed, "the server must be told to stop generating"


def test_narration_before_the_call_still_streams():
    import time

    stream = _Stream(
        [
            {
                "choices": [
                    {"delta": {"content": "Checking the file."}, "finish_reason": None}
                ]
            },
            _call_delta(),
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
    )
    out = list(_provider()._handle_stream(stream, time.perf_counter()))
    assert out[0] == "Checking the file."
    assert json.loads(out[-1])["content"] == "Checking the file."
    assert not stream.closed


def test_gpu_stream_is_left_alone_after_a_call():
    import time

    provider = _provider()
    provider._last_model = "Gemma-4-E4B-it-GGUF"
    stream = _Stream(
        [
            _call_delta(),
            {"choices": [{"delta": {"content": "Done."}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
    )
    list(provider._handle_stream(stream, time.perf_counter()))
    assert not stream.closed
