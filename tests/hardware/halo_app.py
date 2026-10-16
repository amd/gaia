# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A GAIA app for the hardware tests: registers, loads a model, waits to close.

Registers with the daemon exactly as the Agent UI backend does
(``ensure_daemon_owns_lemonade("app", ...)``), loads the chat model with the
UI's boot-preload call, answers one prompt, prints one JSON line, then waits
for ``quit`` on stdin. Exiting normally releases the registration through the
same ``atexit`` hook the app uses, so the daemon sees an app close.

With ``--register-only`` it registers and waits, loading nothing: the
registration alone is what makes the daemon bring a stopped server back.

Usage: python halo_app.py <model> [--register-only]
"""

import json
import sys


def main() -> int:
    model = sys.argv[1]
    from gaia.llm import lemonade_service
    from gaia.llm.lemonade_client import LemonadeClient, resolve_ctx_size

    lemonade_service.ensure_daemon_owns_lemonade("app", "linux-halo-hw-app")
    client_id = lemonade_service._HELD_CLIENT
    if client_id is None:
        print(json.dumps({"error": "the daemon did not accept the registration"}))
        return 1
    if "--register-only" in sys.argv:
        print(json.dumps({"client_id": client_id}), flush=True)
        return _wait_for_quit()
    # The Agent UI warms the model through the daemon-started server first.
    lemonade_service.ensure_lemonade_running()
    client = LemonadeClient(verbose=False)
    # The Agent UI's boot preload, verbatim.
    client.load_model(model, ctx_size=resolve_ctx_size(model), prompt=False)
    reply = client.chat_completions(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word OK."}],
        max_tokens=64,
    )
    text = reply["choices"][0]["message"]["content"]
    print(json.dumps({"client_id": client_id, "reply": text}), flush=True)
    return _wait_for_quit()


def _wait_for_quit() -> int:
    for line in sys.stdin:
        if line.strip() == "quit":
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
