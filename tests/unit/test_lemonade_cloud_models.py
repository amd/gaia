# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cloud inference must never acquire or reconfigure the local model slot."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import responses
from openai import OpenAI

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    cloud_model_provider,
    create_lemonade_client,
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
