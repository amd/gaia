# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the native tool_calls priority inversion implemented across:

    - src/gaia/llm/lemonade_client.py  (is_tool_calling_model, MODELS,
                                        _validate_profile_model_registry)
    - src/gaia/llm/providers/lemonade.py  (LemonadeProvider.chat native path,
                                           _NATIVE_TC_KEY sentinel)
    - src/gaia/agents/base/agent.py  (Agent._parse_llm_response native branch,
                                      Agent._build_openai_tool_schemas,
                                      Agent._openai_tools,
                                      system-prompt gating in
                                      _compose_system_prompt)

These tests must pass WITHOUT a running Lemonade server.  All external
dependencies are mocked via unittest.mock.
"""

import json
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import (
    AGENT_PROFILES,
    AgentProfile,
    _validate_profile_model_registry,
    is_tool_calling_model,
)
from gaia.llm.providers.lemonade import (
    _NATIVE_TC_KEY,
    NATIVE_TOOL_CALLS_PREFIX,
    LemonadeProvider,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_bare_agent(model_id=None):
    """
    Return an Agent instance with ``__init__`` bypassed.

    ``Agent.__init__`` reaches out to LemonadeManager/AgentSDK, which we do not
    want to exercise in unit tests.  ``Agent.__new__`` on an ABC requires a
    concrete subclass, so we define one inline that implements the abstract
    surface with no-ops and then instantiate via ``__new__`` (skipping
    ``__init__``).  Any attributes the method under test needs are set by the
    caller (or here, for the common ones).
    """

    class _ConcreteAgent(Agent):
        def _get_system_prompt(self):
            return ""

        def _register_tools(self):
            return None

    obj = _ConcreteAgent.__new__(_ConcreteAgent)
    obj.model_id = model_id
    obj.error_history = []
    obj.api_mode = False
    # _response_format_template is used by _compose_system_prompt; set a
    # sentinel so tests can assert its presence / absence in composed output.
    obj._response_format_template = Agent._PLANNING_FORMAT
    return obj


@pytest.fixture
def clear_tool_registry():
    """Snapshot + restore _TOOL_REGISTRY so registrations don't leak."""
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


# =============================================================================
# Group 1: is_tool_calling_model helper
# =============================================================================


def test_known_tool_calling_model_true():
    assert is_tool_calling_model("Gemma-4-E4B-it-GGUF") is True


def test_known_embedding_model_false():
    assert is_tool_calling_model("user.embeddinggemma-300m-GGUF") is False


def test_none_returns_false():
    assert is_tool_calling_model(None) is False


def test_empty_string_returns_false():
    assert is_tool_calling_model("") is False


def test_unknown_gguf_returns_true():
    # Optimistic default per Tier 0 empirical testing: any unrecognised
    # Lemonade GGUF is assumed tool-capable.
    assert is_tool_calling_model("some-future-model-GGUF") is True


# =============================================================================
# Group 2: LemonadeProvider.chat() — native tool_calls path
# =============================================================================


