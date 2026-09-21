# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply the output-token limit cut off is never the final answer.

On TheRock benchmark tasks tr-8319-cb and tr-7998-cb, fireworks.deepseek-v4p1-flash
spent its whole 8192-token output budget reasoning. The provider returned that
unfinished reasoning (a plan, cut off mid-sentence) as the reply, and the loop
accepted it as the answer after ~30 of 150 steps, with no file changed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import _MAX_UNFINISHED_ANSWER_REPROMPTS, Agent
from gaia.agents.base.verification import strip_verification_scope
from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.llm.providers.lemonade import LemonadeProvider

# Opening and closing paragraphs of the tr-8319-cb reply, verbatim.
TR8319_CB_REPLY = (
    "Now I understand the structure. The fix: gfx90a is postsubmit-only, but "
    "on PRs with `ci:run-all-archs` it gets selected (all families) and "
    "tested. It should only be tested when the gfx90a label is explicitly "
    "added.\n\n"
    "The cleanest fix consistent with the existing design: add "
    "`trigger_test_label_only: True` to gfx90a's linux entry in the "
    "postsubmit matrix. That flag already exists and does exactly this — "
    "only run tests when the family's label is present on the PR, bypassed on "
    "workflow_dispatch.\n\n"
    "Proceed baseline suite first then edits sequential verify compile "
    "each(python-m py_com pile or read_file validation auto reports syntax "
    "errors good!).\n\n"
    "Finally rerun full targeted"
)

EDIT_CALL = json.dumps(
    {
        "thought": "",
        "tool": "edit_file",
        "tool_args": {
            "file_path": "build_tools/github_actions/amdgpu_family_matrix.py",
            "old_content": '"gfx90a": {',
            "new_content": '"gfx90a": {"trigger_test_label_only": True,',
        },
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
        a.console.confirm_tool_execution = MagicMock(return_value=True)
        return a


def _register_edit_file(agent):
    edits = []

    def _edit_file(file_path, old_content, new_content):
        edits.append(file_path)
        return {"status": "success", "file_path": file_path}

    agent._instance_tools = {
        "edit_file": {
            "name": "edit_file",
            "description": "stub",
            "parameters": {
                "file_path": {"type": "string", "required": True},
                "old_content": {"type": "string", "required": True},
                "new_content": {"type": "string", "required": True},
            },
            "function": _edit_file,
            "atomic": True,
        }
    }
    return edits


def _stub_chat(agent, *replies):
    """``replies`` are ``(text, finish_reason)`` pairs, served in order."""
    replies = list(replies)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        text, finish_reason = replies.pop(0)
        resp = MagicMock()
        resp.text = text
        resp.stats = {}
        resp.finish_reason = finish_reason
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _final(result):
    return strip_verification_scope(result["result"]).strip()


def test_cut_off_plan_is_reprompted_and_work_continues(agent):
    edits = _register_edit_file(agent)
    sent = _stub_chat(
        agent,
        (TR8319_CB_REPLY, "length"),
        (EDIT_CALL, "tool_calls"),
        ("Added trigger_test_label_only to gfx90a.", "stop"),
    )

    result = agent.process_query("Fix gfx90a PR testing", max_steps=20)

    assert len(sent) == 3
    correction = sent[1][-1]["content"]
    assert "cut off" in correction and "output-token limit" in correction
    assert edits == ["build_tools/github_actions/amdgpu_family_matrix.py"]
    assert _final(result) == "Added trigger_test_label_only to gfx90a."


def test_cut_off_reprompts_are_bounded_per_turn(agent):
    replies = [(TR8319_CB_REPLY, "length")] * (_MAX_UNFINISHED_ANSWER_REPROMPTS + 3)
    sent = _stub_chat(agent, *replies)

    agent.process_query("Fix gfx90a PR testing", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1


CONTROLS = [
    pytest.param(
        "Added `trigger_test_label_only: True` to gfx90a's linux entry and "
        "updated the two tests that cover it; all 41 tests pass.",
        id="genuine-final-answer",
    ),
    pytest.param(
        "I could not finish this. I found the cause (gfx90a lacks "
        "`trigger_test_label_only`), but the edit was rejected twice.\n\n"
        "What remains:\n- Add the flag to gfx90a's linux entry\n"
        "- Update tests/test_amdgpu_family_matrix.py",
        id="honest-could-not-finish",
    ),
    pytest.param(
        "gfx90a is AMD's CDNA 2 architecture, used by the MI200 series.",
        id="conversational-reply",
    ),
]


@pytest.mark.parametrize("answer", CONTROLS)
def test_complete_replies_are_not_reprompted(agent, answer):
    sent = _stub_chat(agent, (answer, "stop"))

    result = agent.process_query("What about gfx90a?", max_steps=10)

    assert len(sent) == 1
    assert _final(result) == answer


# ---------------------------------------------------------------------------
# The finish reason reaches the agent
# ---------------------------------------------------------------------------


def _provider(backend_response):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = backend_response
        return LemonadeProvider(model="Gemma-4-E4B-it-GGUF")


def test_provider_reports_a_cut_off_plain_reply():
    provider = _provider(
        {
            "choices": [
                {
                    "message": {"content": "", "reasoning_content": "The fix: add"},
                    "finish_reason": "length",
                }
            ]
        }
    )

    text = provider.chat([{"role": "user", "content": "q"}], stream=False)

    assert text == "The fix: add"
    assert provider.get_last_finish_reason() == "length"


def test_provider_reports_a_cut_off_stream():
    provider = _provider(
        iter(
            [
                {"choices": [{"delta": {"content": "The fix: "}}]},
                {"choices": [{"delta": {"content": "add"}, "finish_reason": "length"}]},
            ]
        )
    )

    text = "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert text == "The fix: add"
    assert provider.get_last_finish_reason() == "length"


def _sdk(client):
    with patch("gaia.chat.sdk.create_client", return_value=client):
        sdk = AgentSDK(config=AgentConfig())
    sdk.get_stats = lambda: {}
    return sdk


def test_sdk_carries_finish_reason_on_send_messages():
    client = MagicMock()
    client.chat.return_value = "The fix: add"
    client.get_last_usage.return_value = None
    client.get_last_finish_reason.return_value = "length"

    response = _sdk(client).send_messages([{"role": "user", "content": "q"}])

    assert response.finish_reason == "length"


def test_sdk_carries_finish_reason_on_the_stream_terminator():
    client = MagicMock()
    client.chat.return_value = iter(["The fix: ", "add"])
    client.get_last_finish_reason.return_value = "length"

    responses = list(
        _sdk(client).send_messages_stream([{"role": "user", "content": "q"}])
    )

    assert responses[-1].is_complete
    assert responses[-1].finish_reason == "length"
