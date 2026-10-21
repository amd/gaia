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
    request. An entry is ``(status, body_bytes, content_type)``,
    ``("headers", status, json_body_bytes, {header: value})``, or
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
                    # An empty payload would be the terminating chunk itself.
                    if payload:
                        self.wfile.write(
                            f"{len(payload):x}\r\n".encode() + payload + b"\r\n"
                        )
                    self.wfile.flush()
                    # No terminating 0-length chunk: the client sees a
                    # connection that died mid-body.
                    self.close_connection = True
                    return
                extra = {}
                if entry[0] == "headers":
                    _, status, body, extra = entry
                    content_type = "application/json"
                else:
                    status, body, content_type = entry
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                for name, value in extra.items():
                    self.send_header(name, value)
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


# ── the AMD LLM gateway's failures, as Lemonade relays them ──────────────
#
# Tasks died on a gateway 429 and on a 502 (#5087). A cloud failure that
# arrives once the stream has opened comes as an SSE error frame inside an
# HTTP 200 response, carrying the upstream status; this frame is the one
# captured live from Lemonade 2026.40.0 (#4898).


def _lemonade_error_frame(status):
    frame = {
        "error": {
            "message": "cloud (amd) request failed",
            "status_code": status,
            "type": "backend_error",
        }
    }
    return (200, _sse([frame], done=False), "text/event-stream")


_GOOD_STREAM = (
    200,
    _sse([_delta_chunk(role="assistant"), _delta_chunk("Hello", finish="stop")]),
    "text/event-stream",
)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_streamed_gateway_failure_frame_is_retried(make_client, status):
    script = [_lemonade_error_frame(status), _GOOD_STREAM]
    with FakeServer(script) as server:
        client = make_client(server)
        chunks = list(
            client.chat_completions(
                model=MODEL, messages=[{"role": "user", "content": "hi"}], stream=True
            )
        )
    assert _stream_text(chunks) == "Hello"
    assert len(server.requests) == 2
    assert client.last_request_retries == 1


def test_streamed_rejection_frame_is_not_retried(make_client):
    script = [_lemonade_error_frame(400)]
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


def test_cloud_rate_limit_gives_up_with_its_own_message_and_retry_state(
    make_client, monkeypatch
):
    """A cloud model that stays rate-limited still ends with the existing
    plain message, now carrying the retry record the agent loop acts on."""
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "3")
    script = [_lemonade_error_frame(429)] * 3
    with FakeServer(script) as server:
        client = make_client(server)
        client.cloud_model_provider = lambda *a, **k: "amd"
        with pytest.raises(LemonadeClientError, match="Cloud rate limit reached") as e:
            list(
                client.chat_completions(
                    model=MODEL,
                    messages=[{"role": "user", "content": "hi"}],
                    stream=True,
                )
            )
    assert len(server.requests) == 3
    state = retry_state_of(e.value)
    assert state is not None and state.attempts == 3
    assert state.last_reason == "HTTP 429"
    # Full jitter: retry n waits at most base * 2**n (base 2 s).
    assert len(client.sleeps) == 2
    assert 0.0 <= client.sleeps[0] <= 2.0
    assert 0.0 <= client.sleeps[1] <= 4.0


# #5087: the gateway sent no response headers for 34 s, then dropped
# the connection. Lemonade 2026.40.0 logged "Stream ended with: Failure when
# receiving data from the peer (response code: 0)" and relayed this frame,
# which carries no status_code.
_SILENT_DROP = (
    "backend connection failed during SSE stream before DONE: "
    "CURL error: Failure when receiving data from the peer"
)


def _streaming_error_frame(message):
    frame = {"error": {"message": message, "type": "streaming_error"}}
    return (200, _sse([frame], done=False), "text/event-stream")


def test_status_less_cloud_drop_is_retried(make_client):
    script = [_streaming_error_frame(_SILENT_DROP), _GOOD_STREAM]
    with FakeServer(script) as server:
        client = make_client(server)
        client.cloud_model_provider = lambda *a, **k: "amd"
        assert _complete(client, True) == "Hello"
    assert len(server.requests) == 2
    assert client.last_request_retries == 1


