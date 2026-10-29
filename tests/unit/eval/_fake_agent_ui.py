# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stand-in Agent UI backend for the eval runner's scenario-hygiene tests.

Speaks the HTTP routes the runner, the driver and the MCP turn control use,
with the behaviour that matters to them: a turn runs detached from the request
that started it, a permission prompt waits until it is answered, cancelled or
its own wait runs out, a turn left running holds the single chat slot, and
``/api/chat/attach`` replays a running turn's events.

What the user sent picks the turn's script:

- ``prompt <tool> <path>`` asks permission for ``<tool>``, then reports it.
- ``hang`` runs until it is cancelled (or ``hang_limit_s`` passes).
- anything else is answered at once.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse


class _Run:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: List[Dict[str, Any]] = []
        self.cond = threading.Condition()
        self.done = False
        self.cancelled = threading.Event()
        self.pending: Optional[Dict[str, Any]] = None
        self.answer: Optional[bool] = None
        self.answered = threading.Event()

    def emit(self, event: Dict[str, Any]) -> None:
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()


class FakeAgentUI:
    def __init__(
        self,
        confirm_wait_s: float = 60.0,
        confirm_status: Optional[int] = None,
        busy_wait_s: float = 8.0,
        hang_limit_s: float = 30.0,
    ) -> None:
        self.confirm_wait_s = confirm_wait_s
        self.confirm_status = confirm_status
        self.busy_wait_s = busy_wait_s
        self.hang_limit_s = hang_limit_s
        self.lock = threading.Lock()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.messages: Dict[str, List[Dict[str, Any]]] = {}
        self.runs: Dict[str, _Run] = {}
        self.slot = threading.Semaphore(1)
        self.cancels: List[str] = []
        self.answers: List[Dict[str, Any]] = []
        self.busy_rejections = 0
        self._server: Optional[ThreadingHTTPServer] = None

    # ── lifecycle ──

    def start(self) -> str:
        fake = self

        class Handler(_Handler):
            backend = fake

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        with self.lock:
            runs = list(self.runs.values())
        for run in runs:
            run.cancelled.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def active(self) -> List[str]:
        with self.lock:
            return list(self.runs)

    # ── turns ──

    def _add_message(self, session_id: str, role: str, content: str, steps=None):
        with self.lock:
            msgs = self.messages.setdefault(session_id, [])
            msgs.append(
                {
                    "id": sum(len(m) for m in self.messages.values()) + 1,
                    "session_id": session_id,
                    "role": role,
                    "content": content,
                    "created_at": "2026-10-17T00:00:00",
                    "agent_steps": steps,
                }
            )

    def start_run(self, session_id: str, message: str) -> Optional[_Run]:
        if not self.slot.acquire(timeout=self.busy_wait_s):
            self.busy_rejections += 1
            return None
        run = _Run(session_id)
        with self.lock:
            self.runs[session_id] = run
        self._add_message(session_id, "user", message)
        threading.Thread(target=self._execute, args=(run, message), daemon=True).start()
        return run

    def _execute(self, run: _Run, message: str) -> None:
        steps = []
        try:
            words = message.split(maxsplit=2)
            content = f"echo: {message}"
            if words and words[0] == "prompt":
                tool = words[1]
                args = {"path": words[2] if len(words) > 2 else ""}
                steps.append({"type": "tool", "tool": tool, "label": tool})
                run.emit({"type": "tool_start", "tool": tool})
                confirm_id = uuid.uuid4().hex
                run.pending = {"confirm_id": confirm_id, "tool": tool, "args": args}
                run.emit(
                    {
                        "type": "permission_request",
                        "tool": tool,
                        "args": args,
                        "confirm_id": confirm_id,
                        "timeout_seconds": self.confirm_wait_s,
                    }
                )
                deadline = time.monotonic() + self.confirm_wait_s
                while time.monotonic() < deadline and not run.cancelled.is_set():
                    if run.answered.wait(0.1):
                        break
                run.pending = None
                verdict = "approved" if run.answer else "denied"
                run.emit({"type": "tool_result", "summary": f"{tool} {verdict}"})
                content = f"{tool} was {verdict}"
            elif message == "hang":
                steps.append({"type": "tool", "tool": "search_file", "label": "x"})
                run.emit({"type": "tool_start", "tool": "search_file"})
                run.cancelled.wait(self.hang_limit_s)
                content = "still searching"
            if run.cancelled.is_set():
                run.emit({"type": "done", "cancelled": True, "content": content})
            else:
                run.emit({"type": "answer", "content": content})
                run.emit({"type": "done", "content": content})
            self._add_message(run.session_id, "assistant", content, steps)
        finally:
            with self.lock:
                self.runs.pop(run.session_id, None)
            run.finish()
            self.slot.release()


