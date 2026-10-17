# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Claude chat backend (ClaudeProvider + AgentSDK routing).

These assert the *shape* of the outgoing Anthropic call — system hoisted out of
the messages array, tools translated (and absent when there are none), no
sampling/stop kwargs leaking through, a claude-* model id — and that tool_use
responses are re-encoded into the exact Lemonade sentinel envelope the agent
loop's parser already understands.
"""

import json
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.llm.providers.lemonade import _NATIVE_TC_KEY, NATIVE_TOOL_CALLS_PREFIX

# ── fake anthropic SDK ──────────────────────────────────────────────────


def _build_fake_anthropic():
    mod = types.ModuleType("anthropic")

    class APIStatusError(Exception):
        def __init__(self, message="boom", status_code=500):
            super().__init__(message)
            self.message = message
            self.status_code = status_code

    class AuthenticationError(APIStatusError):
        pass

    class NotFoundError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class APIConnectionError(Exception):
        pass

    class Anthropic:
        last_init = None

        def __init__(self, **kwargs):
            Anthropic.last_init = kwargs
            self.messages = SimpleNamespace(create=MagicMock())

    mod.Anthropic = Anthropic
    mod.APIStatusError = APIStatusError
    mod.AuthenticationError = AuthenticationError
    mod.NotFoundError = NotFoundError
    mod.RateLimitError = RateLimitError
    mod.APIConnectionError = APIConnectionError
    return mod


@pytest.fixture()
def fake_anthropic(monkeypatch):
    mod = _build_fake_anthropic()
    monkeypatch.setattr("gaia.llm.providers.claude.anthropic", mod)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-unit-test")
    return mod


def _provider(fake_anthropic, **kwargs):
    from gaia.llm.providers.claude import ClaudeProvider

    return ClaudeProvider(**kwargs)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(block_id="toolu_01", name="list_directory", tool_input=None):
    return SimpleNamespace(
        type="tool_use", id=block_id, name=name, input=tool_input or {}
    )


def _response(
    content,
    stop_reason="end_turn",
    input_tokens=10,
    output_tokens=5,
    cache_read=None,
    cache_write=None,
):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    # Left unset by default so the no-cache-fields path (an older anthropic
    # SDK, or a response that predates caching) stays covered.
    if cache_read is not None:
        usage.cache_read_input_tokens = cache_read
    if cache_write is not None:
        usage.cache_creation_input_tokens = cache_write
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=usage,
    )


OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


# ── construction / auth ─────────────────────────────────────────────────


def test_missing_api_key_raises_actionable_error(fake_anthropic, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from gaia.llm.providers.claude import ClaudeProvider

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider()


def test_api_key_goes_to_api_key_kwarg(fake_anthropic):
    _provider(fake_anthropic)
    init = fake_anthropic.Anthropic.last_init
    assert init["api_key"] == "sk-ant-api03-unit-test"
    assert "auth_token" not in init


def test_oauth_token_uses_bearer_auth_and_beta_header(fake_anthropic, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-unit-test")
    _provider(fake_anthropic)
    init = fake_anthropic.Anthropic.last_init
    assert init["auth_token"] == "sk-ant-oat01-unit-test"
    assert init["default_headers"] == {"anthropic-beta": "oauth-2025-04-20"}
    assert "api_key" not in init


def test_non_claude_constructor_model_falls_back_to_default(fake_anthropic):
    from gaia.llm.providers.claude import DEFAULT_CLAUDE_MODEL

    provider = _provider(fake_anthropic, model="Gemma-4-E4B-it-GGUF")
    provider._client.messages.create.return_value = _response([_text_block("hi")])
    provider.chat([{"role": "user", "content": "hi"}])
    call = provider._client.messages.create.call_args.kwargs
    assert call["model"] == DEFAULT_CLAUDE_MODEL


# ── outgoing request shape ──────────────────────────────────────────────


def test_system_hoisted_out_of_messages(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat(
        [
            {"role": "system", "content": "You are GAIA."},
            {"role": "user", "content": "hello"},
        ]
    )
    call = provider._client.messages.create.call_args.kwargs
    assert call["system"][0]["text"] == "You are GAIA."
    assert all(m["role"] != "system" for m in call["messages"])
    assert call["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "hello",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]


def test_tools_translated_to_anthropic_shape(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}], tools=OPENAI_TOOLS)
    call = provider._client.messages.create.call_args.kwargs
    # The cache breakpoint is asserted separately; this is the translation.
    translated = [
        {k: v for k, v in t.items() if k != "cache_control"} for t in call["tools"]
    ]
    assert translated == [
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]


def test_tools_key_absent_when_no_tools(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}], tools=None)
    call = provider._client.messages.create.call_args.kwargs
    assert "tools" not in call


def test_incompatible_kwargs_do_not_reach_anthropic(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat(
        [{"role": "user", "content": "hi"}],
        temperature=0.1,
        top_p=0.9,
        top_k=40,
        stop=["<|im_end|>"],
        frequency_penalty=0.3,
        repeat_penalty=1.1,
    )
    call = provider._client.messages.create.call_args.kwargs
    for banned in (
        "temperature",
        "top_p",
        "top_k",
        "stop",
        "frequency_penalty",
        "repeat_penalty",
    ):
        assert banned not in call


def test_model_arg_ignored_unless_claude(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}], model="Gemma-4-E4B-it-GGUF")
    call = provider._client.messages.create.call_args.kwargs
    assert call["model"].startswith("claude-")


def test_max_tokens_floor_covers_thinking_budget(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}], max_tokens=512)
    call = provider._client.messages.create.call_args.kwargs
    assert call["max_tokens"] >= 8192


def test_empty_messages_after_hoist_fail_loudly(fake_anthropic):
    provider = _provider(fake_anthropic)
    with pytest.raises(ValueError, match="no user/assistant messages"):
        provider.chat([{"role": "system", "content": "only a system prompt"}])


def test_tool_history_translated_to_anthropic_blocks(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("done")])
    provider.chat(
        [
            {"role": "user", "content": "compare these files"},
            {
                "role": "assistant",
                "content": "I'll inspect both.",
                "tool_calls": [
                    {
                        "id": "toolu_a",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "a.txt"}',
                        },
                    },
                    {
                        "id": "toolu_b",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "b.txt"}',
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_a", "content": "alpha"},
            {"role": "tool", "tool_call_id": "toolu_b", "content": "beta"},
        ],
        tools=OPENAI_TOOLS,
    )
    call = provider._client.messages.create.call_args.kwargs
    assert call["messages"] == [
        {"role": "user", "content": "compare these files"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll inspect both."},
                {
                    "type": "tool_use",
                    "id": "toolu_a",
                    "name": "read_file",
                    "input": {"path": "a.txt"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_b",
                    "name": "read_file",
                    "input": {"path": "b.txt"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_a",
                    "content": "alpha",
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_b",
                    "content": "beta",
                    "cache_control": {"type": "ephemeral"},
                },
            ],
        },
    ]


def test_tool_call_only_assistant_turn_is_not_dropped(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("done")])
    provider.chat(
        [
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "a.txt"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "alpha"},
        ],
        tools=OPENAI_TOOLS,
    )
    call = provider._client.messages.create.call_args.kwargs
    assert call["messages"][1]["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "read_file",
            "input": {"path": "a.txt"},
        }
    ]


def test_unmatched_tool_result_remains_plain_user_text(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("done")])
    provider.chat(
        [
            {"role": "user", "content": "follow the plan"},
            {"role": "assistant", "content": "Running the planned step."},
            {
                "role": "tool",
                "name": "plan_step",
                "tool_call_id": "synthetic-id",
                "content": "complete",
            },
        ]
    )
    call = provider._client.messages.create.call_args.kwargs
    assert call["messages"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "[Tool result: plan_step] complete",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }


def test_interrupted_native_tool_history_fails_loudly(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("done")])
    with pytest.raises(ValueError, match="immediately adjacent.*toolu_1"):
        provider.chat(
            [
                {"role": "user", "content": "read it"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "toolu_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "a.txt"}',
                            },
                        }
                    ],
                },
                {"role": "user", "content": "Actually use b.txt."},
                {
                    "role": "tool",
                    "name": "read_file",
                    "tool_call_id": "toolu_1",
                    "content": "alpha",
                },
            ],
            tools=OPENAI_TOOLS,
        )


# ── tool_choice ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool_choice, expected",
    [("none", {"type": "none"}), ("auto", {"type": "auto"})],
)
def test_tool_choice_maps_to_anthropic_shape(fake_anthropic, tool_choice, expected):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat(
        [{"role": "user", "content": "hi"}], tools=OPENAI_TOOLS, tool_choice=tool_choice
    )
    call = provider._client.messages.create.call_args.kwargs
    assert call["tool_choice"] == expected


def test_tool_choice_absent_unless_set(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}], tools=OPENAI_TOOLS)
    assert "tool_choice" not in provider._client.messages.create.call_args.kwargs


@pytest.mark.parametrize("stream", [False, True])
def test_no_call_request_keeps_tool_history_and_tools(fake_anthropic, stream):
    """The agent's step-limit call: tool history, tools, and no calls allowed.

    Anthropic rejects tool_use/tool_result blocks on a request that defines no
    tools, so the tools stay and ``{"type": "none"}`` forbids calling one.
    """
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = (
        iter([]) if stream else _response([_text_block("Here is what I found.")])
    )
    result = provider.chat(
        [
            {"role": "system", "content": "You are GAIA."},
            {"role": "user", "content": "read a.txt"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "a.txt"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "alpha"},
            {"role": "user", "content": "Give the user your final answer now."},
        ],
        tools=OPENAI_TOOLS,
        tool_choice="none",
        stream=stream,
    )
    if stream:
        list(result)

    call = provider._client.messages.create.call_args.kwargs
    assert call["tool_choice"] == {"type": "none"}
    assert [t["name"] for t in call["tools"]] == ["read_file"]
    assert call["messages"] == [
        {"role": "user", "content": "read a.txt"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_file",
                    "input": {"path": "a.txt"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "alpha"}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Give the user your final answer now.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ]


@pytest.mark.parametrize(
    "tool_choice",
    ["required", "any", {"type": "none"}, {"type": "function", "name": "read_file"}],
)
def test_unsupported_tool_choice_fails_loudly(fake_anthropic, tool_choice):
    provider = _provider(fake_anthropic)
    with pytest.raises(ValueError, match="does not support tool_choice"):
        provider.chat(
            [{"role": "user", "content": "hi"}],
            tools=OPENAI_TOOLS,
            tool_choice=tool_choice,
        )
    provider._client.messages.create.assert_not_called()


def test_tool_choice_without_tools_fails_loudly(fake_anthropic):
    provider = _provider(fake_anthropic)
    with pytest.raises(ValueError, match="without tools"):
        provider.chat([{"role": "user", "content": "hi"}], tool_choice="none")
    provider._client.messages.create.assert_not_called()


# ── response → sentinel envelope ────────────────────────────────────────


def test_tool_use_reencoded_as_lemonade_sentinel(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [
            _text_block("Let me check."),
            _tool_use_block("toolu_9", "read_file", {"path": "C:/tmp/a.txt"}),
        ],
        stop_reason="tool_use",
    )
    result = provider.chat([{"role": "user", "content": "read it"}], tools=OPENAI_TOOLS)
    assert isinstance(result, str)
    assert result.startswith(NATIVE_TOOL_CALLS_PREFIX)
    envelope = json.loads(result)
    assert envelope["finish_reason"] == "tool_calls"
    assert envelope["content"] == "Let me check."
    (call,) = envelope[_NATIVE_TC_KEY]
    assert call["id"] == "toolu_9"
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    # ``arguments`` must be a JSON *string*, exactly like the Lemonade envelope.
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"path": "C:/tmp/a.txt"}


def test_thinking_blocks_never_reach_answer_text(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [
            SimpleNamespace(type="thinking", thinking="secret reasoning"),
            _text_block("The answer is 4."),
        ]
    )
    result = provider.chat([{"role": "user", "content": "2+2?"}])
    assert result == "The answer is 4."


def test_usage_captured_from_response(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [_text_block("ok")], input_tokens=123, output_tokens=45
    )
    provider.chat([{"role": "user", "content": "hi"}])
    usage = provider.get_last_usage()
    assert usage["prompt_tokens"] == 123
    assert usage["completion_tokens"] == 45
    assert usage["total_tokens"] == 168
    assert provider.get_performance_stats() == usage


# ── prompt caching ──────────────────────────────────────────────────────
#
# Anthropic caching is opt-in and silent: with no ``cache_control`` breakpoint
# nothing ever caches, and with no cache fields read back the metrics report a
# 0% hit rate whether or not it worked. Both halves are asserted on the *shape*
# of the outgoing request and the parsed usage — never on "create was called".


def test_system_block_carries_the_cache_breakpoint(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat(
        [
            {"role": "system", "content": "You are GAIA."},
            {"role": "user", "content": "hello"},
        ],
        tools=OPENAI_TOOLS,
    )
    call = provider._client.messages.create.call_args.kwargs
    assert call["system"] == [
        {
            "type": "text",
            "text": "You are GAIA.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # Second breakpoint at the end of the tools segment. Caching gives no
    # partial credit, so without it any drift in the system prompt would throw
    # away the tool schemas too.
    assert call["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert not any("cache_control" in t for t in call["tools"][:-1])


def test_tools_are_still_cached_without_a_system_prompt(fake_anthropic):
    """``generate`` and ``vision`` send no system prompt; the schemas are still
    a stable prefix worth caching."""
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}], tools=OPENAI_TOOLS)
    call = provider._client.messages.create.call_args.kwargs
    assert "system" not in call
    assert call["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_breakpoint_never_mutates_the_caller_tool_list(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    tools = json.loads(json.dumps(OPENAI_TOOLS))
    provider.chat([{"role": "user", "content": "hi"}], tools=tools)
    assert tools == OPENAI_TOOLS, "the agent reuses this list on every call"


def test_no_prefill_breakpoint_without_system_or_tools(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}])
    call = provider._client.messages.create.call_args.kwargs
    assert "system" not in call and "tools" not in call


# ── rolling history breakpoint ──────────────────────────────────────────
#
# Without a marker on the conversation itself, every call of an agent loop
# re-bills the whole growing history as fresh input. The rolling marker on the
# last message lets call N+1 read what call N wrote.

_EPHEMERAL = {"type": "ephemeral"}


def _breakpoints(call: dict) -> int:
    """Every ``cache_control`` in the outgoing request, wherever it sits."""

    def count(node) -> int:
        if isinstance(node, dict):
            return ("cache_control" in node) + sum(count(v) for v in node.values())
        if isinstance(node, list):
            return sum(count(v) for v in node)
        return 0

    return (
        count(call.get("tools")) + count(call.get("system")) + count(call["messages"])
    )


def _run(provider, messages, stream, **kwargs):
    provider._client.messages.create.return_value = (
        iter([]) if stream else _response([_text_block("ok")])
    )
    result = provider.chat(messages, stream=stream, **kwargs)
    if stream:
        list(result)
    return provider._client.messages.create.call_args.kwargs


_MULTI_TURN = [
    {"role": "system", "content": "You are GAIA."},
    {"role": "user", "content": "first question"},
    {"role": "assistant", "content": "first answer"},
    {"role": "user", "content": "second question"},
]


@pytest.mark.parametrize("stream", [False, True])
def test_last_message_carries_the_rolling_breakpoint(fake_anthropic, stream):
    provider = _provider(fake_anthropic)
    call = _run(provider, _MULTI_TURN, stream, tools=OPENAI_TOOLS)

    assert call["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "second question", "cache_control": _EPHEMERAL}
        ],
    }
    # Only the newest message is marked; older ones stay plain so the marker
    # rolls forward instead of piling up.
    assert call["messages"][:-1] == [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]
    # tools + system + last message.
    assert _breakpoints(call) == 3


@pytest.mark.parametrize("stream", [False, True])
def test_rolling_breakpoint_marks_the_last_block_of_list_content(
    fake_anthropic, stream
):
    provider = _provider(fake_anthropic)
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }
    call = _run(
        provider,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {
                "role": "user",
                "content": [image, {"type": "text", "text": "what is this?"}],
            },
        ],
        stream,
    )
    last = call["messages"][-1]["content"]
    assert last[0] == image
    assert last[1] == {
        "type": "text",
        "text": "what is this?",
        "cache_control": _EPHEMERAL,
    }
    assert _breakpoints(call) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_rolling_breakpoint_lands_on_a_trailing_tool_result(fake_anthropic, stream):
    """The agent loop's usual shape: the newest message is a tool result.
    Anthropic accepts ``cache_control`` on ``tool_result`` blocks."""
    provider = _provider(fake_anthropic)
    call = _run(
        provider,
        [
            {"role": "system", "content": "You are GAIA."},
            {"role": "user", "content": "compare a and b"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tid,
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": path}),
                        },
                    }
                    for tid, path in (("toolu_a", "a.txt"), ("toolu_b", "b.txt"))
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_a", "content": "alpha"},
            {"role": "tool", "tool_call_id": "toolu_b", "content": "beta"},
        ],
        stream,
        tools=OPENAI_TOOLS,
    )
    results = call["messages"][-1]["content"]
    assert results == [
        {"type": "tool_result", "tool_use_id": "toolu_a", "content": "alpha"},
        {
            "type": "tool_result",
            "tool_use_id": "toolu_b",
            "content": "beta",
            "cache_control": _EPHEMERAL,
        },
    ]
    assert not any("cache_control" in str(m) for m in call["messages"][:-1])
    assert _breakpoints(call) == 3


@pytest.mark.parametrize(
    "trailing",
    [
        {"type": "thinking", "thinking": "hmm", "signature": "sig"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": ""},
    ],
    ids=["thinking", "redacted_thinking", "empty_text"],
)
def test_rolling_breakpoint_skips_blocks_that_cannot_carry_it(fake_anthropic, trailing):
    provider = _provider(fake_anthropic)
    call = _run(
        provider,
        [
            {"role": "user", "content": "go on"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Partial answer"}, trailing],
            },
        ],
        stream=False,
    )
    last = call["messages"][-1]["content"]
    assert last[0] == {
        "type": "text",
        "text": "Partial answer",
        "cache_control": _EPHEMERAL,
    }
    assert last[1] == trailing
    assert _breakpoints(call) == 1


def test_no_rolling_breakpoint_when_no_block_can_carry_it(fake_anthropic):
    provider = _provider(fake_anthropic)
    call = _run(
        provider,
        [
            {"role": "user", "content": "go on"},
            {
                "role": "assistant",
                "content": [{"type": "redacted_thinking", "data": "opaque"}],
            },
        ],
        stream=False,
    )
    assert _breakpoints(call) == 0


def test_no_rolling_breakpoint_on_empty_list_content(fake_anthropic):
    provider = _provider(fake_anthropic)
    call = _run(
        provider,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": []},
        ],
        stream=False,
    )
    assert call["messages"][-1] == {"role": "assistant", "content": []}
    assert _breakpoints(call) == 0


@pytest.mark.parametrize("stream", [False, True])
def test_rolling_breakpoint_never_mutates_the_caller_messages(fake_anthropic, stream):
    """The agent appends to and resends this list every call; a marker left in
    it would pile up a new breakpoint per turn."""
    provider = _provider(fake_anthropic)
    messages = [
        {"role": "system", "content": "You are GAIA."},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "text", "text": "here"},
            ],
        },
    ]
    snapshot = json.loads(json.dumps(messages))
    call = _run(provider, messages, stream, tools=OPENAI_TOOLS)
    assert call["messages"][-1]["content"][-1]["cache_control"] == _EPHEMERAL
    assert messages == snapshot


def test_breakpoints_over_the_api_limit_fail_loudly(fake_anthropic):
    """Anthropic rejects more than 4 breakpoints. Caller-supplied markers on
    older messages plus GAIA's three would exceed it."""
    provider = _provider(fake_anthropic)
    marked = [
        {
            "role": role,
            "content": [{"type": "text", "text": text, "cache_control": _EPHEMERAL}],
        }
        for role, text in (("user", "q1"), ("assistant", "a1"))
    ]
    with pytest.raises(ValueError, match="at most 4"):
        provider.chat(
            [{"role": "system", "content": "You are GAIA."}]
            + marked
            + [{"role": "user", "content": "q2"}],
            tools=OPENAI_TOOLS,
        )
    provider._client.messages.create.assert_not_called()