def _chat_response_with_tool_calls():
    """Return a non-streaming chat_completions response carrying tool_calls."""
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "type": "function",
                            "function": {
                                "name": "search_docs",
                                "arguments": '{"query": "hello"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _chat_response_plain_text(text="Hello there."):
    return {
        "choices": [
            {
                "message": {"content": text},
                "finish_reason": "stop",
            }
        ]
    }


def test_native_tool_calls_encoded_as_sentinel():
    """tool_calls from backend → sentinel JSON string starting with __tool_calls__."""
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        backend = MockBackend.return_value
        backend.chat_completions.return_value = _chat_response_with_tool_calls()

        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        result = provider.chat(
            messages=[{"role": "user", "content": "find docs"}],
            model="Gemma-4-E4B-it-GGUF",
            stream=False,
            tools=[{"type": "function", "function": {"name": "search_docs"}}],
        )

    assert isinstance(result, str)
    assert result.startswith('{"__tool_calls__":')
    envelope = json.loads(result)
    assert _NATIVE_TC_KEY in envelope
    assert envelope[_NATIVE_TC_KEY][0]["function"]["name"] == "search_docs"


def _tool_call_frame(fragment, finish_reason=None):
    return {
        "choices": [
            {"delta": {"tool_calls": [fragment]}, "finish_reason": finish_reason}
        ]
    }


def _text_frame(content, finish_reason=None):
    return {
        "choices": [{"delta": {"content": content}, "finish_reason": finish_reason}]
    }


def test_streaming_stays_on_when_tools_provided():
    """Tools no longer force the stream off — the fragments are reassembled.

    Forcing stream=False here is what made a tool-capable agent unable to show
    a single word before the whole turn finished: it always sends tools, so it
    always took the non-streaming path.
    """

    def _frames():
        # The real Lemonade shape: id and name arrive first, then the JSON
        # arguments one slice per frame.
        yield _tool_call_frame(
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_docs", "arguments": "{"},
            }
        )
        yield _tool_call_frame({"index": 0, "function": {"arguments": '"q": "np'}})
        yield _tool_call_frame(
            {"index": 0, "function": {"arguments": 'u"}'}}, finish_reason="tool_calls"
        )

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        backend = MockBackend.return_value
        backend.chat_completions.return_value = _frames()

        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        chunks = list(
            provider.chat(
                messages=[{"role": "user", "content": "q"}],
                model="Gemma-4-E4B-it-GGUF",
                stream=True,
                tools=[{"type": "function", "function": {"name": "search_docs"}}],
            )
        )

    assert backend.chat_completions.call_args.kwargs["stream"] is True
    assert chunks[-1].startswith(NATIVE_TOOL_CALLS_PREFIX)
    envelope = json.loads(chunks[-1])
    call = envelope[_NATIVE_TC_KEY][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "search_docs"
    assert json.loads(call["function"]["arguments"]) == {"q": "npu"}
    assert envelope["finish_reason"] == "tool_calls"


def test_streamed_text_before_a_tool_call_is_kept():
    """Prose emitted alongside tool_calls streams AND rides the envelope.

    Gemma-4 routinely writes a sentence before calling a tool. It has to reach
    the user as it is typed and still be attached to the assistant message, or
    the model's own framing of why it called the tool is lost.
    """

    def _frames():
        yield _text_frame("Let me ")
        yield _text_frame("look that up.")
        yield _tool_call_frame(
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_docs", "arguments": "{}"},
            },
            finish_reason="tool_calls",
        )

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        MockBackend.return_value.chat_completions.return_value = _frames()
        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        chunks = list(
            provider.chat(
                messages=[{"role": "user", "content": "q"}],
                model="Gemma-4-E4B-it-GGUF",
                stream=True,
                tools=[{"type": "function", "function": {"name": "search_docs"}}],
            )
        )

    assert chunks[:2] == ["Let me ", "look that up."]
    assert json.loads(chunks[-1])["content"] == "Let me look that up."


def test_plain_text_stream_emits_no_envelope():
    """A turn that calls no tool ends with prose, never a trailing sentinel."""

    def _frames():
        yield _text_frame("An NPU ")
        yield _text_frame("is a chip.", finish_reason="stop")

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        MockBackend.return_value.chat_completions.return_value = _frames()
        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        chunks = list(
            provider.chat(
                messages=[{"role": "user", "content": "q"}],
                model="Gemma-4-E4B-it-GGUF",
                stream=True,
                tools=[{"type": "function", "function": {"name": "search_docs"}}],
            )
        )

    assert chunks == ["An NPU ", "is a chip."]


def _reasoning_frame(reasoning, finish_reason=None):
    return {
        "choices": [
            {
                "delta": {"reasoning_content": reasoning},
                "finish_reason": finish_reason,
            }
        ]
    }


def test_reasoning_is_released_a_line_at_a_time():
    """Never a token at a time: a thought renders as ONE replaced line.

    Per-token reasoning blanks the sentence before it, so the status line reads
    as ". / just / `." churn instead of a sentence anyone can follow.
    """

    def _frames():
        for piece in (
            "Thinking",
            " Process",
            ":\n",
            "1. Check",
            " the hub\n",
            "2. Cou",
        ):
            yield _reasoning_frame(piece)
        yield {"choices": [{"delta": {"content": "Done."}, "finish_reason": "stop"}]}

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        MockBackend.return_value.chat_completions.return_value = _frames()
        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        chunks = list(
            provider.chat(
                messages=[{"role": "user", "content": "q"}],
                model="Gemma-4-E4B-it-GGUF",
                stream=True,
                tools=None,
            )
        )

    assert chunks == [
        "<think>",
        "Thinking Process:\n",
        "1. Check the hub\n",
        # The half-line rides out with the closing tag rather than alone.
        "2. Cou</think>",
        "Done.",
    ]


def test_unterminated_reasoning_still_closes_its_block():
    """A stream that ends mid-thought must not leave <think> hanging open —
    the receiving parser would swallow the rest of the turn as reasoning."""

    def _frames():
        yield _reasoning_frame("half a thou", finish_reason="length")

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        MockBackend.return_value.chat_completions.return_value = _frames()
        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        chunks = list(
            provider.chat(
                messages=[{"role": "user", "content": "q"}],
                model="Gemma-4-E4B-it-GGUF",
                stream=True,
                tools=None,
            )
        )

    assert chunks == ["<think>", "half a thou</think>"]


def test_streaming_preserved_without_tools():
    """Without tools, stream=True from the caller is passed through."""

    def _stream_iter():
        # Empty generator — _handle_stream consumes an iterator of chunks.
        if False:
            yield {}
        return

    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        backend = MockBackend.return_value
        backend.chat_completions.return_value = _stream_iter()

        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        result = provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="Gemma-4-E4B-it-GGUF",
            stream=True,
            tools=None,
        )
        # Materialize the streaming iterator to simulate a consumer.
        list(result)

    call_kwargs = backend.chat_completions.call_args.kwargs
    assert call_kwargs["stream"] is True
    assert call_kwargs["tools"] is None


