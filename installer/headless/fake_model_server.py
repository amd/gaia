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

With --catalog it plays a Lemonade on a PC that has downloaded nothing, for the
first-run setup journey. Only --model ids start downloaded. /pull streams
Lemonade's progress events, slowly enough for a bar to move, and refuses the
calls the real server refuses, so a request it accepts is one Lemonade would
take. Anything that needs a model it does not have fails, and only a model
labelled vision accepts an image. Environment:

    FAKE_LEMONADE_PULL_SECONDS     how long each pull takes (default 2)
    FAKE_LEMONADE_PULL_FAIL        disk_full | network | lemonade: pulls stop partway
    FAKE_LEMONADE_PULL_FAIL_TIMES  how many pulls fail before they work (default 1)
    FAKE_LEMONADE_LOAD_SECONDS     how long each /load takes (default 0)
    FAKE_LEMONADE_GPU              discrete (default) | igpu, what /system-info reports
    FAKE_LEMONADE_DISK_FREE_GB     free space in the model store (default 500)

A discrete GPU keeps a run the same on every runner: GAIA reads no free memory
for one, and it would otherwise refuse a load on a small hosted machine.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import socketserver
import struct
import sys
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

ANSWER = "Hello from the fake model server. The install works."
EMBED_DIM = 768
DEFAULT_CTX = 65536
VISION_ANSWER = "red"
TRANSCRIPT = "This is a test."

#: What a pull stops with, by FAKE_LEMONADE_PULL_FAIL.
PULL_ERRORS = {
    "disk_full": "No space left on device while writing the model file",
    "network": "Connection reset by peer while downloading the model file",
    "lemonade": "The model repository answered 500",
}

_CHAT_LABELS = ["chat", "tool-calling"]
#: --catalog's built-ins, with the ids, sizes and labels the real server lists.
CATALOG: Dict[str, Dict[str, Any]] = {
    "Gemma-4-E4B-it-GGUF": {
        "size": 5.56,
        "labels": ["chat", "tool-calling", "vision", "llamacpp"],
        "checkpoint": "unsloth/gemma-4-E4B-it-GGUF:Q4_K_M",
    },
    "Qwen3.6-35B-A3B-GGUF": {
        "size": 21.7,
        "labels": ["chat", "vision", "tool-calling"],
        "checkpoint": "unsloth/Qwen3.6-35B-A3B-GGUF:Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
    },
    "gpt-oss-120b-mxfp-GGUF": {
        "size": 63.4,
        "labels": ["chat", "reasoning", "tool-calling"],
        "checkpoint": "ggml-org/gpt-oss-120b-GGUF:gpt-oss-120b-MXFP4.gguf",
    },
    "Qwen3-VL-4B-Instruct-GGUF": {
        "size": 3.1,
        "labels": ["chat", "vision", "tool-calling"],
        "checkpoint": "Qwen/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M",
    },
    "Qwen3-0.6B-GGUF": {
        "size": 0.38,
        "labels": ["chat", "reasoning"],
        "checkpoint": "unsloth/Qwen3-0.6B-GGUF:Q4_0",
    },
    "Whisper-Large-v3-Turbo": {
        "size": 1.51,
        "labels": ["transcription", "realtime-transcription"],
        "recipe": "whispercpp",
        "checkpoint": "ggerganov/whisper.cpp:ggml-large-v3-turbo.bin",
    },
    "kokoro-v1": {
        "size": None,
        "labels": ["tts"],
        "recipe": "kokoro",
        "checkpoint": "mikkoph/kokoro-onnx",
    },
    "SDXL-Turbo": {
        "size": 6.46,
        "labels": ["image"],
        "recipe": "sd-cpp",
        "checkpoint": "stabilityai/sdxl-turbo:sd_xl_turbo_1.0_fp16.safetensors",
    },
}

_lock = threading.Lock()
_loaded: Dict[str, int] = {}  # model id -> ctx_size
_log_path: Optional[Path] = None
_lemonade_version = "0.0.0"
#: What --catalog lists; empty means every model id counts as downloaded.
_catalog: Dict[str, Dict[str, Any]] = {}
_downloaded: Dict[str, bool] = {}
_installed_backends: Dict[str, bool] = {}
_pull_failures_left = 0


def _log(entry: Dict[str, Any]) -> None:
    if _log_path is None:
        return
    with _lock, _log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