def _tool_round(call_id, arguments, result_content):
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": result_content},
    ]


def test_tool_argument_named_cache_control_is_not_a_breakpoint(fake_anthropic):
    """Tool inputs are arbitrary JSON; a key that happens to be called
    ``cache_control`` is data, not a marker, and must not trip the limit."""
    provider = _provider(fake_anthropic)
    messages = (
        [
            {"role": "system", "content": "You are GAIA."},
            {"role": "user", "content": "read both configs"},
        ]
        + _tool_round("toolu_a", {"path": "a.yml", "cache_control": "off"}, "x: 1")
        + _tool_round("toolu_b", {"path": "b.yml", "cache_control": "on"}, "y: 2")
    )
    call = _run(provider, messages, stream=False, tools=OPENAI_TOOLS)
    assert _breakpoints(call) == 5  # 3 markers + 2 look-alike argument keys
    assert call["messages"][-1]["content"][-1]["cache_control"] == _EPHEMERAL


def test_markers_nested_in_tool_results_count_toward_the_limit(fake_anthropic):
    """Anthropic counts a marker inside a tool_result's own content blocks."""
    provider = _provider(fake_anthropic)
    nested = [{"type": "text", "text": "cached output", "cache_control": _EPHEMERAL}]
    messages = (
        [
            {"role": "system", "content": "You are GAIA."},
            {"role": "user", "content": "read both files"},
        ]
        + _tool_round("toolu_a", {"path": "a.txt"}, nested)
        + _tool_round("toolu_b", {"path": "b.txt"}, nested)
        + [{"role": "user", "content": "now summarise"}]
    )
    with pytest.raises(ValueError, match="at most 4"):
        provider.chat(messages, tools=OPENAI_TOOLS)
    provider._client.messages.create.assert_not_called()


