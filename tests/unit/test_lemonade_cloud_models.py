# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cloud inference must never acquire or reconfigure the local model slot."""

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import responses
from openai import OpenAI

from gaia.llm.lemonade_client import (
    CONVERSATION_SLOT,
    LemonadeClient,
    LemonadeClientError,
    cloud_model_provider,
    create_lemonade_client,
    local_sampling_defaults,
    no_thinking_kwargs,
)
from gaia.llm.providers.lemonade import LemonadeProvider


@pytest.mark.parametrize(
    "model,provider",
    [
        ("fireworks.gemma-4-31b-it", "fireworks"),
        ("fireworks.accounts/fireworks/models/gemma-4-31b-it", "fireworks"),
        ("amd.gpt-4.1", "amd"),
        ("user.embeddinggemma-300m-GGUF", None),
        ("Qwen3.5-35B-GGUF", None),
        ("qwen3.5-35B-GGUF", None),
        ("cloud.example.model", None),
        ("fireworks.", None),
        (None, None),
    ],
)
def test_cloud_model_namespace(model, provider):
    assert cloud_model_provider(model) == provider


def test_cloud_catalog_metadata_identifies_custom_provider():
    assert (
        cloud_model_provider(
            "company.llama-4", {"recipe": "cloud", "cloud_provider": "company"}
        )
        == "company"
    )
    assert cloud_model_provider("company.llama-4", {"recipe": "llamacpp"}) is None


@pytest.fixture
def client(monkeypatch):
    def reject_local_operation(*args, **kwargs):
        pytest.fail("Cloud inference attempted a local model operation")

    client = LemonadeClient(verbose=False, ctx_size_override=1024)
    monkeypatch.setattr(client, "get_status", reject_local_operation)
    monkeypatch.setattr(client, "_ensure_pinned_load", reject_local_operation)
    monkeypatch.setattr(client, "_load_model_leased", reject_local_operation)
    monkeypatch.setattr("gaia.daemon.broker_client.model_lease", reject_local_operation)
    return client


@pytest.mark.parametrize("provider", ["fireworks", "amd"])
@responses.activate
def test_cloud_chat_only_calls_inference_and_preserves_tools(client, provider):
    model = f"{provider}.gemma-4-31b-it"
    reply = {"choices": [{"message": {"content": "hello"}}]}
    responses.post(f"{client.base_url}/chat/completions", json=reply)
    tools = [{"type": "function", "function": {"name": "get_weather"}}]

    assert (
        client.chat_completions(
            model,
            [{"role": "user", "content": "hello"}],
            tools=tools,
            repeat_penalty=1.1,
            repeat_last_n=256,
            frequency_penalty=0.3,
        )
        == reply
    )

    assert len(responses.calls) == 1
    body = json.loads(responses.calls[0].request.body)
    assert body["model"] == model
    assert body["tools"] == tools
    assert body["frequency_penalty"] == 0.3
    assert "repeat_penalty" not in body and "repeat_last_n" not in body
    assert "ctx_size" not in body


def test_cloud_stream_skips_slot_and_preserves_stream_and_tools(client, monkeypatch):
    sdk = MagicMock()
    sdk.chat.completions.create.return_value = [
        SimpleNamespace(
            id="chunk-1",
            created=0,
            model="amd.gemma-4-31b-it",
            choices=[
                SimpleNamespace(
                    index=0,
                    finish_reason=None,
                    delta=SimpleNamespace(
                        role="assistant", content="hello", tool_calls=None
                    ),
                )
            ],
        )
    ]
    monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kwargs: sdk)
    tools = [{"type": "function", "function": {"name": "get_weather"}}]

    chunks = list(
        client.chat_completions(
            "amd.gemma-4-31b-it",
            [],
            stream=True,
            tools=tools,
            repeat_penalty=1.1,
            repeat_last_n=256,
        )
    )

    assert chunks[0]["choices"][0]["delta"]["content"] == "hello"
    sent = sdk.chat.completions.create.call_args.kwargs
    assert sent["stream"] is True and sent["tools"] == tools
    assert "extra_body" not in sent


