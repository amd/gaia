# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The three ways a person reaches the GAIA agent, each driven as its app drives it.

- ``ui``: the Agent UI backend's HTTP API, with the headers the desktop app sends.
- ``cli``: ``gaia -p`` on an installed build, where the agent is its own program
  and the turn goes through GAIA's background service.
- ``stdio``: the agent as the terminal UI's child, one JSON event per line.

Each returns a :class:`Turn`, so one test body covers every surface.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .world import World, gaiacpp_binary, sse_events

SURFACES = ("ui", "cli", "stdio")

#: `gaia` as an installed build runs it: the agent is not in this process, so
#: `gaia -p` relays the turn to the agent the background service runs.
INSTALLED_GAIA = [
    sys.executable,
    "-c",
    "import sys; sys.modules['gaiapy'] = None; sys.argv = ['gaia', *sys.argv[1:]]; "
    "from gaia.cli import main; sys.exit(main())",
]

#: The tool each harness writes a file with, and the argument naming the file.
WRITE_TOOL = {"gaiapy": ("write_file", "file_path"), "gaiacpp": ("file_write", "path")}

#: How long one turn may take against a model server that answers at once.
TURN_TIMEOUT_S = 60


@dataclass
class Turn:
    """What the person got back from one message."""

    answer: str = ""
    error: str = ""
    #: Every question the agent put to the person, as the surface delivered it.
    prompts: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    #: `gaia -p` only: its exit code and JSON record.
    exit_code: Optional[int] = None
    record: Dict[str, Any] = field(default_factory=dict)

    @property
    def said(self) -> str:
        """Everything shown to the person: the answer and any error."""
        return f"{self.answer}\n{self.error}"


Decide = Callable[[Dict[str, Any]], bool]


def write_call(harness: str, path: os.PathLike, content: str) -> Dict[str, Any]:
    """The model's tool call that writes *content* to *path* on this harness."""
    tool, argument = WRITE_TOOL[harness]
    return {"name": tool, "arguments": {argument: str(path), "content": content}}


def is_folder_request(prompt: Dict[str, Any], path: os.PathLike) -> bool:
    """Whether *prompt* asks to let GAIA reach *path*, outside the chat's folders.

    gaiacpp raises the flagship's ``allow_path_access`` card; the Python agent
    says it in the card of the call that needs the path. Both name the path.
    """
    text = json.dumps(prompt)
    if str(path) not in text and json.dumps(str(path))[1:-1] not in text:
        return False
    asked_for = prompt.get("tool") or prompt.get("action")
    return asked_for == "allow_path_access" or "outside this chat's folders" in text


# ── the Agent UI backend ──


def ui_session(world: World) -> str:
    reply = world.api("POST", "/api/sessions", json={})
    assert (
        reply.status_code == 200
    ), f"POST /api/sessions: {reply.status_code} {reply.text}"
    return reply.json()["id"]


def ask_ui(
    world: World,
    text: str,
    decide: Optional[Decide] = None,
    session: Optional[str] = None,
    timeout: float = TURN_TIMEOUT_S,
) -> Turn:
    """One message through ``/api/chat/send``; *world* needs only its ``api``."""
    turn = Turn()
    session = session or ui_session(world)
    body = {"session_id": session, "message": text, "stream": True}
    with world.api(
        "POST", "/api/chat/send", json=body, stream=True, timeout=timeout
    ) as reply:
        assert reply.status_code == 200, f"POST /api/chat/send: {reply.status_code}"
        for event in sse_events(reply):
            turn.events.append(event)
            kind = event.get("type")
            if kind == "permission_request":
                turn.prompts.append(event)
                assert (
                    decide is not None
                ), f"the agent asked and nobody answers: {event}"
                answer = {
                    "session_id": session,
                    "approved": decide(event),
                    "confirm_id": event.get("confirm_id"),
                }
                done = world.api("POST", "/api/chat/confirm-tool", json=answer)
                assert done.status_code == 200, f"confirm-tool: {done.text}"
            elif kind == "answer":
                turn.answer = event.get("content") or ""
            elif kind == "agent_error":
                turn.error = event.get("content") or ""
    return turn


# ── gaia -p ──


def ask_cli(
    world: World, text: str, *flags: str, timeout: float = TURN_TIMEOUT_S
) -> Turn:
    done = world.run(
        [*INSTALLED_GAIA, "-p", text, "--output-format", "json", *flags],
        timeout=timeout,
    )
    turn = Turn(exit_code=done.returncode)
    printed = [out for out in done.stdout.splitlines() if out.startswith("{")]
    assert (
        printed
    ), f"gaia -p printed no JSON record (exit {done.returncode}):\n{done.stderr}"
    turn.record = json.loads(printed[-1])
    turn.answer = turn.record.get("result") or ""
    turn.error = turn.record.get("error") or ""
    return turn


# ── the terminal UI's child ──


class StdioAgent:
    """The agent as gaia-cli starts it: a child that reads lines and writes events."""

    def __init__(self, world: World, harness: str) -> None:
        self.world = world
        env: Dict[str, str] = {}
        if harness == "gaiacpp":
            binary = gaiacpp_binary()
            assert binary is not None
            argv = [str(binary), "--stdio"]
        else:
            # gaia-agent is this module; from source it needs the package on the path.
            argv = [sys.executable, "-m", "gaiapy.entry"]
            src = world.src
            env["PYTHONPATH"] = os.pathsep.join(
                [str(src / "src"), str(src / "hub" / "agents" / "gaia" / "python")]
            )
        self.child: subprocess.Popen = world.spawn(argv, **env)
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.child.stdout is not None
        for line in self.child.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                self.events.put(json.loads(line))
            except ValueError:
                continue
        self.events.put({"type": "exited"})

    def _send(self, line: str) -> None:
        assert self.child.stdin is not None
        self.child.stdin.write(line + "\n")
        self.child.stdin.flush()

    def ask(self, text: str, decide: Optional[Decide] = None) -> Turn:
        turn = Turn()
        self._send(text)
        deadline = time.monotonic() + TURN_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=1)
            except queue.Empty:
                continue
            turn.events.append(event)
            kind = event.get("type")
            if kind == "needs_confirmation":
                turn.prompts.append(event)
                assert (
                    decide is not None
                ), f"the agent asked and nobody answers: {event}"
                decision = "allow" if decide(event) else "deny"
                control = {
                    "gaia_control": "tool_decision",
                    "decision": decision,
                    "confirm_id": event.get("confirm_id"),
                }
                self._send(json.dumps(control))
            elif kind == "final":
                turn.answer = event.get("answer") or ""
                return turn
            elif kind == "error":
                turn.error = event.get("detail") or event.get("message") or ""
                return turn
            elif kind == "exited":
                raise AssertionError(
                    f"the agent exited mid-turn:\n{self.world.describe()}"
                )
        raise AssertionError(
            f"no final or error event within {TURN_TIMEOUT_S} s; last events: "
            f"{turn.events[-3:]}\n{self.world.describe()}"
        )