class _MarkingClient:
    """A client wrapper that adds its own marker on the newest message's last
    block and an effort setting, then forwards — copying or editing in place."""

    def __init__(self, inner, copy_request):
        self._inner = inner
        self._copy = copy_request
        self.sent = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        request = json.loads(json.dumps(kwargs)) if self._copy else kwargs
        last = request["messages"][-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}
        request["output_config"] = {"effort": "high"}
        self.sent.append(json.loads(json.dumps(request)))
        return self._inner.messages.create(**request)


@pytest.mark.parametrize("copy_request", [True, False], ids=["copies", "in_place"])
def test_a_client_wrapper_that_marks_the_last_block_stays_within_the_limit(
    fake_anthropic, copy_request
):
    """The wrapper re-marks the block GAIA already marked, so the count stays at
    3; and GAIA never writes a marker into the caller's history, so a wrapper
    editing in place can't pile markers up across calls."""
    provider = _provider(fake_anthropic)
    wrapper = _MarkingClient(provider._client, copy_request)
    provider._client = wrapper
    history = [{"role": "system", "content": "You are GAIA."}]
    for step in range(4):
        # Caller-owned block list as the newest message: the one place a
        # marker written in place could leak into history.
        history.append(
            {"role": "user", "content": [{"type": "text", "text": f"Part {step}."}]}
        )
        wrapper._inner.messages.create.return_value = _response(
            [_text_block(f"Done with part {step}.")]
        )
        reply = provider.chat(history, tools=OPENAI_TOOLS)
        history.append({"role": "assistant", "content": reply})

    assert len(wrapper.sent) == 4
    for request in wrapper.sent:
        assert _breakpoints(request) == 3
        assert request["output_config"] == {"effort": "high"}
    assert "cache_control" not in json.dumps(history)


