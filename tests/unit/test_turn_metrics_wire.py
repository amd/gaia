# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-turn record's trip from the agent to the screen.

``turn_metrics.py`` records where a turn's seconds went; these tests pin the
wire path that carries it out — the ``answer`` event, then the canonical
``final.usage``. The half that matters most is the negative one: with
``GAIA_TURN_LOG`` unset, an ordinary user's payload must be byte-identical to
what it was before any of this existed.
"""

import json
import logging
import time

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.turn_metrics import TurnRecorder
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

RUN_ID = "run-turn-metrics"


def _record(total_s: float = 34.5) -> dict:
    """A minimal but structurally real gaia.turn/1 record."""
    return {
        "schema": "gaia.turn/1",
        "turn_id": "a3f1c2d4e5f6",
        "agent": "GaiaAgent",
        "model": "Gemma-4-E4B-it-GGUF",
        "started_at": "2026-08-18T22:47:35.120000+00:00",
        "ended_at": "2026-08-18T22:48:09.640000+00:00",
        "total_s": total_s,
        "steps": 2,
        "prompt": {"fixed_prefill_tokens": 17004, "tools_sent": 66},
        "llm_calls": [{"step": 1, "wall_s": 12.8, "ttft_s": 4.9}],
        "tool_calls": [{"step": 1, "name": "run_shell_command", "wall_s": 2.1}],
        "totals": {
            "llm_s": 28.4,
            "tool_s": 4.8,
            "overhead_s": 1.3,
            "input_tokens_local": 51204,
            "input_tokens_cached_local": 38110,
            "input_tokens_new_local": 13094,
            "output_tokens_server": 210,
        },
    }


def _drain(handler: SSEOutputHandler) -> list:
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _answer_event(handler: SSEOutputHandler) -> dict:
    answers = [e for e in _drain(handler) if e.get("type") == "answer"]
    assert len(answers) == 1, f"expected exactly one answer event, got {answers}"
    return answers[0]


@pytest.fixture(autouse=True)
def _no_ambient_turn_log(monkeypatch):
    """A developer's exported GAIA_TURN_LOG must not flip these tests."""
    monkeypatch.delenv("GAIA_TURN_LOG", raising=False)


class TestAnswerEventPayload:
    def test_off_by_default_payload_is_unchanged(self):
        """The whole point of the gate: an ordinary turn pays nothing.

        Byte-identical, not merely metrics-free — an extra key, a reordered
        one, or a null placeholder would all reach every user of the API.
        """
        handler = SSEOutputHandler()
        handler.print_final_answer("done", total_tokens=210, ttft_seconds=2.1)
        got = _answer_event(handler)

        assert json.dumps(got) == json.dumps(
            {
                "type": "answer",
                "content": "done",
                "elapsed": got["elapsed"],
                "steps": 0,
                "tools_used": 0,
                "tokens": 210,
                "ttft": 2.1,
            }
        )

    def test_a_stashed_record_alone_does_not_leak(self, monkeypatch):
        """The env var is the gate, not the presence of a record.

        A record can be stashed by an agent process that inherited a recorder
        from an earlier configuration; only the live env var may emit it.
        """
        handler = SSEOutputHandler()
        handler.print_turn_metrics(_record())
        handler.print_final_answer("done")
        assert "metrics" not in _answer_event(handler)

    def test_carried_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GAIA_TURN_LOG", str(tmp_path / "turns.jsonl"))
        handler = SSEOutputHandler()
        handler.print_turn_metrics(_record())
        handler.print_final_answer("done", total_tokens=210, ttft_seconds=2.1)

        metrics = _answer_event(handler)["metrics"]
        assert metrics["total_s"] == 34.5
        assert metrics["prompt"]["fixed_prefill_tokens"] == 17004
        assert metrics["totals"]["input_tokens_cached_local"] == 38110
        # The three things the user asked for by name, all on the wire.
        assert metrics["started_at"] and metrics["ended_at"]
        assert metrics["totals"]["output_tokens_server"] == 210

    def test_record_does_not_survive_into_the_next_turn(self, monkeypatch, tmp_path):
        """A stale record on turn two would mislabel it with turn one's cost."""
        monkeypatch.setenv("GAIA_TURN_LOG", str(tmp_path / "turns.jsonl"))
        handler = SSEOutputHandler()
        handler.print_turn_metrics(_record())
        handler.print_final_answer("first")
        _drain(handler)

        handler.print_final_answer("second")
        assert "metrics" not in _answer_event(handler)


