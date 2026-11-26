# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A scripted OpenAI-compatible model server, and the engine to test against."""

import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")


class ModelServer:
    """Answers POST /v1/chat/completions from a queue, and records what it got."""

    def __init__(self):
        self.replies = []
        self.requests = []
        self.authorization = []
        # Seconds to wait before the next reply; plays a slow provider.
        self.delay = 0.0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server's name
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                server.requests.append(json.loads(body))
                server.authorization.append(self.headers.get("Authorization", ""))
                status, reply = server.replies.pop(0) if server.replies else (500, {})
                delay, server.delay = server.delay, 0.0
                time.sleep(delay)
                payload = json.dumps(reply).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    # The engine gave up on a slow reply and closed the socket.
                    return

            def log_message(self, *args):
                pass

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._http.server_address[1]}/v1"
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()

    def say(self, text, output_tokens=7):
        self._push({"role": "assistant", "content": text}, output_tokens)

    def call(self, name, output_tokens=7, **arguments):
        self._push(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            },
            output_tokens,
        )

    def fail(self, status=503):
        self.replies.append((status, {"error": {"message": "upstream overloaded"}}))

    def _push(self, message, output_tokens):
        self.replies.append(
            (
                200,
                {
                    "choices": [{"message": message}],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": output_tokens,
                        "total_tokens": 100 + output_tokens,
                    },
                },
            )
        )

    def close(self):
        self._http.shutdown()
        self._http.server_close()


@pytest.fixture
def model():
    server = ModelServer()
    yield server
    server.close()


def _real_engine():
    chosen = os.environ.get("GAIA_ENGINE", "").strip()
    if chosen:
        return chosen
    return shutil.which("gaiacpp")


@pytest.fixture
def real_engine():
    """The gaiacpp binary, or a skip that says how to provide one."""
    engine = _real_engine()
    if not engine:
        pytest.skip("no gaiacpp engine: set GAIA_ENGINE to a built gaiacpp binary")
    return engine


@pytest.fixture
def fake_engine(tmp_path):
    """A stand-in engine: a script that speaks the wire from a canned scenario."""
    if sys.platform == "win32":
        launcher = tmp_path / "engine.cmd"
        launcher.write_text(
            f'@"{sys.executable}" "{FAKE_ENGINE}" %*\r\n', encoding="utf-8"
        )
    else:
        launcher = tmp_path / "engine"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_ENGINE}" "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    return str(launcher)