def test_usage_reads_both_cache_counters(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [_text_block("ok")], input_tokens=180, output_tokens=28, cache_read=12200
    )
    provider.chat([{"role": "user", "content": "hi"}])
    usage = provider.get_last_usage()
    assert usage["cache_read_input_tokens"] == 12200
    assert usage["cache_creation_input_tokens"] == 0
    assert usage["uncached_input_tokens"] == 180
    # input_tokens is the uncached remainder, so the prompt is the sum — a
    # working cache must not read as a prompt that shrank by 98%.
    assert usage["prompt_tokens"] == 12380
    assert usage["total_tokens"] == 12408
    assert provider.get_performance_stats() == usage


def test_usage_counts_a_cache_write_toward_the_prompt(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [_text_block("ok")], input_tokens=180, output_tokens=28, cache_write=12200
    )
    provider.chat([{"role": "user", "content": "hi"}])
    usage = provider.get_last_usage()
    assert usage["cache_creation_input_tokens"] == 12200
    assert usage["cache_read_input_tokens"] == 0
    assert usage["prompt_tokens"] == 12380


def test_usage_survives_a_response_with_no_cache_fields(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [_text_block("ok")], input_tokens=123, output_tokens=45
    )
    provider.chat([{"role": "user", "content": "hi"}])
    usage = provider.get_last_usage()
    assert usage["prompt_tokens"] == 123
    assert usage["cache_read_input_tokens"] == 0


# ── streaming ───────────────────────────────────────────────────────────


def _stream_events():
    return iter(
        [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=42)),
            ),
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(type="thinking"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="thinking_delta", thinking="hmm"),
            ),
            SimpleNamespace(
                type="content_block_start",
                index=1,
                content_block=SimpleNamespace(type="text"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=1,
                delta=SimpleNamespace(type="text_delta", text="Check"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=1,
                delta=SimpleNamespace(type="text_delta", text="ing."),
            ),
            SimpleNamespace(
                type="content_block_start",
                index=2,
                content_block=SimpleNamespace(
                    # Must be a tool this request actually declared
                    # (OPENAI_TOOLS) — the API only returns declared names.
                    type="tool_use",
                    id="toolu_s1",
                    name="read_file",
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=2,
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"pa'),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=2,
                delta=SimpleNamespace(
                    type="input_json_delta", partial_json='th": "C:/"}'
                ),
            ),
            SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="tool_use"),
                usage=SimpleNamespace(output_tokens=17),
            ),
        ]
    )


def test_stream_assembles_input_json_deltas_into_sentinel(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _stream_events()
    chunks = list(
        provider.chat(
            [{"role": "user", "content": "ls"}], stream=True, tools=OPENAI_TOOLS
        )
    )
    # Prose streamed first; thinking deltas never surfaced as answer text.
    assert chunks[:-1] == ["Check", "ing."]
    sentinel = chunks[-1]
    assert sentinel.startswith(NATIVE_TOOL_CALLS_PREFIX)
    envelope = json.loads(sentinel)
    (call,) = envelope[_NATIVE_TC_KEY]
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "C:/"}
    assert envelope["finish_reason"] == "tool_calls"
    assert envelope["content"] == "Checking."
    # Streaming request still carried stream=True to the SDK.
    assert provider._client.messages.create.call_args.kwargs["stream"] is True
    usage = provider.get_last_usage()
    assert usage["prompt_tokens"] == 42
    assert usage["completion_tokens"] == 17
    assert usage["total_tokens"] == 59


def test_stream_reads_cache_counters_off_message_start(fake_anthropic):
    """The streaming path is where the flagship actually runs, so a cache read
    that only the non-streaming path parsed would report 0% in the TUI."""
    provider = _provider(fake_anthropic)
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=180,
                    output_tokens=1,
                    cache_read_input_tokens=12200,
                    cache_creation_input_tokens=0,
                )
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="hi"),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=28),
        ),
    ]
    provider._client.messages.create.return_value = iter(events)
    assert list(provider.chat([{"role": "user", "content": "hi"}], stream=True)) == [
        "hi"
    ]
    usage = provider.get_last_usage()
    assert usage["cache_read_input_tokens"] == 12200
    assert usage["prompt_tokens"] == 12380
    # message_delta reports the final output count and carries no cache fields;
    # absorbing it must not wipe what message_start established.
    assert usage["completion_tokens"] == 28


# ── AgentSDK routing ────────────────────────────────────────────────────


class _FakeLLMClient:
    def __init__(self):
        self.calls = []

    def chat(self, messages, model=None, stream=False, **kwargs):
        self.calls.append(
            {"messages": messages, "model": model, "stream": stream, "kwargs": kwargs}
        )
        if stream:
            return iter(["ok"])
        return "ok"

    def generate(self, prompt, model=None, stream=False, **kwargs):
        self.calls.append({"prompt": prompt, "model": model, "stream": stream})
        return "ok"

    accepts_reasoning_history = False

    def get_last_usage(self):
        return None

    def get_last_finish_reason(self):
        return None

    def get_last_reasoning(self):
        return None

    def get_performance_stats(self):
        return {}


@pytest.fixture()
def claude_sdk(monkeypatch):
    fake = _FakeLLMClient()
    monkeypatch.setattr("gaia.chat.sdk.create_client", lambda **kwargs: fake)
    from gaia.chat.sdk import AgentConfig, AgentSDK

    sdk = AgentSDK(AgentConfig(use_claude=True))
    return sdk, fake


def test_sdk_routes_claude_model_not_local_model(claude_sdk):
    sdk, fake = claude_sdk
    assert sdk.effective_model == "claude-sonnet-5"
    sdk.send_messages([{"role": "user", "content": "hi"}])
    assert fake.calls[-1]["model"] == "claude-sonnet-5"


def test_sdk_omits_tools_when_none_non_streaming(claude_sdk):
    sdk, fake = claude_sdk
    sdk.send_messages([{"role": "user", "content": "hi"}], tools=None)
    assert "tools" not in fake.calls[-1]["kwargs"]


def test_sdk_forwards_tools_when_present(claude_sdk):
    sdk, fake = claude_sdk
    sdk.send_messages([{"role": "user", "content": "hi"}], tools=OPENAI_TOOLS)
    assert fake.calls[-1]["kwargs"]["tools"] == OPENAI_TOOLS


@pytest.mark.parametrize("stream", [False, True])
def test_sdk_forwards_tool_choice_with_the_tools(claude_sdk, stream):
    sdk, fake = claude_sdk
    messages = [{"role": "user", "content": "hi"}]
    if stream:
        list(sdk.send_messages_stream(messages, tools=OPENAI_TOOLS, tool_choice="none"))
    else:
        sdk.send_messages(messages, tools=OPENAI_TOOLS, tool_choice="none")
    sent = fake.calls[-1]["kwargs"]
    assert sent["tools"] == OPENAI_TOOLS
    assert sent["tool_choice"] == "none"