def _listed_id(model_id: Optional[str]) -> str:
    """The id a model is listed under: a registered ``user.`` model drops the prefix."""
    return (model_id or "").removeprefix("user.")


def _model_entry(model_id: str) -> Dict[str, Any]:
    embed = "embed" in model_id.lower()
    known = _catalog.get(_listed_id(model_id), {})
    entry = {
        "id": model_id,
        "object": "model",
        "created": 1700000000,
        "owned_by": "lemonade",
        "checkpoint": known.get("checkpoint", f"fake/{model_id}"),
        "recipe": known.get("recipe", "llamacpp"),
        "downloaded": _is_downloaded(model_id),
        "suggested": True,
        "labels": known.get(
            "labels", ["embeddings"] if embed else ["llm", "tool-calling"]
        ),
        "size": known.get("size", 0.1),
    }
    if _catalog:
        with _lock:
            ctx = _loaded.get(_listed_id(model_id))
        entry["recipe_options"] = {"ctx_size": ctx} if ctx else {}
    return entry


def _is_downloaded(model_id: Optional[str]) -> bool:
    return not _catalog or bool(_downloaded.get(_listed_id(model_id)))


def _known_models(show_all: bool = False) -> List[str]:
    with _lock:
        if not _catalog:
            return sorted(_loaded)
        return sorted(_catalog) if show_all else sorted(_downloaded)


def _fake_png() -> bytes:
    """A small two-colour PNG: GAIA rejects one flat colour as a failed generation."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    size = 8
    rows = b"".join(
        b"\x00" + b"".join(bytes((x * 30, 0, 200)) for x in range(size))
        for _ in range(size)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _has_image(body: Dict[str, Any]) -> bool:
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for part in content
        ):
            return True
    return False


def _gpu_devices() -> Dict[str, Any]:
    if os.environ.get("FAKE_LEMONADE_GPU") == "igpu":
        return {
            "amd_gpu": [
                {
                    "available": True,
                    "family": "gfx1151",
                    "integrated": True,
                    "name": "Fake Radeon Graphics",
                    "vram_gb": 8.0,
                }
            ]
        }
    return {
        "nvidia_gpu": [{"available": True, "name": "Fake GeForce", "vram_gb": 16.0}]
    }


def _system_info() -> Dict[str, Any]:
    def backend(recipe: str, name: str) -> Dict[str, Any]:
        installed = _installed_backends.get(f"{recipe}:{name}", not _catalog)
        return {
            "devices": ["cpu"],
            "format": "native",
            "state": "installed" if installed else "installable",
            "tier": "core",
        }

    def recipe(name: str, default: str) -> Dict[str, Any]:
        return {
            "default_backend": default,
            "backends": {default: backend(name, default)},
        }

    free = int(_env_float("FAKE_LEMONADE_DISK_FREE_GB", 500) * 1e9)
    return {
        "OS Version": "fake",
        "Processor": "fake CPU",
        "Physical Memory": "64.0 GB",
        "devices": {
            "cpu": {"name": "fake CPU", "available": True},
            **_gpu_devices(),
        },
        "model_storage": {
            "path": str(Path(tempfile.gettempdir()) / "fake-lemonade-store"),
            "free_bytes": free,
            "total_bytes": free * 2,
            "used_bytes": free,
        },
        "recipes": {
            "llamacpp": recipe("llamacpp", "cpu"),
            "whispercpp": recipe("whispercpp", "cpu"),
            "kokoro": recipe("kokoro", "cpu"),
            "sd-cpp": recipe("sd-cpp", "cpu"),
        },
    }


def _pull_problem(body: Dict[str, Any], model: Optional[str]) -> Optional[str]:
    """Why Lemonade would refuse this /pull, or None."""
    if not model:
        return "/pull needs a model_name"
    if model.startswith("user."):
        if not (body.get("checkpoint") and body.get("recipe")):
            return f"registering {model} needs both checkpoint and recipe"
        return None
    if body.get("checkpoint") or body.get("recipe"):
        return f"{model} is built in: a new registration needs the user. prefix"
    if model not in _catalog:
        return f"{model} is not in the model catalog"
    return None


def _pull_failure() -> Optional[str]:
    """The message this pull stops with, while FAKE_LEMONADE_PULL_FAIL has any left."""
    global _pull_failures_left
    kind = os.environ.get("FAKE_LEMONADE_PULL_FAIL")
    if not kind:
        return None
    with _lock:
        if _pull_failures_left <= 0:
            return None
        _pull_failures_left -= 1
    return PULL_ERRORS[kind]


def _multipart_fields(raw: bytes) -> Dict[str, Any]:
    """A multipart body's field names, with the text of its ``model`` field."""
    fields: Dict[str, Any] = {
        name.decode("ascii"): True for name in re.findall(rb'name="([^"]+)"', raw)
    }
    model = re.search(rb'name="model"\r\n\r\n([^\r]+)\r\n', raw)
    if model:
        fields["model"] = model.group(1).decode("utf-8")
    return fields


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