@responses.activate
def test_missing_cloud_model_fails_without_download_or_retry(client):
    responses.post(
        f"{client.base_url}/chat/completions",
        status=404,
        json={"error": {"message": "model not found"}},
    )
    with pytest.raises(LemonadeClientError):
        client.chat_completions("fireworks.missing", [])
    assert len(responses.calls) == 1


@pytest.mark.parametrize("operation", ["load_model", "pull_model", "pull_model_stream"])
def test_explicit_local_operations_on_cloud_fail_before_network(client, operation):
    with pytest.raises(LemonadeClientError, match="Cloud model"):
        result = getattr(client, operation)("fireworks.gemma-4-31b-it")
        if operation == "pull_model_stream":
            list(result)


@responses.activate
def test_cloud_availability_does_not_require_downloaded(client):
    responses.get(
        f"{client.base_url}/models?show_all=true",
        json={"data": [{"id": "fireworks.gemma-4-31b-it", "downloaded": False}]},
    )
    assert client.check_model_available("fireworks.gemma-4-31b-it")
    assert client.ensure_model_downloaded("fireworks.gemma-4-31b-it")


@responses.activate
def test_discovered_custom_cloud_avoids_local_load(client):
    responses.get(
        f"{client.base_url}/models?show_all=true",
        json={
            "data": [
                {"id": "company.gemma", "recipe": "cloud", "cloud_provider": "company"}
            ]
        },
    )
    responses.post(
        f"{client.base_url}/chat/completions",
        json={"choices": [{"message": {"content": "hello"}}]},
    )
    client.list_models(show_all=True)
    client.chat_completions("company.gemma", [])
    assert len(responses.calls) == 2


@pytest.mark.parametrize(
    "status,remedy",
    [
        (401, "Reconnect"),
        (403, "model access"),
        (404, "deployed model"),
        (429, "Wait before retrying"),
        (402, "spending limit"),
        (412, "spending limit"),
        (500, "Check the provider"),
    ],
)
@responses.activate
def test_cloud_error_never_echoes_upstream_body(client, status, remedy, caplog):
    reflected_key = "test-upstream-key-do-not-display"
    responses.post(
        f"{client.base_url}/chat/completions",
        status=status,
        json={"error": {"message": reflected_key}},
    )
    with pytest.raises(LemonadeClientError) as error:
        client.chat_completions("fireworks.gemma-4-31b-it", [])
    assert str(status) in str(error.value)
    assert "provider settings" in str(error.value)
    assert remedy in str(error.value)
    assert reflected_key not in str(error.value)
    assert reflected_key not in caplog.text


def test_cloud_factory_auto_load_does_not_download_or_load(monkeypatch):
    load = MagicMock()
    pull = MagicMock()
    monkeypatch.setattr(LemonadeClient, "load_model", load)
    monkeypatch.setattr(LemonadeClient, "pull_model", pull)

    client = create_lemonade_client(
        model="fireworks.gemma-4-31b-it", auto_load=True, auto_pull=True
    )

    assert client.model == "fireworks.gemma-4-31b-it"
    load.assert_not_called()
    pull.assert_not_called()