def test_sdk_no_chatml_stop_tokens_for_claude(monkeypatch):
    fake = _FakeLLMClient()
    monkeypatch.setattr("gaia.chat.sdk.create_client", lambda **kwargs: fake)
    from gaia.chat.sdk import AgentConfig, AgentSDK

    # A qwen local model id would normally inject ChatML stop tokens.
    sdk = AgentSDK(AgentConfig(use_claude=True, model="Qwen3-4B-Instruct-2507-GGUF"))
    sdk.send_messages([{"role": "user", "content": "hi"}])
    assert "stop" not in fake.calls[-1]["kwargs"]


@pytest.mark.parametrize("stream", [False, True])
def test_sdk_preserves_claude_tool_history(claude_sdk, stream):
    sdk, fake = claude_sdk
    messages = [
        {"role": "user", "content": "list my files"},
        {
            "role": "assistant",
            "content": "Checking now.",
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "arguments": '{"path": "C:/"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "list_directory",
            "tool_call_id": "toolu_1",
            "content": "a.txt",
        },
    ]
    if stream:
        list(sdk.send_messages_stream(messages))
    else:
        sdk.send_messages(messages)
    sent = fake.calls[-1]["messages"]
    assert sent[1:] == [
        {
            "role": "assistant",
            "content": "Checking now.",
            "tool_calls": messages[1]["tool_calls"],
        },
        {
            "role": "tool",
            "content": "a.txt",
            "name": "list_directory",
            "tool_call_id": "toolu_1",
        },
    ]
    assert "[Called tools:" not in str(sent)
    assert "[Tool result:" not in str(sent)


# ── stdio transport flag contract ───────────────────────────────────────


def test_stdio_flag_literals_match_tui_contract():
    """The Go TUI appends these literal strings to the child argv."""
    stdio = pytest.importorskip("gaia_agent.stdio")

    args = stdio.build_parser().parse_args(
        ["--use-claude", "--claude-model", "claude-sonnet-5", "--dev"]
    )
    assert args.use_claude is True
    assert args.claude_model == "claude-sonnet-5"

    defaults = stdio.build_parser().parse_args([])
    assert defaults.use_claude is False
    assert defaults.claude_model is None


# ── Agent tool-capability gate ──────────────────────────────────────────


def test_openai_tools_gate_counts_claude_as_tool_calling():
    from gaia.agents.base.agent import Agent

    class _Stub(Agent):
        def _register_tools(self):
            pass

    agent = _Stub.__new__(_Stub)
    agent.model_id = None  # would gate tools OFF on the Lemonade path
    agent._use_claude = True
    agent._instance_tools = {
        "read_file": {
            "description": "Read a file",
            "parameters": {
                "path": {"type": "str", "required": True, "description": "path"}
            },
        }
    }
    schemas = agent._openai_tools
    assert schemas is not None
    assert schemas[0]["function"]["name"] == "read_file"

    agent._use_claude = False
    assert agent._openai_tools is None


class TestToolNameSanitization:
    """Skill tools are namespaced ``<skill>/<tool>`` — the ``/`` 400s the
    Anthropic API (pattern ``^[a-zA-Z0-9_-]{1,128}$``), so names must be
    sanitized outbound and restored on returned tool_use blocks."""

    def _tools(self, *names):
        return [
            {
                "type": "function",
                "function": {"name": n, "description": "", "parameters": {}},
            }
            for n in names
        ]

    def test_slash_name_is_sanitized_and_mapped(self, fake_anthropic):
        p = _provider(fake_anthropic)
        converted, name_map = p._to_anthropic_tools(self._tools("rss-digest/fetch_rss"))
        assert converted[0]["name"] == "rss-digest_fetch_rss"
        assert (
            p._restore_tool_name(name_map, "rss-digest_fetch_rss")
            == "rss-digest/fetch_rss"
        )

    def test_valid_names_pass_through_untouched(self, fake_anthropic):
        p = _provider(fake_anthropic)
        converted, name_map = p._to_anthropic_tools(
            self._tools("read_file", "query-docs")
        )
        assert [t["name"] for t in converted] == ["read_file", "query-docs"]
        assert p._restore_tool_name(name_map, "read_file") == "read_file"

    def test_map_is_per_call_not_per_provider(self, fake_anthropic):
        """One provider instance serving two tool sets must not let the second
        rewrite the first's names — the map belongs to the call."""
        p = _provider(fake_anthropic)
        _, first = p._to_anthropic_tools(self._tools("rss-digest/fetch_rss"))
        _, second = p._to_anthropic_tools(self._tools("notes/save_note"))

        assert first["rss-digest_fetch_rss"] == "rss-digest/fetch_rss"
        assert second["notes_save_note"] == "notes/save_note"
        # The first call's map is untouched by the second.
        assert "notes_save_note" not in first
        assert "rss-digest_fetch_rss" not in second

    def test_unmapped_returned_name_fails_loudly(self, fake_anthropic, caplog):
        """A miss means request and response were shaped against different
        tool sets. Returning the name unmapped surfaces later as "unknown
        tool", blaming the model for a bug that is here."""
        p = _provider(fake_anthropic)
        _, name_map = p._to_anthropic_tools(self._tools("read_file"))
        with pytest.raises(RuntimeError, match="not in the tool set sent"):
            p._restore_tool_name(name_map, "some_other_tool")
        # The user-facing message stays short; the diagnostic goes to the log.
        assert "read_file" in caplog.text

    @pytest.mark.parametrize(
        "entry",
        [
            {"type": "function", "function": {"parameters": {}}},  # OpenAI shape
            {"input_schema": {}},  # already-Anthropic shape
        ],
        ids=["openai_shape", "anthropic_shape"],
    )
    def test_nameless_tool_entry_fails_loudly(self, fake_anthropic, entry):
        """Anthropic requires a name on every tool entry, so a nameless one is
        malformed input — not something to pass through. Without the guard two
        of them both sanitize to '' and trip the collision error, which names
        the wrong problem."""
        p = _provider(fake_anthropic)
        with pytest.raises(ValueError, match="has no name"):
            p._to_anthropic_tools([entry])

    def test_sanitization_collision_fails_loudly(self, fake_anthropic):
        p = _provider(fake_anthropic)
        with pytest.raises(ValueError, match="both map to"):
            p._to_anthropic_tools(self._tools("a/b", "a.b"))

    def test_sanitized_name_shadowing_a_builtin_is_refused(self, fake_anthropic):
        p = _provider(fake_anthropic)
        # `write/file` -> `write_file`, which is already a real builtin.
        with pytest.raises(ValueError, match="both map to"):
            p._to_anthropic_tools(self._tools("write_file", "write/file"))

    def test_order_does_not_hide_the_collision(self, fake_anthropic):
        p = _provider(fake_anthropic)
        with pytest.raises(ValueError, match="both map to"):
            p._to_anthropic_tools(self._tools("write/file", "write_file"))

    def test_chat_round_trips_the_skill_name_end_to_end(self, fake_anthropic):
        """The map is written while shaping the request and read while parsing
        the response — the coupling a refactor would silently break."""
        p = _provider(fake_anthropic)
        p._client.messages.create.return_value = _response(
            [_tool_use_block("toolu_5", "rss-digest_fetch_rss", {"url": "http://x"})],
            stop_reason="tool_use",
        )
        envelope = json.loads(
            p.chat(
                [{"role": "user", "content": "digest it"}],
                tools=self._tools("rss-digest/fetch_rss"),
            )
        )
        # Anthropic saw the sanitized name; the agent gets the registered one.
        sent = p._client.messages.create.call_args.kwargs["tools"]
        assert [t["name"] for t in sent] == ["rss-digest_fetch_rss"]
        (call,) = envelope[_NATIVE_TC_KEY]
        assert call["function"]["name"] == "rss-digest/fetch_rss"

    def test_stream_round_trips_the_skill_name_end_to_end(self, fake_anthropic):
        p = _provider(fake_anthropic)
        p._client.messages.create.return_value = iter(
            [
                SimpleNamespace(
                    type="content_block_start",
                    index=0,
                    content_block=SimpleNamespace(
                        type="tool_use",
                        id="toolu_6",
                        name="rss-digest_fetch_rss",
                        input={},
                    ),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=0,
                    delta=SimpleNamespace(
                        type="input_json_delta", partial_json='{"url": "http://x"}'
                    ),
                ),
                SimpleNamespace(
                    type="message_delta",
                    delta=SimpleNamespace(stop_reason="tool_use"),
                    usage=SimpleNamespace(output_tokens=3),
                ),
            ]
        )
        chunks = list(
            p.chat(
                [{"role": "user", "content": "digest it"}],
                stream=True,
                tools=self._tools("rss-digest/fetch_rss"),
            )
        )
        (call,) = json.loads(chunks[-1])[_NATIVE_TC_KEY]
        assert call["function"]["name"] == "rss-digest/fetch_rss"


