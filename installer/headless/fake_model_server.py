#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stand-in Lemonade Server for the installer journeys: no model, no GPU.

It answers the Lemonade and OpenAI-compatible endpoints GAIA's clients call --
health, the model list, load/unload, chat completions (streamed or not) and
embeddings -- with fixed, well-formed replies, so an installed gaia-tui and its
gaia-agent can complete one real turn on a hosted runner. Every model id it is
asked about counts as downloaded and loaded.

What it proves: the installed programs start, find each other, and complete a
turn over the real wire format. What it does not: that a model downloads or
runs. That needs hardware and is the job of the eval and hardware lanes.

    python fake_model_server.py --port-file port.txt --log requests.jsonl
    # then LEMONADE_BASE_URL=http://127.0.0.1:<port>/api/v1

Every request is appended to --log, so a turn that fails shows which endpoint
the client wanted and what it was told.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

ANSWER = "Hello from the fake model server. The install works."
EMBED_DIM = 768
DEFAULT_CTX = 65536

_lock = threading.Lock()
_loaded: Dict[str, int] = {}  # model id -> ctx_size
_log_path: Optional[Path] = None
_lemonade_version = "0.0.0"


def _log(entry: Dict[str, Any]) -> None:
    if _log_path is None:
        return
    with _lock, _log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _model_entry(model_id: str) -> Dict[str, Any]:
    embed = "embed" in model_id.lower()
    return {
        "id": model_id,
        "object": "model",
        "created": 1700000000,
        "owned_by": "lemonade",
        "checkpoint": f"fake/{model_id}",
        "recipe": "llamacpp",
        "downloaded": True,
        "suggested": True,
        "labels": ["embeddings"] if embed else ["llm", "tool-calling"],
        "size": 0.1,
    }


def _known_models() -> List[str]:
    with _lock:
        return sorted(_loaded)


def _remember(model_id: Optional[str], ctx: Optional[int] = None) -> None:
    if not model_id:
        return
    with _lock:
        _loaded[model_id] = int(ctx or _loaded.get(model_id) or DEFAULT_CTX)