def test_tools_not_passed_for_non_tool_calling_model():
    """Non-tool-calling models (e.g. embeddinggemma) must not receive tools."""
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as MockBackend:
        backend = MockBackend.return_value
        backend.chat_completions.return_value = _chat_response_plain_text()

        provider = LemonadeProvider(model="user.embeddinggemma-300m-GGUF")
        provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="user.embeddinggemma-300m-GGUF",
            stream=False,
            tools=[{"type": "function", "function": {"name": "search_docs"}}],
        )

    call_kwargs = backend.chat_completions.call_args.kwargs
    assert call_kwargs["tools"] is None, (
        "is_tool_calling_model returns False for the embedding model, so "
        "effective_tools must be None regardless of what the caller passed."
    )


# =============================================================================
# Group 3: Agent._parse_llm_response — native sentinel parsing
# =============================================================================


def test_parse_native_single_tool_call():
    """Single-call native envelope returns BOTH the legacy single-tool
    fields (``tool``/``tool_args`` for backward compatibility) AND the
    new ``tool_calls`` list shape introduced for parallel-call support
    (issue #944). The list always carries a ``tool_call_id`` so result
    messages can correlate back to the originating call.
    """
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "some_tool",
                    "arguments": '{"arg1": "val"}',
                },
            }
        ],
        "finish_reason": "tool_calls",
    }
    response = json.dumps(envelope)

    result = agent._parse_llm_response(response)

    # Legacy single-call fields preserved for backward compatibility.
    assert result["thought"] == ""
    assert result["goal"] == ""
    assert result["tool"] == "some_tool"
    assert result["tool_args"] == {"arg1": "val"}
    # New normalised tool_calls list also populated.
    assert result["tool_calls"] == [
        {"id": "call_0", "name": "some_tool", "tool_args": {"arg1": "val"}}
    ]
    # ``content`` defaults to None when no assistant text accompanies
    # the tool_calls (legacy envelopes from before the content carry-
    # through landed have no ``content`` key — None is the expected
    # value for both).
    assert result.get("content") is None


def test_parse_native_finish_reason_length_raises():
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "some_tool",
                    "arguments": '{"arg1": "val',  # truncated
                },
            }
        ],
        "finish_reason": "length",
    }
    response = json.dumps(envelope)

    with pytest.raises(ValueError, match="truncated"):
        agent._parse_llm_response(response)


