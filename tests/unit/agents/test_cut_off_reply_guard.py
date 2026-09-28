# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply the output-token limit cut off is never the final answer.

All three failed TheRock benchmark runs (tr-7998, tr-7998-cb, tr-8319-cb) ended
on a fireworks.deepseek-v4p1-flash reply that used the whole 8192-token output
budget. The provider dropped ``finish_reason=length``, so the loop took each
cut-off reply as the final answer after ~30 of 150 steps: two were unfinished
reasoning handed back as text, one a tool call cut off mid-argument.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _CUT_OFF_FAILURE_ANSWER,
    _MAX_CUT_OFF_CONTINUATIONS,
    Agent,
)
from gaia.agents.base.verification import strip_verification_scope
from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.llm.providers.lemonade import LemonadeProvider

# The final model replies of the three failed runs. tr-7998 is verbatim; the
# other two are 38-39K characters, so their opening and closing paragraphs are
# kept verbatim.
TR7998_REPLY = (
    "Baseline confirmed empirically just now:\n\n"
    "**58 passed** (+19 subtests), rc=0 \u2014 recorded above from an actual "
    "run this turn (`pytest -q`).\n\n"
    "Now let me make the fix real rather than describing it. First change "
    "\u2014 teach the loader about components that have no node of their own:"
    "\n\n<\uff5cDSML\uff5c calls>\n"
    '<\uff5cDSML\uff5c invoke name="edit_file">\n'
    '<\uff5cDSML\uff5c parameter name="file_path" string="true">'
    "/Users/Shared/gaia-bench-work/runs-therock/fireworks_deepseek-v4p1-flash/"
    "tr-7998/test_tools/determine_rocm_test_dependencies.py"
    "</\uff5cDSML\uff5c parameter>\n"
    '<\uff5cDSML\uff5c parameter name="old_content" string="true">Algorithm\n'
    "---------\n1. Load the consumer graph.\n"
    "2. For each changed subproject, walk its `consumers` edges to a depth set "
    "by the\n   component's policy level (see the level ladder below).\n"
    "3. UNION the per-subproject walk results across all changed subprojects"
)
TR7998_CB_REPLY = (
    "Now I understand the domain. Let me think about the fix.\n\n"
    "The problem: TensileLite is bundled inside hipblaslt and hipsparselt, has "
    "no CMake target, so no node in the consumer graph. A change confined to "
    "TensileLite selects no tests at all.\n\n"
    "Edge interplay:_apply_policies include/exclude none default fine optional "
    "future exclude hipplas possible demonstration unnecessary avoid clutter "
    "keep minimal clean table w explanatory comment block matching file tone "
    "rich commentary"
)
TR8319_CB_REPLY = (
    "Now I understand the structure. The fix: gfx90a is postsubmit-only, but "
    "on PRs with `ci:run-all-archs` it gets selected (all families) and "
    "tested. It should only be tested when the gfx90a label is explicitly "
    "added.\n\n"
    "The cleanest fix consistent with the existing design: add "
    "`trigger_test_label_only: True` to gfx90a's linux entry in the "
    "postsubmit matrix. That flag already exists and does exactly this \u2014 "
    "only run tests when the family's label is present on the PR, bypassed on "
    "workflow_dispatch.\n\n"
    "Proceed baseline suite first then edits sequential verify compile "
    "each(python-m py_com pile or read_file validation auto reports syntax "
    "errors good!).\n\n"
    "Finally rerun full targeted"
)
FAILED_RUN_REPLIES = [
    pytest.param(TR7998_REPLY, id="tr-7998-cut-off-tool-call"),
    pytest.param(TR7998_CB_REPLY, id="tr-7998-cb-cut-off-reasoning"),
    pytest.param(TR8319_CB_REPLY, id="tr-8319-cb-cut-off-reasoning"),
]

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


