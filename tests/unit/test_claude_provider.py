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
    assert call["messages"] == [{"role": "user", "content": "hello"}]


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


def test_no_breakpoint_when_there_is_nothing_stable_to_cache(fake_anthropic):
    provider = _provider(fake_anthropic)
    provider._client.messages.create.return_value = _response([_text_block("ok")])
    provider.chat([{"role": "user", "content": "hi"}])
    call = provider._client.messages.create.call_args.kwargs
    assert "system" not in call and "tools" not in call


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
                    type="tool_use", id="toolu_s1", name="list_directory"
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
    assert call["function"]["name"] == "list_directory"
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

    def get_last_usage(self):
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


def test_sdk_no_chatml_stop_tokens_for_claude(monkeypatch):
    fake = _FakeLLMClient()
    monkeypatch.setattr("gaia.chat.sdk.create_client", lambda **kwargs: fake)
    from gaia.chat.sdk import AgentConfig, AgentSDK

    # A qwen local model id would normally inject ChatML stop tokens.
    sdk = AgentSDK(AgentConfig(use_claude=True, model="Qwen3-4B-Instruct-2507-GGUF"))
    sdk.send_messages([{"role": "user", "content": "hi"}])
    assert "stop" not in fake.calls[-1]["kwargs"]


def test_sdk_flattens_assistant_tool_call_turn(claude_sdk):
    sdk, fake = claude_sdk
    sdk.send_messages(
        [
            {"role": "user", "content": "list my files"},
            {
                "role": "assistant",
                "content": None,
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
            {"role": "tool", "name": "list_directory", "content": "a.txt"},
        ]
    )
    sent = fake.calls[-1]["messages"]
    assistant_turns = [m for m in sent if m["role"] == "assistant"]
    assert assistant_turns == [
        {
            "role": "assistant",
            "content": '[Called tools: list_directory({"path": "C:/"})]',
        }
    ]
    # The flattened history never shows the "None" placeholder.
    assert all(m["content"] != "None" for m in sent)


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
        converted = p._to_anthropic_tools(self._tools("rss-digest/fetch_rss"))
        assert converted[0]["name"] == "rss-digest_fetch_rss"
        assert p._restore_tool_name("rss-digest_fetch_rss") == "rss-digest/fetch_rss"

    def test_valid_names_pass_through_untouched(self, fake_anthropic):
        p = _provider(fake_anthropic)
        converted = p._to_anthropic_tools(self._tools("read_file", "query-docs"))
        assert [t["name"] for t in converted] == ["read_file", "query-docs"]
        assert p._restore_tool_name("read_file") == "read_file"

    def test_sanitization_collision_fails_loudly(self, fake_anthropic):
        import pytest

        p = _provider(fake_anthropic)
        with pytest.raises(ValueError, match="sanitize to"):
            p._to_anthropic_tools(self._tools("a/b", "a.b"))
