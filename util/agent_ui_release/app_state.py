#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read the Agent UI backend the window is talking to: the second witness for every screenshot.

A screenshot shows what rendered; the backend shows what the agent really did
(which tools ran, whether they succeeded, the exact answer text). Check both
before calling a chat item passed.

    python app_state.py find                   # every running Agent UI backend: port, pid, version
    python app_state.py health                 # version, model, Lemonade, setup error
    python app_state.py sessions [-n 5]        # newest chats
    python app_state.py last [SESSION_ID]      # last turn: answer text, tool steps, timing
    python app_state.py wait [--timeout 600]   # block until no answer is being written or indexed
    python app_state.py grep TEXT [SESSION_ID] # is TEXT in the last answer? exit 1 if not

Every command but `find` needs the test app's backend port: `--port`, or
GAIA_UI_PORT=$(launch_isolated.py port --home ...). There is no default, because
the person's own app may be on 4200 and these commands would read their chats.
Read-only: nothing here changes app state.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HEADERS = {"X-Gaia-UI": "1", "Accept": "application/json"}


def get(port: int, path: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(
            f"GET {path} -> HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"
        )
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        sys.exit(
            f"No Agent UI backend on 127.0.0.1:{port} ({e}). Is the app open? "
            "Check the port with `launch_isolated.py port --home ...`."
        )


def latest_session(port: int) -> str:
    sessions = get(port, "/api/sessions").get("sessions", [])
    if not sessions:
        sys.exit("No chats yet.")
    return max(sessions, key=lambda s: s.get("updated_at", ""))["id"]


def last_turn(port: int, sid: str) -> tuple[dict | None, dict | None]:
    msgs = get(port, f"/api/sessions/{sid}/messages?limit=10000").get("messages", [])
    user = next((m for m in reversed(msgs) if m["role"] == "user"), None)
    reply = next((m for m in reversed(msgs) if m["role"] == "assistant"), None)
    if user and reply and reply["id"] < user["id"]:
        reply = None  # the newest question has no answer yet
    return user, reply


def listening() -> list[tuple[int, int]]:
    """(port, pid) for every loopback listener."""
    if sys.platform == "win32":
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=30
        ).stdout
        rx = re.compile(
            r"^\s*TCP\s+(?:127\.0\.0\.1|0\.0\.0\.0):(\d+)\s+\S+\s+LISTENING\s+(\d+)",
            re.M,
        )
    else:
        out = subprocess.run(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-nP"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        rx = re.compile(r"^\S+\s+(\d+)\s.*?(?:127\.0\.0\.1|\*):(\d+) \(LISTEN\)", re.M)
        return sorted({(int(port), int(pid)) for pid, port in rx.findall(out)})
    return sorted({(int(port), int(pid)) for port, pid in rx.findall(out)})


def cmd_find(a) -> None:
    found = 0
    for port, pid in listening():
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health", headers=HEADERS
        )
        try:
            with urllib.request.urlopen(req, timeout=0.5) as r:
                body = json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError, http.client.HTTPException):
            # Not every loopback listener speaks HTTP; those are simply not GAIA.
            continue
        if isinstance(body, dict) and body.get("service") == "gaia-agent-ui":
            st = get(port, "/api/system/status")
            print(
                f"port {port}  pid {pid}  version {st.get('version')}  model {st.get('model_loaded')}"
            )
            found += 1
    if not found:
        sys.exit(
            "No Agent UI backend is listening. Is the app open and past its setup screen?"
        )


def cmd_health(a) -> None:
    st = get(a.port, "/api/system/status")
    print(
        json.dumps(
            {
                k: st.get(k)
                for k in (
                    "version",
                    "initialized",
                    "setup_error",
                    "lemonade_running",
                    "lemonade_error",
                    "model_loaded",
                    "embedding_model_loaded",
                    "memory_available_gb",
                    "disk_space_gb",
                )
            },
            indent=1,
        )
    )


def cmd_sessions(a) -> None:
    sessions = sorted(
        get(a.port, "/api/sessions").get("sessions", []),
        key=lambda s: s.get("updated_at", ""),
        reverse=True,
    )
    for s in sessions[: a.n]:
        print(
            f"{s['id']}  {s['updated_at']}  {s.get('message_count', 0):>3} msgs  "
            f"{s.get('effective_model') or s.get('model')}  {s['title']}"
        )


def cmd_last(a) -> None:
    sid = a.session or latest_session(a.port)
    user, reply = last_turn(a.port, sid)
    print(f"session {sid}")
    print(f"user: {user['content'][:500] if user else '-'}")
    if not reply:
        print("assistant: (no answer yet)")
        return
    steps = reply.get("agent_steps") or []
    thinking = sum(1 for st in steps if st["type"] == "thinking")
    if thinking:
        print(f"  {thinking} thinking steps")
    for st in (st for st in steps if st["type"] != "thinking"):
        ok = {True: "ok", False: "FAILED", None: ""}[st.get("success")]
        print(
            f"  step {st['type']:<10} {st.get('tool') or '':<24} {ok:<6} {st['label'][:100]}"
        )
    stats = reply.get("stats") or {}
    if stats:
        print(
            "  stats "
            + ", ".join(f"{k}={v}" for k, v in stats.items() if v is not None)
        )
    print(f"assistant: {reply['content'] if a.full else reply['content'][:1500]}")


def cmd_wait(a) -> None:
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        wip = get(a.port, "/api/system/work-in-progress")
        if not wip["answers"] and not wip["indexing"]:
            print("idle")
            return
        time.sleep(2)
    sys.exit(
        f"Still busy after {a.timeout}s: {json.dumps(wip)}. A stuck answer is a finding."
    )


def cmd_grep(a) -> None:
    sid = a.session or latest_session(a.port)
    _, reply = last_turn(a.port, sid)
    if reply and a.text.lower() in reply["content"].lower():
        print(f"found in session {sid}")
        return
    print(f"'{a.text}' is not in the last answer of session {sid}")
    sys.exit(1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    env_port = os.environ.get("GAIA_UI_PORT")
    ap.add_argument(
        "--port",
        type=int,
        default=int(env_port) if env_port else None,
        help="The test app's backend port (or GAIA_UI_PORT)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find").set_defaults(fn=cmd_find)
    sub.add_parser("health").set_defaults(fn=cmd_health)
    sp = sub.add_parser("sessions")
    sp.add_argument("-n", type=int, default=5)
    sp.set_defaults(fn=cmd_sessions)
    sp = sub.add_parser("last")
    sp.add_argument("session", nargs="?")
    sp.add_argument(
        "--full", action="store_true", help="Whole answer, not the first 1500 chars"
    )
    sp.set_defaults(fn=cmd_last)
    sp = sub.add_parser("wait")
    sp.add_argument("--timeout", type=int, default=600)
    sp.set_defaults(fn=cmd_wait)
    sp = sub.add_parser("grep")
    sp.add_argument("text")
    sp.add_argument("session", nargs="?")
    sp.set_defaults(fn=cmd_grep)
    a = ap.parse_args()
    if a.cmd != "find" and a.port is None:
        ap.error("give --port or set GAIA_UI_PORT (launch_isolated.py port --home ...)")
    a.fn(a)


if __name__ == "__main__":
    main()
