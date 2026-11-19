# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gpt-oss's reply format through the whole stack, to both apps.

The frames are the shapes gpt-oss-20b produced on Lemonade v2026.40.0 (llama.cpp
``--jinja``): reasoning in ``reasoning_content`` deltas, the answer in
``content``, and tool calls as ``tool_calls`` deltas whose arguments arrive in
pieces. A real agent loop, the real Lemonade provider and the real
``SSEOutputHandler`` run on top of them; the handler's events are what the Agent
UI streams, and ``CanonicalTranslator`` turns the same events into what the
TUI's sidecar stream carries. Reasoning must show as thinking and never as
answer text in either.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import GPT_OSS_20B_MODEL_NAME
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

# Reasoning that looks like a tool call, or like gpt-oss's own channel markup.
_TRICKY_REASONING = (
    "The user wants Paris weather. I could write "
    '<tool_call>{"name": "weather_contract_test", "arguments": {"city": "x"}}'
    "</tool_call> but I should call the tool natively. "
    '{"tool": "weather_contract_test", "tool_args": {"city": "Paris"}} '
    "<|channel|>final<|message|> is not for me to write. Calling it now."
)
_ANSWER = "Paris is clear and 18 °C."


def _reasoning(text, size=7):
    return [
        {"choices": [{"delta": {"reasoning_content": text[i : i + size]}}]}
        for i in range(0, len(text), size)
    ]


def _tool_call(call_id, name, args):
    pieces = ["{", '"', "city", '":"', args["city"], '"}']
    frames = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": pieces[0]},
                            }
                        ]
                    }
                }
            ]
        }
    ]
    frames += [
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": p}}]}}
            ]
        }
        for p in pieces[1:]
    ]
    frames.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
    return frames


def _answer(text, reasoning="Tool said clear, 18 C. Answer briefly.", finish="stop"):
    frames = _reasoning(reasoning)
    frames += [
        {"choices": [{"delta": {"content": text[i : i + 5]}}]}
        for i in range(0, len(text), 5)
    ]
    frames.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return frames


class _WeatherAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def weather_contract_test(city: str) -> dict:
            """Weather for a city."""
            self.calls.append(city)
            return {"city": city, "sky": "clear", "temp_c": 18}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _run(clean_registry, *replies, query="What's the weather in Paris?"):
    """Drive one turn; return (result, handler events, canonical events, backend)."""
    del clean_registry
    queue = [list(r) for r in replies]
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_cls:
        backend = backend_cls.return_value
        backend.cloud_model_provider.return_value = None
        backend.slot_count.return_value = 2

        def _complete(**kwargs):
            assert kwargs["stream"] is True
            if not queue:
                raise AssertionError("the model was asked more often than scripted")
            return iter(queue.pop(0))

        backend.chat_completions.side_effect = _complete
        agent = _WeatherAgent(
            silent_mode=True,
            skip_lemonade=True,
            streaming=True,
            model_id=GPT_OSS_20B_MODEL_NAME,
        )
        agent.calls = []
        agent.verification_scope_statement = lambda: None
        handler = SSEOutputHandler()
        agent.console = handler
        result = agent.process_query(query)
    handler.signal_done()
    events = []
    while True:
        event = handler.event_queue.get_nowait()
        if event is None:
            break
        events.append(event)
    translator = CanonicalTranslator(run_id=None, agent_id="gaia")
    canonical = [c for e in events for c in translator.translate(e)]
    canonical += list(translator.flush())
    return result, events, canonical, agent, backend


def _answer_text(events):
    return "".join(
        e.get("content") or "" for e in events if e["type"] in ("chunk", "answer")
    )


def _thinking_text(events):
    return " ".join(e.get("content") or "" for e in events if e["type"] == "thinking")


def _tokens(canonical):
    """What the TUI streams into the answer: canonical ``token`` deltas."""
    tokens = [c for c in canonical if c["type"] == "token"]
    assert all("delta" in c for c in tokens), tokens
    return "".join(c["delta"] for c in tokens)