@pytest.mark.parametrize("provider", ["fireworks", "amd"])
@pytest.mark.parametrize("stream", [False, True])
@responses.activate
def test_cloud_provider_preserves_native_tool_only_response(
    client, monkeypatch, provider, stream
):
    model = f"{provider}.gemma-4-31b-it"
    tool_call = {
        "id": "call-weather",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
    }
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    sent = []
    if stream:
        fragments = [
            {
                "index": 0,
                "id": tool_call["id"],
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":'},
            },
            {"index": 0, "function": {"arguments": '"Paris"}'}},
        ]
        chunks = [
            {
                "id": "chat-weather",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": None, "tool_calls": [fragment]},
                        "finish_reason": None,
                    }
                ],
            }
            for fragment in fragments
        ]
        chunks.append(
            {
                "id": "chat-weather",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }
        )
        wire = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        wire += "data: [DONE]\n\n"

        def handle(request):
            assert str(request.url) == f"{client.base_url}/chat/completions"
            sent.append(json.loads(request.content))
            return httpx.Response(
                200, content=wire, headers={"content-type": "text/event-stream"}
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handle))
        sdk = OpenAI(
            base_url=client.base_url,
            api_key="test-placeholder",
            http_client=http_client,
        )
        monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kwargs: sdk)
    else:
        responses.post(
            f"{client.base_url}/chat/completions",
            json={
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "total_tokens": 19,
                },
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tool_call],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    adapter = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    adapter._backend = client
    global_stats = MagicMock(
        return_value={"output_tokens": 999, "tokens_per_second": 999}
    )
    monkeypatch.setattr(client, "get_stats", global_stats)
    try:
        result = adapter.chat(
            [{"role": "user", "content": "What's the weather in Paris?"}],
            model=model,
            tools=tools,
            stream=stream,
        )
        if stream:
            events = list(result)
            assert len(events) == 1
            envelope = json.loads(events[0])
        else:
            envelope = json.loads(result)
            assert len(responses.calls) == 1
            sent.append(json.loads(responses.calls[0].request.body))
    finally:
        if stream:
            sdk.close()

    assert envelope == {
        "__tool_calls__": [tool_call],
        "finish_reason": "tool_calls",
        "content": None,
    }
    assert len(sent) == 1
    assert sent[0]["model"] == model
    assert sent[0]["tools"] == tools
    assert sent[0]["stream"] is stream
    assert "repeat_penalty" not in sent[0] and "repeat_last_n" not in sent[0]
    assert adapter.get_performance_stats() == (
        {}
        if stream
        else {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    )
    global_stats.assert_not_called()


def test_local_provider_keeps_lemonade_performance_stats(monkeypatch):
    adapter = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    stats = {"input_tokens": 12, "output_tokens": 7, "tokens_per_second": 25.0}
    get_stats = MagicMock(return_value=stats)
    monkeypatch.setattr(adapter._backend, "get_stats", get_stats)

    assert adapter.get_performance_stats() == stats
    get_stats.assert_called_once()


def test_local_provider_prefers_the_calls_own_usage(monkeypatch):
    """/stats counts only uncached tokens of the server's last request (#4003)."""
    adapter = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    get_stats = MagicMock(
        return_value={"input_tokens": 89, "cache_tokens": 6553, "output_tokens": 7}
    )
    monkeypatch.setattr(adapter._backend, "get_stats", get_stats)
    adapter._last_model = "Gemma-4-E4B-it-GGUF"
    adapter._last_usage = {
        "prompt_tokens": 6642,
        "completion_tokens": 7,
        "total_tokens": 6649,
        "tokens_per_second": 25.0,
    }

    assert adapter.get_performance_stats() == adapter._last_usage
    get_stats.assert_not_called()


def test_model_availability_failure_emits_diagnostic(client, monkeypatch, caplog):
    monkeypatch.setattr(
        client,
        "list_models",
        MagicMock(side_effect=LemonadeClientError("private upstream body")),
    )
    assert client.check_model_available("fireworks.gemma-4-31b-it") is False
    assert "Could not check model availability" in caplog.text
    assert "Check the Lemonade connection and authentication" in caplog.text
    assert "private upstream body" not in caplog.text


def test_model_availability_does_not_hide_programming_errors(client, monkeypatch):
    monkeypatch.setattr(
        client, "list_models", MagicMock(side_effect=RuntimeError("bug"))
    )
    with pytest.raises(RuntimeError, match="bug"):
        client.check_model_available("fireworks.gemma-4-31b-it")


@pytest.mark.parametrize(
    "status,remedy",
    [
        (401, "Reconnect"),
        (403, "model access"),
        (404, "deployed model"),
        (429, "Wait before retrying"),
        (402, "spending limit"),
        (412, "spending limit"),
        (500, "Check the provider"),
    ],
)
def test_cloud_sse_backend_error_uses_status_without_reflecting_body(
    client, monkeypatch, caplog, status, remedy
):
    reflected_key = "private-upstream-response-and-key"
    error_frame = {
        "error": {
            "type": "backend_error",
            "message": reflected_key,
            "details": {"status_code": status, "response": {"error": reflected_key}},
        }
    }

    def handle(request):
        return httpx.Response(
            200,
            content=f"data: {json.dumps(error_frame)}\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        with OpenAI(
            base_url=client.base_url, api_key="test-placeholder", http_client=transport
        ) as sdk:
            monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kwargs: sdk)
            with pytest.raises(LemonadeClientError) as error:
                list(client.chat_completions("fireworks.not-deployed", [], stream=True))

    assert str(status) in str(error.value)
    assert remedy in str(error.value)
    assert reflected_key not in str(error.value)
    assert reflected_key not in caplog.text