def _embedding(text: str) -> List[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [
        ((digest[i % len(digest)] + i) % 251) / 251.0 - 0.5 for i in range(EMBED_DIM)
    ]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


class LoopbackServer(ThreadingHTTPServer):
    """HTTPServer without the reverse-DNS lookup in server_bind.

    The stock server_bind calls socket.getfqdn(), which stalls for seconds on
    hosted macOS runners -- long enough that the journey gave up waiting for
    the port file. A loopback test server has no use for its own FQDN.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FakeLemonade/1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib name
        sys.stderr.write("[fake-lemonade] " + fmt % args + "\n")

    # ── plumbing ──
    def _path(self) -> str:
        path = self.path.split("?", 1)[0]
        # /api/v1/x, /api/v0/x and /v1/x are all the same endpoint here.
        return re.sub(r"^/(api/)?v\d+", "", path) or "/"

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body: Optional[Dict[str, Any]], status: int) -> None:
        _log(
            {
                "t": time.time(),
                "method": self.command,
                "path": self.path,
                "status": status,
                "body_keys": sorted(body) if body else [],
                "model": (body or {}).get("model"),
            }
        )

    # ── GET ──
    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        path = self._path()
        if path == "/health":
            models = _known_models()
            payload = {
                "status": "ok",
                "version": _lemonade_version,
                "model_loaded": models[0] if models else None,
                "all_models_loaded": [
                    {
                        "model_name": m,
                        "checkpoint": f"fake/{m}",
                        "type": "embedding" if "embed" in m.lower() else "llm",
                        "device": "gpu",
                        "recipe": "llamacpp",
                        "recipe_options": {"ctx_size": _loaded[m]},
                        "backend_url": "http://127.0.0.1:0",
                    }
                    for m in models
                ],
                "max_models": {"llm": 4, "embedding": 2, "reranking": 1},
            }
            self._json(200, payload)
        elif path == "/models":
            self._json(
                200,
                {"object": "list", "data": [_model_entry(m) for m in _known_models()]},
            )
        elif path.startswith("/models/"):
            model_id = path[len("/models/") :]
            _remember(model_id)
            self._json(200, _model_entry(model_id))
        elif path == "/system-info":
            self._json(
                200,
                {
                    "OS Version": "fake",
                    "Processor": "fake CPU",
                    "Physical Memory": "64.0 GB",
                    "devices": {"cpu": {"name": "fake CPU", "available": True}},
                },
            )
        else:
            self._record(None, 404)
            self._json(
                404, {"error": {"message": f"fake server has no GET {self.path}"}}
            )
            return
        self._record(None, 200)

    # ── POST ──
    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        path = self._path()
        body = self._body()
        model = body.get("model") or body.get("model_name")
        if path in ("/load", "/pull"):
            _remember(model, body.get("ctx_size"))
            self._json(200, {"status": "success", "message": f"{path[1:]}ed {model}"})
        elif path in ("/unload", "/delete"):
            with _lock:
                _loaded.pop(model, None)
            self._json(200, {"status": "success"})
        elif path == "/chat/completions":
            _remember(model)
            self._chat(body, model or "fake-model")
        elif path == "/completions":
            _remember(model)
            self._json(
                200,
                {
                    "id": "cmpl-fake",
                    "object": "text_completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "text": ANSWER, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 10,
                        "total_tokens": 11,
                    },
                },
            )
        elif path == "/embeddings":
            _remember(model)
            inputs = body.get("input")
            items = inputs if isinstance(inputs, list) else [inputs or ""]
            self._json(
                200,
                {
                    "object": "list",
                    "model": model,
                    "data": [
                        {
                            "object": "embedding",
                            "index": i,
                            "embedding": _embedding(str(t)),
                        }
                        for i, t in enumerate(items)
                    ],
                    "usage": {"prompt_tokens": len(items), "total_tokens": len(items)},
                },
            )
        elif path in ("/params", "/install", "/uninstall"):
            self._json(200, {"status": "success"})
        else:
            self._record(body, 404)
            self._json(
                404, {"error": {"message": f"fake server has no POST {self.path}"}}
            )
            return
        self._record(body, 200)

    def _chat(self, body: Dict[str, Any], model: str) -> None:
        created = int(time.time())
        usage = {"prompt_tokens": 12, "completion_tokens": 12, "total_tokens": 24}
        if not body.get("stream"):
            self._json(
                200,
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": ANSWER},
                        }
                    ],
                    "usage": usage,
                },
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        base = {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
        }
        chunks = [
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": None,
                    }
                ],
            },
            *[
                {
                    **base,
                    "choices": [
                        {"index": 0, "delta": {"content": w}, "finish_reason": None}
                    ],
                }
                for w in re.findall(r"\S+\s*", ANSWER)
            ],
            {
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            },
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def main(argv: Optional[List[str]] = None) -> int:
    global _log_path, _lemonade_version
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 picks a free port")
    ap.add_argument("--port-file", help="write the bound port here once listening")
    ap.add_argument("--log", help="append one JSON line per request here")
    ap.add_argument(
        "--lemonade-version",
        default="",
        help="version /health reports; default: LEMONADE_VERSION from src/gaia/version.py",
    )
    ap.add_argument(
        "--model", action="append", default=[], help="model ids to list from the start"
    )
    args = ap.parse_args(argv)

    _log_path = Path(args.log) if args.log else None
    version = args.lemonade_version
    if not version:
        version_py = Path(__file__).resolve().parents[2] / "src" / "gaia" / "version.py"
        m = re.search(
            r'LEMONADE_VERSION = "([^"]+)"', version_py.read_text(encoding="utf-8")
        )
        if not m:
            print(
                f"error: no LEMONADE_VERSION in {version_py}; pass --lemonade-version",
                file=sys.stderr,
            )
            return 1
        version = m.group(1)
    _lemonade_version = version
    for m_id in args.model:
        _remember(m_id)

    server = LoopbackServer((args.host, args.port), Handler)
    port = server.server_address[1]
    if args.port_file:
        Path(args.port_file).write_text(str(port), encoding="utf-8")
    print(
        f"fake Lemonade {version} listening on http://{args.host}:{port}/api/v1",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