class TestTranslatorPassthrough:
    def test_metrics_reaches_final_usage(self):
        translator = CanonicalTranslator(RUN_ID, agent_id="gaia")
        out = translator.translate(
            {
                "type": "answer",
                "content": "done",
                "elapsed": 34.5,
                "steps": 2,
                "tools_used": 1,
                "tokens": 210,
                "ttft": 2.1,
                "metrics": _record(),
            }
        )
        final = [e for e in out if e.get("type") == "final"]
        assert len(final) == 1
        usage = final[0]["usage"]
        # Verbatim: the client decides what of the record to show, so the
        # translator must not curate it down to today's fields.
        assert usage["metrics"] == _record()
        assert usage["steps"] == 2 and usage["tokens"] == 210

    def test_usage_is_unchanged_without_metrics(self):
        translator = CanonicalTranslator(RUN_ID, agent_id="gaia")
        out = translator.translate(
            {
                "type": "answer",
                "content": "done",
                "elapsed": 34.5,
                "steps": 2,
                "tools_used": 1,
                "tokens": 210,
                "ttft": 2.1,
            }
        )
        final = [e for e in out if e.get("type") == "final"][0]
        assert final["usage"] == {
            "steps": 2,
            "tools_used": 1,
            "elapsed": 34.5,
            "tokens": 210,
            "ttft": 2.1,
        }


def _bare_agent() -> Agent:
    """An Agent with nothing initialized but the recorder's own state."""

    class _RecorderOnlyAgent(Agent):
        def _register_tools(self):  # pragma: no cover - never invoked
            pass

    agent = _RecorderOnlyAgent.__new__(_RecorderOnlyAgent)
    agent.chat = None
    agent._turn_recorder = None
    return agent


class TestAgentSideHooks:
    def test_finish_is_idempotent(self, tmp_path):
        """Sealed at the answer, and again at function exit for the paths that
        never printed one. The second call must not write a second record."""
        log = tmp_path / "turns.jsonl"
        agent = _bare_agent()
        agent._turn_recorder = TurnRecorder(
            query="q",
            agent_name="A",
            model_id="m",
            system_prompt="sys",
            tool_schemas=None,
            path=log,
        )

        first = agent._finish_turn_record("a", 1)
        assert first is not None and first["schema"] == "gaia.turn/1"
        assert agent._finish_turn_record("a", 1) is None
        assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_a_broken_console_hook_cannot_fail_the_turn(self):
        """Metrics are a diagnostic. A console that rejects them loses the
        numbers, never the user's answer."""

        class _AngryConsole:
            def print_turn_metrics(self, record):
                raise RuntimeError("no thanks")

        agent = _bare_agent()
        agent.console = _AngryConsole()
        agent._publish_turn_metrics(_record())  # must not raise

    def test_a_console_without_the_hook_is_skipped(self):
        agent = _bare_agent()
        agent.console = object()
        agent._publish_turn_metrics(_record())  # must not raise


def test_seal_happens_before_the_answer_is_printed():
    """total_s must mean "until the answer was on screen".

    Pinned on the source because the ordering is the whole reason the seal was
    moved off the end of ``_process_query_impl`` — a later edit that restores
    the old position would silently change what the number means. Matched on
    the order the two calls appear in, not on exact whitespace, so a reformat
    cannot turn this into a confusing ValueError.
    """
    import inspect
    import re

    src = inspect.getsource(Agent._process_query_impl)
    # Call sites only — the branch also mentions print_final_answer in a comment.
    calls = re.findall(
        r"self\.console\.print_final_answer\(|self\._finish_turn_record\(", src
    )
    printed = [c for c in calls if "print_final_answer" in c]
    assert printed, "the completion branch moved"
    before = calls[: calls.index(printed[0])]
    assert (
        len(before) == 1
    ), f"exactly one seal must precede the printed answer; saw {calls}"


