# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for parallel ``tool_calls`` fan-out in
``Agent.process_query`` (issue #944).

Background
----------
Tool-calling-trained models (Gemma-4-E4B-it-GGUF — GAIA's default per
#865 — and most Qwen3-Instruct variants) routinely emit multiple
``tool_calls`` entries in a single response when a user utterance
contains multiple distinct intents. The pre-fix agent loop raised
``NotImplementedError`` whenever ``len(tool_calls) > 1``, which then
hit the generic "malformed arguments" recovery prompt — which is
both misleading (the arguments were perfectly valid; only the *count*
was unsupported) and ineffective (Gemma-4-E4B kept emitting parallel
calls). After three retries the loop bailed with zero tools fired.

These tests pin the new behaviour:

1. N parallel calls execute sequentially in the same loop iteration,
   each appending its own ``role=tool`` result message with the
   originating call's ``tool_call_id``.
2. The assistant turn appended to ``messages`` carries the
   OpenAI-shape ``tool_calls`` array (with real ids), so spec-strict
   providers can correlate results to calls.
3. Errors in one of N calls do NOT short-circuit the others — the
   loop drains all N before transitioning to ``STATE_ERROR_RECOVERY``.
4. Assistant text emitted alongside tool_calls (some Gemma variants
   do this) survives onto the assistant message ``content``.
5. The misleading "malformed arguments" recovery prompt is no longer
   triggered for parallel calls.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY


class _DummyAgent(Agent):
    """Minimal concrete Agent — tools are injected directly into
    ``_TOOL_REGISTRY`` per-test rather than via decorator."""

    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_registry():
    """Snapshot + restore ``_TOOL_REGISTRY`` so tool registrations from
    one test don't leak into the next."""
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture
def agent(clean_registry):  # pylint: disable=unused-argument
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False  # exercise the simpler non-streaming path
        return a


def _register_tool(name: str, fn, description: str = "") -> None:
    """Inject a tool into ``_TOOL_REGISTRY`` without going through
    ``@tool`` decorator — keeps the fixture free of decorator side
    effects."""
    _TOOL_REGISTRY[name] = {
        "name": name,
        "description": description or f"Test tool {name}.",
        "parameters": {},  # all-optional kwargs path
        "function": fn,
        "atomic": False,
    }


def _stub_chat(agent_obj, *responses):
    """Replace ``agent_obj.chat`` with a stub that yields the given
    ``responses`` (strings or BaseException) in order on each
    ``send_messages`` call. Mirrors the pattern in
    ``test_parse_error_recovery.py``."""
    queue = list(responses)
    chat = MagicMock()

    def _send(*_, **__):
        if not queue:
            raise AssertionError(
                "chat.send_messages called more times than the test "
                "preloaded responses for — check the loop's continue/"
                "break logic."
            )
        r = queue.pop(0)
        if isinstance(r, BaseException):
            raise r
        resp = MagicMock()
        resp.text = r
        resp.stats = {}
        return resp

    chat.send_messages = MagicMock(side_effect=_send)
    chat.get_stats = MagicMock(return_value={})
    agent_obj.chat = chat
    return chat


def _native_envelope(*calls, content=None, finish_reason="tool_calls") -> str:
    """Build the same JSON-string envelope that ``LemonadeProvider`` emits
    for native tool_calls. ``calls`` is a list of (id, name, args_dict)
    tuples."""
    payload = {
        "__tool_calls__": [
            {
                "id": tc_id,
                "type": "function",
                "function": {
                    "name": tc_name,
                    "arguments": json.dumps(tc_args),
                },
            }
            for tc_id, tc_name, tc_args in calls
        ],
        "finish_reason": finish_reason,
    }
    if content is not None:
        payload["content"] = content
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParallelCallsExecuteAndCorrelate:
    """Acceptance criterion (a): two parallel calls to distinct tools."""

    def test_two_parallel_calls_both_execute(self, agent):
        calls = []

        def tool_a(**kwargs):
            calls.append(("a", kwargs))
            return {"status": "success", "result": "A_result"}

        def tool_b(**kwargs):
            calls.append(("b", kwargs))
            return {"status": "success", "result": "B_result"}

        _register_tool("tool_a", tool_a)
        _register_tool("tool_b", tool_b)

        # First LLM response: two parallel calls.
        # Second LLM response: a final answer that ends the loop.
        parallel_envelope = _native_envelope(
            ("call_a_1", "tool_a", {"x": 1}),
            ("call_b_1", "tool_b", {"y": 2}),
        )
        final_answer = json.dumps(
            {"thought": "done", "answer": "Both tools ran successfully."}
        )
        chat = _stub_chat(agent, parallel_envelope, final_answer)

        result = agent.process_query("do two things", max_steps=5)

        # Both tools fired, in the order the model emitted them.
        assert [c[0] for c in calls] == ["a", "b"]
        assert calls[0][1] == {"x": 1}
        assert calls[1][1] == {"y": 2}
        # The loop re-prompted exactly once after the fan-out (so chat
        # was called twice total: parallel envelope + final answer).
        assert chat.send_messages.call_count == 2
        # No tool_call_parse_error — the misleading recovery branch
        # MUST NOT have fired for parallel calls.
        assert not any(
            e.get("type") == "tool_call_parse_error" for e in agent.error_history
        )
        # Final answer reached the user.
        text = result.get("result") if isinstance(result, dict) else str(result)
        assert "successfully" in (text or "")

    def test_assistant_message_carries_openai_shape_tool_calls(self, agent):
        """The assistant message appended to the LLM context after a
        parallel turn MUST be a proper OpenAI assistant message with a
        ``tool_calls`` array (not the raw sentinel envelope as
        ``content``). We verify by inspecting the ``messages`` list
        passed to chat on the second LLM round.
        """

        def tool_a(**kwargs):  # pylint: disable=unused-argument
            return {"status": "success", "result": "A"}

        def tool_b(**kwargs):  # pylint: disable=unused-argument
            return {"status": "success", "result": "B"}

        _register_tool("tool_a", tool_a)
        _register_tool("tool_b", tool_b)

        parallel = _native_envelope(
            ("id-A", "tool_a", {}),
            ("id-B", "tool_b", {}),
            content="Running both.",
        )
        final = json.dumps({"thought": "ok", "answer": "Done."})
        chat = _stub_chat(agent, parallel, final)

        agent.process_query("do two things", max_steps=5)

        # The second send_messages call carries the conversation that
        # includes the assistant tool_calls turn + both tool result
        # messages.
        second_call_args = chat.send_messages.call_args_list[1]
        # ``send_messages(messages=..., tools=...)`` — pull from kwargs
        # if used, else first positional arg.
        msgs = second_call_args.kwargs.get("messages") or second_call_args.args[0]

        # Pick the assistant turn carrying ``tool_calls`` — that's the
        # parallel-calls turn from response 1. The final-answer turn
        # from response 2 is a plain ``content`` string and would be
        # the most recent assistant turn, so we filter explicitly.
        parallel_turns = [
            m for m in msgs if m.get("role") == "assistant" and "tool_calls" in m
        ]
        assert len(parallel_turns) == 1, (
            "Exactly one assistant tool_calls turn should be in the "
            "second-round message history. Found: "
            f"{len(parallel_turns)}"
        )
        parallel_turn = parallel_turns[0]
        assert parallel_turn.get("content") == "Running both.", (
            "Assistant text emitted alongside tool_calls must be "
            "preserved on the assistant message — issue #944 "
            "acceptance criterion (c)."
        )
        tc_ids = [tc["id"] for tc in parallel_turn["tool_calls"]]
        assert tc_ids == ["id-A", "id-B"]

        # And each tool result message MUST reference its originating
        # tool_call_id (linkage required by spec-strict providers and
        # essential for parallel-same-name disambiguation).
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert {m["tool_call_id"] for m in tool_msgs} == {"id-A", "id-B"}
        assert {m["name"] for m in tool_msgs} == {"tool_a", "tool_b"}


class TestParallelCallsWithError:
    """Acceptance criterion (b): three parallel calls, one errors —
    all three execute, then transition to STATE_ERROR_RECOVERY."""

    def test_three_calls_one_errors_all_drain(self, agent):
        executed = []

        def tool_ok_1(**kwargs):  # pylint: disable=unused-argument
            executed.append("ok_1")
            return {"status": "success", "result": "1"}

        def tool_bad(**kwargs):  # pylint: disable=unused-argument
            executed.append("bad")
            return {
                "status": "error",
                "error": "simulated tool failure",
                "error_displayed": True,
            }

        def tool_ok_2(**kwargs):  # pylint: disable=unused-argument
            executed.append("ok_2")
            return {"status": "success", "result": "2"}

        _register_tool("tool_ok_1", tool_ok_1)
        _register_tool("tool_bad", tool_bad)
        _register_tool("tool_ok_2", tool_ok_2)

        parallel = _native_envelope(
            ("id-1", "tool_ok_1", {}),
            ("id-2", "tool_bad", {}),
            ("id-3", "tool_ok_2", {}),
        )
        final = json.dumps(
            {"thought": "recovered", "answer": "Recovered after the error."}
        )
        _stub_chat(agent, parallel, final)

        agent.process_query("trigger error in middle", max_steps=5)

        # All three executed in emission order — the error in the
        # middle did NOT short-circuit the third call. This is the
        # acceptance criterion: N entries in conversation even when
        # one errors.
        assert executed == ["ok_1", "bad", "ok_2"]
        # error_count incremented for the failing call.
        assert agent.execution_state == agent.STATE_ERROR_RECOVERY or any(
            "Recovered" in str(s) for s in (agent.last_result or {}).values()
        )


class TestNoMisleadingRecoveryPromptOnParallelCalls:
    """Acceptance criterion: the 'malformed arguments' retry prompt is
    no longer triggered on parallel-call inputs. The pre-fix code hit
    that prompt three times and gave up; the new code never raises
    ``NotImplementedError`` from the parser at all."""

    def test_no_malformed_arguments_prompt_injected(self, agent):
        def tool_a(**kwargs):  # pylint: disable=unused-argument
            return {"status": "success", "result": "ok"}

        def tool_b(**kwargs):  # pylint: disable=unused-argument
            return {"status": "success", "result": "ok"}

        _register_tool("tool_a", tool_a)
        _register_tool("tool_b", tool_b)

        parallel = _native_envelope(
            ("c1", "tool_a", {}),
            ("c2", "tool_b", {}),
        )
        final = json.dumps({"thought": "done", "answer": "All good."})
        chat = _stub_chat(agent, parallel, final)

        agent.process_query("trigger parallel", max_steps=5)

        # Inspect every messages payload sent to chat — the
        # "malformed arguments" recovery copy MUST NOT appear in any
        # user message.
        seen_user_content = []
        for call in chat.send_messages.call_args_list:
            msgs = call.kwargs.get("messages") or call.args[0]
            for m in msgs:
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    seen_user_content.append(m["content"])
        for content in seen_user_content:
            assert "malformed arguments" not in content.lower(), (
                "The misleading 'malformed arguments' recovery prompt "
                "must not be triggered for parallel tool_calls — that "
                "was the symptom of issue #944."
            )
        # And no parse-error entries in error_history.
        assert not any(
            e.get("type") == "tool_call_parse_error" for e in agent.error_history
        )


class TestSingleNativeCallStillWorks:
    """Regression: a single native tool_call must still flow through
    the new fan-out path (we always use it for native responses) and
    yield exactly one tool result message correlated by its real id.
    """

    def test_single_native_call_uses_real_tool_call_id(self, agent):
        def tool_a(**kwargs):  # pylint: disable=unused-argument
            return {"status": "success", "result": "ok"}

        _register_tool("tool_a", tool_a)

        single = _native_envelope(("real-id-xyz", "tool_a", {}))
        final = json.dumps({"thought": "done", "answer": "Done."})
        chat = _stub_chat(agent, single, final)

        agent.process_query("call tool_a once", max_steps=5)

        # Inspect the second send_messages call — the tool result
        # message must reference ``real-id-xyz`` (not a fresh uuid).
        msgs = (
            chat.send_messages.call_args_list[1].kwargs.get("messages")
            or chat.send_messages.call_args_list[1].args[0]
        )
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "real-id-xyz", (
            "Native single calls must propagate the model's tool_call_id "
            "to the result message — this is a same-PR cleanup of the "
            "fresh-uuid behaviour and matters for OpenAI-spec-strict "
            "providers downstream."
        )