def test_status_less_cloud_drop_gives_up_in_plain_words(make_client, monkeypatch):
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "3")
    script = [_streaming_error_frame(_SILENT_DROP)] * 3
    with FakeServer(script) as server:
        client = make_client(server)
        client.cloud_model_provider = lambda *a, **k: "amd"
        with pytest.raises(LemonadeClientError) as raised:
            _complete(client, True)
    assert len(server.requests) == 3
    message = str(raised.value)
    assert message.startswith("The cloud provider dropped the connection")
    assert "provider settings" not in message
    assert "CURL" not in message
    state = retry_state_of(raised.value)
    assert state is not None and state.last_reason == "connection dropped"


def test_cloud_timeout_frame_is_retried_with_its_own_message(make_client, monkeypatch):
    monkeypatch.setenv("GAIA_LLM_RETRY_ATTEMPTS", "2")
    frame = _streaming_error_frame("SSE stream failed: CURL error: Timeout was reached")
    with FakeServer([frame, frame]) as server:
        client = make_client(server)
        client.cloud_model_provider = lambda *a, **k: "amd"
        with pytest.raises(LemonadeClientError, match="did not answer within"):
            _complete(client, True)
    assert len(server.requests) == 2


def test_cloud_model_not_loaded_is_not_retried(make_client):
    frame = {"error": {"message": "Cloud model not loaded", "type": "model_not_loaded"}}
    script = [(200, _sse([frame], done=False), "text/event-stream")]
    with FakeServer(script) as server:
        client = make_client(server)
        client.cloud_model_provider = lambda *a, **k: "amd"
        with pytest.raises(LemonadeClientError) as raised:
            _complete(client, True)
    assert len(server.requests) == 1
    assert client.sleeps == []
    assert retry_state_of(raised.value) is None


def _rate_limited(retry_after=None):
    body = json.dumps({"error": {"message": "Too Many Requests"}}).encode()
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return ("headers", 429, body, headers)


def _complete(client, stream):
    result = client.chat_completions(
        model=MODEL, messages=[{"role": "user", "content": "hi"}], stream=stream
    )
    if stream:
        return _stream_text(list(result))
    return result["choices"][0]["message"]["content"]


@pytest.mark.parametrize("stream", [False, True], ids=["non-streaming", "streaming"])
@pytest.mark.parametrize(
    "retry_after, expected",
    [("7", 7.0), ("600", 30.0)],
    ids=["honoured", "capped-at-max-delay"],
)
def test_http_429_waits_as_long_as_retry_after_says(
    make_client, stream, retry_after, expected
):
    script = [
        _rate_limited(retry_after),
        _GOOD_STREAM if stream else _json(200, COMPLETION),
    ]
    with FakeServer(script) as server:
        client = make_client(server)
        assert _complete(client, stream) == "Hello"
    assert client.sleeps == [expected]
    assert len(server.requests) == 2


@pytest.mark.parametrize("stream", [False, True], ids=["non-streaming", "streaming"])
def test_http_429_without_retry_after_backs_off_with_jitter(make_client, stream):
    script = [
        _rate_limited(),
        _rate_limited(),
        _GOOD_STREAM if stream else _json(200, COMPLETION),
    ]
    with FakeServer(script) as server:
        client = make_client(server)
        assert _complete(client, stream) == "Hello"
    assert len(client.sleeps) == 2
    assert 0.0 <= client.sleeps[0] <= 2.0
    assert 0.0 <= client.sleeps[1] <= 4.0
    assert len(server.requests) == 3


def test_connection_reset_before_output_is_retried(make_client):
    """A connection dropped before any token (right after the headers, or
    after only the role frame) is resent; the text arrives exactly once."""
    script = [
        ("cut", b""),
        ("cut", _sse([_delta_chunk(role="assistant")], done=False)),
        _GOOD_STREAM,
    ]
    with FakeServer(script) as server:
        client = make_client(server)
        assert _complete(client, True) == "Hello"
    assert len(server.requests) == 3
    assert client.last_request_retries == 2