# ---------------------------------------------------------------------------
# finish_reason (#4054)
# ---------------------------------------------------------------------------


def test_max_tokens_is_reported_as_a_length_finish(fake_anthropic):
    """The agent loop only continues a cut-off reply if the provider says so."""
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [_text_block("The fix: add")], stop_reason="max_tokens"
    )

    text = provider.chat([{"role": "user", "content": "q"}])

    assert text == "The fix: add"
    assert provider.get_last_finish_reason() == "length"


def test_a_complete_reply_is_not_reported_as_cut_off(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [_text_block("done")], stop_reason="end_turn"
    )

    provider.chat([{"role": "user", "content": "q"}])

    assert provider.get_last_finish_reason() == "stop"


def test_stream_reports_a_length_finish_once_consumed(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = iter(
        [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=10, output_tokens=1)
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="text_delta", text="The fix: add"),
            ),
            SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="max_tokens"),
                usage=SimpleNamespace(output_tokens=8192),
            ),
        ]
    )

    chunks = list(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert chunks == ["The fix: add"]
    assert provider.get_last_finish_reason() == "length"


def test_a_new_call_clears_the_previous_finish_reason(fake_anthropic):
    """A stale "length" would make the next good reply look cut off."""
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response(
        [_text_block("The fix: add")], stop_reason="max_tokens"
    )
    provider.chat([{"role": "user", "content": "q"}])

    provider._client.messages.create.return_value = _response(
        [_text_block("done")], stop_reason="end_turn"
    )
    provider.chat([{"role": "user", "content": "q"}])

    assert provider.get_last_finish_reason() == "stop"


# ---------------------------------------------------------------------------
# Thinking blocks round-trip with their tool turn
# ---------------------------------------------------------------------------
#
# A tool loop with thinking enabled re-derives its plan on every call when the
# thinking blocks are dropped from the replayed assistant turn. The agent's
# history is OpenAI-shaped and carries no thinking, so the provider keeps the
# original blocks and re-emits them — but only while the turn and everything
# sent before it are unchanged, or the API rejects the stale signature.

_SYSTEM = {"role": "system", "content": "You are GAIA."}
_ASK = {"role": "user", "content": "Summarise notes.txt for me."}
_PLAN = SimpleNamespace(
    type="thinking",
    thinking="The user wants a summary, so I should read notes.txt first.",
    signature="EqQBCgIYAhIM-sig-plan",
)
_REDACTED = SimpleNamespace(type="redacted_thinking", data="EmwKAhgBEgy-redacted")
_NOTES = "Meeting moved to Thursday. Budget approved."


def _thinking_dict(block):
    if block.type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    return {
        "type": "thinking",
        "thinking": block.thinking,
        "signature": block.signature,
    }


def _as_agent_turn(envelope_text):
    """The assistant message the agent appends: OpenAI shape, no thinking."""
    envelope = json.loads(envelope_text)
    return {
        "role": "assistant",
        "content": envelope["content"],
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["function"]["name"],
                    "arguments": json.dumps(
                        json.loads(call["function"]["arguments"]), ensure_ascii=False
                    ),
                },
            }
            for call in envelope[_NATIVE_TC_KEY]
        ],
    }


def _tool_turn_response(*thinking, call_id="toolu_r1", path="notes.txt"):
    return _response(
        [
            *thinking,
            _text_block("Reading the file."),
            _tool_use_block(call_id, "read_file", {"path": path}),
        ],
        stop_reason="tool_use",
    )


def _thinking_stream(call_id="toolu_s1"):
    """A thinking + tool_use turn as stream events, signature_delta included."""
    return iter(
        [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=50)),
            ),
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(
                    type="thinking", thinking="", signature=""
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="thinking_delta", thinking="I should read "),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(
                    type="thinking_delta", thinking="notes.txt first."
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(type="signature_delta", signature="EqQB-sig-s"),
            ),
            SimpleNamespace(
                type="content_block_start",
                index=1,
                content_block=SimpleNamespace(type="text", text=""),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=1,
                delta=SimpleNamespace(type="text_delta", text="Reading the file."),
            ),
            SimpleNamespace(
                type="content_block_start",
                index=2,
                content_block=SimpleNamespace(
                    type="tool_use", id=call_id, name="read_file", input={}
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=2,
                delta=SimpleNamespace(
                    type="input_json_delta", partial_json='{"path": "notes.txt"}'
                ),
            ),
            SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="tool_use"),
                usage=SimpleNamespace(output_tokens=40),
            ),
        ]
    )


def _first_call(provider, response, stream, messages=None, tools=OPENAI_TOOLS):
    """Run call 1 and return the history the agent would send on call 2."""
    messages = messages or [_SYSTEM, _ASK]
    provider._client.messages.create.return_value = response
    result = provider.chat(messages, tools=tools, stream=stream)
    text = list(result)[-1] if stream else result
    turn = _as_agent_turn(text)
    result_msg = {
        "role": "tool",
        "tool_call_id": turn["tool_calls"][0]["id"],
        "content": _NOTES,
    }
    return messages + [turn, result_msg]


def _second_call(provider, history, stream=False, tools=OPENAI_TOOLS):
    provider._client.messages.create.return_value = (
        iter([]) if stream else _response([_text_block("Summary.")])
    )
    result = provider.chat(history, tools=tools, stream=stream)
    if stream:
        list(result)
    return provider._client.messages.create.call_args.kwargs


def _assistant_turns(call):
    return [m for m in call["messages"] if m["role"] == "assistant"]


@pytest.mark.parametrize("send_stream", [False, True])
def test_create_path_thinking_returns_on_its_tool_turn(fake_anthropic, send_stream):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)

    call = _second_call(provider, history, stream=send_stream)

    (turn,) = _assistant_turns(call)
    assert turn["content"] == [
        _thinking_dict(_PLAN),
        {"type": "text", "text": "Reading the file."},
        {
            "type": "tool_use",
            "id": "toolu_r1",
            "name": "read_file",
            "input": {"path": "notes.txt"},
        },
    ]


@pytest.mark.parametrize("send_stream", [False, True])
def test_stream_path_rebuilds_thinking_with_its_signature(fake_anthropic, send_stream):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _thinking_stream(), stream=True)

    call = _second_call(provider, history, stream=send_stream)

    (turn,) = _assistant_turns(call)
    assert turn["content"][0] == {
        "type": "thinking",
        "thinking": "I should read notes.txt first.",
        "signature": "EqQB-sig-s",
    }
    assert [b["type"] for b in turn["content"]] == ["thinking", "text", "tool_use"]
    assert turn["content"][2]["input"] == {"path": "notes.txt"}