class TestTimingHoldsUpOnTheUnhappyPaths:
    """Every one of these is a turn someone would actually ask about — a
    cancel, a nested tool, a retry — and each used to misreport its own cost.
    """

    @staticmethod
    def _sdk(recorder):
        """An AgentSDK stub with only what the recorder hooks touch."""
        from gaia.chat.sdk import AgentSDK

        sdk = AgentSDK.__new__(AgentSDK)
        sdk.turn_recorder = recorder
        sdk.turn_step = 1
        sdk.log = logging.getLogger("test.sdk")
        return sdk

    def _recorder(self, tmp_path):
        return TurnRecorder(
            query="q",
            agent_name="A",
            model_id="m",
            system_prompt="sys",
            tool_schemas=None,
            path=tmp_path / "turns.jsonl",
        )

    def test_an_abandoned_stream_still_closes_its_call(self, tmp_path):
        """Cancel closes the generator mid-yield, raising GeneratorExit at the
        yield. Without the finally the call stays open and finish() charges its
        seconds to agent overhead instead of to the model."""
        from unittest.mock import MagicMock, patch

        from gaia.chat.sdk import AgentConfig, AgentSDK

        with patch("gaia.chat.sdk.create_client") as create:
            client = MagicMock()
            client.chat.return_value = iter(["one ", "two ", "three"])
            create.return_value = client
            sdk = AgentSDK(config=AgentConfig())
        # An abandoned stream must not reach for stats; a failure here means it
        # did, which on Lemonade is a live HTTP request during a cancel.
        sdk.get_stats = lambda: pytest.fail("cancelled turn fetched stats")

        recorder = self._recorder(tmp_path)
        sdk.turn_recorder = recorder
        sdk.turn_step = 1

        stream = sdk.send_messages_stream([{"role": "user", "content": "q"}])
        next(stream)
        time.sleep(0.02)
        stream.close()  # what console cancellation does

        record = recorder.finish(answer="", steps=1)
        assert len(record["llm_calls"]) == 1, "the abandoned call was never closed"
        assert record["llm_calls"][0]["wall_s"] > 0
        assert record["totals"]["llm_s"] > 0, "its seconds landed in overhead"

    def test_closing_twice_records_one_call(self, tmp_path):
        """The normal path closes the call, then the finally closes it again."""
        recorder = self._recorder(tmp_path)
        sdk = self._sdk(recorder)

        sdk._recorder_begin([{"role": "user", "content": "hi"}], None)
        sdk._recorder_end(stats={"time_to_first_token": 0.5})
        sdk._recorder_end(stats={})

        record = recorder.finish(answer="", steps=1)
        assert len(record["llm_calls"]) == 1
        assert record["llm_calls"][0]["ttft_s"] == 0.5, "the backup close overwrote it"

    def test_the_stats_fetch_is_not_counted_as_model_time(self, tmp_path):
        """Lemonade's get_stats() is an HTTP round-trip. Folded into wall_s it
        would be reported as time the model spent generating."""
        recorder = self._recorder(tmp_path)
        recorder.start_llm_call(1, "prompt")
        recorder.mark_llm_call_end()
        marked = recorder._open_call["wall_s"]

        time.sleep(0.05)  # stands in for the /stats round-trip
        recorder.end_llm_call({"time_to_first_token": 0.1})

        assert recorder.llm_calls[0]["wall_s"] == marked

    def test_a_nested_tool_call_is_timed_once(self, tmp_path):
        """A tool body may call another tool (CodeAgent's orchestration does).
        Timing both would double-count the inner one into tool_s. Even when the
        body reaches for the timed entry point, only the outermost is recorded."""
        recorder = self._recorder(tmp_path)
        agent = _bare_agent()
        agent._turn_recorder = recorder
        agent.console = object()

        def _impl(name, args):
            if name == "outer":
                time.sleep(0.02)
                return agent._execute_tool_timed("inner", {})
            return {"status": "ok"}

        agent._execute_tool = _impl
        agent._is_error_result = staticmethod(lambda r: False)

        agent._execute_tool_timed("outer", {})
        assert [c["name"] for c in recorder.tool_calls] == ["outer"]

    def test_a_raising_tool_is_not_recorded_as_ok(self, tmp_path):
        recorder = self._recorder(tmp_path)
        agent = _bare_agent()
        agent._turn_recorder = recorder

        def _impl(name, args):
            raise RuntimeError("tool blew up")

        agent._execute_tool = _impl
        agent._is_error_result = staticmethod(lambda r: False)

        with pytest.raises(RuntimeError):
            agent._execute_tool_timed("boom", {})
        assert recorder.tool_calls[0]["ok"] is False

    def test_a_broken_recorder_cannot_replace_a_tool_error(self, tmp_path):
        """The user must still see the tool's own failure, not the metrics bug
        that happened while recording it."""
        recorder = self._recorder(tmp_path)
        recorder.record_tool = lambda **kw: (_ for _ in ()).throw(
            ValueError("recorder is broken")
        )
        agent = _bare_agent()
        agent._turn_recorder = recorder

        def _impl(name, args):
            raise RuntimeError("tool blew up")

        agent._execute_tool = _impl
        with pytest.raises(RuntimeError, match="tool blew up"):
            agent._execute_tool_timed("boom", {})

    def test_a_raising_turn_still_seals_its_record(self, tmp_path, monkeypatch):
        """_process_query_impl re-raises on purpose (the wrong-ctx reload its
        caller retries). An unsealed recorder would fold the retry's calls into
        the abandoned turn."""
        log = tmp_path / "turns.jsonl"
        monkeypatch.setenv("GAIA_TURN_LOG", str(log))
        agent = _bare_agent()
        agent._namespaced_agent_id = lambda: "test"
        agent._agent_identity_context = lambda ns: None

        def _boom(*a, **kw):
            agent._turn_recorder = self._recorder(tmp_path)
            raise RuntimeError("ctx reload")

        agent._process_query_impl = _boom

        with pytest.raises(RuntimeError, match="ctx reload"):
            Agent.process_query(agent, "q")

        assert agent._turn_recorder is None, "the recorder outlived the turn"
        assert log.exists(), "the failed turn wrote no record"


