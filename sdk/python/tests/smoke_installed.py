# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One turn through an installed gaia-sdk wheel, with nothing else installed.

Run with the interpreter of a clean environment that has only the wheel:

    python smoke_installed.py

It uses the standard library alone, and fails unless the SDK was imported from
site-packages, the engine came from inside the wheel, and no other third-party
package was loaded.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.pop("GAIA_ENGINE", None)

import gaia_sdk  # noqa: E402 - after the environment is cleared
from gaia_sdk import Agent, tool  # noqa: E402
from gaia_sdk.engine import find_engine  # noqa: E402

REPLIES = [
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "get_order",
                    "arguments": json.dumps({"order_number": "1001"}),
                },
            }
        ],
    },
    {"role": "assistant", "content": "It has shipped."},
]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's name
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.dumps(
            {
                "choices": [{"message": REPLIES.pop(0)}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 7},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@tool
def get_order(order_number: str) -> dict:
    """Look up an order by its number."""
    return {"status": "shipped", "order": order_number}


def main() -> int:
    installed = os.path.normcase(os.path.dirname(gaia_sdk.__file__))
    if "site-packages" not in installed:
        print(f"FAIL: gaia_sdk was imported from {installed}, not an installed wheel")
        return 1
    (engine,) = find_engine()
    if not os.path.normcase(engine).startswith(installed):
        print(f"FAIL: the engine is {engine}, not the one inside the wheel")
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with Agent(
            model="host-model",
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            system_prompt="You answer questions about orders.",
            tools=[get_order],
        ) as agent:
            turn = agent.run("Where is order 1001?")
    finally:
        server.shutdown()

    if not (turn.ok and turn.text == "It has shipped." and len(turn.tool_calls) == 1):
        print(f"FAIL: unexpected turn {turn}")
        return 1
    third_party = sorted(
        name.split(".")[0]
        for name, module in sys.modules.items()
        if "site-packages" in (getattr(module, "__file__", None) or "")
    )
    extra = sorted(set(third_party) - {"gaia_sdk"})
    if extra:
        print(f"FAIL: the SDK pulled in other packages: {extra}")
        return 1
    print(f"OK: one turn through {engine} (engine {agent.engine_version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
