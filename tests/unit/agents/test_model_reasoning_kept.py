# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The model's reasoning is kept, sent back within a job, and never shown as
the answer.

GAIA used to drop it: the provider read ``reasoning_content`` only when a
reply had no text, and the loop deleted inline ``<think>`` blocks. On a
reasoning model most output tokens are reasoning (91% on one DeepSeek call),
so every step re-derived its plan from scratch.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.verification import strip_verification_scope
from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.llm.providers.lemonade import LemonadeProvider

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def _provider(backend_response):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = backend_response
        return LemonadeProvider(model="Gemma-4-E4B-it-GGUF")


def _reply(message, finish_reason="stop"):
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def test_provider_keeps_reasoning_apart_from_the_answer():
    provider = _provider(
        _reply({"content": "It is 4.", "reasoning_content": "2 + 2 is 4."})
    )

    assert provider.chat([{"role": "user", "content": "2+2?"}]) == "It is 4."
    assert provider.get_last_reasoning() == "2 + 2 is 4."


def test_provider_never_returns_reasoning_as_the_answer():
    provider = _provider(
        _reply({"content": "", "reasoning_content": "The fix: add"}, "length")
    )

    assert provider.chat([{"role": "user", "content": "fix it"}]) == ""
    assert provider.get_last_reasoning() == "The fix: add"


def test_provider_recovers_an_answer_routed_into_reasoning_content():
    # Some llama.cpp builds put a *finished* answer in ``reasoning_content``.
    # Dropping it loses the reply; the user gets the empty-response apology.
    provider = _provider(_reply({"content": "", "reasoning_content": "It is 4."}))

    assert provider.chat([{"role": "user", "content": "2+2?"}]) == "It is 4."
    # Promoted to the answer, so it is no longer reasoning — otherwise it would
    # be both shown and resent as `reasoning_content`.
    assert provider.get_last_reasoning() is None


def test_provider_keeps_reasoning_on_a_tool_call_reply():
    provider = _provider(
        _reply(
            {
                "content": None,
                "reasoning_content": "Need the file first.",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "read_it", "arguments": "{}"}}
                ],
            },
            "tool_calls",
        )
    )

    envelope = json.loads(provider.chat([{"role": "user", "content": "q"}]))

    assert envelope["content"] is None
    assert provider.get_last_reasoning() == "Need the file first."


def test_provider_keeps_streamed_reasoning():
    provider = _provider(
        iter(
            [
                {"choices": [{"delta": {"reasoning_content": "Think "}}]},
                {"choices": [{"delta": {"reasoning_content": "hard."}}]},
                {"choices": [{"delta": {"content": "Done."}}]},
            ]
        )
    )

    "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert provider.get_last_reasoning() == "Think hard."


# ---------------------------------------------------------------------------
# SDK
# ---------------------------------------------------------------------------


def _sdk(client):
    with patch("gaia.chat.sdk.create_client", return_value=client):
        sdk = AgentSDK(config=AgentConfig())
    sdk.get_stats = lambda: {}
    return sdk


def _client(reply="ok", reasoning=None, accepts=True):
    client = MagicMock()
    client.chat.return_value = reply
    client.get_last_usage.return_value = None
    client.get_last_reasoning.return_value = reasoning
    client.accepts_reasoning_history = accepts
    return client


HISTORY = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "a", "reasoning_content": "because"},
    {"role": "user", "content": "q2"},
]


def test_sdk_returns_reasoning_with_the_reply():
    response = _sdk(_client("It is 4.", "2 + 2 is 4.")).send_messages(HISTORY[:1])

    assert response.text == "It is 4."
    assert response.reasoning == "2 + 2 is 4."


def test_sdk_sends_reasoning_back_on_assistant_messages():
    client = _client()
    _sdk(client).send_messages(HISTORY)

    sent = client.chat.call_args.kwargs["messages"]
    assistant = [m for m in sent if m["role"] == "assistant"]
    assert assistant == [
        {"role": "assistant", "content": "a", "reasoning_content": "because"}
    ]


def test_sdk_leaves_reasoning_out_for_providers_that_reject_it():
    client = _client(accepts=False)
    _sdk(client).send_messages(HISTORY)

    sent = client.chat.call_args.kwargs["messages"]
    assert all("reasoning_content" not in m for m in sent)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


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
    a._instance_tools = {
        "read_it": {
            "name": "read_it",
            "description": "stub",
            "parameters": {},
            "function": lambda: {"status": "success", "text": "file body"},
            "atomic": True,
        }
    }
    return a


TOOL_CALL = json.dumps(
    {
        "__tool_calls__": [
            {"id": "call_1", "function": {"name": "read_it", "arguments": "{}"}}
        ],
        "finish_reason": "tool_calls",
        "content": None,
    }
)