class TestThePrefixProxyMatchesWhereTheServerPutsThings:
    """The cached/new split is only meaningful if our rendered proxy orders
    things the way the chat template does. Caught in a live Haiku run: tools
    were appended AFTER the conversation, so the shared prefix stopped at the
    first history message and a turn whose entire system+tools header was
    reusable reported 27% cache hit instead of ~99%."""

    @staticmethod
    def _sdk(recorder):
        from gaia.chat.sdk import AgentSDK

        sdk = AgentSDK.__new__(AgentSDK)
        sdk.turn_recorder = recorder
        sdk.turn_step = 1
        sdk.log = logging.getLogger("test.sdk")
        return sdk

    def _recorder(self, tmp_path):
        return TurnRecorder(
            query="q",
            agent_name="A",
            model_id="m",
            system_prompt="sys",
            tool_schemas=None,
            path=tmp_path / "turns.jsonl",
        )

    def test_growing_history_still_reuses_the_system_and_tools_header(self, tmp_path):
        recorder = self._recorder(tmp_path)
        sdk = self._sdk(recorder)
        system = {"role": "system", "content": "S" * 12000}
        tools = [
            {
                "type": "function",
                "function": {"name": f"t{i}", "description": "d" * 400},
            }
            for i in range(30)
        ]

        # Step 1: system + one user message.
        sdk._recorder_begin([system, {"role": "user", "content": "hello"}], tools)
        sdk._recorder_end(stats={})
        # Step 2: a tool call and its result appended — the header is untouched.
        sdk._recorder_begin(
            [
                system,
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "calling"},
                {"role": "user", "content": "[Tool result: shell] ok"},
            ],
            tools,
        )
        sdk._recorder_end(stats={})

        second = recorder.llm_calls[1]
        assert second["cache_hit_ratio"] > 0.95, (
            "the system+tools header must stay in the shared prefix when history "
            f"grows; got {second['cache_hit_ratio']:.0%}"
        )
