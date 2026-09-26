# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The model gateway: the upstream key goes in, the client's goes nowhere."""

import gzip
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from gaia.eval.bench import gateway as gw

UPSTREAM_KEY = "lemonade-upstream-key-0123456789"
# Built in pieces: a leak scan over this repository must find no string that
# looks like a credential, not even a made-up one.
CLIENT_CREDENTIAL = "sk-" + "ant-oat01-client-subscription-credential"

pytestmark = pytest.mark.allow_network


class _Upstream(BaseHTTPRequestHandler):
    """Records each request; answers like Lemonade does."""

    protocol_version = "HTTP/1.1"
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        type(self).seen.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
        )
        if self.path.endswith("/v1/messages"):
            payload = (
                'event: message_start\ndata: {"type":"message_start","message":'
                '{"usage":{"input_tokens":120,"cache_read_input_tokens":30}}}\n\n'
                'event: message_delta\ndata: {"type":"message_delta",'
                '"usage":{"output_tokens":7}}\n\n'
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
        else:
            payload = gzip.compress(
                json.dumps(
                    {
                        "choices": [{"message": {"content": "hi"}}],
                        "usage": {
                            "prompt_tokens": 50,
                            "completion_tokens": 5,
                            "prompt_tokens_details": {"cached_tokens": 20},
                        },
                    }
                ).encode()
            )
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-encoding", "gzip")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def upstream():
    _Upstream.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    server.shutdown()
    server.server_close()


def test_the_client_credential_is_replaced_by_the_upstream_key(upstream, caplog):
    caplog.set_level(logging.DEBUG)
    with gw.Gateway(upstream, UPSTREAM_KEY) as gateway:
        reply = requests.post(
            f"{gateway.url}/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "x-api-key": CLIENT_CREDENTIAL,
                "authorization": f"Bearer {CLIENT_CREDENTIAL}",
            },
            timeout=30,
        )
    assert reply.status_code == 200
    headers = {k.lower(): v for k, v in _Upstream.seen[0]["headers"].items()}
    assert headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert "x-api-key" not in headers
    assert CLIENT_CREDENTIAL not in json.dumps(_Upstream.seen)
    assert UPSTREAM_KEY not in caplog.text and CLIENT_CREDENTIAL not in caplog.text


def test_system_entries_inside_messages_survive_as_user_entries(upstream):
    messages = [
        {"role": "user", "content": "task"},
        {"role": "system", "content": "cwd: /work/toybox, git: clean"},
        {"role": "assistant", "content": "ok"},
    ]
    with gw.Gateway(upstream, UPSTREAM_KEY) as gateway:
        requests.post(
            f"{gateway.url}/v1/messages",
            json={"model": "m", "system": "top-level stays", "messages": messages},
            timeout=30,
        )
    sent = _Upstream.seen[0]["body"]
    assert [m["role"] for m in sent["messages"]] == ["user", "user", "assistant"]
    assert sent["messages"][1]["content"] == "cwd: /work/toybox, git: clean"
    assert sent["system"] == "top-level stays"


def test_openai_calls_pass_through_untouched_and_decoded(upstream):
    body = {"model": "m", "messages": [{"role": "system", "content": "prompt"}]}
    with gw.Gateway(upstream, UPSTREAM_KEY) as gateway:
        reply = requests.post(
            f"{gateway.url}/api/v1/chat/completions", json=body, timeout=30
        )
        usage = gateway.take_usage()
    assert reply.json()["choices"][0]["message"]["content"] == "hi"
    # A system prompt on the OpenAI route is Lemonade's to read; it is not rewritten.
    assert _Upstream.seen[0]["body"] == body
    assert (usage.calls, usage.input, usage.cached, usage.output) == (1, 50, 20, 5)


def test_streamed_usage_is_counted_per_call_and_taken_once(upstream):
    with gw.Gateway(upstream, UPSTREAM_KEY) as gateway:
        for _ in range(2):
            requests.post(
                f"{gateway.url}/v1/messages",
                json={"model": "m", "messages": []},
                timeout=30,
            ).content
        first, second = gateway.take_usage(), gateway.take_usage()
    assert (first.calls, first.input, first.cached, first.output) == (2, 300, 60, 14)
    assert second.calls == 0


def test_an_unreachable_upstream_is_a_named_502_and_counted():
    with gw.Gateway("http://127.0.0.1:9/api/v1", UPSTREAM_KEY) as gateway:
        reply = requests.post(
            f"{gateway.url}/api/v1/chat/completions", json={}, timeout=30
        )
        usage = gateway.take_usage()
    assert reply.status_code == 502
    assert reply.json()["error"]["type"] == "gateway_upstream_unreachable"
    assert usage.unreachable == 1


@pytest.mark.parametrize(
    "base, root",
    [
        ("http://localhost:8000/api/v1", "http://localhost:8000"),
        ("http://localhost:8000/api/v1/", "http://localhost:8000"),
        ("http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_paths_pass_through_as_sent(base, root):
    assert gw.upstream_root(base) == root


def test_a_body_that_is_not_json_is_forwarded_as_is():
    assert gw.keep_system_messages(b"not json") == b"not json"