def _stub_chat(agent, *replies):
    """``replies`` are ``(text, reasoning)`` pairs, served in order."""
    replies = list(replies)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        text, reasoning = replies.pop(0)
        resp = MagicMock()
        resp.text = text
        resp.stats = {}
        resp.reasoning = reasoning
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _assistant_reasoning(conversation):
    return [m.get("reasoning") for m in conversation if m.get("role") == "assistant"]


def test_reasoning_is_stored_and_sent_back_within_a_job(agent):
    sent = _stub_chat(
        agent,
        (TOOL_CALL, "Read the file before answering."),
        ("The file says hello.", "It greets."),
    )

    result = agent.process_query("What does the file say?", max_steps=5)

    tool_turn = next(m for m in sent[1] if m.get("tool_calls"))
    assert tool_turn["reasoning_content"] == "Read the file before answering."
    assert _assistant_reasoning(result["conversation"]) == [
        "Read the file before answering.",
        "It greets.",
    ]
    answer = strip_verification_scope(result["result"]).strip()
    assert answer == "The file says hello."


def test_inline_think_block_is_reasoning_not_answer(agent):
    call = json.dumps({"tool": "read_it", "tool_args": {}})
    sent = _stub_chat(
        agent,
        (f"<think>Read it first.</think>{call}", None),
        ("<think>Short file.</think>It says hello.", None),
    )

    result = agent.process_query("What does the file say?", max_steps=5)

    assert sent[1][1]["role"] == "assistant"
    assert sent[1][1]["reasoning_content"] == "Read it first."
    assert "<think>" not in sent[1][1]["content"]
    assert _assistant_reasoning(result["conversation"]) == [
        "Read it first.",
        "Short file.",
    ]
    answer = strip_verification_scope(result["result"]).strip()
    assert answer == "It says hello."


def test_unclosed_think_block_is_not_the_answer(agent):
    sent = _stub_chat(
        agent,
        ("<think>Still working out the fix, then", None),
        ("It says hello.", None),
    )

    result = agent.process_query("What does the file say?", max_steps=5)

    answer = strip_verification_scope(result["result"]).strip()
    assert "Still working" not in answer
    # Name the outcome rather than only asserting an absence: today the loop
    # ends on the empty-response branch without re-prompting. Continuing a
    # cut-off reply is #4054's job, so that PR must update this assertion.
    assert len(sent) == 1
    assert "empty response" in answer.lower()


def test_a_reply_merely_mentioning_think_is_not_truncated(agent):
    # `<think>` part-way through prose is the model *talking about* the tag.
    # Treating it as a cut-off thought silently deleted the rest of the answer.
    mention = "Wrap reasoning in a <think> tag to hide it from the user."
    _stub_chat(agent, (mention, None))

    result = agent.process_query("How do I hide reasoning?", max_steps=3)

    assert strip_verification_scope(result["result"]).strip() == mention


def _stub_stream(agent, *replies):
    """Same contract as ``_stub_chat``, but over the streaming branch.

    The loop reads ``reasoning`` off the ``is_complete`` chunk only, so a bug
    there is invisible to every non-streaming test.
    """
    replies = list(replies)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        text, reasoning = replies.pop(0)
        for piece in (text, ""):
            chunk = MagicMock()
            chunk.text = piece
            chunk.is_complete = piece == ""
            chunk.stats = {}
            chunk.reasoning = reasoning
            yield chunk

    agent.streaming = True
    agent.chat = MagicMock()
    agent.chat.send_messages_stream = MagicMock(side_effect=_send)
    return sent


def test_streaming_loop_keeps_reasoning_and_sends_it_back(agent):
    call = json.dumps({"tool": "read_it", "tool_args": {}})
    sent = _stub_stream(
        agent,
        (call, "Read the file before answering."),
        ("It says hello.", "It greets."),
    )

    result = agent.process_query("What does the file say?", max_steps=5)

    assert sent[1][1]["reasoning_content"] == "Read the file before answering."
    assert _assistant_reasoning(result["conversation"]) == [
        "Read the file before answering.",
        "It greets.",
    ]
    assert strip_verification_scope(result["result"]).strip() == "It says hello."


PRIOR_REQUEST = [
    {"role": "user", "content": "Earlier question"},
    {
        "role": "assistant",
        "content": "Earlier answer",
        "reasoning_content": "Earlier thinking",
    },
]


def test_earlier_requests_reasoning_is_not_resent_by_default(agent):
    agent.conversation_history = [dict(m) for m in PRIOR_REQUEST]
    sent = _stub_chat(agent, ("Hi.", None))

    agent.process_query("Hello", max_steps=3)

    assert all("reasoning_content" not in m for m in sent[0])
    assert agent.conversation_history[1]["reasoning_content"] == "Earlier thinking"


def test_earlier_requests_reasoning_is_resent_when_enabled(agent):
    agent.resend_reasoning_across_requests = True
    agent.conversation_history = [dict(m) for m in PRIOR_REQUEST]
    sent = _stub_chat(agent, ("Hi.", None))

    agent.process_query("Hello", max_steps=3)

    assert sent[0][1]["reasoning_content"] == "Earlier thinking"