def _stub_chat_stream(agent, *replies):
    """Same contract as ``_stub_chat``, over the streaming API.

    The loop reads the finish reason off the terminator chunk here, not the
    response object, so the non-streaming tests cannot cover this path — and
    streaming is what the Agent UI runs.
    """
    replies = list(replies)
    sent = []

    def _send_stream(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        text, finish_reason = replies.pop(0)
        for piece in (text[: len(text) // 2], text[len(text) // 2 :]):
            chunk = MagicMock()
            chunk.is_complete = False
            chunk.text = piece
            yield chunk
        terminator = MagicMock()
        terminator.is_complete = True
        terminator.text = ""
        terminator.stats = {}
        terminator.finish_reason = finish_reason
        yield terminator

    agent.chat = MagicMock()
    agent.chat.send_messages_stream = MagicMock(side_effect=_send_stream)
    return sent


def _final(result):
    return strip_verification_scope(result["result"]).strip()


@pytest.mark.parametrize("reply", FAILED_RUN_REPLIES)
def test_cut_off_reply_is_continued_not_answered(agent, reply):
    edits = _register_edit_file(agent)
    sent = _stub_chat(
        agent,
        (reply, "length"),
        (EDIT_CALL, "tool_calls"),
        ("Added trigger_test_label_only to gfx90a.", "stop"),
    )

    result = agent.process_query("Fix gfx90a PR testing", max_steps=20)

    assert len(sent) == 3
    assert sent[1][-2] == {"role": "assistant", "content": reply}
    assert "cut off at the output-token limit" in sent[1][-1]["content"]
    assert edits == ["build_tools/github_actions/amdgpu_family_matrix.py"]
    assert _final(result) == "Added trigger_test_label_only to gfx90a."


def test_cut_off_reply_is_continued_when_streaming(agent):
    agent.streaming = True
    edits = _register_edit_file(agent)
    sent = _stub_chat_stream(
        agent,
        (TR8319_CB_REPLY, "length"),
        (EDIT_CALL, "tool_calls"),
        ("Added trigger_test_label_only to gfx90a.", "stop"),
    )

    result = agent.process_query("Fix gfx90a PR testing", max_steps=20)

    assert len(sent) == 3
    assert sent[1][-2] == {"role": "assistant", "content": TR8319_CB_REPLY}
    assert "cut off at the output-token limit" in sent[1][-1]["content"]
    assert edits == ["build_tools/github_actions/amdgpu_family_matrix.py"]
    assert _final(result) == "Added trigger_test_label_only to gfx90a."


def test_complete_streamed_reply_is_not_reprompted(agent):
    agent.streaming = True
    answer = "gfx90a is AMD's CDNA 2 architecture, used by the MI200 series."
    sent = _stub_chat_stream(agent, (answer, "stop"))

    result = agent.process_query("What about gfx90a?", max_steps=10)

    assert len(sent) == 1
    assert _final(result) == answer


def test_repeated_cut_offs_end_with_an_honest_failure(agent):
    replies = [(TR8319_CB_REPLY, "length")] * (_MAX_CUT_OFF_CONTINUATIONS + 3)
    sent = _stub_chat(agent, *replies)

    result = agent.process_query("Fix gfx90a PR testing", max_steps=20)

    assert len(sent) == _MAX_CUT_OFF_CONTINUATIONS + 1
    assert _final(result) == _CUT_OFF_FAILURE_ANSWER


def test_cut_off_on_the_last_step_is_an_honest_failure(agent):
    sent = _stub_chat(agent, (TR8319_CB_REPLY, "length"))

    result = agent.process_query("Fix gfx90a PR testing", max_steps=1)

    assert len(sent) == 1
    assert _final(result) == _CUT_OFF_FAILURE_ANSWER


CONTROLS = [
    pytest.param(
        "Added `trigger_test_label_only: True` to gfx90a's linux entry, so "
        "it is tested on a pull request only when the gfx90a label is set.",
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
                    "message": {"content": "The fix: add"},
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
