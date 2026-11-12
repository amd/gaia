# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model server that answers like Lemonade and writes down every request.

GAIA's code under test talks to it over real HTTP, so nothing about the client
is mocked: a test asserts what reached the server (which model was pulled,
loaded or asked to chat), not which Python method was called.

    with FakeLemonade(models=[cloud("fireworks.deepseek-v4p1-flash")]) as server:
        monkeypatch.setenv("LEMONADE_BASE_URL", server.base_url)
        ...run GAIA code...
        assert server.pulls == []

It downloads nothing and loads nothing; "pull" and "load" only change what its
model list and health report afterwards.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

#: What ``/system-info`` reports for a PC with a GPU and no NPU.
GPU_ONLY = {
    "OS Version": "Windows 11",
    "Processor": "AMD Ryzen AI test CPU",
    "Physical Memory": "128.0 GB",
    "devices": {
        "cpu": {"name": "test CPU", "available": True},
        "amd_igpu": {"name": "test GPU", "available": True, "vram_gb": 96.0},
        "npu": {"name": "", "available": False},
    },
}


def local(
    model_id: str, downloaded: bool = True, size_gb: float = 5.0, **extra
) -> dict:
    """A model that runs on this PC."""
    return {
        "id": model_id,
        "object": "model",
        "recipe": "llamacpp",
        "downloaded": downloaded,
        "suggested": True,
        "labels": ["tool-calling"],
        "size": size_gb,
        **extra,
    }


def npu(model_id: str, downloaded: bool = True, size_gb: float = 9.1, **extra) -> dict:
    """A FastFlowLM model, which only a PC with an NPU can run."""
    return {**local(model_id, downloaded, size_gb, **extra), "recipe": "flm"}


def cloud(model_id: str, **extra) -> dict:
    """A model a cloud provider runs; there is nothing to download."""
    return {
        "id": model_id,
        "object": "model",
        "recipe": "cloud",
        "cloud_provider": model_id.partition(".")[0],
        "downloaded": True,
        "labels": ["tool-calling"],
        **extra,
    }


@dataclass
class Request:
    """One request the server received."""

    method: str
    path: str
    query: dict
    body: Any

    @property
    def model(self) -> Optional[str]:
        """The model the request names, wherever that endpoint puts it."""
        if not isinstance(self.body, dict):
            return None
        return self.body.get("model_name") or self.body.get("model")