_PENALTIES = (
    "frequency_penalty",
    "presence_penalty",
    "repeat_penalty",
    "repeat_last_n",
)


def _sent_body(monkeypatch, model, stream, **chat_kwargs):
    """Send one LemonadeProvider.chat through the real client; return the wire body."""
    client = LemonadeClient(verbose=False, ctx_size_override=1024)
    monkeypatch.setattr(client, "_ensure_model_loaded", lambda *a, **k: None)
    monkeypatch.setattr(
        client, "_model_slot_lease", lambda *a, **k: contextlib.nullcontext()
    )
    adapter = LemonadeProvider(model=model)
    adapter._backend = client
    messages = [{"role": "user", "content": "hi"}]
    sent = []

    if stream:
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [
                {"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}
            ],
        }

        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(
                200,
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )

        with OpenAI(
            base_url=client.base_url,
            api_key="test-placeholder",
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        ) as sdk:
            monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kw: sdk)
            list(adapter.chat(messages, stream=True, **chat_kwargs))
    else:

        def handle(request):
            sent.append(json.loads(request.body))
            return 200, {}, json.dumps({"choices": [{"message": {"content": "ok"}}]})

        with responses.RequestsMock() as mock:
            mock.add_callback(
                responses.POST, f"{client.base_url}/chat/completions", callback=handle
            )
            adapter.chat(messages, **chat_kwargs)

    assert len(sent) == 1
    return sent[0]


@pytest.mark.parametrize("stream", [False, True])
def test_cloud_model_request_carries_no_repetition_penalties(monkeypatch, stream):
    body = _sent_body(monkeypatch, "fireworks.deepseek-v4p1-flash", stream)
    assert [key for key in _PENALTIES if key in body] == []


@pytest.mark.parametrize("stream", [False, True])
def test_cloud_model_request_is_not_near_greedy(monkeypatch, stream):
    """Temperature 0.1 sent DeepSeek's reasoning into 32K-token runaways."""
    body = _sent_body(monkeypatch, "fireworks.deepseek-v4p1-flash", stream)
    assert body["temperature"] == 0.7


@pytest.mark.parametrize("stream", [False, True])
def test_cloud_model_request_keeps_an_explicit_temperature(monkeypatch, stream):
    body = _sent_body(
        monkeypatch, "fireworks.deepseek-v4p1-flash", stream, temperature=0.4
    )
    assert body["temperature"] == 0.4


@pytest.mark.parametrize("stream", [False, True])
def test_local_model_request_keeps_repetition_penalties(monkeypatch, stream):
    body = _sent_body(monkeypatch, "Gemma-4-E4B-it-GGUF", stream)
    assert body["temperature"] == 0.1
    assert {key: body[key] for key in _PENALTIES} == {
        "frequency_penalty": 0.3,
        "presence_penalty": 0.1,
        "repeat_penalty": 1.1,
        "repeat_last_n": 256,
    }


