# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stand-in for ``gaiacpp --engine`` that plays a scenario named in the model id.

It lets the SDK's handling of the wire be tested where no engine binary is
built. It is not a model of the engine's behaviour: those tests use the real
binary.
"""

import json
import sys


def send(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def receive():
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def final(turn, **fields):
    event = {
        "type": "final",
        "turn": turn,
        "status": "success",
        "stop_reason": "answered",
        "text": "",
        "tool_calls": [],
        "usage": {
            "model_calls": 1,
            "input_tokens": 100,
            "cached_tokens": 0,
            "output_tokens": 7,
        },
        "seconds": 0.01,
        "failure": None,
    }
    event.update(fields)
    send(event)


def main():
    if sys.argv[1:] != ["--engine"]:
        sys.stderr.write(f"fake engine started with {sys.argv[1:]!r}\n")
        return 64
    init = receive()
    scenario = init["model"]
    if scenario == "refuse-init":
        send({"type": "error", "code": "bad_init", "message": "refused on purpose"})
        return 2
    if scenario == "old-protocol":
        send({"type": "ready", "protocol": 0, "version": "fake"})
    elif scenario == "die-at-init":
        return 3
    else:
        send({"type": "ready", "protocol": init["protocol"], "version": "fake"})

    while True:
        message = receive()
        if message is None or message["type"] == "shutdown":
            return 0
        turn = message["id"]
        if scenario == "echo-init":
            final(turn, text=json.dumps({"init": init, "turn": message}))
        elif scenario == "call-each-tool":
            calls = []
            for index, tool in enumerate(init["tools"], start=1):
                arguments = {"order_number": "1001"}
                send(
                    {
                        "type": "tool_request",
                        "id": f"{turn}:{index}",
                        "turn": turn,
                        "name": tool["name"],
                        "arguments": arguments,
                    }
                )
                answer = receive()
                failed = "error" in answer
                calls.append(
                    {
                        "name": tool["name"],
                        "arguments": arguments,
                        "result": answer.get("result", answer.get("error")),
                        "failed": failed,
                    }
                )
            final(turn, text="done", tool_calls=calls)
        elif scenario == "unknown-tool":
            send(
                {
                    "type": "tool_request",
                    "id": f"{turn}:1",
                    "turn": turn,
                    "name": "no_such_tool",
                    "arguments": {},
                }
            )
            answer = receive()
            final(turn, text=json.dumps(answer))
        elif scenario == "bad-turn":
            send({"type": "error", "code": "bad_turn", "message": "refused on purpose"})
        elif scenario == "die-mid-turn":
            return 9
        elif scenario == "new-event":
            send({"type": "from_the_future", "turn": turn})
        else:
            send(
                {
                    "type": "model_call",
                    "turn": turn,
                    "step": 1,
                    "seconds": 0.01,
                    "usage": {
                        "input_tokens": 100,
                        "cached_tokens": 0,
                        "output_tokens": 7,
                    },
                }
            )
            final(turn, text="Hello there.")


if __name__ == "__main__":
    sys.exit(main())
