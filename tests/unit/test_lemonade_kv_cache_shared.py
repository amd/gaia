# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Two requests that outgrow a model's shared KV cache are a collision, not a
conversation that got too long, and every request on the model takes its lease.
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.providers.lemonade import (
    LemonadeContextOverflowError,
    LemonadeKVCacheSharedError,
    _classify_lemonade_response,
    classify_lemonade_exception,
)
from gaia.llm.retry import (
    KV_CACHE_SHARED_REASON,
    ModelCallRetrier,
    RetryPolicy,
    RetryState,
    classify_http_failure,
)

MODEL = "Gemma-4-E4B-it-GGUF"

# llama.cpp's reply to every request active when a decode found no KV cell.
SHARED_BODY = json.dumps(
    {
        "error": {
            "code": 500,
            "message": "Context size has been exceeded.",
            "type": "server_error",
        }
    }
)
# The same, wrapped the way Lemonade relays a backend error.
SHARED_WRAPPED = json.dumps(
    {
        "error": {
            "message": "llama-server returned an error",
            "type": "backend_error",
            "details": {
                "response": {
                    "error": {
                        "message": "failed to find free space in the KV cache",
                        "type": "server_error",
                    }
                }
            },
        }
    }
)
# A prompt that alone does not fit the window: a real overflow.
OVERFLOW_BODY = json.dumps(
    {
        "error": {
            "code": 400,
            "message": "the request exceeds the available context size, try "
            "increasing it",
            "type": "exceed_context_size_error",
            "n_prompt_tokens": 70000,
            "n_ctx": 65536,
        }
    }
)


@pytest.mark.parametrize("body", [SHARED_BODY, SHARED_WRAPPED])
def test_shared_cache_failure_is_not_a_context_overflow(body):
    assert lc.is_kv_cache_shared_error(body)
    assert not lc.is_context_overflow_error(body)


def test_real_overflow_still_reads_as_overflow():
    assert lc.is_context_overflow_error(OVERFLOW_BODY)
    assert not lc.is_kv_cache_shared_error(OVERFLOW_BODY)
    assert classify_http_failure(400, OVERFLOW_BODY) is None


@pytest.mark.parametrize("status", [500, None])
def test_shared_cache_failure_is_retried(status):
    assert classify_http_failure(status, SHARED_BODY) == KV_CACHE_SHARED_REASON


def test_user_is_not_told_to_start_a_fresh_task():
    error = lc.LemonadeClientError(
        f"Error in chat completions (status 500): {SHARED_BODY}"
    )
    classified = classify_lemonade_exception(error)
    assert isinstance(classified, LemonadeKVCacheSharedError)
    assert classified.retryable
    assert "fresh task" not in classified.user_message
    assert "send your message again" in classified.user_message.lower()


def test_response_envelope_classified_as_shared():
    classified, is_error = _classify_lemonade_response(json.loads(SHARED_WRAPPED))
    assert is_error and isinstance(classified, LemonadeKVCacheSharedError)


def test_response_envelope_overflow_unchanged():
    classified, _ = _classify_lemonade_response(json.loads(OVERFLOW_BODY), MODEL)
    assert isinstance(classified, LemonadeContextOverflowError)


def _response(status, body):
    response = MagicMock()
    response.status_code = status
    response.text = body
    response.headers = {}
    response.json.return_value = json.loads(body)
    return response


def test_chat_retries_a_shared_cache_collision():
    """The colliding request is sent again once the other one has finished,
    instead of the turn failing as "conversation too long"."""
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    client._retry_sleep = lambda _s: None
    ok = json.dumps({"choices": [{"message": {"content": "hi"}}]})
    replies = [_response(500, SHARED_BODY), _response(200, ok)]
    with (
        patch.object(lc.requests, "post", side_effect=replies) as post,
        patch.object(lc.LemonadeClient, "_server_load_in_progress", return_value=None),
    ):
        result = client.chat_completions(
            MODEL, [{"role": "user", "content": "hi"}], auto_download=False
        )
    assert result["choices"][0]["message"]["content"] == "hi"
    assert post.call_count == 2
    assert client.last_request_retries == 1


@pytest.mark.parametrize("stream", [False, True])
def test_text_completions_take_the_model_lease(stream):
    """/completions shares the model's KV pool, so it queues like chat."""
    client = lc.LemonadeClient(base_url="http://127.0.0.1:1/api/v1", verbose=False)
    leased = []

    @contextmanager
    def lease(model, **_kwargs):
        leased.append(model)
        yield

    ok = {"choices": [{"text": "hi", "finish_reason": "stop"}]}
    with (
        patch.object(client, "_model_slot_lease", side_effect=lease),
        patch.object(client, "_ensure_model_loaded"),
        patch.object(lc.requests, "post", return_value=_response(200, json.dumps(ok))),
        patch.object(client, "_stream_completions_leased", return_value=iter([ok])),
    ):
        result = client.completions(MODEL, "hi", stream=stream)
        if stream:
            list(result)
    assert leased == [MODEL]


def test_a_collision_after_output_is_not_replayed():
    """Mid-stream, a retry would repeat what the user already saw: the
    collision is surfaced (and marked transient) instead."""
    import openai

    def open_stream():
        yield "partial answer"
        raise openai.APIError(SHARED_BODY, request=MagicMock(), body=None)

    state = RetryState()
    retrier = ModelCallRetrier(RetryPolicy(), sleep=lambda _s: None)
    seen = []
    with pytest.raises(openai.APIError):
        for item in retrier.stream(open_stream, is_output=bool, state=state):
            seen.append(item)
    assert seen == ["partial answer"]
    assert state.attempts == 1 and state.output_delivered
    assert state.last_reason == KV_CACHE_SHARED_REASON