def test_redacted_thinking_round_trips_unchanged(fake_anthropic):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_REDACTED, _PLAN), stream=False)

    call = _second_call(provider, history)

    (turn,) = _assistant_turns(call)
    assert turn["content"][:2] == [_thinking_dict(_REDACTED), _thinking_dict(_PLAN)]


def test_interleaved_thinking_keeps_its_original_order(fake_anthropic):
    provider = _provider(fake_anthropic)
    between = SimpleNamespace(
        type="thinking", thinking="Now the second file.", signature="EqQB-sig-2"
    )
    response = _response(
        [
            _PLAN,
            _tool_use_block("toolu_i1", "read_file", {"path": "a.txt"}),
            between,
            _tool_use_block("toolu_i2", "read_file", {"path": "b.txt"}),
        ],
        stop_reason="tool_use",
    )
    provider._client.messages.create.return_value = response
    turn = _as_agent_turn(provider.chat([_SYSTEM, _ASK], tools=OPENAI_TOOLS))
    history = [
        _SYSTEM,
        _ASK,
        turn,
        {"role": "tool", "tool_call_id": "toolu_i1", "content": "alpha"},
        {"role": "tool", "tool_call_id": "toolu_i2", "content": "beta"},
    ]

    call = _second_call(provider, history)

    (sent,) = _assistant_turns(call)
    assert [b.get("signature", b.get("id")) for b in sent["content"]] == [
        "EqQBCgIYAhIM-sig-plan",
        "toolu_i1",
        "EqQB-sig-2",
        "toolu_i2",
    ]


def test_a_turn_without_thinking_is_sent_as_before(fake_anthropic):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(), stream=False)

    call = _second_call(provider, history)

    (turn,) = _assistant_turns(call)
    assert [b["type"] for b in turn["content"]] == ["text", "tool_use"]


def test_a_streamed_thinking_block_without_a_signature_is_not_replayed(
    fake_anthropic,
):
    """A cut-off stream can end before signature_delta; an unsigned block is
    rejected by the API, so the turn goes back without it."""
    provider = _provider(fake_anthropic)
    events = [
        e
        for e in _thinking_stream()
        if e.type != "content_block_delta" or e.delta.type != "signature_delta"
    ]
    history = _first_call(provider, iter(events), stream=True)

    call = _second_call(provider, history)

    (turn,) = _assistant_turns(call)
    assert [b["type"] for b in turn["content"]] == ["text", "tool_use"]


def _edit_text(history):
    history[2] = {**history[2], "content": "Reading notes.txt now."}


def _edit_args(history):
    call = history[2]["tool_calls"][0]
    edited = {**call, "function": {**call["function"], "arguments": '{"path": "x"}'}}
    history[2] = {**history[2], "tool_calls": [edited]}


def _edit_earlier_message(history):
    history[1] = {"role": "user", "content": "Summarise notes.txt, briefly."}


def _edit_system(history):
    history[0] = {"role": "system", "content": "You are GAIA. Today is Friday."}


def _trim_history(history):
    del history[0:2]
    history.insert(0, {"role": "user", "content": "(earlier turns trimmed)"})


@pytest.mark.parametrize(
    "edit",
    [_edit_text, _edit_args, _edit_earlier_message, _edit_system, _trim_history],
    ids=["text", "arguments", "earlier_message", "system_prompt", "trimmed"],
)
def test_an_edited_history_does_not_send_stale_thinking(fake_anthropic, edit, caplog):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)
    edit(history)

    with caplog.at_level("DEBUG", logger="gaia.llm.providers.claude"):
        call = _second_call(provider, history)

    (turn,) = _assistant_turns(call)
    assert not any(b["type"] == "thinking" for b in turn["content"])
    assert "thinking" in caplog.text


def test_a_changed_tool_set_does_not_send_stale_thinking(fake_anthropic):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)
    more_tools = OPENAI_TOOLS + [
        {
            "type": "function",
            "function": {"name": "list_dir", "description": "List", "parameters": {}},
        }
    ]

    call = _second_call(provider, history, tools=more_tools)

    (turn,) = _assistant_turns(call)
    assert not any(b["type"] == "thinking" for b in turn["content"])


def test_thinking_survives_a_multi_step_loop(fake_anthropic):
    """Each turn's prefix includes the earlier turns' re-sent thinking; a
    consistent record keeps all of them valid on call 3."""
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)

    second_plan = SimpleNamespace(
        type="thinking", thinking="Now the archive.", signature="EqQB-sig-two"
    )
    provider._client.messages.create.return_value = _tool_turn_response(
        second_plan, call_id="toolu_r2", path="archive.txt"
    )
    turn2 = _as_agent_turn(provider.chat(history, tools=OPENAI_TOOLS))
    history += [turn2, {"role": "tool", "tool_call_id": "toolu_r2", "content": "old"}]

    call = _second_call(provider, history)

    first, second = _assistant_turns(call)
    assert first["content"][0] == _thinking_dict(_PLAN)
    assert second["content"][0] == _thinking_dict(second_plan)


def test_an_edit_invalidates_every_later_turns_thinking(fake_anthropic):
    """The API voids a block and every block after it once the prefix
    changes, so an edit to turn 1 must strip turn 2's thinking too."""
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)
    second_plan = SimpleNamespace(
        type="thinking", thinking="Now the archive.", signature="EqQB-sig-two"
    )
    provider._client.messages.create.return_value = _tool_turn_response(
        second_plan, call_id="toolu_r2", path="archive.txt"
    )
    turn2 = _as_agent_turn(provider.chat(history, tools=OPENAI_TOOLS))
    history += [turn2, {"role": "tool", "tool_call_id": "toolu_r2", "content": "old"}]
    _edit_text(history)

    call = _second_call(provider, history)

    for turn in _assistant_turns(call):
        assert not any(b["type"] == "thinking" for b in turn["content"])


def test_dropping_the_oldest_turns_thinking_keeps_later_turns_valid(
    fake_anthropic, monkeypatch
):
    """Thinking blocks chain to the one before them rather than binding it as
    prefix, so the API accepts a leading run removed oldest-first. A full
    replay store sheds its oldest turn without voiding the rest."""
    monkeypatch.setattr("gaia.llm.providers.claude._MAX_REPLAY_TURNS", 1)
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)
    second_plan = SimpleNamespace(
        type="thinking", thinking="Now the archive.", signature="EqQB-sig-two"
    )
    provider._client.messages.create.return_value = _tool_turn_response(
        second_plan, call_id="toolu_r2", path="archive.txt"
    )
    turn2 = _as_agent_turn(provider.chat(history, tools=OPENAI_TOOLS))
    history += [turn2, {"role": "tool", "tool_call_id": "toolu_r2", "content": "old"}]

    call = _second_call(provider, history)

    first, second = _assistant_turns(call)
    assert not any(b["type"] == "thinking" for b in first["content"])
    assert second["content"][0] == _thinking_dict(second_plan)


