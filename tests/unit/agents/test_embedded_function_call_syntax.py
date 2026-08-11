# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for #2521: embedded tool-call syntax echoed as text on the
non-tool-calling (NPU / FastFlowLM) path.

On that path the model sometimes emits a Python-call-style invocation
instead of the JSON shape the prompt teaches, e.g.::

    remember(fact="TechCrunch emails are low priority", category="preference")

Before this fix ``_extract_embedded_tool_call`` only recognised the JSON
shape (``{"tool": ..., "tool_args": ...}``); the call-syntax text fell
through to the plain-text answer path, so the raw syntax reached the user
and the tool never ran.
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
        a.streaming = False
        return a


def _register_remember(agent, result=None):
    """Register a fake ``remember`` tool in this instance's snapshot only."""
    calls = []
    result = result if result is not None else {"status": "success"}

    def _remember(**kwargs):
        calls.append(kwargs)
        return result

    agent._instance_tools = {
        "remember": {
            "name": "remember",
            "description": "stub",
            "parameters": {
                "fact": {"type": "string", "required": True},
                "category": {"type": "string", "required": False},
            },
            "function": _remember,
            "atomic": True,
        }
    }
    return calls


# ---------------------------------------------------------------------------
# 1. Core guard: bare function-call syntax dispatches the tool
# ---------------------------------------------------------------------------


class TestFunctionCallSyntaxParsing:
    def test_bare_call_is_parsed_as_tool_call(self, agent):
        _register_remember(agent)
        response = (
            'remember(fact="TechCrunch emails are low priority", '
            'category="preference")'
        )
        parsed = agent._parse_llm_response(response)
        assert parsed.get("tool") == "remember"
        assert parsed.get("tool_args") == {
            "fact": "TechCrunch emails are low priority",
            "category": "preference",
        }
        # The raw call syntax must not be echoed as an "answer".
        assert "answer" not in parsed or parsed.get("answer") != response

    def test_unregistered_name_is_left_as_plain_text(self, agent):
        """A word(...) pattern that isn't a registered tool stays plain text."""
        _register_remember(agent)
        response = 'Please call cleanup(now="true") if needed.'
        parsed = agent._parse_llm_response(response)
        assert not parsed.get("tool")
        assert parsed.get("answer") == response

    def test_call_followed_by_prose_still_dispatches(self, agent):
        """A success-claiming sentence after the call must not become the
        final answer -- the tool call is dispatched instead (same class as
        #2520: never let the model claim an unexecuted action succeeded)."""
        _register_remember(agent)
        response = (
            'remember(fact="TechCrunch emails are low priority", '
            'category="preference")\n'
            "I have updated my preferences to treat emails from "
            "TechCrunch as low priority."
        )
        parsed = agent._parse_llm_response(response)
        assert parsed.get("tool") == "remember"
        assert parsed.get("tool_args") == {
            "fact": "TechCrunch emails are low priority",
            "category": "preference",
        }
        # The trailing success claim must not surface as the answer.
        assert parsed.get("answer") != response
        assert "answer" not in parsed or "updated my preferences" not in (
            parsed.get("answer") or ""
        )


# ---------------------------------------------------------------------------
# 2. Unparseable call -> loud, actionable failure (never echoed)
# ---------------------------------------------------------------------------


class TestUnparseableCallRaises:
    def test_unquoted_kwarg_value_raises_actionable_error(self, agent):
        """A registered tool name followed by malformed args must raise --
        not be echoed back to the user as plain text."""
        _register_remember(agent)
        response = "remember(fact=TechCrunch is low priority, category=preference)"
        with pytest.raises(ValueError, match="remember"):
            agent._parse_llm_response(response)

    def test_unterminated_call_raises_actionable_error(self, agent):
        _register_remember(agent)
        response = 'remember(fact="TechCrunch emails are low priority"'
        with pytest.raises(ValueError, match="remember"):
            agent._parse_llm_response(response)


# ---------------------------------------------------------------------------
# 3. Native tool-calling models are unaffected
# ---------------------------------------------------------------------------


class TestNativeToolCallingUnaffected:
    def test_native_sentinel_envelope_unaffected(self, agent):
        """The __tool_calls__ sentinel path is handled before any embedded
        extraction and must keep working unchanged."""
        _register_remember(agent)
        response = json.dumps(
            {
                "__tool_calls__": [
                    {
                        "function": {
                            "name": "remember",
                            "arguments": json.dumps(
                                {
                                    "fact": "TechCrunch emails are low priority",
                                    "category": "preference",
                                }
                            ),
                        }
                    }
                ]
            }
        )
        parsed = agent._parse_llm_response(response)
        assert parsed["tool"] == "remember"
        assert parsed["tool_args"]["category"] == "preference"

    def test_plain_prose_without_any_registered_tool_name_untouched(self, agent):
        _register_remember(agent)
        response = "Sure, I can help with that. What would you like to know?"
        parsed = agent._parse_llm_response(response)
        assert not parsed.get("tool")
        assert parsed.get("answer") == response


# ---------------------------------------------------------------------------
# 4. End-to-end: process_query dispatches the tool and never leaks raw syntax
# ---------------------------------------------------------------------------


class TestProcessQueryDispatchesEmbeddedFunctionCallSyntax:
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

    def test_embedded_call_executes_and_raw_syntax_never_reaches_user(self, agent):
        calls = _register_remember(agent)
        step1 = (
            'remember(fact="TechCrunch emails are low priority", '
            'category="preference")'
        )
        step2 = json.dumps({"thought": "done", "answer": "Got it, noted."})
        self._stub_chat(agent, step1, step2)

        result = agent.process_query(
            "From now on treat anything from TechCrunch as low priority.",
            max_steps=5,
        )

        # The tool actually ran.
        assert calls == [
            {
                "fact": "TechCrunch emails are low priority",
                "category": "preference",
            }
        ]
        text = result.get("result") if isinstance(result, dict) else str(result)
        # Raw internal call syntax never reached the user-visible output.
        assert "remember(" not in (text or "")
