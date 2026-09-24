# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic inference fixture; test-only, never included in the image."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "fireworks.container-fixture"


class Handler(BaseHTTPRequestHandler):
    def reply(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def authorized(self):
        if self.headers.get("Authorization") != "Bearer fixture-inference-key":
            self.reply({"error": "Missing inference credential"}, 401)
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path.startswith("/api/v1/models"):
            self.reply(
                {
                    "data": [
                        {"id": MODEL, "recipe": "cloud", "cloud_provider": "fireworks"}
                    ]
                }
            )
        elif self.path == "/api/v1/health":
            self.reply({"status": "ok", "version": "11.8.1", "all_models_loaded": []})
        else:
            self.reply({"error": "Unexpected fixture endpoint", "path": self.path}, 404)

    def do_POST(self):
        if not self.authorized():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/api/v1/chat/completions":
            self.reply({"error": "Unexpected fixture endpoint", "path": self.path}, 404)
            return
        if body.get("model") != MODEL:
            self.reply({"error": "Unexpected model"}, 400)
            return
        if any("fixture-wait" in str(m.get("content", "")) for m in body["messages"]):
            time.sleep(8)
        content = json.dumps({"answer": "Container inference fixture passed."})
        usage = {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}
        if not body.get("stream"):
            self.reply(
                {
                    "id": "fixture",
                    "object": "chat.completion",
                    "model": MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, reason in [(content, None), ("", "stop")]:
            chunk = {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "model": MODEL,
                "choices": [
                    {"index": 0, "delta": {"content": delta}, "finish_reason": reason}
                ],
            }
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
