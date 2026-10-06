# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""LemonadeClient against a tiny local OpenAI-compatible server that fails on
purpose: N transient failures then success, a stream cut mid-way, permanent
rejections. Real sockets, real HTTP clients (``requests`` for the plain path,
the OpenAI SDK for streaming); only the backoff sleep is replaced.
"""

import json
import threading
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.retry import retry_state_of

pytestmark = pytest.mark.allow_network

MODEL = "Fake-Model-GGUF"


def _sse(chunks, *, done=True):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    if done:
        body += "data: [DONE]\n\n"
    return body.encode()


def _delta_chunk(content=None, role=None, finish=None):
    delta = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


USAGE_CHUNK = {
    "id": "chatcmpl-1",
    "object": "chat.completion.chunk",
    "created": 0,
    "model": MODEL,
    "choices": [],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}

COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": MODEL,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
}


class FakeServer:
    """Answers POST /api/v1/chat/completions from a script, one entry per
    request. An entry is ``(status, body_bytes, content_type)`` or
    ``("cut", body_bytes)``: send *body_bytes* as chunked SSE, then drop the
    connection without the terminating chunk."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                server.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                entry = server.script.pop(0)
                if entry[0] == "cut":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    payload = entry[1]
                    self.wfile.write(
                        f"{len(payload):x}\r\n".encode() + payload + b"\r\n"
                    )
                    self.wfile.flush()
                    # No terminating 0-length chunk: the client sees a
                    # connection that died mid-body.
                    self.close_connection = True
                    return
                status, body, content_type = entry
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _json(status, payload):
    return (status, json.dumps(payload).encode(), "application/json")


def _text(status, text):
    return (status, text.encode(), "text/plain")


@pytest.fixture
def make_client(monkeypatch):
    for name in (
        "GAIA_LLM_RETRY_ATTEMPTS",
        "GAIA_LLM_RETRY_BASE_DELAY",
        "GAIA_LLM_RETRY_MAX_DELAY",
        "GAIA_LLM_RETRY_DEADLINE",
        "LEMONADE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    def make(server):
        client = LemonadeClient(host="127.0.0.1", port=server.port, verbose=False)
        # The server is a bare OpenAI-compatible endpoint: no model management.
        client._ensure_model_loaded = lambda *a, **k: None
        client._model_slot_lease = lambda *a, **k: nullcontext()
        client.cloud_model_provider = lambda *a, **k: None
        client.sleeps = []
        client._retry_sleep = client.sleeps.append
        return client

    return make


def _stream_text(chunks):
    return "".join(
        (c["choices"][0]["delta"].get("content") or "") for c in chunks if c["choices"]
    )


# ── non-streaming (requests) ─────────────────────────────────────────────


def test_non_streaming_recovers_after_transient_failures(make_client):
    script = [
        _json(503, {"error": {"message": "Service Unavailable"}}),
        _text(500, "An error occurred while sending the request."),
        _json(200, COMPLETION),
    ]
    with FakeServer(script) as server:
        client = make_client(server)
        result = client.chat_completions(
            model=MODEL, messages=[{"role": "user", "content": "hi"}]
        )
    assert result["choices"][0]["message"]["content"] == "Hello"
    assert len(server.requests) == 3
    # Each attempt sent the identical request.
    assert server.requests[0] == server.requests[1] == server.requests[2]
    assert client.last_request_retries == 2
    assert len(client.sleeps) == 2


def test_non_streaming_permanent_500_is_not_retried(make_client):
    script = [_text(500, "Request type is not supported for this deployment.")]
    with FakeServer(script) as server:
        client = make_client(server)
        with pytest.raises(LemonadeClientError) as raised:
            client.chat_completions(
                model=MODEL, messages=[{"role": "user", "content": "hi"}]
            )
    assert len(server.requests) == 1
    assert client.sleeps == []
    assert retry_state_of(raised.value) is None


def test_non_streaming_content_filter_is_not_retried(make_client):
    script = [_json(400, {"error": {"code": "content_filter", "message": "blocked"}})]
    with FakeServer(script) as server:
        client = make_client(server)
        with pytest.raises(LemonadeClientError):
            client.chat_completions(
                model=MODEL, messages=[{"role": "user", "content": "hi"}]
            )
    assert len(server.requests) == 1


def test_non_streaming_gives_up_with_retry_state(make_client, monkeypatch):
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "3")
    script = [_json(504, {"error": {"message": "Gateway Timeout"}})] * 3
    with FakeServer(script) as server:
        client = make_client(server)
        with pytest.raises(LemonadeClientError) as raised:
            client.chat_completions(
                model=MODEL, messages=[{"role": "user", "content": "hi"}]
            )
    assert len(server.requests) == 3
    state = retry_state_of(raised.value)
    assert state is not None and state.attempts == 3
    assert state.last_reason == "HTTP 504"


