# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The image-generation answer guard acts only on a recorded ``generate_image`` call.

Issue #4582: an answer that merely mentioned image generation was discarded and
the model was ordered to generate an image of the user's question.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.verification import strip_verification_scope

CAPABILITY_ANSWER = (
    "I can answer questions about your documents, search the web, and I can "
    "generate images with Stable Diffusion when you ask for one."
)
CONDITIONAL_CLAIM = "I can generate images when the --sd flag is active."


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


def _script(agent, *responses):
    """Make the model reply with ``responses`` in order; return the chat mock."""
    queue = list(responses)
    chat = MagicMock()

    def _send(*_, **__):
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return chat


def _register_generate_image(agent, *, status: str = "success") -> None:
    result = (
        {"status": "success", "image_path": "/tmp/img.png", "model": "SDXL-Turbo"}
        if status == "success"
        else {"status": "error", "error": "SD backend not available"}
    )
    agent._instance_tools = {
        **agent._tools_registry,
        "generate_image": {
            "name": "generate_image",
            "description": "Generate an image from a text prompt.",
            "parameters": {"prompt": {"type": "string", "required": True}},
            "function": lambda prompt="", _r=result: _r,
            "atomic": True,
        },
    }


def _answer(text: str) -> str:
    return json.dumps({"answer": text})


def _generate_call() -> str:
    return json.dumps({"tool": "generate_image", "tool_args": {"prompt": "a forest"}})


def _sent_text(chat) -> str:
    """Everything the agent sent to the model across the turn."""
    return "\n".join(str(call) for call in chat.send_messages.call_args_list)


def _final(result) -> str:
    return strip_verification_scope(result["result"]).strip()


class TestCapabilityAnswerIsNotOverridden:
    def test_capability_question_answer_reaches_the_user(self, agent):
        """#4582: "What can you do?" must not turn into a forced image."""
        _register_generate_image(agent)
        chat = _script(agent, _answer(CAPABILITY_ANSWER))

        result = agent.process_query("What can you do?", max_steps=10)

        assert _final(result) == CAPABILITY_ANSWER
        assert chat.send_messages.call_count == 1
        assert "generate_image tool call" not in _sent_text(chat)

    def test_conditional_claim_without_a_call_is_left_alone(self, agent):
        """No recorded call means there is no outcome to contradict."""
        _register_generate_image(agent)
        chat = _script(agent, _answer(CONDITIONAL_CLAIM))

        result = agent.process_query("Tell me about yourself", max_steps=10)

        assert _final(result) == CONDITIONAL_CLAIM
        assert chat.send_messages.call_count == 1

    def test_agent_without_the_tool_is_never_touched(self, agent):
        assert "generate_image" not in agent._tools_registry
        chat = _script(agent, _answer(CAPABILITY_ANSWER))

        result = agent.process_query("What can you do?", max_steps=10)

        assert _final(result) == CAPABILITY_ANSWER
        assert chat.send_messages.call_count == 1

    def test_call_to_an_unregistered_tool_does_not_arm_the_guard(self, agent):
        """A hallucinated ``generate_image`` call is not a recorded outcome."""
        assert "generate_image" not in agent._tools_registry
        verbose = "I apologize for the confusion. Let me explain what I would do."
        chat = _script(agent, _generate_call(), _answer(verbose))

        result = agent.process_query("What can you do?", max_steps=10)

        assert _final(result) == verbose
        assert chat.send_messages.call_count == 2
        assert "Image generation is not available" not in result["result"]


class TestAnswerMustMatchRecordedOutcome:
    def test_claim_after_failed_call_is_corrected_with_the_outcome(self, agent):
        _register_generate_image(agent, status="error")
        honest = "I tried, but image generation failed: SD backend not available."
        chat = _script(
            agent, _generate_call(), _answer(CONDITIONAL_CLAIM), _answer(honest)
        )

        result = agent.process_query("Draw a forest", max_steps=10)

        assert _final(result) == honest
        assert chat.send_messages.call_count == 3
        assert "generate_image returned an error" in _sent_text(chat)

    def test_claim_after_successful_call_is_corrected_with_the_outcome(self, agent):
        _register_generate_image(agent, status="success")
        honest = "Here is the image of a forest."
        chat = _script(
            agent, _generate_call(), _answer(CONDITIONAL_CLAIM), _answer(honest)
        )

        result = agent.process_query("Draw a forest", max_steps=10)

        assert _final(result) == honest
        assert chat.send_messages.call_count == 3
        assert "generate_image succeeded" in _sent_text(chat)

    def test_correction_is_issued_once_per_turn(self, agent):
        _register_generate_image(agent, status="error")
        chat = _script(
            agent,
            _generate_call(),
            _answer(CONDITIONAL_CLAIM),
            _answer(CONDITIONAL_CLAIM),
        )

        result = agent.process_query("Draw a forest", max_steps=10)

        assert _final(result) == CONDITIONAL_CLAIM
        assert chat.send_messages.call_count == 3

    def test_answer_that_reports_the_outcome_passes(self, agent):
        _register_generate_image(agent, status="success")
        honest = "Here is the image you asked for."
        chat = _script(agent, _generate_call(), _answer(honest))

        result = agent.process_query("Draw a forest", max_steps=10)

        assert _final(result) == honest
        assert chat.send_messages.call_count == 2