@pytest.mark.parametrize("copy_request", [True, False], ids=["copies", "in_place"])
def test_a_client_wrapper_that_marks_the_last_block_keeps_thinking_valid(
    fake_anthropic, copy_request
):
    """The replay check ignores markers, so a wrapper's extra marker and effort
    setting never stop a tool turn's thinking from going back."""
    provider = _provider(fake_anthropic)
    wrapper = _MarkingClient(provider._client, copy_request)
    provider._client = wrapper
    history = [_SYSTEM, _ASK]
    plans = []
    for step in range(4):
        plan = SimpleNamespace(
            type="thinking", thinking=f"Step {step}.", signature=f"EqQB-sig-{step}"
        )
        plans.append(plan)
        wrapper._inner.messages.create.return_value = _tool_turn_response(
            plan, call_id=f"toolu_w{step}", path=f"part{step}.txt"
        )
        turn = _as_agent_turn(provider.chat(history, tools=OPENAI_TOOLS))
        history += [
            turn,
            {"role": "tool", "tool_call_id": f"toolu_w{step}", "content": "ok"},
            # Caller-owned block list as the newest message: the one place a
            # marker written in place could leak into history and pile up.
            {"role": "user", "content": [{"type": "text", "text": "Next part."}]},
        ]

    assert len(wrapper.sent) == 4
    for request in wrapper.sent:
        assert _breakpoints(request) == 3
        assert request["output_config"] == {"effort": "high"}
    assert "cache_control" not in json.dumps(history)
    final = _assistant_turns(wrapper.sent[-1])
    assert [turn["content"][0] for turn in final] == [
        _thinking_dict(p) for p in plans[:-1]
    ]


def test_replaying_thinking_never_mutates_the_caller_messages(fake_anthropic):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)
    snapshot = json.loads(json.dumps(history))

    _second_call(provider, history)

    assert history == snapshot


def test_rolling_breakpoint_stays_valid_with_replayed_thinking(fake_anthropic):
    provider = _provider(fake_anthropic)
    history = _first_call(provider, _tool_turn_response(_PLAN), stream=False)

    call = _second_call(provider, history)

    (turn,) = _assistant_turns(call)
    assert turn["content"][0]["type"] == "thinking"
    assert not any("cache_control" in b for b in turn["content"])
    assert call["messages"][-1]["content"][-1]["cache_control"] == _EPHEMERAL
    assert _breakpoints(call) == 3


# The kept thinking blocks must be an unbroken run of the original sequence:
# dropping from the front is accepted, a gap in the middle is a 400 on models
# that check the chain. Only the newest unbroken run of assistant turns that
# all have stored thinking goes back.


def _plan(tag):
    return SimpleNamespace(
        type="thinking", thinking=f"Plan {tag}.", signature=f"EqQB-sig-{tag}"
    )


def _tool_step(provider, history, response, call_id, stream=False):
    provider._client.messages.create.return_value = response
    result = provider.chat(history, tools=OPENAI_TOOLS, stream=stream)
    text = list(result)[-1] if stream else result
    return history + [
        _as_agent_turn(text),
        {"role": "tool", "tool_call_id": call_id, "content": "ok"},
    ]


def _answer_step(provider, history, *thinking):
    """A final-answer turn: its thinking is never stored for replay."""
    provider._client.messages.create.return_value = _response(
        [*thinking, _text_block("Here is the summary.")]
    )
    answer = provider.chat(history, tools=OPENAI_TOOLS)
    return history + [{"role": "assistant", "content": answer}]


def _thinking_of(call):
    return [
        (
            [b.get("signature") for b in turn["content"] if b["type"] == "thinking"]
            if isinstance(turn["content"], list)
            else []
        )
        for turn in _assistant_turns(call)
    ]


def test_a_gap_in_the_middle_drops_every_older_turns_thinking(fake_anthropic):
    """ask -> T1(tool) -> answer -> ask -> T2(tool): T1 must not go back, or
    the request carries thinking [T1], [], [T2] and the API rejects the gap."""
    provider = _provider(fake_anthropic)
    history = _tool_step(
        provider, [_SYSTEM, _ASK], _tool_turn_response(_plan(1)), "toolu_r1"
    )
    history = _answer_step(provider, history, _plan("answer"))
    history.append({"role": "user", "content": "Now the archive, please."})
    history = _tool_step(
        provider,
        history,
        _tool_turn_response(_plan(2), call_id="toolu_r2", path="archive.txt"),
        "toolu_r2",
    )

    call = _second_call(provider, history)

    assert _thinking_of(call) == [[], [], ["EqQB-sig-2"]]
    first = _assistant_turns(call)[0]
    assert first["content"] == [
        {"type": "text", "text": "Reading the file."},
        {
            "type": "tool_use",
            "id": "toolu_r1",
            "name": "read_file",
            "input": {"path": "notes.txt"},
        },
    ]


def test_a_dropped_turn_is_never_put_back(fake_anthropic):
    """Re-adding a removed block voids the blocks produced while it was gone,
    so T1 stays out once a later turn broke the run."""
    provider = _provider(fake_anthropic)
    history = _tool_step(
        provider, [_SYSTEM, _ASK], _tool_turn_response(_plan(1)), "toolu_r1"
    )
    history = _answer_step(provider, history, _plan("answer"))
    history.append({"role": "user", "content": "Now the archive, please."})
    for step in (2, 3):
        history = _tool_step(
            provider,
            history,
            _tool_turn_response(_plan(step), call_id=f"toolu_r{step}"),
            f"toolu_r{step}",
        )

    call = _second_call(provider, history)

    assert _thinking_of(call) == [[], [], ["EqQB-sig-2"], ["EqQB-sig-3"]]


def test_a_cut_off_turn_breaks_the_run(fake_anthropic):
    """A streamed turn that ended before its signature can't be replayed, so
    the stored turn before it must go back without thinking as well."""
    provider = _provider(fake_anthropic)
    history = _tool_step(
        provider, [_SYSTEM, _ASK], _tool_turn_response(_plan(1)), "toolu_r1"
    )
    unsigned = iter(
        e
        for e in _thinking_stream(call_id="toolu_s1")
        if e.type != "content_block_delta" or e.delta.type != "signature_delta"
    )
    history = _tool_step(provider, history, unsigned, "toolu_s1", stream=True)

    call = _second_call(provider, history)
    assert _thinking_of(call) == [[], []]

    history = _tool_step(
        provider,
        history,
        _tool_turn_response(_plan(3), call_id="toolu_r3"),
        "toolu_r3",
    )
    call = _second_call(provider, history)
    assert _thinking_of(call) == [[], [], ["EqQB-sig-3"]]


@pytest.mark.parametrize("stream", [False, True])
def test_an_all_tool_turn_loop_sends_every_turns_thinking(fake_anthropic, stream):
    provider = _provider(fake_anthropic)
    history = [_SYSTEM, _ASK]
    for step in range(3):
        history = _tool_step(
            provider,
            history,
            _tool_turn_response(_plan(step), call_id=f"toolu_l{step}"),
            f"toolu_l{step}",
        )

    call = _second_call(provider, history, stream=stream)

    assert _thinking_of(call) == [[f"EqQB-sig-{step}"] for step in range(3)]
    for turn in _assistant_turns(call):
        assert [b["type"] for b in turn["content"]] == ["thinking", "text", "tool_use"]
        assert turn["content"][0]["signature"]
        assert not any("cache_control" in b for b in turn["content"])
    assert call["messages"][-1]["content"][-1]["cache_control"] == _EPHEMERAL
    assert _breakpoints(call) == 3


def test_thinking_off_sends_the_same_request_as_without_replay(fake_anthropic):
    """With no thinking in any response nothing is stored, so every turn goes
    out exactly as a provider with no replay store would send it."""
    provider = _provider(fake_anthropic)
    history = _tool_step(provider, [_SYSTEM, _ASK], _tool_turn_response(), "toolu_r1")
    history = _answer_step(provider, history)
    history.append({"role": "user", "content": "Now the archive, please."})
    history = _tool_step(
        provider,
        history,
        _tool_turn_response(call_id="toolu_r2", path="archive.txt"),
        "toolu_r2",
    )

    call = _second_call(provider, history)
    fresh = _provider(fake_anthropic)
    baseline = _second_call(fresh, history)

    assert provider._replay_turns == {}
    assert call["messages"] == baseline["messages"]
    assert _thinking_of(call) == [[], [], []]