# ── streaming (OpenAI SDK) ───────────────────────────────────────────────


def test_streaming_recovers_before_first_token_without_duplication(make_client):
    script = [
        _json(502, {"error": {"message": "Bad Gateway"}}),
        # Headers and a role-only frame, then the connection dies.
        ("cut", _sse([_delta_chunk(role="assistant")], done=False)),
        (
            200,
            _sse(
                [
                    _delta_chunk(role="assistant"),
                    _delta_chunk("Hel"),
                    _delta_chunk("lo", finish="stop"),
                    USAGE_CHUNK,
                ]
            ),
            "text/event-stream",
        ),
    ]
    with FakeServer(script) as server:
        client = make_client(server)
        chunks = list(
            client.chat_completions(
                model=MODEL, messages=[{"role": "user", "content": "hi"}], stream=True
            )
        )
    assert _stream_text(chunks) == "Hello"
    roles = [c for c in chunks if c["choices"] and c["choices"][0]["delta"]["role"]]
    assert len(roles) == 1
    assert chunks[-1]["usage"]["total_tokens"] == 7
    assert len(server.requests) == 3
    assert client.last_request_retries == 2


def test_streaming_cut_after_tokens_is_not_retried(make_client):
    script = [
        (
            "cut",
            _sse(
                [_delta_chunk(role="assistant"), _delta_chunk("Partial ")], done=False
            ),
        ),
        (200, _sse([_delta_chunk("must not be sent")]), "text/event-stream"),
    ]
    received = []
    with FakeServer(script) as server:
        client = make_client(server)
        with pytest.raises(LemonadeClientError) as raised:
            for chunk in client.chat_completions(
                model=MODEL, messages=[{"role": "user", "content": "hi"}], stream=True
            ):
                received.append(chunk)
    assert _stream_text(received) == "Partial "
    assert len(server.requests) == 1
    state = retry_state_of(raised.value)
    assert state is not None and state.output_delivered


def test_streaming_permanent_rejection_is_not_retried(make_client):
    script = [_json(422, {"error": {"message": "bad schema"}})]
    with FakeServer(script) as server:
        client = make_client(server)
        with pytest.raises(LemonadeClientError) as raised:
            list(
                client.chat_completions(
                    model=MODEL,
                    messages=[{"role": "user", "content": "hi"}],
                    stream=True,
                )
            )
    assert len(server.requests) == 1
    assert client.sleeps == []
    assert retry_state_of(raised.value) is None


def test_streaming_sdk_does_not_retry_on_its_own(make_client, monkeypatch):
    """One attempt per GAIA attempt: the SDK's built-in retries are off."""
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "1")
    script = [_json(503, {"error": {"message": "busy"}})] * 3
    with FakeServer(script) as server:
        client = make_client(server)
        with pytest.raises(LemonadeClientError):
            list(
                client.chat_completions(
                    model=MODEL,
                    messages=[{"role": "user", "content": "hi"}],
                    stream=True,
                )
            )
    assert len(server.requests) == 1