def test_parse_native_parallel_calls_returns_list():
    """Parallel native tool_calls used to raise ``NotImplementedError``,
    breaking GAIA's own default model (Gemma-4-E4B) on multi-intent
    inputs (issue #944). They now parse into a normalised ``tool_calls``
    list with ids preserved for downstream correlation.
    """
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "tool_a", "arguments": '{"x": 1}'},
            },
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "tool_b", "arguments": "{}"},
            },
        ],
        "finish_reason": "tool_calls",
    }
    response = json.dumps(envelope)

    result = agent._parse_llm_response(response)

    assert result["tool_calls"] == [
        {"id": "call_0", "name": "tool_a", "tool_args": {"x": 1}},
        {"id": "call_1", "name": "tool_b", "tool_args": {}},
    ]
    # Legacy single-call fields are intentionally absent for N>1 — the
    # fan-out path in process_query MUST consume ``tool_calls`` and
    # treating ``tool``/``tool_args`` as authoritative would silently
    # drop calls 1..N-1.
    assert "tool" not in result
    assert "tool_args" not in result


def test_parse_native_three_calls_with_mixed_args():
    """Three parallel calls, one with empty args and one with dict-shape
    args (some llama.cpp builds emit pre-parsed dicts). All three must
    survive the parse path with their ids intact.
    """
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "id": "tc-A",
                "type": "function",
                "function": {"name": "alpha", "arguments": '{"k": "v"}'},
            },
            {
                "id": "tc-B",
                "type": "function",
                "function": {"name": "beta", "arguments": ""},
            },
            {
                "id": "tc-C",
                "type": "function",
                "function": {"name": "gamma", "arguments": {"already": "dict"}},
            },
        ],
        "finish_reason": "tool_calls",
    }

    result = agent._parse_llm_response(json.dumps(envelope))

    assert [tc["id"] for tc in result["tool_calls"]] == ["tc-A", "tc-B", "tc-C"]
    assert result["tool_calls"][1]["tool_args"] == {}
    assert result["tool_calls"][2]["tool_args"] == {"already": "dict"}


def test_parse_native_content_alongside_tool_calls():
    """Some tool-calling models (notably Gemma-4-E4B) emit assistant
    text *and* tool_calls in the same response. The provider now
    surfaces that text in the envelope's ``content`` field; the parser
    propagates it so the agent loop can attach it to the assistant
    message instead of silently discarding it.
    """
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "tool_a", "arguments": "{}"},
            }
        ],
        "finish_reason": "tool_calls",
        "content": "Got it — running tool_a now.",
    }

    result = agent._parse_llm_response(json.dumps(envelope))

    assert result["content"] == "Got it — running tool_a now."
    assert result["tool_calls"][0]["name"] == "tool_a"


def test_parse_native_synthesises_id_when_missing():
    """If llama.cpp ever omits ``id`` (some forks do), the parser
    synthesises one rather than crashing — otherwise downstream tool
    result correlation would silently fail.
    """
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "type": "function",
                "function": {"name": "tool_a", "arguments": "{}"},
            }
        ],
        "finish_reason": "tool_calls",
    }

    result = agent._parse_llm_response(json.dumps(envelope))

    tc_id = result["tool_calls"][0]["id"]
    assert isinstance(tc_id, str) and tc_id, "synthesised id must be non-empty"
    assert tc_id.startswith("call_0_")


def test_parse_native_malformed_arguments_raises():
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "some_tool",
                    "arguments": "{not valid json",
                },
            }
        ],
        "finish_reason": "tool_calls",
    }
    response = json.dumps(envelope)

    with pytest.raises(ValueError, match="Malformed tool_call arguments"):
        agent._parse_llm_response(response)


def test_parse_native_empty_arguments_ok():
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    envelope = {
        _NATIVE_TC_KEY: [
            {
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "noarg_tool",
                    "arguments": "",
                },
            }
        ],
        "finish_reason": "tool_calls",
    }
    response = json.dumps(envelope)

    result = agent._parse_llm_response(response)

    assert result["tool"] == "noarg_tool"
    assert result["tool_args"] == {}


def test_parse_native_malformed_envelope_raises():
    """Starts with the sentinel prefix but the rest is not valid JSON."""
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    bad = '{"__tool_calls__": [this is not json}'

    with pytest.raises(ValueError, match="Malformed native tool_calls envelope"):
        agent._parse_llm_response(bad)