def _wire(model, stream, **sampling):
    """The full request body Lemonade receives for one local ``hi`` turn."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 1000,
        "stream": stream,
        "id_slot": CONVERSATION_SLOT,
        **sampling,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


@pytest.mark.parametrize("stream", [False, True])
def test_gemma_request_body_is_unchanged(monkeypatch, stream):
    """The committed eval baseline was captured under exactly this sampling."""
    body = _sent_body(monkeypatch, "Gemma-4-E4B-it-GGUF", stream)
    assert body == _wire(
        "Gemma-4-E4B-it-GGUF",
        stream,
        temperature=0.1,
        frequency_penalty=0.3,
        presence_penalty=0.1,
        repeat_penalty=1.1,
        repeat_last_n=256,
    )


_QWEN3_30B = "Qwen3-30B-A3B-Instruct-2507-GGUF"


@pytest.mark.parametrize("stream", [False, True])
def test_qwen3_30b_request_carries_its_model_card_sampling(monkeypatch, stream):
    """Card: Temperature=0.7, TopP=0.8, TopK=20, MinP=0; presence 0-2; no other penalty."""
    body = _sent_body(monkeypatch, _QWEN3_30B, stream)
    assert body == _wire(
        _QWEN3_30B,
        stream,
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        min_p=0.0,
        presence_penalty=1.0,
    )


@pytest.mark.parametrize("stream", [False, True])
def test_qwen3_30b_request_keeps_explicit_caller_sampling(monkeypatch, stream):
    body = _sent_body(
        monkeypatch, _QWEN3_30B, stream, temperature=0.0, presence_penalty=0.0
    )
    assert body["temperature"] == 0.0
    assert body["presence_penalty"] == 0.0
    assert (body["top_p"], body["top_k"], body["min_p"]) == (0.8, 20, 0.0)


_QWEN3_6_THINKING = {
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.0,
    "repeat_penalty": 1.0,
}
_QWEN3_6_INSTRUCT = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
}


@pytest.mark.parametrize(
    "template_kwargs,expected",
    [
        # Not in MODELS, so GAIA sends no switch and the template thinks.
        (None, _QWEN3_6_THINKING),
        ({"enable_thinking": True}, _QWEN3_6_THINKING),
        ({"enable_thinking": False}, _QWEN3_6_INSTRUCT),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
def test_qwen3_6_mtp_sampling_follows_the_thinking_mode(
    monkeypatch, stream, template_kwargs, expected
):
    model = "Qwen3.6-35B-A3B-MTP-GGUF"
    kwargs = {"chat_template_kwargs": template_kwargs} if template_kwargs else {}
    body = _sent_body(monkeypatch, model, stream, **kwargs)
    assert body == _wire(model, stream, **kwargs, **expected)


_QWEN3_6 = "Qwen3.6-35B-A3B-GGUF"


@pytest.mark.parametrize(
    "template_kwargs,sent_switch,expected",
    [
        # GAIA's choice for the default, sent explicitly rather than left to the template.
        (None, True, _QWEN3_6_THINKING),
        ({"enable_thinking": True}, True, _QWEN3_6_THINKING),
        ({"enable_thinking": False}, False, _QWEN3_6_INSTRUCT),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
def test_qwen3_6_sends_its_thinking_mode_with_the_matching_sampling(
    monkeypatch, stream, template_kwargs, sent_switch, expected
):
    kwargs = {"chat_template_kwargs": template_kwargs} if template_kwargs else {}
    body = _sent_body(monkeypatch, _QWEN3_6, stream, **kwargs)
    assert body == _wire(
        _QWEN3_6,
        stream,
        chat_template_kwargs={"enable_thinking": sent_switch},
        **expected,
    )


_FLASH = "user.Qwen3.8-Flash-Next-GGUF"
_FLASH_THINKING = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.0,
}
_FLASH_INSTRUCT = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
}


@pytest.mark.parametrize(
    "template_kwargs,sent_switch,expected",
    [
        (None, True, _FLASH_THINKING),
        ({"enable_thinking": False}, False, _FLASH_INSTRUCT),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
def test_flash_sends_its_thinking_mode_with_its_card_sampling(
    monkeypatch, stream, template_kwargs, sent_switch, expected
):
    kwargs = {"chat_template_kwargs": template_kwargs} if template_kwargs else {}
    body = _sent_body(monkeypatch, _FLASH, stream, **kwargs)
    assert body == _wire(
        _FLASH,
        stream,
        chat_template_kwargs={"enable_thinking": sent_switch},
        **expected,
    )


@pytest.mark.parametrize("stream", [False, True])
def test_turning_the_default_non_thinking_flips_switch_and_sampling_together(
    monkeypatch, stream
):
    """Sampling and the request read one resolver, so they cannot disagree."""
    import dataclasses

    from gaia.llm import lemonade_client as lc

    key = next(k for k, mr in lc.MODELS.items() if mr.model_id == _QWEN3_6)
    monkeypatch.setitem(
        lc.MODELS, key, dataclasses.replace(lc.MODELS[key], thinking=False)
    )
    body = _sent_body(monkeypatch, _QWEN3_6, stream)
    assert body == _wire(
        _QWEN3_6,
        stream,
        chat_template_kwargs={"enable_thinking": False},
        **_QWEN3_6_INSTRUCT,
    )


@pytest.mark.parametrize("stream", [False, True])
def test_side_request_reaches_lemonade_with_thinking_off(monkeypatch, stream):
    """GAIA forces thinking on for Qwen3.6; a side call's switch must win on the wire."""
    body = _sent_body(monkeypatch, _QWEN3_6, stream, **no_thinking_kwargs(_QWEN3_6))
    assert body == _wire(
        _QWEN3_6,
        stream,
        chat_template_kwargs={"enable_thinking": False},
        **_QWEN3_6_INSTRUCT,
    )


