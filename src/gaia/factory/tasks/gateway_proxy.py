# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Let the agent loop reach the AMD gateway's open-weights models.

**The gap this works around is real and worth stating plainly:** GAIA cannot
talk to AMD's own LLM gateway for anything except Claude. The gateway
authenticates on an ``Ocp-Apim-Subscription-Key`` header — bearer auth returns
401, measured — and nothing in ``gaia/llm/`` can send a custom header. The
Claude path only works because the Anthropic SDK reads
``ANTHROPIC_CUSTOM_HEADERS`` from the environment itself.

So this listens locally, speaks the shape the Lemonade client expects, and
forwards chat completions to the gateway with the header attached. The model
still receives the agent's real prompt and tools and answers for itself —
nothing about the measurement changes. Only the route does.

Endpoints that exist purely to satisfy the client's startup probes return
minimal truthful answers. ``/embeddings`` deliberately returns an error rather
than zero vectors: retrieval quietly ranking against fabricated embeddings is
exactly the kind of silent wrongness that is worse than a failure.

Usage::

    python -m gaia.factory.tasks.gateway_proxy --port 13377 &
    GAIA_TASK_BASE_URL=http://127.0.0.1:13377/api/v1 \\
        python -m gaia.factory.tasks.runner --transport openai --model Kimi-K2.7-Code …
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UNIFIED = "https://llm-api.amd.com/Unified/v1"

#: Port 4001 is reserved on this machine and must never be bound.
FORBIDDEN_PORTS = {4001}

#: Upstream calls are slow for large open-weights models; a 35B MoE answering a
#: 60-tool prompt has taken minutes. Short timeouts here would show up as model
#: failures, which is the wrong diagnosis.
UPSTREAM_TIMEOUT_S = 600

#: The shared gateway throttles under a sustained agent workload: a full
#: 17K-token prompt with 60 tool schemas succeeds once and then returns
#: HTTP 429 "Rate Limit reached from Azure OpenAI" for a while. Without a
#: retry, a single 429 fails a whole task and the model wears the blame for
#: the queue it was standing in.
#:
#: Backoff is long because the limit is per-minute upstream; retrying quickly
#: just burns attempts against a window that has not moved.
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})
BACKOFF_SECONDS = (20, 45, 90, 150)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1


def subscription_key() -> str:
    hdr = os.environ.get("ANTHROPIC_CUSTOM_HEADERS", "")
    m = re.search(r"Ocp-Apim-Subscription-Key:\s*(\S+)", hdr)
    if not m:
        raise SystemExit(
            "No Ocp-Apim-Subscription-Key found in ANTHROPIC_CUSTOM_HEADERS. The "
            "unified gateway authenticates on that header (bearer auth returns "
            "401), so the proxy has nothing to forward. Export it and retry."
        )
    return m.group(1)


class Handler(BaseHTTPRequestHandler):
    key = ""

    def log_message(self, fmt, *args):  # noqa: A003 - quiet by default
        if os.environ.get("GAIA_PROXY_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.rstrip("/")
        if path.endswith("/health"):
            self._send(200, {"status": "ok", "proxy": "amd-unified-gateway"})
        elif path.endswith("/models"):
            self._send(200, {"object": "list", "data": []})
        elif path.endswith(("/params", "/stats", "/system-info")):
            self._send(200, {})
        else:
            self._send(404, {"error": f"proxy does not serve {self.path}"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        path = self.path.rstrip("/")

        if path.endswith(("/load", "/unload", "/pull")):
            # The gateway serves whatever model the request names; there is no
            # resident slot to manage, so these are honestly no-ops.
            self._send(200, {"status": "ok"})
            return
        if path.endswith("/embeddings"):
            self._send(
                501,
                {
                    "error": {
                        "message": (
                            "This proxy forwards chat completions only. The "
                            "gateway exposes no embedding model, and returning "
                            "placeholder vectors would let retrieval rank "
                            "against noise while appearing to work."
                        )
                    }
                },
            )
            return
        if not path.endswith("/chat/completions"):
            self._send(404, {"error": f"proxy does not serve {self.path}"})
            return

        detail, code = "", 502
        for attempt in range(MAX_ATTEMPTS):
            req = urllib.request.Request(
                f"{UNIFIED}/chat/completions",
                data=raw,
                headers={
                    "Content-Type": "application/json",
                    "Ocp-Apim-Subscription-Key": self.key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT_S) as resp:
                    body = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except urllib.error.HTTPError as exc:
                code = exc.code
                detail = exc.read().decode("utf-8", "replace")[:2000]
                if code not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                    break
            except Exception as exc:  # noqa: BLE001 - surfaced to the caller
                code, detail = 502, f"{type(exc).__name__}: {exc}"
                if attempt == MAX_ATTEMPTS - 1:
                    break
            time.sleep(BACKOFF_SECONDS[attempt])

        # Whatever the gateway last said, said plainly. A proxy that turns an
        # upstream error into a success is how a throttled endpoint gets scored
        # as a weak model.
        self._send(code, {"error": {"message": detail, "upstream": code}})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=13377)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if a.port in FORBIDDEN_PORTS:
        raise SystemExit(f"port {a.port} is reserved on this machine; pick another")

    Handler.key = subscription_key()
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(
        f"forwarding http://{a.host}:{a.port}/api/v1/chat/completions -> {UNIFIED}\n"
        f"set GAIA_TASK_BASE_URL=http://{a.host}:{a.port}/api/v1",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
