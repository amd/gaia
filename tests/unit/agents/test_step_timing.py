# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-step timing in the agent trace, and the run summary built from it.

A benchmark run took 5x longer than a reference agent and the trace could not
say why: each step's stats record carried token counts and no timing. These
tests pin the shape of what is stored, from the provider's per-call
measurements through the SDK into the step record and the run summary.
"""

import json
import threading
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.step_timing import StepTimer
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.llm.base_client import LLMClient
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.providers.lemonade import LemonadeProvider

TIMING_FIELDS = {
    "step_seconds": float,
    "llm_seconds": float,
    "tool_seconds": float,
    "overhead_seconds": float,
    "llm_calls": int,
    "tools": list,
}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _call(seconds, ttft=None, finish="stop", completion=None, reasoning=None):
    return {
        "seconds": seconds,
        "ttft_seconds": ttft,
        "finish_reason": finish,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
    }


def _record(step):
    return {"type": "stats", "step": step, "performance_stats": {"x": 1}}


# ─────────────────────────── StepTimer ─────────────────────────────────────


class TestStepRecord:
    def test_record_gets_every_field_and_the_parts_sum_to_the_step(self):
        clock = FakeClock()
        timer = StepTimer(clock=clock)
        timer.begin_step(1)
        # A reply cut off by the token limit, then its continuation.
        timer.record_llm_call(
            _call(3.0, ttft=0.5, finish="length", completion=100, reasoning=40)
        )
        timer.record_llm_call(_call(2.0, ttft=0.2, finish="stop", completion=50))
        timer.record_tool("run_tests", 1.5)
        record = _record(1)
        timer.attach(record)
        clock.now = 10.0
        timer.begin_step(2)

        for key, typ in TIMING_FIELDS.items():
            assert isinstance(record[key], typ), key
        assert record["step_seconds"] == 10.0
        assert record["llm_seconds"] == 5.0
        assert record["tool_seconds"] == 1.5
        assert record["overhead_seconds"] == 3.5
        assert (
            record["llm_seconds"] + record["tool_seconds"] + record["overhead_seconds"]
            == record["step_seconds"]
        )
        assert record["ttft_seconds"] == 0.5
        assert record["finish_reason"] == "stop"
        assert record["reasoning_tokens"] == 40
        assert record["llm_calls"] == 2
        assert record["tools"] == [{"name": "run_tests", "seconds": 1.5}]
        # Tokens after the first, over the time after the first token.
        assert record["output_tok_per_s"] == pytest.approx(
            (99 + 49) / ((3.0 - 0.5) + (2.0 - 0.2)), abs=0.01
        )

    def test_existing_keys_are_left_as_they_were(self):
        clock = FakeClock()
        timer = StepTimer(clock=clock)
        timer.begin_step(1)
        timer.record_llm_call(_call(1.0))
        record = _record(1)
        timer.attach(record)
        timer.finish()
        assert record["type"] == "stats"
        assert record["step"] == 1
        assert record["performance_stats"] == {"x": 1}

    def test_non_streamed_call_has_null_ttft_and_rate_over_wall_time(self):
        clock = FakeClock()
        timer = StepTimer(clock=clock)
        timer.begin_step(1)
        timer.record_llm_call(_call(4.0, completion=200))
        record = _record(1)
        timer.attach(record)
        clock.now = 5.0
        timer.finish()
        assert record["ttft_seconds"] is None
        assert record["output_tok_per_s"] == 50.0

    def test_unreported_tokens_are_null_not_zero(self):
        clock = FakeClock()
        timer = StepTimer(clock=clock)
        timer.begin_step(1)
        timer.record_llm_call(_call(4.0, finish=None))
        record = _record(1)
        timer.attach(record)
        timer.finish()
        assert record["reasoning_tokens"] is None
        assert record["output_tok_per_s"] is None
        assert record["finish_reason"] is None

    def test_approval_wait_is_inside_tool_time_and_named(self):
        clock = FakeClock()
        timer = StepTimer(clock=clock)
        timer.begin_step(1)
        timer.record_tool("run_shell_command", 12.0, waited=10.0)
        record = _record(1)
        timer.attach(record)
        clock.now = 13.0
        timer.finish()
        assert record["tool_seconds"] == 12.0
        assert record["tools"] == [
            {
                "name": "run_shell_command",
                "seconds": 12.0,
                "approval_wait_seconds": 10.0,
            }
        ]

    def test_calls_from_another_thread_are_not_counted(self):
        # A tool body runs on a worker thread; its LLM call is tool time.
        timer = StepTimer(clock=FakeClock())
        timer.begin_step(1)
        worker = threading.Thread(target=timer.record_llm_call, args=(_call(9.0),))
        worker.start()
        worker.join()
        record = _record(1)
        timer.attach(record)
        timer.finish()
        assert record["llm_calls"] == 0
        assert record["llm_seconds"] == 0.0

    def test_a_call_without_wall_time_fails_loudly(self):
        timer = StepTimer(clock=FakeClock())
        timer.begin_step(1)
        with pytest.raises(ValueError, match="seconds"):
            timer.record_llm_call({"finish_reason": "stop"})


class TestRunSummary:
    def test_slowest_steps_are_ordered_and_say_why(self):
        clock = FakeClock()
        timer = StepTimer(clock=clock)
        clock.now = 1.0  # setup before the loop is run overhead
        # (step, wall, llm call, tool seconds)
        plan = [
            (1, 10.0, _call(8.0, finish="tool_calls", completion=400), 1.0),
            (2, 4.0, _call(1.0, finish="tool_calls", completion=50), 2.5),
            (3, 30.0, _call(29.0, finish="length", completion=1500, reasoning=1400), 0),
            (4, 1.0, _call(0.5, finish="stop", completion=10), 0),
        ]
        for step, wall, call, tool_s in plan:
            timer.begin_step(step)
            timer.record_llm_call(call)
            if tool_s:
                timer.record_tool("t", tool_s)
            timer.attach(_record(step))
            clock.now += wall
        summary = timer.finish()

        assert [s["step"] for s in summary["slowest_steps"]] == [3, 1, 2]
        top = summary["slowest_steps"][0]
        assert top == {
            "step": 3,
            "step_seconds": 30.0,
            "dominant": "llm",
            "llm_seconds": 29.0,
            "tool_seconds": 0.0,
            "overhead_seconds": 1.0,
            "output_tokens": 1500,
            "reasoning_tokens": 1400,
            "finish_reason": "length",
            "llm_calls": 1,
            "tools": [],
        }
        assert summary["slowest_steps"][2]["dominant"] == "tools"
        assert summary["wall_seconds"] == 46.0
        assert summary["llm_seconds"] == 38.5
        assert summary["tool_seconds"] == 3.5
        assert summary["overhead_seconds"] == 4.0
        assert summary["llm_calls"] == 4
        assert summary["output_tokens"] == 1960

    def test_finish_is_idempotent(self):
        timer = StepTimer(clock=FakeClock())
        timer.begin_step(1)
        assert timer.finish() is timer.finish()


# ─────────────────────────── AgentSDK → sink ───────────────────────────────


class FakeProvider(LLMClient):
    """A provider that reports what a real one measures, without a network."""

    def __init__(self, replies, delay=0.0, usage=None, finish="stop", ttft=None):
        self.replies = list(replies)
        self.delay = delay
        self.usage = usage
        self.finish = finish
        self.ttft = ttft
        self.streamed = []

    @property
    def provider_name(self):
        return "Fake"

    def generate(self, prompt, model=None, stream=False, **kwargs):
        raise NotImplementedError

    def chat(self, messages, model=None, stream=False, **kwargs):
        self.streamed.append(stream)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if stream:
            return self._stream(reply)
        time.sleep(self.delay)
        return reply

    def _stream(self, reply):
        time.sleep(self.delay)
        yield reply

    def get_performance_stats(self):
        return dict(self.usage or {})

    def get_last_usage(self):
        return self.usage

    def get_last_finish_reason(self):
        return self.finish

    def get_last_ttft_seconds(self):
        return self.ttft


def _sdk(provider):
    chat = AgentSDK.__new__(AgentSDK)
    chat.config = AgentConfig(model="test-model", system_prompt="", show_stats=False)
    chat.chat_history = deque(maxlen=4)
    chat.log = Mock()
    chat.llm_client = provider
    chat.rag = None
    chat.rag_enabled = False
    return chat


class TestSdkReportsEachCall:
    def test_non_streamed_call_reports_wall_time_and_own_usage(self):
        usage = {"completion_tokens": 30, "reasoning_tokens": 12}
        chat = _sdk(FakeProvider(["hi"], delay=0.02, usage=usage, finish="length"))
        calls = []
        chat.llm_call_sink = calls.append

        chat.send_messages([{"role": "user", "content": "x"}])

        assert len(calls) == 1
        call = calls[0]
        assert call["seconds"] >= 0.02
        assert call["ttft_seconds"] is None
        assert call["finish_reason"] == "length"
        assert call["completion_tokens"] == 30
        assert call["reasoning_tokens"] == 12

    def test_streamed_call_reports_the_providers_ttft(self):
        chat = _sdk(FakeProvider(["hi"], delay=0.02, ttft=0.01))
        calls = []
        chat.llm_call_sink = calls.append

        list(chat.send_messages_stream([{"role": "user", "content": "x"}]))

        assert len(calls) == 1
        assert calls[0]["ttft_seconds"] == 0.01
        assert calls[0]["seconds"] >= 0.02
        assert calls[0]["completion_tokens"] is None

    def test_a_failed_call_still_reports_its_seconds(self):
        chat = _sdk(FakeProvider([RuntimeError("context overflow")], usage={}))
        calls = []
        chat.llm_call_sink = calls.append

        with pytest.raises(RuntimeError):
            chat.send_messages([{"role": "user", "content": "x"}])

        assert len(calls) == 1
        assert calls[0]["finish_reason"] is None
        assert isinstance(calls[0]["seconds"], float)


# ─────────────────────────── Lemonade provider ─────────────────────────────


def _provider(backend):
    provider = LemonadeProvider.__new__(LemonadeProvider)
    provider._backend = backend
    provider._model = "test-model"
    provider._last_model = "test-model"
    provider._system_prompt = None
    provider._last_usage = None
    provider._last_finish_reason = None
    provider._last_ttft_seconds = None
    provider._last_streamed = False
    return provider


class TestLemonadeProviderMeasures:
    def test_non_streamed_reply_reports_finish_reason_and_reasoning_tokens(self):
        backend = MagicMock()
        backend.chat_completions.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 200,
                "total_tokens": 211,
                "completion_tokens_details": {"reasoning_tokens": 180},
            },
        }
        provider = _provider(backend)

        provider.chat([{"role": "user", "content": "x"}])

        assert provider.get_last_finish_reason() == "length"
        assert provider.get_last_ttft_seconds() is None
        assert provider.get_last_usage()["reasoning_tokens"] == 180
        assert provider.get_last_usage()["completion_tokens"] == 200

    def test_stream_stamps_ttft_on_a_tool_call_fragment_and_keeps_trailing_usage(
        self,
    ):
        # A tool-call-only reply never yields prose until the very end; its
        # first token is the first tool-call fragment, not the final envelope.
        stream = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {"name": "f", "arguments": "{}"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 9,
                    "total_tokens": 14,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        ]
        backend = MagicMock()
        backend.chat_completions.return_value = iter(stream)
        backend.cloud_model_provider.return_value = None
        backend.get_stats.return_value = {"time_to_first_token": 0.4}
        provider = _provider(backend)

        out = list(
            provider.chat([{"role": "user", "content": "x"}], stream=True, tools=[{}])
        )

        assert out and out[-1].startswith('{"')
        ttft = provider.get_last_ttft_seconds()
        assert isinstance(ttft, float) and ttft >= 0
        assert provider.get_last_finish_reason() == "tool_calls"
        assert provider.get_last_usage()["completion_tokens"] == 9
        assert provider.get_last_usage()["reasoning_tokens"] == 3
        # A local stream keeps /stats, which carries the ttft the UI shows.
        assert provider.get_performance_stats() == {"time_to_first_token": 0.4}


class TestLemonadeClientStreamUsage:
    def _stream(self, monkeypatch, model):
        client = LemonadeClient(verbose=False, ctx_size_override=1024)
        monkeypatch.setattr(client, "_ensure_model_loaded", lambda *a, **k: None)
        monkeypatch.setattr(
            "gaia.daemon.broker_client.model_lease",
            lambda *a, **k: MagicMock(),
        )
        sdk = MagicMock()
        usage = MagicMock()
        usage.model_dump.return_value = {"completion_tokens": 7}
        sdk.chat.completions.create.return_value = [
            SimpleNamespace(id="u", created=0, model=model, choices=[], usage=usage)
        ]
        monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kw: sdk)
        chunks = list(client.chat_completions(model, [], stream=True))
        return chunks, sdk.chat.completions.create.call_args.kwargs

    def test_cloud_stream_asks_for_usage_and_passes_it_through(self, monkeypatch):
        chunks, sent = self._stream(monkeypatch, "fireworks.some-model")
        assert sent["stream_options"] == {"include_usage": True}
        assert chunks[-1]["usage"] == {"completion_tokens": 7}

    def test_local_stream_request_is_unchanged(self, monkeypatch):
        _chunks, sent = self._stream(monkeypatch, "Gemma-4-E4B-it-GGUF")
        assert "stream_options" not in sent


# ─────────────────────────── the agent loop ────────────────────────────────


@pytest.fixture
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _TimedAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass


LLM_DELAY = 0.05
TOOL_DELAY = 0.03


def test_process_query_trace_and_summary_carry_the_breakdown(_clean_registry):
    @tool
    def slow_lookup() -> dict:
        """Look something up slowly."""
        time.sleep(TOOL_DELAY)
        return {"status": "success", "value": 42}

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _TimedAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    replies = [
        json.dumps({"thought": "look", "tool": "slow_lookup", "tool_args": {}}),
        json.dumps({"thought": "done", "answer": "The value is 42."}),
    ]
    provider = FakeProvider(
        replies,
        delay=LLM_DELAY,
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        finish="stop",
    )
    agent.chat = _sdk(provider)

    result = agent.process_query("What is the value?", max_steps=5)

    records = [
        m["content"]
        for m in result["conversation"]
        if m.get("role") == "system"
        and isinstance(m.get("content"), dict)
        and m["content"].get("type") == "stats"
    ]
    assert len(records) == 2
    for rec in records:
        for key, typ in TIMING_FIELDS.items():
            assert isinstance(rec[key], typ), key
        assert rec["performance_stats"]["completion_tokens"] == 20
        assert rec["llm_calls"] == 1
        assert rec["llm_seconds"] >= LLM_DELAY
        assert rec["ttft_seconds"] is None
        assert rec["reasoning_tokens"] is None
        assert rec["finish_reason"] == "stop"
        # Overhead is the remainder of the rounded parts, so this is exact at
        # the reported precision, not merely close.
        parts = rec["llm_seconds"] + rec["tool_seconds"] + rec["overhead_seconds"]
        assert round(parts, 4) == rec["step_seconds"]
    assert records[0]["tools"][0]["name"] == "slow_lookup"
    assert records[0]["tool_seconds"] >= TOOL_DELAY
    assert records[1]["tools"] == []

    summary = result["timing_summary"]
    assert summary["llm_calls"] == 2
    assert summary["output_tokens"] == 40
    assert summary["llm_seconds"] == pytest.approx(
        sum(r["llm_seconds"] for r in records), abs=1e-3
    )
    assert (
        round(
            summary["llm_seconds"]
            + summary["tool_seconds"]
            + summary["overhead_seconds"],
            4,
        )
        == summary["wall_seconds"]
    )
    slowest = summary["slowest_steps"]
    assert [s["step"] for s in slowest] == sorted(
        (1, 2), key=lambda n: -records[n - 1]["step_seconds"]
    )
    # The turn is over, so the SDK no longer reports into it.
    assert agent.chat.llm_call_sink is None