def test_parse_legacy_embedded_json_still_works():
    """A plain embedded-JSON response (no sentinel) should hit the legacy path."""
    agent = _make_bare_agent(model_id="Qwen3.5-35B-A3B-GGUF")
    response = json.dumps(
        {
            "thought": "I should search",
            "goal": "answer the user",
            "tool": "search_docs",
            "tool_args": {"query": "hello"},
        }
    )

    result = agent._parse_llm_response(response)

    assert result["tool"] == "search_docs"
    assert result["tool_args"] == {"query": "hello"}


# =============================================================================
# Group 4: Agent._build_openai_tool_schemas
# =============================================================================


def test_build_schemas_returns_list_of_functions(clear_tool_registry):
    @tool
    def sample_tool(query: str) -> dict:
        """Search the knowledge base."""
        return {"ok": True}

    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    schemas = agent._build_openai_tool_schemas()

    assert isinstance(schemas, list)
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "sample_tool"
    assert "description" in schema["function"]
    assert schema["function"]["parameters"]["type"] == "object"


def test_build_schema_type_mapping(clear_tool_registry):
    """Agent._python_to_json_type mapping: str→string, int→integer, bool→boolean.

    We populate ``_TOOL_REGISTRY`` directly with Python-style type names
    ("str", "int", "bool") because the ``@tool`` decorator pre-normalises
    annotations to JSON names.  The mapping logic under test lives in
    ``_build_openai_tool_schemas`` and is what handles raw Python type
    strings (e.g. when a schema is registered programmatically, not via the
    decorator).
    """
    _TOOL_REGISTRY["mixed_types"] = {
        "name": "mixed_types",
        "description": "Tool with mixed param types.",
        "parameters": {
            "text": {"type": "str", "required": True},
            "count": {"type": "int", "required": True},
            "flag": {"type": "bool", "required": True},
        },
        "function": lambda **_: None,
        "atomic": False,
    }

    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    schemas = agent._build_openai_tool_schemas()

    props = schemas[0]["function"]["parameters"]["properties"]
    assert props["text"]["type"] == "string"
    assert props["count"]["type"] == "integer"
    assert props["flag"]["type"] == "boolean"


def test_build_schema_required_params(clear_tool_registry):
    """Parameters without defaults land in the ``required`` list."""

    @tool
    def req_and_optional(query: str, limit: int = 10) -> dict:
        """Tool with one required and one optional param."""
        return {}

    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    schemas = agent._build_openai_tool_schemas()

    required = schemas[0]["function"]["parameters"]["required"]
    assert "query" in required
    assert "limit" not in required


# =============================================================================
# Group 5: System-prompt gating
# =============================================================================


def test_format_template_absent_for_tool_calling_model(clear_tool_registry):
    """For a tool-calling model the embedded-JSON format block must NOT be added."""
    agent = _make_bare_agent(model_id="Gemma-4-E4B-it-GGUF")
    composed = agent._compose_system_prompt()
    # The planning-format template's signature line
    assert "==== RESPONSE FORMAT ====" not in composed
    assert "You must respond ONLY in valid JSON" not in composed


def test_format_template_present_for_legacy_model(clear_tool_registry):
    """For a non-tool-calling model the embedded-JSON format block IS appended."""
    # Use the embedding model, which has tool_calling=False in MODELS.
    agent = _make_bare_agent(model_id="user.embeddinggemma-300m-GGUF")
    composed = agent._compose_system_prompt()
    assert "==== RESPONSE FORMAT ====" in composed
    assert "You must respond ONLY in valid JSON" in composed


# =============================================================================
# Group 6: Startup validator (_validate_profile_model_registry)
# =============================================================================


def test_validate_profile_registry_passes():
    """Current MODELS/AGENT_PROFILES state must validate cleanly."""
    # If the module imported at top-of-file, the validator already ran once
    # without raising.  Call it again to prove the current state is still OK.
    _validate_profile_model_registry()  # must not raise


def test_validate_profile_registry_detects_missing_key():
    """Injecting a profile referencing an unknown model key must raise ValueError."""
    bad_profile = AgentProfile(
        name="bogus",
        display_name="Bogus",
        models=["definitely-not-in-MODELS"],
        min_ctx_size=1024,
        description="test-only profile",
    )
    with patch.dict(AGENT_PROFILES, {"bogus": bad_profile}, clear=False):
        with pytest.raises(ValueError, match="not declared in MODELS"):
            _validate_profile_model_registry()
