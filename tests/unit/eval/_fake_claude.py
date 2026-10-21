# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stand-in for the ``claude -p`` eval driver, launched as a real process.

It finds the backend in the ``--mcp-config`` the runner hands it, then follows
the JSON plan in ``FAKE_CLAUDE_PLAN``: create ``Eval: <scenario_id>``, send each
message and read the turn's stream to its end the way ``send_message`` does,
then either print a judged result or sleep past the scenario's timeout.
``grandchild_pidfile`` starts a child that inherits the driver's output pipes,
as the driver's MCP server does.
"""

import json
import os
import subprocess
import sys
import time

import requests

HEADERS = {"X-Gaia-UI": "1"}


def _backend(argv):
    config_path = argv[argv.index("--mcp-config") + 1]
    with open(config_path, encoding="utf-8") as f:
        args = json.load(f)["mcpServers"]["gaia-agent-ui"]["args"]
    return args[args.index("--backend") + 1]


def _send(backend, session_id, message):
    resp = requests.post(
        f"{backend}/api/chat/send",
        json={"session_id": session_id, "message": message, "stream": True},
        headers=HEADERS,
        stream=True,
        timeout=(5, 300),
    )
    if resp.status_code != 200:
        return None, resp.json().get("detail", f"HTTP {resp.status_code}")
    content = ""
    for line in resp.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            event = json.loads(line[6:])
            if event.get("type") in ("answer", "done") and event.get("content"):
                content = event["content"]
    return content, None


def main():
    backend = _backend(sys.argv[1:])
    sys.stdin.read()
    plan = json.loads(os.environ["FAKE_CLAUDE_PLAN"])

    if plan.get("grandchild_pidfile"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        with open(plan["grandchild_pidfile"], "w", encoding="utf-8") as f:
            f.write(str(child.pid))

    session_id = None
    turns = []
    error = None
    if plan.get("messages"):
        session = requests.post(
            f"{backend}/api/sessions",
            json={"title": f"Eval: {plan['scenario_id']}"},
            headers=HEADERS,
            timeout=10,
        ).json()
        session_id = session["id"]
        for n, message in enumerate(plan["messages"], start=1):
            reply, error = _send(backend, session_id, message)
            if error:
                break
            turns.append(
                {
                    "turn": n,
                    "user_message": message,
                    "agent_response": reply,
                    "agent_tools": [],
                    "scores": {
                        k: 9
                        for k in (
                            "correctness",
                            "tool_selection",
                            "context_retention",
                            "completeness",
                            "efficiency",
                            "personality",
                            "error_recovery",
                        )
                    },
                    "overall_score": 9,
                    "pass": True,
                }
            )

    if plan.get("then") == "sleep":
        time.sleep(plan.get("sleep_s", 40))

    result = {
        "scenario_id": plan["scenario_id"],
        "session_id": session_id,
        "status": "INFRA_ERROR" if error else "PASS",
        "overall_score": None if error else 9,
        "turns": turns,
    }
    if error:
        result["error"] = error
    print(json.dumps({"structured_output": result}))


if __name__ == "__main__":
    main()