@dataclass
class FakeLemonade:
    """The server. Start it with ``with``; read ``requests`` and its views."""

    models: list = field(default_factory=list)
    system_info: dict = field(default_factory=lambda: json.loads(json.dumps(GPU_ONLY)))
    #: Text of each chat reply in turn; the last one repeats.
    replies: list = field(default_factory=lambda: ["ok"])
    #: Called with each chat request; its return value replaces ``replies``.
    on_chat: Optional[Callable[[Request], str]] = None
    #: ``{path suffix: (status, json body)}`` to answer instead of the default.
    overrides: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.requests: list[Request] = []
        self.loaded: list[str] = []
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- what a test reads --------------------------------------------------

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/api/v1"

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def _named(self, suffix: str) -> list[str]:
        return [
            r.model
            for r in self.requests
            if r.method == "POST" and r.path.endswith(suffix) and r.model
        ]

    @property
    def pulls(self) -> list[str]:
        """Models GAIA asked the server to download, in order."""
        return self._named("/pull")

    @property
    def loads(self) -> list[str]:
        """Models GAIA asked the server to load, in order."""
        return self._named("/load")

    @property
    def chats(self) -> list[str]:
        """The model of each chat request, in order."""
        return self._named("/chat/completions")

    @property
    def chat_requests(self) -> list[Request]:
        return [r for r in self.requests if r.path.endswith("/chat/completions")]

    @property
    def downloads(self) -> list[str]:
        """Models whose weights the real server would have fetched.

        A pull, and also a load or a chat on a model that is not downloaded:
        Lemonade fetches a missing model by itself before loading it.
        """
        return list(self._downloads)

    def model(self, model_id: str) -> Optional[dict]:
        return next((m for m in self.models if m["id"] == model_id), None)

    # -- the server ---------------------------------------------------------

    def __enter__(self) -> "FakeLemonade":
        self._downloads: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                pass

            def _handle(self, method: str) -> None:
                split = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw.decode("utf-8", "replace")
                request = Request(method, split.path, parse_qs(split.query), body)
                with fake._lock:
                    fake.requests.append(request)
                    status, payload, stream = fake._answer(request)
                if stream:
                    self._send_stream(payload)
                    return
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_stream(self, chunks: list) -> None:
                data = b"".join(
                    f"data: {json.dumps(chunk)}\n\n".encode("utf-8") for chunk in chunks
                )
                data += b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # pylint: disable=invalid-name
                self._handle("GET")

            def do_POST(self) -> None:  # pylint: disable=invalid-name
                self._handle("POST")

            def do_DELETE(self) -> None:  # pylint: disable=invalid-name
                self._handle("DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _fetch_if_missing(self, model_id: Optional[str]) -> None:
        entry = self.model(model_id) if model_id else None
        if entry is not None and not entry.get("downloaded"):
            self._downloads.append(model_id)
            entry["downloaded"] = True

    def _answer(self, request: Request) -> tuple[int, Any, bool]:
        path = request.path
        for suffix, (status, payload) in self.overrides.items():
            if path.endswith(suffix):
                return status, payload, False
        if path.endswith("/health"):
            return 200, self._health(), False
        if path.endswith("/models"):
            show_all = request.query.get("show_all", ["false"])[0] == "true"
            listed = [m for m in self.models if show_all or m.get("downloaded")]
            return 200, {"object": "list", "data": listed}, False
        if "/models/" in path:
            entry = self.model(path.rsplit("/models/", 1)[1])
            if entry is None:
                return 404, {"error": {"message": "model not found"}}, False
            return 200, entry, False
        if path.endswith("/system-info"):
            return 200, self.system_info, False
        if path.endswith("/pull"):
            if request.model and self.model(request.model) is None:
                self.models.append(local(request.model, downloaded=False))
            self._fetch_if_missing(request.model)
            return 200, {"status": "success"}, False
        if path.endswith("/load"):
            self._fetch_if_missing(request.model)
            if request.model and request.model not in self.loaded:
                self.loaded.append(request.model)
            return 200, {"status": "success"}, False
        if path.endswith("/unload"):
            if request.model in self.loaded:
                self.loaded.remove(request.model)
            elif not request.model:
                self.loaded.clear()
            return 200, {"status": "success"}, False
        if path.endswith("/chat/completions"):
            return self._chat(request)
        if path.endswith("/embeddings"):
            texts = (request.body or {}).get("input") or []
            texts = [texts] if isinstance(texts, str) else texts
            data = [
                {"object": "embedding", "index": i, "embedding": [0.0] * 8}
                for i, _ in enumerate(texts)
            ]
            return 200, {"object": "list", "data": data}, False
        return 200, {}, False

    def _health(self) -> dict:
        all_loaded = [
            {
                "model_name": name,
                "type": "llm",
                "recipe": (self.model(name) or {}).get("recipe", "llamacpp"),
                "ctx_size": 65536,
            }
            for name in self.loaded
        ]
        return {
            "status": "ok",
            "version": "10.8.0",
            "model_loaded": self.loaded[-1] if self.loaded else None,
            "all_models_loaded": all_loaded,
        }

    def _chat(self, request: Request) -> tuple[int, Any, bool]:
        body = request.body if isinstance(request.body, dict) else {}
        model = body.get("model")
        entry = self.model(model) if model else None
        if entry is not None and entry.get("recipe") != "cloud":
            self._fetch_if_missing(model)
            if model not in self.loaded:
                self.loaded.append(model)
        if self.on_chat is not None:
            text = self.on_chat(request)
        else:
            sent = len(self.chat_requests) - 1
            text = self.replies[min(sent, len(self.replies) - 1)]
        usage = {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
        if body.get("stream"):
            first = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": text},
                        "finish_reason": None,
                    }
                ],
            }
            last = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            }
            return 200, [first, last], True
        return (
            200,
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            },
            False,
        )