_NEEDS_A_DOWNLOADED_MODEL = frozenset(
    {
        "/load",
        "/chat/completions",
        "/completions",
        "/embeddings",
        "/audio/transcriptions",
        "/audio/speech",
        "/images/generations",
    }
)


def _catalog_id(model: Optional[str]) -> Optional[str]:
    """*model* as /health should name it: as listed once --catalog knows it."""
    return _listed_id(model) if _catalog and model else model


def _finish_pull(model: str, body: Dict[str, Any]) -> None:
    listed = _listed_id(model)
    with _lock:
        _downloaded[listed] = True
        if listed not in _catalog:
            _catalog[listed] = {
                "size": 0.311 if body.get("embedding") else 11.3,
                "labels": (
                    ["custom", "embeddings"]
                    if body.get("embedding")
                    else ["custom", *_CHAT_LABELS]
                ),
                "recipe": body.get("recipe"),
                "checkpoint": body.get("checkpoint"),
            }


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

    def _query(self, name: str) -> Optional[str]:
        return (parse_qs(urlsplit(self.path).query).get(name) or [None])[0]

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        if (self.headers.get("Content-Type") or "").startswith("multipart/form-data"):
            return _multipart_fields(raw)
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

    def _refuse(self, status: int, message: str, body: Dict[str, Any]) -> None:
        self._record(body, status)
        self._json(status, {"error": {"message": message}})

    def _record(self, body: Optional[Dict[str, Any]], status: int) -> None:
        _log(
            {
                "t": time.time(),
                "method": self.command,
                "path": self.path,
                "status": status,
                "body_keys": sorted(body) if body else [],
                "model": (body or {}).get("model") or (body or {}).get("model_name"),
            }
        )

    # ── GET ──
    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        path = self._path()
        if path == "/health":
            with _lock:
                models = sorted(_loaded)
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
            show_all = self._query("show_all") == "true"
            self._json(
                200,
                {
                    "object": "list",
                    "data": [_model_entry(m) for m in _known_models(show_all)],
                },
            )
        elif path.startswith("/models/"):
            model_id = path[len("/models/") :]
            if _catalog and _listed_id(model_id) not in _catalog:
                self._refuse(404, f"no model {model_id}", {})
                return
            if not _catalog:
                _remember(model_id)
            self._json(200, _model_entry(model_id))
        elif path == "/system-info":
            self._json(200, _system_info())
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
        if path in _NEEDS_A_DOWNLOADED_MODEL and not _is_downloaded(model):
            self._refuse(404, f"{model} is not downloaded", body)
            return
        if path == "/pull" and _catalog:
            problem = _pull_problem(body, model)
            if problem:
                self._refuse(400, problem, body)
                return
            self._record(body, 200)
            self._pull(body, str(model))
            return
        if path == "/load":
            time.sleep(_env_float("FAKE_LEMONADE_LOAD_SECONDS", 0))
        if path in ("/load", "/pull"):
            _remember(_catalog_id(model), body.get("ctx_size"))
            self._json(200, {"status": "success", "message": f"{path[1:]}ed {model}"})
        elif path in ("/unload", "/delete"):
            with _lock:
                _loaded.pop(_catalog_id(model), None)
                if path == "/delete":
                    _downloaded.pop(_listed_id(model), None)
            self._json(200, {"status": "success"})
        elif path == "/chat/completions":
            if _catalog and _has_image(body):
                if "vision" not in _model_entry(str(model))["labels"]:
                    self._refuse(400, f"{model} does not accept images", body)
                    return
            _remember(_catalog_id(model))
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
            _remember(_catalog_id(model))
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
        elif path == "/audio/transcriptions":
            if not body.get("file"):
                self._refuse(400, "the transcription request has no audio file", body)
                return
            _remember(_catalog_id(model))
            self._json(
                200,
                {
                    "text": TRANSCRIPT,
                    "language": "en",
                    "duration": 1.0,
                    "segments": [{"start": 0.0, "end": 1.0, "text": TRANSCRIPT}],
                },
            )
        elif path == "/audio/speech":
            _remember(_catalog_id(model))
            self._pcm()
        elif path == "/images/generations":
            _remember(_catalog_id(model))
            image = base64.b64encode(_fake_png()).decode("ascii")
            self._json(
                200, {"created": int(time.time()), "data": [{"b64_json": image}]}
            )
        elif path == "/install":
            _installed_backends[f"{body.get('recipe')}:{body.get('backend')}"] = True
            self._json(200, {"status": "success"})
        elif path in ("/params", "/uninstall"):
            self._json(200, {"status": "success"})
        else:
            self._record(body, 404)
            self._json(
                404, {"error": {"message": f"fake server has no POST {self.path}"}}
            )
            return
        self._record(body, 200)

    def _pcm(self) -> None:
        """A quarter second of a 440 Hz tone, as Lemonade's ``pcm`` format sends it."""
        rate = 24000
        samples = (
            int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate // 4)
        )
        body = b"".join(struct.pack("<h", s) for s in samples)
        self.send_response(200)
        self.send_header("Content-Type", f"audio/l16;rate={rate}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, event: str, data: Dict[str, Any]) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(payload.encode("utf-8"))
        self.wfile.flush()

    def _pull(self, body: Dict[str, Any], model: str) -> None:
        """Lemonade's /pull: ``progress`` per file, then ``complete`` or ``error``."""
        if not body.get("stream"):
            _finish_pull(model, body)
            self._json(200, {"status": "success", "message": f"pulled {model}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        size = int(float(_catalog.get(model, {}).get("size") or 0.3) * 1e9)
        files = [(f"{_listed_id(model)}.gguf", size - 1024), ("config.json", 1024)]
        try:
            if self._stream_pull(size, files, _pull_failure()):
                return
            _finish_pull(model, body)
            self._sse(
                "complete",
                {"file_index": len(files), "total_files": len(files), "percent": 100},
            )
        except (BrokenPipeError, ConnectionError):
            # Hanging up is how a client cancels a pull: nothing was downloaded.
            self.log_message("the client hung up on the pull of %s", model)

    def _stream_pull(
        self, size: int, files: List[Tuple[str, int]], failure: Optional[str]
    ) -> bool:
        """Send the progress events; True when *failure* stopped the pull."""
        ticks = 8
        pause = _env_float("FAKE_LEMONADE_PULL_SECONDS", 2) / ticks
        done_before = 0
        for index, (name, file_size) in enumerate(files, start=1):
            steps = ticks if index == 1 else 1
            for step in range(1, steps + 1):
                part = file_size * step // steps
                self._sse(
                    "progress",
                    {
                        "file": name,
                        "file_index": index,
                        "total_files": len(files),
                        "bytes_downloaded": part,
                        "bytes_total": file_size,
                        "total_download_size": size,
                        "percent": (done_before + part) * 100 // size,
                    },
                )
                if failure and step * 2 >= steps:
                    self._sse("error", {"error": failure})
                    return True
                time.sleep(pause)
            done_before += file_size
        return False

    def _chat(self, body: Dict[str, Any], model: str) -> None:
        created = int(time.time())
        usage = {"prompt_tokens": 12, "completion_tokens": 12, "total_tokens": 24}
        answer = VISION_ANSWER if _has_image(body) else ANSWER
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
                            "message": {"role": "assistant", "content": answer},
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
                for w in re.findall(r"\S+\s*", answer)
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
    global _log_path, _lemonade_version, _pull_failures_left
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
    ap.add_argument(
        "--catalog",
        action="store_true",
        help="play a PC with nothing downloaded: only --model ids start downloaded",
    )
    args = ap.parse_args(argv)
    fail = os.environ.get("FAKE_LEMONADE_PULL_FAIL")
    if fail and fail not in PULL_ERRORS:
        print(
            f"error: FAKE_LEMONADE_PULL_FAIL={fail!r} is not one of "
            f"{sorted(PULL_ERRORS)}",
            file=sys.stderr,
        )
        return 1

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
    if args.catalog:
        _catalog.update(CATALOG)
        for m_id in args.model:
            _finish_pull(m_id, {"embedding": "embed" in m_id.lower()})
    else:
        for m_id in args.model:
            _remember(m_id)
    _pull_failures_left = int(os.environ.get("FAKE_LEMONADE_PULL_FAIL_TIMES") or 1)

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