def test_tool_then_answer_reaches_both_apps_without_reasoning(clean_registry):
    result, events, canonical, agent, _ = _run(
        clean_registry,
        _reasoning(_TRICKY_REASONING)
        + _tool_call("c1", "weather_contract_test", {"city": "Paris"}),
        _answer(_ANSWER),
    )

    assert agent.calls == ["Paris"]  # the native call ran, exactly once
    assert result["result"] == _ANSWER
    shown = _answer_text(events)
    assert _ANSWER in shown
    for leak in ("tool_call", "<|channel|>", "tool_args", "The user wants"):
        assert leak not in shown, leak
    # Reasoning is shown as thinking where the UI shows thinking.
    assert "should call the tool natively" in _thinking_text(events)

    finals = [c for c in canonical if c["type"] == "final"]
    assert len(finals) == 1 and finals[0]["answer"] == _ANSWER
    assert _tokens(canonical) == _ANSWER
    assert [c["type"] for c in canonical].count("tool_call") == 1


def test_reasoning_resent_within_the_turn_is_never_the_answer(clean_registry):
    _, _, _, _, backend = _run(
        clean_registry,
        _reasoning(_TRICKY_REASONING)
        + _tool_call("c1", "weather_contract_test", {"city": "Paris"}),
        _answer(_ANSWER),
    )
    second = backend.chat_completions.call_args_list[1].kwargs["messages"]
    assistant = [m for m in second if m.get("role") == "assistant"][-1]
    # Kept apart as reasoning, so the model keeps its plan and the template
    # never sees analysis text posing as the reply.
    assert "The user wants" not in str(assistant.get("content") or "")
    assert assistant["tool_calls"][0]["function"]["name"] == "weather_contract_test"


def test_cut_off_mid_reasoning_shows_no_reasoning_as_the_answer(clean_registry):
    result, events, canonical, _, _ = _run(
        clean_registry,
        _reasoning("We need to produce a proof. Classic: Euclid. Provide")
        + [{"choices": [{"delta": {}, "finish_reason": "length"}]}],
        _answer("There are infinitely many primes (Euclid)."),
    )
    shown = _answer_text(events)
    assert "Classic: Euclid. Provide" not in shown
    assert "Classic: Euclid. Provide" not in str(result.get("result"))
    assert "Classic: Euclid. Provide" not in _tokens(canonical)


def test_many_tool_calls_in_a_row(clean_registry):
    cities = [f"City{i}" for i in range(12)]
    replies = [
        _reasoning(f"Next is {c}. " * 30)
        + _tool_call(f"c{i}", "weather_contract_test", {"city": c})
        for i, c in enumerate(cities)
    ]
    replies.append(_answer("All twelve are clear."))
    result, events, canonical, agent, _ = _run(
        clean_registry, *replies, query="Weather for twelve cities, one at a time."
    )
    assert agent.calls == cities
    assert result["result"] == "All twelve are clear."
    assert "Next is" not in _answer_text(events)
    assert [c["type"] for c in canonical].count("tool_call") == 12


def test_long_reasoning_streams_as_thinking_not_answer(clean_registry):
    long_thought = "Step: weigh the options carefully.\n" * 2000
    result, events, canonical, _, _ = _run(
        clean_registry, _answer(_ANSWER, reasoning=long_thought), query="Think hard."
    )
    assert result["result"] == _ANSWER
    assert "weigh the options" not in _answer_text(events)
    assert _tokens(canonical) == _ANSWER
    assert "weigh the options" in _thinking_text(events)


def test_stream_dropped_mid_reasoning_never_shows_the_partial_thought(
    clean_registry, monkeypatch
):
    import gaia.agents.base.agent as agent_mod

    partial = _reasoning("Half a thought about the forecast and")

    def _raising():
        yield from partial
        raise ConnectionError("Lemonade closed the stream")

    monkeypatch.setattr(agent_mod.time, "sleep", lambda *_: None, raising=False)
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend_cls:
        backend = backend_cls.return_value
        backend.cloud_model_provider.return_value = None
        backend.slot_count.return_value = 2
        replies = [_raising(), iter(_answer(_ANSWER))]
        backend.chat_completions.side_effect = lambda **_: replies.pop(0)
        agent = _WeatherAgent(
            silent_mode=True,
            skip_lemonade=True,
            streaming=True,
            model_id=GPT_OSS_20B_MODEL_NAME,
        )
        agent.calls = []
        agent.verification_scope_statement = lambda: None
        handler = SSEOutputHandler()
        agent.console = handler
        result = agent.process_query("Weather?")
    handler.signal_done()
    events = []
    while (event := handler.event_queue.get_nowait()) is not None:
        events.append(event)
    assert "Half a thought" not in _answer_text(events)
    assert "Half a thought" not in str(result.get("result"))