class _Handler(BaseHTTPRequestHandler):
    backend: FakeAgentUI
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # keep test output quiet
        pass

    # ── helpers ──

    def _json(self, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _stream(self, run: _Run) -> None:
        # Chunked, as uvicorn sends it, so a client sees each event as it lands.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        sent = 0
        try:
            while True:
                with run.cond:
                    while sent >= len(run.events) and not run.done:
                        if not run.cond.wait(1.0):
                            break
                    batch = run.events[sent:]
                    finished = run.done and sent + len(batch) >= len(run.events)
                sent += len(batch)
                for event in batch:
                    self._chunk(f"data: {json.dumps(event)}\n\n".encode())
                if not batch:
                    self._chunk(b": keepalive\n\n")
                if finished:
                    self.wfile.write(b"0\r\n\r\n")
                    return
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # the run carries on without this subscriber

    # ── routes ──

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        b = self.backend
        url = urlparse(self.path)
        parts = url.path.strip("/").split("/")
        if url.path == "/api/health":
            return self._json(200, {"status": "ok"})
        if url.path == "/api/chat/active":
            return self._json(200, {"session_ids": b.active()})
        if url.path == "/api/chat/attach":
            sid = parse_qs(url.query).get("session_id", [""])[0]
            with b.lock:
                run = b.runs.get(sid)
            if run is None:
                return self._json(404, {"detail": "No active run for this session."})
            return self._stream(run)
        if url.path == "/api/sessions":
            with b.lock:
                sessions = list(reversed(list(b.sessions.values())))
            return self._json(200, {"sessions": sessions, "total": len(sessions)})
        if parts[:2] == ["api", "sessions"] and len(parts) == 3:
            with b.lock:
                session = b.sessions.get(parts[2])
            if session is None:
                return self._json(404, {"detail": "Session not found"})
            return self._json(200, session)
        if parts[:2] == ["api", "sessions"] and parts[3:] == ["messages"]:
            with b.lock:
                msgs = list(b.messages.get(parts[2], []))
            return self._json(200, {"messages": msgs, "total": len(msgs)})
        return self._json(404, {"detail": f"no route {url.path}"})

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        b = self.backend
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/sessions":
            sid = uuid.uuid4().hex
            session = {"id": sid, "title": body.get("title", "New Chat")}
            with b.lock:
                b.sessions[sid] = session
                b.messages[sid] = []
            return self._json(200, session)
        if path == "/api/chat/send":
            sid = body["session_id"]
            if sid in b.active():
                return self._json(409, {"detail": "already in progress"})
            run = b.start_run(sid, body["message"])
            if run is None:
                return self._json(
                    429,
                    {
                        "detail": "The server is busy processing other chat "
                        "requests. Please try again in a few moments."
                    },
                )
            return self._stream(run)
        if path == "/api/chat/cancel":
            with b.lock:
                run = b.runs.get(body["session_id"])
            if run is None:
                return self._json(404, {"detail": "No active chat session found"})
            b.cancels.append(body["session_id"])
            run.cancelled.set()
            return self._json(200, {"status": "ok", "cancelled": True})
        if path == "/api/chat/confirm-tool":
            if b.confirm_status is not None:
                return self._json(b.confirm_status, {"detail": "backend broke"})
            with b.lock:
                run = b.runs.get(body["session_id"])
            if run is None:
                return self._json(404, {"detail": "The reply has ended."})
            pending = run.pending
            if pending is None or pending["confirm_id"] != body.get("confirm_id"):
                return self._json(409, {"detail": "no longer waiting"})
            b.answers.append(
                {
                    "session_id": body["session_id"],
                    "tool": pending["tool"],
                    "approved": bool(body.get("approved")),
                }
            )
            run.answer = bool(body.get("approved"))
            run.answered.set()
            return self._json(
                200, {"status": "ok", "approved": run.answer, "granted": None}
            )
        if path == "/api/eval/scripted-user":
            return self._json(200, {"status": "ok", "decline_commands": []})
        return self._json(404, {"detail": f"no route {path}"})
