# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exercise startup and reload against a loopback HTTP protocol fixture."""

# pylint: disable=protected-access

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from gaia.cli import initialize_lemonade_for_agent
from gaia.config import GaiaConfig
from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.lemonade_manager import LemonadeManager


@pytest.mark.parametrize("window", [16384, 131072])
def test_startup_then_eviction_preserves_requested_window(monkeypatch, window):
    """Use real HTTP, manager startup, client reload, and serialized load bodies."""
    monkeypatch.setenv("GAIA_CTX_SIZE", str(window))
    monkeypatch.setattr(GaiaConfig, "load", lambda: GaiaConfig(default_device="gpu"))
    loaded = []
    loads = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            """Suppress the fixture server's access log."""

        def reply(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # pylint: disable=invalid-name
            if self.path == "/api/v1/health":
                self.reply({"status": "ok", "all_models_loaded": loaded})
            elif self.path == "/api/v1/models":
                self.reply({"data": []})
            else:
                self.send_error(404)

        def do_POST(self):  # pylint: disable=invalid-name
            if self.path != "/api/v1/load":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            loads.append(body)
            loaded[:] = [
                {
                    "model_name": body["model_name"],
                    "type": "llm",
                    "recipe_options": {"ctx_size": body["ctx_size"]},
                }
            ]
            self.reply({"status": "success"})

    LemonadeManager.reset()
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/v1"
        try:
            assert initialize_lemonade_for_agent("chat", base_url=base) == (True, base)
            assert len(loads) == 1
            loaded.clear()  # Model has been evicted between turns.
            client = LemonadeClient(base_url=base, keep_alive=True)
            client._ensure_model_loaded(
                "user.custom-model"
            )  # pylint: disable=protected-access
            assert len(loads) == 2
            assert [load["ctx_size"] for load in loads] == [window, window]
            assert loads[-1]["model_name"] == "user.custom-model"
            client._ensure_model_loaded(
                "user.custom-model"
            )  # pylint: disable=protected-access
            assert len(loads) == 2  # Sufficient resident window is reused.
            print(
                f"Startup -> eviction -> reload -> reuse: ctx={window}; loads={loads}"
            )
        finally:
            server.shutdown()
            server_thread.join(timeout=5)
            LemonadeManager.reset()
