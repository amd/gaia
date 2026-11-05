# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model that fails to load is reported as a load failure, not a network blip (#6632).

Lemonade answers a failed load with HTTP 500 and ``code: model_load_error``.
GAIA used to read every 5xx as "Couldn't reach the local LLM ... try again",
which hid the real reason (an NPU driver that fails validation, out of memory,
a broken model) and promised that a resend would help.

The errors here are raised by the real stack: ``requests`` against a stub
HTTP server for the plain path, the OpenAI SDK against the same stub for
streaming, and ``_ensure_model_loaded`` wrapping a real ``/load`` 500.
"""

import json
import socket
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest
import requests
import responses

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    embedded_lemonade_log_path,
    lemonade_log_hint,
)
from gaia.llm.providers.lemonade import (
    LemonadeModelLoadError,
    LemonadeNetworkError,
    LemonadeServerError,
    LemonadeServerUnreachableError,
    _classify_lemonade_response,
    classify_lemonade_exception,
)
from gaia.llm.retry import classify_http_failure
from tests.unit.test_lemonade_retry_fake_server import FakeServer, _json

pytestmark = pytest.mark.allow_network

FLM_MODEL = "gemma4-it-e4b-FLM"
DRIVER_URL = "https://lemonade-server.ai/driver_install.html"

#: The body Lemonade sent in #6632 when FastFlowLM's NPU check failed.
FLM_LOAD_ERROR = {
    "error": {
        "code": "model_load_error",
        "message": (
            f"Failed to load model '{FLM_MODEL}': FLM NPU validation failed: "
            "flm validate failed with exit code 1\n"
            f"Visit {DRIVER_URL} to install or update the NPU driver.\n"
            "  at FlmServer::validate (flm_server.cpp:212)"
        ),
        "type": "model_load_error",
    }
}

GENERIC_500 = {"error": {"message": "The request fails unexpectedly"}}

#: FLM's memlock failure as Lemonade words it: the driver page is appended to
#: every validation failure, though the driver is not the problem here.
FLM_MEMLOCK_ERROR = {
    "error": {
        "code": "model_load_error",
        "message": (
            f"Failed to load model '{FLM_MODEL}': FLM NPU validation failed: "
            "Memlock limits are too low\n"
            f"Visit {DRIVER_URL} for driver installation instructions."
        ),
        "type": "model_load_error",
    }
}
MEMLOCK_GUIDE = "https://amd-gaia.ai/docs/guides/npu#the-locked-memory-limit"


def _assert_flm_load_failure(classified):
    assert isinstance(classified, LemonadeModelLoadError)
    assert classified.retryable is False
    message = classified.user_message
    assert message.startswith(
        f"The model `{FLM_MODEL}` couldn't load: FLM NPU validation failed: "
        "flm validate failed with exit code 1."
    )
    assert "fail the same way" in message
    assert f"{DRIVER_URL}." in message
    assert "Couldn't reach" not in message
    # The trace below Lemonade's first line stays in the log, not the chat.
    assert "flm_server.cpp" not in message
    assert "Visit" not in message


@pytest.fixture
def make_client(monkeypatch):
    for name in (
        "GAIA_LLM_RETRY_BASE_DELAY",
        "GAIA_LLM_RETRY_MAX_DELAY",
        "GAIA_LLM_RETRY_DEADLINE",
        "LEMONADE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "1")

    def make(port):
        client = LemonadeClient(host="127.0.0.1", port=port, verbose=False)
        client._ensure_model_loaded = lambda *a, **k: None
        client._model_slot_lease = lambda *a, **k: nullcontext()
        client.cloud_model_provider = lambda *a, **k: None
        client._retry_sleep = lambda _seconds: None
        return client

    return make


def _chat(client, stream):
    result = client.chat_completions(
        model=FLM_MODEL, messages=[{"role": "user", "content": "hi"}], stream=stream
    )
    if stream:
        list(result)
    return result


# ── the chat request itself answers 500 model_load_error ──────────────────


@pytest.mark.parametrize("stream", [False, True], ids=["requests", "openai-sdk"])
def test_chat_500_model_load_error_is_a_load_failure(make_client, stream):
    with FakeServer([_json(500, FLM_LOAD_ERROR)] * 3) as server:
        client = make_client(server.port)
        with pytest.raises(LemonadeClientError) as raised:
            _chat(client, stream)
    _assert_flm_load_failure(classify_lemonade_exception(raised.value))
    # Not retried: the same load fails the same way.
    assert len(server.requests) == 1


def test_retry_policy_does_not_resend_a_failed_load():
    assert classify_http_failure(500, json.dumps(FLM_LOAD_ERROR)) is None
    crashed = {
        "error": {
            "code": "model_load_error",
            "message": "Failed to load model 'X': llama-server failed to start",
        }
    }
    assert classify_http_failure(500, json.dumps(crashed)) == "HTTP 500"


# ── the pre-flight /load answers 500 model_load_error ─────────────────────


@responses.activate
def test_preflight_load_500_is_a_load_failure_that_names_the_model(monkeypatch):
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    client = LemonadeClient(host="127.0.0.1", port=13999, verbose=False)
    client._check_free_memory = lambda *a, **k: None
    base = client.base_url
    responses.add(
        responses.GET,
        f"{base}/health",
        json={"status": "ok", "model_loaded": None, "all_models_loaded": []},
    )
    responses.add(
        responses.GET,
        f"{base}/models",
        json={"data": [{"id": FLM_MODEL, "recipe": "flm", "downloaded": True}]},
    )
    responses.add(responses.POST, f"{base}/load", json=FLM_LOAD_ERROR, status=500)

    with pytest.raises(LemonadeClientError) as raised:
        client._ensure_model_loaded(FLM_MODEL)

    text = str(raised.value)
    assert "Request failed with status 500" in text
    assert "~/.cache/lemonade/server.log" not in text
    _assert_flm_load_failure(classify_lemonade_exception(raised.value))


def test_load_error_keeps_its_reason_through_an_agent_wrapper():
    inner = LemonadeClientError(
        f"Request failed with status 500: {json.dumps(FLM_LOAD_ERROR)}"
    )
    try:
        try:
            raise inner
        except LemonadeClientError as e:
            raise RuntimeError(f"LLM call failed: {e}") from e
    except RuntimeError as wrapped:
        _assert_flm_load_failure(classify_lemonade_exception(wrapped))


def test_agent_answer_carries_the_load_reason():
    from gaia.agents.base.agent import Agent

    agent = MagicMock(spec=Agent)
    for name in (
        "_extract_lemonade_user_message",
        "_model_call_failed_answer",
        "_model_failure_answer",
    ):
        setattr(agent, name, getattr(Agent, name).__get__(agent))
    agent._model_server_down = Agent._model_server_down
    agent._model_service_label = lambda: "Lemonade on this machine"
    agent._turn_model_retries = {}

    exc = LemonadeClientError(
        f"Request failed with status 500: {json.dumps(FLM_LOAD_ERROR)}"
    )
    answer = agent._model_call_failed_answer(
        exc, agent._extract_lemonade_user_message(exc), 1, []
    )
    assert "FLM NPU validation failed" in answer
    assert FLM_MODEL in answer
    assert "Couldn't reach" not in answer
    assert "fail the same way" in answer
    # The server answered, so the turn is not flagged as a down server.
    assert "server_down" not in agent._turn_model_retries


def test_response_dict_with_load_code_is_a_load_failure():
    err, is_err = _classify_lemonade_response(FLM_LOAD_ERROR, FLM_MODEL)
    assert is_err is True
    _assert_flm_load_failure(err)


def test_a_backend_that_crashed_at_start_may_load_next_time():
    body = {
        "error": {
            "code": "model_load_error",
            "message": "Failed to load model 'Gemma-4-E4B-it-GGUF': "
            "llama-server failed to start",
            "type": "model_load_error",
        }
    }
    classified = classify_lemonade_exception(
        LemonadeClientError(f"Request failed with status 500: {json.dumps(body)}")
    )
    assert isinstance(classified, LemonadeModelLoadError)
    assert classified.retryable is True
    assert "send the message again" in classified.user_message


def _assert_memlock_points_at_gaias_guide(classified):
    assert isinstance(classified, LemonadeModelLoadError)
    assert classified.retryable is False
    message = classified.user_message
    assert "FLM NPU validation failed: Memlock limits are too low." in message
    assert f"{MEMLOCK_GUIDE}." in message
    # The driver page is not the fix for a memlock failure.
    assert DRIVER_URL not in message
    assert "Lemonade's help for this error" not in message
    assert classified.help_url is None


@pytest.mark.parametrize("stream", [False, True], ids=["requests", "openai-sdk"])
def test_memlock_failure_points_at_gaias_npu_guide(make_client, stream):
    with FakeServer([_json(500, FLM_MEMLOCK_ERROR)] * 3) as server:
        client = make_client(server.port)
        with pytest.raises(LemonadeClientError) as raised:
            _chat(client, stream)
    classified = classify_lemonade_exception(raised.value)
    _assert_memlock_points_at_gaias_guide(classified)
    assert classified.model_id == FLM_MODEL
    assert len(server.requests) == 1


def test_memlock_failure_without_a_model_prefix_points_at_the_guide():
    """The body as a repro captured it: ``type`` only, no prefix or driver link."""
    body = {
        "error": {
            "type": "model_load_error",
            "message": "FLM NPU validation failed: Memlock limits are too low.",
        }
    }
    err, is_err = _classify_lemonade_response(body, FLM_MODEL)
    assert is_err is True
    assert MEMLOCK_GUIDE in err.user_message
    assert "Lemonade's help for this error" not in err.user_message


def test_a_driver_failure_keeps_lemonades_driver_page():
    classified = classify_lemonade_exception(
        LemonadeClientError(
            f"Request failed with status 500: {json.dumps(FLM_LOAD_ERROR)}"
        )
    )
    assert f"Lemonade's help for this error: {DRIVER_URL}." in classified.user_message
    assert MEMLOCK_GUIDE not in classified.user_message


# ── other 5xx: the server answered, so never "couldn't reach" ─────────────


@pytest.mark.parametrize("stream", [False, True], ids=["requests", "openai-sdk"])
def test_generic_500_says_the_server_answered(make_client, stream):
    with FakeServer([_json(500, GENERIC_500)] * 3) as server:
        client = make_client(server.port)
        with pytest.raises(LemonadeClientError) as raised:
            _chat(client, stream)
    classified = classify_lemonade_exception(raised.value)
    assert isinstance(classified, LemonadeServerError)
    assert classified.retryable is True
    assert classified.user_message.startswith(
        "The local LLM server answered with an error (HTTP 500): "
        "The request fails unexpectedly."
    )


def test_502_from_a_crashed_backend_says_the_server_answered(make_client):
    with FakeServer([_json(502, {"error": {"message": "Bad Gateway"}})]) as server:
        client = make_client(server.port)
        with pytest.raises(LemonadeClientError) as raised:
            _chat(client, False)
    classified = classify_lemonade_exception(raised.value)
    assert isinstance(classified, LemonadeServerError)
    assert "(HTTP 502)" in classified.user_message


# ── no HTTP response at all: the server is down ───────────────────────────


@pytest.mark.parametrize("stream", [False, True], ids=["requests", "openai-sdk"])
def test_connection_refused_says_the_server_is_down(make_client, stream):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = make_client(port)
    # The plain path lets requests' ConnectionError through unwrapped.
    with pytest.raises((LemonadeClientError, requests.ConnectionError)) as raised:
        _chat(client, stream)
    classified = classify_lemonade_exception(raised.value)
    # Still a connectivity failure, but a resend fails until the server starts.
    assert isinstance(classified, LemonadeServerUnreachableError)
    assert isinstance(classified, LemonadeNetworkError)
    assert classified.retryable is False
    assert f"127.0.0.1:{port}" in classified.user_message
    assert "isn't answering" in classified.user_message
    assert not isinstance(classified, (LemonadeServerError, LemonadeModelLoadError))


# ── the log a remedy points at ────────────────────────────────────────────


def test_gaias_own_server_names_its_lemond_log(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.running_embedded_lemonade_url",
        lambda: "http://localhost:41234/api/v1",
    )
    path = embedded_lemonade_log_path("http://127.0.0.1:41234/api/v1")
    assert path == tmp_path / "lemonade" / "lemond.log"
    assert str(path) in lemonade_log_hint("http://127.0.0.1:41234/api/v1")


def test_an_installed_server_is_not_given_a_guessed_path(monkeypatch):
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.running_embedded_lemonade_url",
        lambda: "http://localhost:41234/api/v1",
    )
    assert embedded_lemonade_log_path("http://localhost:13305/api/v1") is None
    hint = lemonade_log_hint("http://localhost:13305/api/v1")
    assert hint == "Lemonade's server log has the full error."
    assert "server.log" not in hint


def test_a_bare_error_body_with_only_a_message_keeps_its_detail():
    """openai 3.x hands over only the inner error object and drops it from str()."""
    import httpx
    import openai

    request = httpx.Request("POST", "http://127.0.0.1:13305/api/v1/chat/completions")
    # The exact shape openai 3.46 raises for Lemonade's ``{"error": {"message": ...}}``.
    sdk_error = openai.InternalServerError(
        "Error code: 500 - The request fails unexpectedly",
        response=httpx.Response(500, request=request),
        body={"message": "The request fails unexpectedly"},
    )
    assert sdk_error.status_code == 500
    assert "{" not in str(sdk_error)

    with pytest.raises(LemonadeClientError) as raised:
        try:
            raise sdk_error
        except openai.InternalServerError as inner:
            raise LemonadeClientError(
                "Streaming chat completion failed (HTTP 500)"
            ) from inner
    classified = classify_lemonade_exception(raised.value)
    assert isinstance(classified, LemonadeServerError)
    assert classified.user_message.startswith(
        "The local LLM server answered with an error (HTTP 500): "
        "The request fails unexpectedly."
    )