@pytest.mark.parametrize(
    "model_id,expected",
    [
        (_QWEN3_6, {"chat_template_kwargs": {"enable_thinking": False}}),
        (
            "user.Qwen3.6-35B-A3B-GGUF",
            {"chat_template_kwargs": {"enable_thinking": False}},
        ),
        # No thinking switch registered: the template's own mode, nothing sent.
        ("Gemma-4-E4B-it-GGUF", {}),
        (_QWEN3_30B, {}),
        # Cloud providers do not take llama.cpp template kwargs.
        ("fireworks.deepseek-v4p1-flash", {}),
        (None, {}),
    ],
)
def test_no_thinking_kwargs(model_id, expected):
    assert no_thinking_kwargs(model_id) == expected


def test_single_mode_model_ignores_the_thinking_switch():
    """Qwen3-30B-2507 Instruct cannot think; its template ignores the switch."""
    assert local_sampling_defaults(
        _QWEN3_30B, enable_thinking=True
    ) == local_sampling_defaults(_QWEN3_30B)


def test_sampling_defaults_are_a_copy():
    local_sampling_defaults(_QWEN3_30B)["temperature"] = 2.0
    local_sampling_defaults("Gemma-4-E4B-it-GGUF")["temperature"] = 2.0
    assert local_sampling_defaults(_QWEN3_30B)["temperature"] == 0.7
    assert local_sampling_defaults("Gemma-4-E4B-it-GGUF")["temperature"] == 0.1


@pytest.mark.parametrize("stream", [False, True])
def test_cloud_model_request_keeps_explicit_caller_penalty(monkeypatch, stream):
    body = _sent_body(
        monkeypatch, "fireworks.deepseek-v4p1-flash", stream, frequency_penalty=0.5
    )
    assert body["frequency_penalty"] == 0.5
    assert "presence_penalty" not in body


class TestCachedTokenCapture:
    """A cached-token count is reported when measured and omitted when not.

    Fireworks bills cached prompt tokens at a tenth of the input rate, so the
    count drives a real dollar figure. That makes the absent case matter as
    much as the present one: a backend that said nothing about caching must
    not come back as "0 cached", which reads as a measurement and prices the
    turn as if the whole prompt were billed fresh.
    """

    def capture(self, usage):
        adapter = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        adapter._capture_usage(usage, timings=None)
        return adapter._last_usage

    def test_reported_counts_are_kept(self):
        captured = self.capture(
            {
                "prompt_tokens": 120,
                "completion_tokens": 8,
                "total_tokens": 128,
                "prompt_tokens_details": {"cached_tokens": 96},
                "completion_tokens_details": {"reasoning_tokens": 3},
            }
        )
        assert captured["cached_tokens"] == 96
        assert captured["reasoning_tokens"] == 3

    def test_a_reported_zero_is_a_measurement(self):
        captured = self.capture(
            {
                "prompt_tokens": 120,
                "completion_tokens": 8,
                "total_tokens": 128,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
        )
        assert captured["cached_tokens"] == 0

    def test_an_unreported_count_is_left_out(self):
        captured = self.capture(
            {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}
        )
        assert "cached_tokens" not in captured
        assert "reasoning_tokens" not in captured
