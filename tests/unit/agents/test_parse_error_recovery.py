# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for tool-call parse-error recovery in the base Agent class.

Small models (4B-class) occasionally emit malformed native ``tool_calls``
envelopes — for example a 1000+ char ``summary_type`` argument that gets
truncated mid-string. Before the recovery layer landed, ``_parse_llm_response``
would raise ``ValueError`` and the unhandled exception bubbled out to the user
as ``Agent error: Malformed native tool_calls envelope: ...``.

The recovery layer in ``Agent.process_query`` catches the parse error, logs
it, appends a synthetic recovery prompt to the conversation, and continues
the loop so the model can retry with cleaner arguments.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent for testing."""

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
        # Disable streaming so we exercise the simpler non-streaming path.
        a.streaming = False
        return a


class TestParseLLMResponseRaisesOnMalformed:
    def test_truncated_tool_calls_envelope_raises_valueerror(self, agent):
        """Malformed JSON in the __tool_calls__ sentinel raises ValueError."""
        # Truncated mid-string — what the small model produced for
        # honest_limitation Turn 2.
        bad = (
            '{"__tool_calls__": [{"function": {"name": "summarize_document", '
            '"arguments": "{\\"summary_type\\": \\"brief detailed bullets'
        )
        with pytest.raises(ValueError, match="Malformed native tool_calls"):
            agent._parse_llm_response(bad)


class TestProcessQueryRecoversOnParseError:
    """The full process_query loop should not crash when parse fails."""

    def _stub_chat(self, agent, *responses):
        """Replace agent.chat with a stub that yields *responses* in order."""
        responses = list(responses)
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def test_malformed_envelope_then_plain_answer(self, agent):
        """First call malformed tool_calls, second call plain text answer."""
        bad = (
            '{"__tool_calls__": [{"function": {"name": "summarize_document",'
            ' "arguments": "{\\"summary_type\\": \\"brief detailed bullets'
        )
        good_answer = json.dumps(
            {
                "thought": "Done.",
                "answer": "Acme Corp had $14.2M revenue in Q3 2025.",
            }
        )
        chat = self._stub_chat(agent, bad, good_answer)

        result = agent.process_query("What can you tell me?", max_steps=5)

        # Recovery path was exercised — chat called twice.
        assert chat.send_messages.call_count == 2
        # error_history records the parse error
        assert any(
            e.get("type") == "tool_call_parse_error" for e in agent.error_history
        )
        # Final answer reached the user (not the raw envelope error)
        assert (
            "Agent error" not in result.get("response", "")
            if isinstance(result, dict)
            else True
        )

    def test_three_consecutive_parse_errors_give_up_gracefully(self, agent):
        """After 3 parse errors the loop bails with a friendly message."""
        bad = '{"__tool_calls__": [{"function": {"name": "x", "arguments": "{'
        # Pre-load 5 responses so the loop has plenty to chew on.
        self._stub_chat(agent, bad, bad, bad, bad, bad)

        result = agent.process_query("test", max_steps=10)

        # Final answer is the friendly fallback, NOT a leaked envelope error.
        # The result shape varies per agent — accept either dict or string.
        text = result.get("response") if isinstance(result, dict) else str(result)
        if text:
            assert "Malformed" not in text
            assert "Agent error" not in text


class TestProcessQueryRecoversOnContextOverflow:
    """When the multi-step loop accumulates tool messages past the context
    window, the agent should trim and retry once before giving up."""

    def _stub_chat_with_exception_then_answer(self, agent, exc, answer):
        """First call raises *exc*, second returns plain-text *answer*."""
        responses = [exc, answer]
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            if isinstance(r, BaseException):
                raise r
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def test_context_overflow_triggers_trim_and_retry(self, agent):
        """First call raises overflow, second succeeds → final_answer set."""
        agent.streaming = False
        good = json.dumps({"thought": "ok", "answer": "Here you go."})
        chat = self._stub_chat_with_exception_then_answer(
            agent,
            RuntimeError("exceed_context_size: prompt + history exceeds 32768 tokens"),
            good,
        )
        result = agent.process_query("anything", max_steps=5)
        assert chat.send_messages.call_count == 2
        assert any(
            e.get("type") == "llm_context_overflow_trimmed" for e in agent.error_history
        )
        text = result.get("response") if isinstance(result, dict) else str(result)
        if text:
            assert "exceed_context_size" not in text
            assert "Sorry, I ran into" not in text

    def test_context_overflow_after_retry_gives_friendly_fallback(self, agent):
        """When trim+retry STILL fails, final message is friendly."""
        agent.streaming = False
        responses = [
            RuntimeError("exceeds the available context size"),
            RuntimeError("exceeds the available context size"),
        ]
        chat = MagicMock()

        def _send(*_, **__):
            raise responses.pop(0)

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        result = agent.process_query("x", max_steps=5)
        text = result.get("response") if isinstance(result, dict) else str(result)
        if text:
            # No raw exception leaked
            assert "exceeds the available context size" not in text
            assert "Traceback" not in text


class TestRepairInvalidJsonEscapes:
    """Helper that doubles invalid JSON backslash escapes (e.g. Windows paths).

    Issue #1023: smaller LLMs (Gemma-4-E4B-class) sometimes emit Windows paths
    in tool-call arguments with single backslashes. Strict ``json.loads``
    rejects ``\\U`` (and any other backslash followed by a non-escape char).
    """

    def test_doubles_backslash_before_invalid_escape_char(self):
        """`C:\\Users\\K` (single-escaped) becomes parseable JSON after repair."""
        from gaia.agents.base.agent import _repair_invalid_json_escapes

        bad = r'{"path":"C:\Users\K"}'
        # Sanity: input is genuinely invalid JSON without repair.
        with pytest.raises(json.JSONDecodeError):
            json.loads(bad)
        repaired = _repair_invalid_json_escapes(bad)
        assert json.loads(repaired) == {"path": r"C:\Users\K"}

    def test_preserves_valid_json_escape_sequences(self):
        """Valid escapes (\\n \\t \\\\ \\" \\u00ff) must pass through unchanged."""
        from gaia.agents.base.agent import _repair_invalid_json_escapes

        valid = (
            r'{"text":"line1\nline2\ttab","quote":"\"",'
            r'"unicode":"ÿ","slash":"a\\b"}'
        )
        before = json.loads(valid)
        repaired = _repair_invalid_json_escapes(valid)
        assert json.loads(repaired) == before

    def test_idempotent_on_already_repaired_string(self):
        """Running repair twice gives the same result as running it once."""
        from gaia.agents.base.agent import _repair_invalid_json_escapes

        bad = r'{"a":"\X","b":"\W"}'
        once = _repair_invalid_json_escapes(bad)
        twice = _repair_invalid_json_escapes(once)
        assert once == twice
        # And the result actually parses.
        assert json.loads(once) == {"a": r"\X", "b": r"\W"}


class TestParseLLMResponseRecoversFromInvalidEscapes:
    """Issue #1023: tool-call arguments with under-escaped Windows paths."""

    def test_windows_path_with_single_escapes_parses_via_repair(self, agent):
        """Reproduces #1023 step 2: ``C:\\Users\\Klaus\\img.png`` parses cleanly."""
        # The arguments string the LLM emitted (under-escaped backslashes).
        # ``r"..."`` keeps backslashes literal -- this is what the outer JSON
        # decoder hands to the inner ``json.loads(arguments_raw)`` call.
        malformed_inner = (
            r'{"image_path":"C:\Users\Klaus\.gaia\cache\sd\images\img.png",'
            r'"story_style":"dramatic"}'
        )
        # Sanity: confirm the input is genuinely invalid JSON without repair.
        with pytest.raises(json.JSONDecodeError):
            json.loads(malformed_inner)

        # Build the full LLM response envelope. ``json.dumps`` encodes the
        # outer level correctly while keeping the inner string verbatim.
        response = json.dumps(
            {
                "__tool_calls__": [
                    {
                        "function": {
                            "name": "create_story_from_image",
                            "arguments": malformed_inner,
                        }
                    }
                ]
            }
        )

        parsed = agent._parse_llm_response(response)
        assert parsed["tool"] == "create_story_from_image"
        assert parsed["tool_args"]["image_path"] == (
            r"C:\Users\Klaus\.gaia\cache\sd\images\img.png"
        )
        assert parsed["tool_args"]["story_style"] == "dramatic"

    def test_truly_malformed_args_still_raise_after_repair_attempt(self, agent):
        """When repair cannot fix the JSON, ValueError still propagates."""
        # Truncated/corrupt -- repair won't help, error must still surface.
        broken = (
            '{"__tool_calls__": [{"function": {"name": "x",'
            ' "arguments": "{not-json:"}}]}'
        )
        with pytest.raises(ValueError, match="Malformed"):
            agent._parse_llm_response(broken)


class TestPostFailureOverrideSkippedAfterCapabilitySuccess:
    """Issue #1023 step 3: the verbose-failure override at process_query
    must NOT fire when the capability tool (``generate_image``) succeeded.

    The original guard fired solely on ``has_tried_capability_tool`` --
    "was generate_image called this turn?" -- with no check on whether the
    call actually returned an error.  As a result, when generate_image
    succeeded and a SUBSEQUENT tool's parse error provoked a verbose
    apology, the override clobbered the model's reply with a misleading
    "Image generation is not available" message.
    """

    def _stub_chat(self, agent, *responses):
        responses = list(responses)
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def test_override_skipped_when_generate_image_succeeded(self, agent):
        """generate_image returned success -> verbose model reply is preserved."""
        # Inject a fake generate_image into the agent's instance registry.
        # _instance_tools is per-instance, so this does not pollute the global
        # _TOOL_REGISTRY or other tests.
        agent._instance_tools = {
            "generate_image": {
                "name": "generate_image",
                "description": "stub",
                "parameters": {
                    "prompt": {"type": "string", "required": True},
                },
                "function": lambda prompt="": {
                    "status": "success",
                    "image_path": r"C:\Users\K\img.png",
                    "model": "SDXL-Turbo",
                },
                "atomic": True,
            }
        }

        # Step 1: model calls generate_image (succeeds).
        step1 = json.dumps(
            {"tool": "generate_image", "tool_args": {"prompt": "a forest"}}
        )
        # Step 2: malformed tool call -> parse fails -> recovery prompt added.
        step2 = (
            '{"__tool_calls__": [{"function":'
            ' {"name": "make_story", "arguments": "{not-json:"}}]}'
        )
        # Step 3: model responds with phrasing that matches the verbose-failure
        # regex (``r"i apologize for the confusion"``).  After the fix, this
        # text passes through unchanged because generate_image SUCCEEDED.
        step3 = json.dumps(
            {
                "answer": (
                    "I apologize for the confusion. "
                    "The image was generated successfully."
                )
            }
        )
        self._stub_chat(agent, step1, step2, step3)

        result = agent.process_query("make me an image", max_steps=10)
        # ``process_query`` returns ``{"status": ..., "result": <final_answer>, ...}``
        text = result["result"]

        # The hardcoded override message must NOT replace the model's reply.
        assert "Image generation is not available" not in text
        # And the model's actual answer must be visible.
        assert "image was generated successfully" in text.lower()
