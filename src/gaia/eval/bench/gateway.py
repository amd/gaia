# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A local gateway between an agent under test and Lemonade.

Both harnesses reach the model through it, so neither agent process holds a
key: the gateway adds the upstream credential itself. It also fixes two things
that stop Claude Code from working against Lemonade's Anthropic-format
``/v1/messages`` endpoint:

- Claude Code ignores ``ANTHROPIC_AUTH_TOKEN`` and sends its own subscription
  credential, which Lemonade rejects. Every client credential is dropped and
  replaced with the upstream's.
- Claude Code puts per-turn context (working directory, git state) in
  ``system``-role entries inside ``messages``, and Lemonade drops those
  (lemonade-sdk/lemonade#2662), about 9.5 KB a turn. They are rewritten as
  ``user`` entries in place, so the content and its position survive.

It counts tokens per call from the responses, since Claude Code reports none
against a non-Anthropic endpoint. Where the upstream reports none either —
Lemonade sends zero usage on a streamed Anthropic reply, for a local and a
cloud-routed model alike (observed 2026-09) — the run's cost reads "n/a" rather
than zero, and ``--meter`` is the way to price it. It never logs a header.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from gaia.logger import get_logger

log = get_logger(__name__)

#: Headers that belong to one hop, or carry the client's own credential.
_DROP_REQUEST = frozenset(
    {"host", "content-length", "x-api-key", "authorization", "connection"}
)
#: The body is forwarded decoded, so its encoding headers no longer apply.
_DROP_RESPONSE = frozenset(
    {"content-length", "transfer-encoding", "connection", "content-encoding"}
)
UPSTREAM_TIMEOUT_S = 1800


def upstream_root(base_url: str) -> str:
    """``http://host:port/api/v1`` -> ``http://host:port``: paths pass through as sent."""
    root = base_url.rstrip("/")
    for suffix in ("/api/v1", "/v1"):
        if root.endswith(suffix):
            return root[: -len(suffix)]
    return root


def keep_system_messages(body: bytes) -> bytes:
    """Rewrite ``system`` entries inside ``messages`` as ``user`` entries, in place."""
    try:
        data = json.loads(body)
    except ValueError:
        return body
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not any(
        isinstance(m, dict) and m.get("role") == "system" for m in messages
    ):
        return body
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            message["role"] = "user"
    return json.dumps(data).encode("utf-8")


def usage_from(payload: Any) -> Dict[str, int]:
    """Token counts from one response object, in either API's spelling."""
    if not isinstance(payload, dict):
        return {}
    message = payload.get("message")
    usage = payload.get("usage") or (
        message.get("usage") if isinstance(message, dict) else None
    )
    if not isinstance(usage, dict):
        return {}
    out: Dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
        out["cached_tokens"] = details["cached_tokens"]
    return out


def response_usage(body: bytes) -> Dict[str, int]:
    """Usage from a whole response: one JSON object, or a server-sent event stream.

    A streamed call reports input on its first event and output on its last,
    each cumulative, so the largest value per key is the call's total.
    """
    if body[:1] == b"{":
        try:
            return usage_from(json.loads(body))
        except ValueError:
            return {}
    raw: Dict[str, int] = {}
    for line in body.split(b"\n"):
        if not line.startswith(b"data: "):
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        for key, value in usage_from(event).items():
            raw[key] = max(raw.get(key, 0), value)
    return raw


def normalize_usage(raw: Dict[str, int]) -> Dict[str, int]:
    """``input`` (all prompt tokens), ``cached`` and ``output``, whichever API answered."""
    if "prompt_tokens" in raw or "completion_tokens" in raw:
        return {
            "input": raw.get("prompt_tokens", 0),
            "cached": raw.get("cached_tokens", 0),
            "output": raw.get("completion_tokens", 0),
        }
    cached = raw.get("cache_read_input_tokens", 0)
    return {
        "input": raw.get("input_tokens", 0)
        + cached
        + raw.get("cache_creation_input_tokens", 0),
        "cached": cached,
        "output": raw.get("output_tokens", 0),
    }


@dataclass
class Usage:
    calls: int = 0
    input: int = 0
    cached: int = 0
    output: int = 0
    #: Requests the gateway could not deliver: the model backend was not there.
    unreachable: int = 0
    records: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, record: Dict[str, Any]) -> None:
        tokens = record.get("tokens") or {}
        if record.get("unreachable"):
            self.unreachable += 1
        self.calls += 1
        self.input += tokens.get("input", 0)
        self.cached += tokens.get("cached", 0)
        self.output += tokens.get("output", 0)
        self.records.append(record)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "_Server"

    def log_message(self, *args: Any) -> None:  # the stdlib default prints headers
        pass

    def _forward(self, method: str) -> None:
        gateway = self.server.gateway
        path = self.path
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        if "/messages" in urlparse(path).path:
            body = keep_system_messages(body)
        headers = {
            k: v for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST
        }
        headers["Accept-Encoding"] = "identity"
        if gateway.api_key:
            headers["Authorization"] = f"Bearer {gateway.api_key}"
        started = time.time()
        try:
            upstream = requests.request(
                method,
                gateway.upstream + path,
                data=body or None,
                headers=headers,
                stream=True,
                timeout=UPSTREAM_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            message = json.dumps(
                {
                    "error": {
                        "type": "gateway_upstream_unreachable",
                        "message": f"GAIA bench gateway could not reach "
                        f"{gateway.upstream}: {type(exc).__name__}",
                    }
                }
            ).encode()
            log.error("gateway: %s %s -> unreachable upstream", method, path)
            # Counted before the client can see the reply and move on.
            gateway.record({"path": urlparse(path).path, "unreachable": True})
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return
        self.send_response(upstream.status_code)
        for key, value in upstream.headers.items():
            if key.lower() not in _DROP_RESPONSE:
                self.send_header(key, value)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        buffered = bytearray()
        for chunk in upstream.iter_content(chunk_size=None):
            if not chunk:
                continue
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
            buffered += chunk
        if method == "POST":
            raw = response_usage(bytes(buffered))
            # Counted before the closing chunk, so a caller that has the whole
            # response also has its count.
            gateway.record(
                {
                    "path": urlparse(path).path,
                    "status": upstream.status_code,
                    "seconds": round(time.time() - started, 2),
                    "tokens": normalize_usage(raw) if raw else {},
                }
            )
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_POST(self) -> None:  # noqa: N802 - the stdlib's naming
        self._forward("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._forward("GET")

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    gateway: "Gateway"

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A client that hangs up mid-stream (a killed agent) is routine here.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, BrokenPipeError)):
            log.debug("gateway: client %s went away: %s", client_address, exc)
            return
        super().handle_error(request, client_address)


class Gateway:
    """The gateway on ``127.0.0.1:port`` (0 picks a free port), in a thread."""

    def __init__(self, upstream_url: str, api_key: Optional[str], port: int = 0):
        self.upstream = upstream_root(upstream_url)
        self.api_key = api_key
        self._server = _Server(("127.0.0.1", port), _Handler)
        self._server.gateway = self
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._usage = Usage()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def record(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self._usage.add(record)

    def take_usage(self) -> Usage:
        """Everything counted since the last call. Runs are serial, so it is one task's."""
        with self._lock:
            usage, self._usage = self._usage, Usage()
        return usage

    def start(self) -> "Gateway":
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="gaia-bench-gateway", daemon=True
        )
        self._thread.start()
        log.info("gateway %s -> %s", self.url, self.upstream)
        return self

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "Gateway":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
