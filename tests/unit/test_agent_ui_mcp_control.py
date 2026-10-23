# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn control for the Agent UI MCP server: drive, answer, read back -- and refuse.

The backend here is a real HTTP server streaming real SSE, and it enforces the
same confirm_id rules as ``/api/chat/confirm-tool``, so these tests check what
actually goes over the wire, not just that a method was called.
"""

import json
import math
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from gaia.mcp.servers.agent_ui_control import (
    MAX_WAIT_MS,
    TurnRegistry,
    TurnTracker,
    parse_timeout_ms,
)

# The fake backend is a loopback HTTP server.
pytestmark = pytest.mark.allow_network

UI = {"X-Gaia-UI": "1"}
SCOPE = "writing files in C:/work"


# ── Fake Agent UI backend ──────────────────────────────────────────────────


class Run:
    def __init__(self, session_id, scenario, prompt_timeout, always_scope, wait=None):
        self.session_id = session_id
        self.scenario = scenario
        self.prompt_timeout = prompt_timeout
        # How long this run really waits; defaults to the timeout it advertises.
        self.wait = prompt_timeout if wait is None else wait
        self.always_scope = always_scope
        self.log = []
        self.cond = threading.Condition()
        self.finished = False
        self.decision = None
        self.cancelled = False
        self.live_confirm = None

    def emit(self, event):
        with self.cond:
            self.log.append(event)
            self.cond.notify_all()

    def end(self):
        with self.cond:
            self.finished = True
            self.cond.notify_all()

    def wait_decision(self):
        deadline = time.monotonic() + self.wait
        with self.cond:
            while self.decision is None and not self.cancelled:
                left = deadline - time.monotonic()
                if left <= 0:
                    return "timeout"
                self.cond.wait(left)
            return "cancel" if self.decision is None else self.decision

    def produce(self):
        if self.scenario == "drop":
            self.emit({"type": "chunk", "content": "partial"})
            self.end()
            return
        if self.scenario == "plain":
            self.emit({"type": "thinking", "content": "Let me "})
            self.emit({"type": "thinking", "content": "think."})
            self.emit({"type": "chunk", "content": "Hello there"})
            self.emit(
                {
                    "type": "answer",
                    "content": "Hello there",
                    "steps": 1,
                    "tools_used": 0,
                }
            )
            self.emit({"type": "done", "message_id": 7, "content": "Hello there"})
            self.end()
            return
        self.emit({"type": "status", "status": "working", "message": "Planning"})
        self.emit({"type": "status", "status": "working", "message": "Writing"})
        self.emit({"type": "tool_start", "tool": "write_file", "detail": "Write file"})
        args = {"path": "C:/work/out.txt", "content": "x" * 2000}
        self.emit(
            {
                "type": "tool_args",
                "tool": "write_file",
                "args": args,
                "detail": "out.txt",
            }
        )
        cid = str(uuid.uuid4())
        with self.cond:
            self.live_confirm = cid
        prompt = {
            "type": "permission_request",
            "tool": "write_file",
            "args": args,
            "confirm_id": cid,
            "risk": "write",
            "timeout_seconds": self.prompt_timeout,
        }
        if self.always_scope:
            prompt["always_scope"] = self.always_scope
        self.emit(prompt)
        outcome = self.wait_decision()
        with self.cond:
            self.live_confirm = None
        if outcome == "cancel":
            self.emit(
                {
                    "type": "done",
                    "message_id": 9,
                    "content": "Cancelled.",
                    "cancelled": True,
                }
            )
        elif outcome in ("allow", "always"):
            self.emit({"type": "tool_end", "success": True})
            self.emit(
                {
                    "type": "tool_result",
                    "summary": "Wrote 2000 bytes",
                    "success": True,
                    "latency_ms": 4.0,
                }
            )
            self.emit({"type": "chunk", "content": "Done: wrote out.txt"})
            self.emit(
                {
                    "type": "answer",
                    "content": "Done: wrote out.txt",
                    "elapsed": 1.5,
                    "steps": 3,
                    "tools_used": 1,
                    "tokens": 120,
                    "input_tokens": 900,
                }
            )
            self.emit(
                {
                    "type": "done",
                    "message_id": 8,
                    "content": "Done: wrote out.txt",
                    "stats": {
                        "tokens_per_second": 25.0,
                        "time_to_first_token": 0.3,
                        "input_tokens": 900,
                        "output_tokens": 40,
                    },
                }
            )
        else:
            self.emit(
                {
                    "type": "tool_confirm_denied",
                    "tool": "write_file",
                    "message": "denied",
                }
            )
            self.emit({"type": "answer", "content": "I did not write it."})
            self.emit(
                {"type": "done", "message_id": 10, "content": "I did not write it."}
            )
        self.end()


class FakeBackend:
    def __init__(self):
        self.sessions = {}  # session_id -> scenario
        self.runs = {}
        self.confirms = []  # bodies the confirm endpoint received
        self.headers_seen = []
        self.cancels = []
        self.inputs = []
        self.prompt_timeout = 30.0
        self.prompt_wait = None
        self.always_scope = SCOPE
        self.confirm_delay = 0.0
        self.lingering = set()  # sessions /active still lists after their done
        self.attaches = 0
        self.lock = threading.Lock()
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def _stream(self, run):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                i = 0
                while True:
                    with run.cond:
                        while i >= len(run.log) and not run.finished:
                            if not run.cond.wait(0.1):
                                break
                        batch = run.log[i:]
                        i = len(run.log)
                        finished = run.finished and i >= len(run.log)
                    try:
                        if not batch:
                            self.wfile.write(b": keepalive\n\n")
                        for event in batch:
                            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                        self.wfile.flush()
                    except OSError:
                        return
                    if finished:
                        return

            def do_GET(self):
                url = urlsplit(self.path)
                backend.headers_seen.append(dict(self.headers))
                if url.path == "/api/chat/active":
                    live = [s for s, r in backend.runs.items() if not r.finished]
                    return self._json(
                        200, {"session_ids": live + sorted(backend.lingering)}
                    )
                if url.path == "/api/chat/attach":
                    sid = parse_qs(url.query)["session_id"][0]
                    run = backend.runs.get(sid)
                    if run is None or (run.finished and sid not in backend.lingering):
                        return self._json(
                            404, {"detail": "No active run for this session."}
                        )
                    backend.attaches += 1
                    return self._stream(run)
                return self._json(404, {"detail": "not found"})

            def do_POST(self):
                url = urlsplit(self.path)
                backend.headers_seen.append(dict(self.headers))
                if self.headers.get("X-Gaia-UI") != "1":
                    return self._json(403, {"detail": "missing X-Gaia-UI header"})
                body = self._body()
                sid = body.get("session_id")
                if url.path == "/api/chat/send":
                    if sid not in backend.sessions:
                        return self._json(404, {"detail": "Session not found"})
                    existing = backend.runs.get(sid)
                    if existing is not None and not existing.finished:
                        return self._json(
                            409, {"detail": "A chat request is already in progress"}
                        )
                    run = backend.start_run(sid)
                    return self._stream(run)
                run = backend.runs.get(sid)
                if url.path == "/api/chat/confirm-tool":
                    if backend.confirm_delay:
                        time.sleep(backend.confirm_delay)
                    with backend.lock:
                        backend.confirms.append(body)
                    if run is None or run.finished:
                        return self._json(
                            404, {"detail": "No active chat session found"}
                        )
                    with run.cond:
                        if body.get("confirm_id") != run.live_confirm or run.decision:
                            return self._json(
                                409,
                                {
                                    "detail": "That permission prompt is no longer waiting for an answer."
                                },
                            )
                        if body.get("always") and body.get("approved"):
                            run.decision = "always"
                        else:
                            run.decision = "allow" if body.get("approved") else "deny"
                        run.cond.notify_all()
                    granted = SCOPE if run.decision == "always" else None
                    return self._json(
                        200,
                        {
                            "status": "ok",
                            "approved": body["approved"],
                            "granted": granted,
                        },
                    )
                if url.path == "/api/chat/cancel":
                    backend.cancels.append(body)
                    if run is None or run.finished:
                        return self._json(
                            404, {"detail": "No active chat session found"}
                        )
                    with run.cond:
                        run.cancelled = True
                        run.cond.notify_all()
                    return self._json(200, {"status": "ok", "cancelled": True})
                return self._json(404, {"detail": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def start_run(self, sid):
        run = Run(
            sid,
            self.sessions[sid],
            self.prompt_timeout,
            self.always_scope,
            self.prompt_wait,
        )
        self.runs[sid] = run
        threading.Thread(target=run.produce, daemon=True).start()
        return run

    def close(self):
        for run in self.runs.values():
            with run.cond:
                run.cancelled = True
                run.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backend():
    b = FakeBackend()
    yield b
    b.close()


@pytest.fixture
def turns(backend):
    return TurnRegistry(backend.url, UI)


def _prompt(turns, sid):
    w = turns.wait(sid, until="prompt", timeout_ms=5000)
    assert w.get("matched"), w
    return w["pending_prompt"]


# ── The golden path ────────────────────────────────────────────────────────


def test_full_turn_prompt_approve_read_back(backend, turns):
    backend.sessions["s1"] = "write"
    started = turns.start("s1", "write out.txt")
    assert started["accepted"] is True

    prompt = _prompt(turns, "s1")
    status = turns.status("s1")
    assert status["state"] == "awaiting_permission"
    assert prompt["tool"] == "write_file"
    assert prompt["risk"] == "write"
    assert prompt["always_scope"] == SCOPE
    assert "path=C:/work/out.txt" in prompt["args_summary"]
    assert len(prompt["args"]["content"]) < 2000  # clipped, not dumped
    assert 0 < prompt["expires_in_s"] <= 30
    assert status["current_step"]["tool"] == "write_file"

    answered = turns.answer_prompt("s1", prompt["confirm_id"], "allow_once")
    assert answered["status"] == "ok"
    assert backend.confirms == [
        {
            "session_id": "s1",
            "approved": True,
            "always": False,
            "confirm_id": prompt["confirm_id"],
        }
    ]

    assert turns.wait("s1", until="idle", timeout_ms=5000)["matched"]
    view = turns.view("s1")
    assert view["content"] == "Done: wrote out.txt"
    assert view["outcome"] == "completed"
    assert view["stopped"] is False
    tool_steps = [s for s in view["steps"] if s["type"] == "tool"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["result"] == "Wrote 2000 bytes"
    assert tool_steps[0]["success"] is True
    assert tool_steps[0]["detail"] == "out.txt"
    # Consecutive working statuses fold into one line, as in the UI.
    assert [s["label"] for s in view["steps"] if s["type"] == "status"] == ["Writing"]
    assert "25.0 tok/s" in view["footer"]["text"]
    assert "300ms TTFT" in view["footer"]["text"]
    assert "900 → 40 tokens" in view["footer"]["text"]

    m = turns.metrics("s1")
    assert (m["steps"], m["tool_calls"], m["tokens_in"], m["tokens_out"]) == (
        3,
        1,
        900,
        40,
    )
    assert m["agent_elapsed_s"] == 1.5 and m["wall_s"] > 0
    # Every request carried the CSRF header the backend requires.
    assert all(h.get("X-Gaia-UI") == "1" for h in backend.headers_seen)


def test_always_grants_only_the_scope_shown(backend, turns):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    prompt = _prompt(turns, "s1")
    out = turns.answer_prompt("s1", prompt["confirm_id"], "always", always_scope=SCOPE)
    assert out["status"] == "ok" and out["granted"] == SCOPE
    assert backend.confirms[-1]["always"] is True
    assert backend.confirms[-1]["confirm_id"] == prompt["confirm_id"]


def test_deny_is_reported_as_a_denial(backend, turns):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    prompt = _prompt(turns, "s1")
    assert turns.answer_prompt("s1", prompt["confirm_id"], "deny")["status"] == "ok"
    assert backend.confirms[-1]["approved"] is False
    turns.wait("s1", until="idle", timeout_ms=5000)
    view = turns.view("s1")
    assert view["denials"][0]["tool"] == "write_file"
    assert view["content"] == "I did not write it."


# ── Prompt answering: adversarial ──────────────────────────────────────────


def test_answering_twice_is_refused_locally(backend, turns):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    cid = _prompt(turns, "s1")["confirm_id"]
    assert turns.answer_prompt("s1", cid, "allow_once")["status"] == "ok"
    again = turns.answer_prompt("s1", cid, "deny")
    assert again["refused"] == "already_answered"
    assert len(backend.confirms) == 1  # the second never reached the backend


def test_concurrent_answers_reach_the_backend_once(backend, turns):
    backend.sessions["s1"] = "write"
    backend.confirm_delay = 0.3
    turns.start("s1", "go")
    cid = _prompt(turns, "s1")["confirm_id"]
    results = []
    threads = [
        threading.Thread(
            target=lambda d=d: results.append(turns.answer_prompt("s1", cid, d))
        )
        for d in ("allow_once", "deny", "allow_once", "deny")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert sum(r.get("status") == "ok" for r in results) == 1
    assert len(backend.confirms) == 1


def test_expired_prompt_is_refused(backend, turns):
    backend.sessions["s1"] = "write"
    backend.prompt_timeout = 0.4
    # The run outlives the 0.4 s it advertises, so a slow runner still sees the
    # prompt before it ends; expiry is judged from the advertised timeout.
    backend.prompt_wait = 30.0
    turns.start("s1", "go")
    cid = _prompt(turns, "s1")["confirm_id"]
    time.sleep(0.5)
    out = turns.answer_prompt("s1", cid, "allow_once")
    assert out["status"] == "error"
    assert out["refused"] == "expired"
    assert backend.confirms == []


def test_prompt_id_from_another_session_is_refused(backend, turns):
    backend.sessions.update({"a": "write", "b": "write"})
    turns.start("a", "go")
    turns.start("b", "go")
    cid_a = _prompt(turns, "a")["confirm_id"]
    _prompt(turns, "b")
    out = turns.answer_prompt("b", cid_a, "allow_once")
    assert out["refused"] == "wrong_session"
    assert backend.confirms == []
    # A's prompt is untouched and still answerable in A.
    assert turns.answer_prompt("a", cid_a, "allow_once")["status"] == "ok"


@pytest.mark.parametrize("cid", ["", "   ", None, "not-a-real-id"])
def test_blind_or_unknown_prompt_id_is_refused(backend, turns, cid):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    _prompt(turns, "s1")
    out = turns.answer_prompt("s1", cid, "allow_once")
    assert out["status"] == "error"
    assert backend.confirms == []  # never sent without a known id


def test_replaced_prompt_id_is_stale(backend, turns):
    """An id this session saw earlier, but not the one waiting now."""
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    first = _prompt(turns, "s1")["confirm_id"]
    turns.answer_prompt("s1", first, "allow_once")
    turns.wait("s1", until="idle", timeout_ms=5000)
    turns.start("s1", "again")
    _prompt(turns, "s1")
    out = turns.answer_prompt("s1", first, "allow_once")
    assert out["refused"] == "stale"
    assert out["pending_prompt"]["confirm_id"] != first
    assert len(backend.confirms) == 1


@pytest.mark.parametrize(
    "scope", ["", "writing files anywhere", SCOPE + " and more", SCOPE.upper()]
)
def test_always_with_a_different_scope_is_refused(backend, turns, scope):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    cid = _prompt(turns, "s1")["confirm_id"]
    out = turns.answer_prompt("s1", cid, "always", always_scope=scope)
    assert out["refused"] == "scope_mismatch"
    assert backend.confirms == []


def test_always_is_refused_when_the_prompt_does_not_offer_it(backend, turns):
    backend.sessions["s1"] = "write"
    backend.always_scope = None
    turns.start("s1", "go")
    cid = _prompt(turns, "s1")["confirm_id"]
    out = turns.answer_prompt("s1", cid, "always", always_scope="anything")
    assert out["refused"] == "always_not_offered"
    assert backend.confirms == []


@pytest.mark.parametrize("decision", ["yes", "ALLOW_ONCE", "", "approve"])
def test_unknown_decision_is_refused(backend, turns, decision):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    cid = _prompt(turns, "s1")["confirm_id"]
    assert turns.answer_prompt("s1", cid, decision)["status"] == "error"
    assert backend.confirms == []


def test_backend_rejection_is_surfaced(backend, turns):
    """The backend has the last word: its 409 comes back, never an ok."""
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    cid = _prompt(turns, "s1")["confirm_id"]
    backend.runs["s1"].live_confirm = "something-else"
    out = turns.answer_prompt("s1", cid, "allow_once")
    assert out["status"] == "error" and out["refused"] == "backend_rejected"


# ── wait_for_turn ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        -1,
        0,
        MAX_WAIT_MS + 1,
        10**18,
        float("nan"),
        float("inf"),
        -float("inf"),
        1.5,
        True,
        False,
        None,
        "abc",
        "1e9",
        [],
        {},
    ],
)
def test_absurd_wait_timeouts_are_refused_without_blocking(turns, bad):
    t0 = time.monotonic()
    out = turns.wait("s1", until="idle", timeout_ms=bad)
    assert out["status"] == "error"
    assert "timeout_ms" in out["detail"]
    assert time.monotonic() - t0 < 0.5


@pytest.mark.parametrize("good", [1, 60000, MAX_WAIT_MS, "500", 2.0])
def test_sane_wait_timeouts_parse(good):
    ms, problem = parse_timeout_ms(good)
    assert problem is None and ms == int(float(good))


def test_unknown_condition_and_missing_text_are_refused(turns):
    assert turns.wait("s1", until="forever")["status"] == "error"
    assert turns.wait("s1", until="text", text="")["status"] == "error"


def test_wait_times_out_with_the_state_it_had(backend, turns):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    _prompt(turns, "s1")
    t0 = time.monotonic()
    out = turns.wait("s1", until="idle", timeout_ms=300)
    assert out["timed_out"] is True and out["matched"] is False
    assert out["state"] == "awaiting_permission"
    assert 0.25 < time.monotonic() - t0 < 2


def test_wait_returns_when_the_turn_ends_without_the_condition(backend, turns):
    backend.sessions["s1"] = "plain"
    turns.start("s1", "hi")
    out = turns.wait("s1", until="prompt", timeout_ms=5000)
    assert out["ended"] is True and out["matched"] is False


def test_wait_for_text(backend, turns):
    backend.sessions["s1"] = "plain"
    turns.start("s1", "hi")
    assert turns.wait("s1", until="text", text="Hello", timeout_ms=5000)["matched"]


# ── stop, start, attach, disconnect ────────────────────────────────────────


def test_stop_ends_a_turn_waiting_on_a_prompt(backend, turns):
    backend.sessions["s1"] = "write"
    turns.start("s1", "go")
    _prompt(turns, "s1")
    assert turns.stop("s1")["stop_requested"] is True
    assert backend.cancels == [{"session_id": "s1"}]
    assert turns.wait("s1", until="idle", timeout_ms=5000)["matched"]
    view = turns.view("s1")
    assert view["outcome"] == "cancelled" and view["stopped"] is True
    assert backend.confirms == []


def test_stop_with_nothing_running_is_an_error(backend, turns):
    assert turns.stop("nobody")["status"] == "error"


def test_start_reports_missing_and_busy_sessions(backend, turns):
    assert "Session not found" in turns.start("ghost", "hi")["detail"]
    backend.sessions["s1"] = "write"
    assert turns.start("s1", "go")["accepted"] is True
    busy = turns.start("s1", "again")
    assert busy["status"] == "error" and "already running" in busy["detail"]


def test_turn_started_in_the_browser_is_attached(backend, turns):
    backend.sessions["s1"] = "write"
    backend.start_run("s1")  # as if the user hit Send in the UI
    deadline = time.monotonic() + 5
    while backend.runs["s1"].live_confirm is None and time.monotonic() < deadline:
        time.sleep(0.02)
    status = turns.status("s1")
    assert status["origin"] == "attached"
    prompt = _prompt(turns, "s1")
    assert (
        turns.answer_prompt("s1", prompt["confirm_id"], "allow_once")["status"] == "ok"
    )
    assert turns.wait("s1", until="idle", timeout_ms=5000)["matched"]


def test_finished_turn_is_not_replaced_by_a_replay_of_itself(backend, turns):
    """The backend lists a run briefly after done; that must not re-attach."""
    backend.sessions["s1"] = "plain"
    turns.start("s1", "hi")
    assert turns.wait("s1", until="idle", timeout_ms=5000)["matched"]
    backend.lingering.add("s1")
    status = turns.status("s1")
    assert status["origin"] == "mcp" and status["outcome"] == "completed"
    assert turns.wait("s1", until="idle", timeout_ms=1000)["matched"]
    assert backend.attaches == 0


def test_stream_that_closes_without_done_is_not_a_success(backend, turns):
    backend.sessions["s1"] = "drop"
    turns.start("s1", "hi")
    turns.wait("s1", until="idle", timeout_ms=5000)
    view = turns.view("s1")
    assert view["outcome"] == "disconnected"
    assert view["error"]
    assert view["footer"]["tokens_per_second"] is None


def test_reads_before_any_turn_are_explicit(turns):
    assert turns.view("s1")["status"] == "error"
    assert turns.metrics("s1")["status"] == "error"


# ── Folding rules (mirror ChatView.tsx) ────────────────────────────────────


def test_fold_matches_the_ui():
    t = TurnTracker("s", "mcp")
    for e in [
        {"type": "thinking", "content": "a"},
        {"type": "thinking", "content": "b"},
        {"type": "chunk", "content": "Before"},
        {"type": "tool_start", "tool": "read_file", "detail": "Read"},
        {"type": "status", "status": "working", "message": "Executing read_file"},
        {"type": "tool_result", "summary": "ok", "success": True},
        {"type": "chunk", "content": "After"},
        {"type": "step", "step": 2, "total": 10},
        {"type": "agent_error", "content": "boom"},
    ]:
        t.apply(e)
    with t.cond:
        steps = [s["type"] for s in t.steps]
        assert steps == ["thinking", "tool", "error"]
        assert t.steps[0]["detail"] == "ab"
        assert t.content() == "Before\n\nAfter"
        t.apply({"type": "answer", "content": "Final"})
        assert t.content() == "Final"


def test_metrics_never_invent_zeros():
    t = TurnTracker("s", "mcp")
    t.apply({"type": "answer", "content": "x"})
    t.apply({"type": "done", "content": "x"})
    with t.cond:
        m = t.metrics()
    assert m["tokens_in"] is None and m["tokens_out"] is None and m["steps"] is None
    assert not math.isnan(m["wall_s"])


# ── Registration and transport ─────────────────────────────────────────────


NEW_TOOLS = {
    "start_message",
    "turn_status",
    "wait_for_turn",
    "answer_prompt",
    "answer_question",
    "stop_turn",
    "turn_view",
    "turn_metrics",
    "get_settings",
    "list_models",
    "set_model",
    "set_model_server",
}
LEGACY_TOOLS = {
    "system_status",
    "list_sessions",
    "create_session",
    "get_session",
    "delete_session",
    "get_messages",
    "send_message",
    "list_documents",
    "index_document",
    "index_folder",
    "browse_files",
    "search_files",
    "preview_file",
    "take_screenshot",
    "open_session_in_browser",
}


def _server(monkeypatch, url="http://127.0.0.1:9"):
    pytest.importorskip("mcp")
    monkeypatch.setenv("GAIA_MEMORY_MCP_ALWAYS", "1")
    from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

    return create_agent_ui_mcp(url)


def test_new_tools_registered_and_old_ones_kept(monkeypatch):
    tools = set(_server(monkeypatch)._tool_manager._tools)
    assert NEW_TOOLS <= tools
    assert LEGACY_TOOLS <= tools


def test_settings_tools_hit_the_ui_endpoints(monkeypatch):
    from unittest.mock import patch

    tools = _server(monkeypatch)._tool_manager._tools
    with patch("gaia.mcp.servers.agent_ui_mcp._api", return_value={"ok": 1}) as api:
        tools["set_model"].fn(model="Gemma-4-E4B-it-GGUF")
        tools["set_model_server"].fn(mode="external", base_url="http://localhost:13305")
        tools["list_models"].fn(provider="../sessions")
        tools["get_settings"].fn()
    calls = [(c.args[1], c.args[2], c.kwargs.get("json")) for c in api.call_args_list]
    assert calls == [
        ("post", "/providers/select", {"model": "Gemma-4-E4B-it-GGUF"}),
        (
            "post",
            "/lemonade/target",
            {"mode": "external", "base_url": "http://localhost:13305"},
        ),
        ("get", "/providers/..%2Fsessions/models", None),
        ("get", "/providers/active", None),
        ("get", "/lemonade/target", None),
    ]


def _http_app(monkeypatch, host="localhost"):
    mcp = _server(monkeypatch)
    from gaia.mcp.servers.agent_ui_control import transport_security_settings

    return mcp.streamable_http_app(
        transport_security=transport_security_settings(host), host=host
    )


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "http://localhost:3000",  # another app on this PC
        "http://127.0.0.1:4200",  # even the Agent UI's own origin
        "null",
        "file://",
    ],
)
def test_cross_origin_requests_are_refused(monkeypatch, origin):
    from starlette.testclient import TestClient

    with TestClient(_http_app(monkeypatch), base_url="http://localhost:8766") as client:
        r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Origin": origin})
    assert r.status_code == 403


def test_rebinding_host_is_refused(monkeypatch):
    from starlette.testclient import TestClient

    with TestClient(
        _http_app(monkeypatch), base_url="http://attacker.example:8766"
    ) as client:
        r = client.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 421


def test_an_mcp_client_without_origin_is_served(monkeypatch):
    from starlette.testclient import TestClient

    with TestClient(_http_app(monkeypatch), base_url="http://localhost:8766") as client:
        r = client.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 200


# ── Non-loopback binds: opt-in plus bearer token ───────────────────────────

TOKEN = "s3cr3t-" + "k" * 40
NETWORK_HOSTS = ["0.0.0.0", "192.168.1.20", "10.0.0.5", "::", "my-pc.local"]


@pytest.mark.parametrize("host", NETWORK_HOSTS)
def test_network_bind_refused_without_opt_in(host):
    from gaia.mcp.servers.agent_ui_control import RemoteBindError, remote_token

    with pytest.raises(RemoteBindError) as exc:
        remote_token(host, allow_remote=False, token=TOKEN)
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize("token", [None, "", "   ", "short-token", "x" * 31])
def test_opt_in_without_a_real_token_is_refused(token):
    from gaia.mcp.servers.agent_ui_control import RemoteBindError, remote_token

    with pytest.raises(RemoteBindError) as exc:
        remote_token("0.0.0.0", allow_remote=True, token=token)
    if token and token.strip():
        assert token not in str(exc.value)


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "127.0.0.5", "::1", "[::1]"]
)
def test_loopback_needs_no_token(host):
    from gaia.mcp.servers.agent_ui_control import remote_token

    assert remote_token(host, allow_remote=False, token=None) is None


def test_run_http_cannot_be_called_around_the_check(monkeypatch):
    from gaia.mcp.servers.agent_ui_control import RemoteBindError
    from gaia.mcp.servers.agent_ui_mcp import run_http

    mcp = _server(monkeypatch)
    from unittest.mock import patch

    with patch.object(mcp, "run") as run, pytest.raises(RemoteBindError):
        run_http(mcp, "0.0.0.0", 9999)
    run.assert_not_called()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::", "myhost.lan"])
def test_loopback_transport_settings_refuse_a_network_host(host):
    from gaia.mcp.servers.agent_ui_control import (
        RemoteBindError,
        transport_security_settings,
    )

    with pytest.raises(RemoteBindError):
        transport_security_settings(host)


def _remote_client(monkeypatch):
    from starlette.testclient import TestClient

    from gaia.mcp.servers.agent_ui_control import remote_app

    app = remote_app(_server(monkeypatch), "0.0.0.0", TOKEN)
    return TestClient(app, base_url="http://192.168.1.20:8766")


@pytest.mark.parametrize(
    "auth",
    [
        None,
        "",
        "Bearer ",
        "Bearer wrong-token",
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN}x",
        TOKEN,  # no scheme
        f"Basic {TOKEN}",
        f"bearer {TOKEN}",
    ],
)
def test_missing_wrong_or_empty_token_is_refused(monkeypatch, caplog, auth):
    caplog.set_level("DEBUG")
    headers = dict(MCP_HEADERS)
    if auth is not None:
        headers["Authorization"] = auth
    with _remote_client(monkeypatch) as client:
        r = client.post("/mcp", json=INIT, headers=headers)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
    assert TOKEN not in r.text and TOKEN not in caplog.text


def test_right_token_is_served_and_never_logged(monkeypatch, caplog, capsys):
    caplog.set_level("DEBUG")
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    with _remote_client(monkeypatch) as client:
        ok = client.post("/mcp", json=INIT, headers=headers)
        browser = client.post(
            "/mcp", json=INIT, headers={**headers, "Origin": "http://192.168.1.20"}
        )
    assert ok.status_code == 200
    assert browser.status_code == 403  # a token does not let a web page in
    out = capsys.readouterr()
    for text in (caplog.text, out.out, out.err, ok.text, browser.text):
        assert TOKEN not in text


def test_main_serves_a_network_bind_only_with_opt_in_and_token(monkeypatch, capsys):
    from unittest.mock import patch

    from gaia.mcp.servers import agent_ui_mcp
    from gaia.mcp.servers.agent_ui_control import BearerTokenMiddleware

    server = _server(monkeypatch)
    monkeypatch.setenv("GAIA_AGENT_UI_MCP_TOKEN", TOKEN)
    argv = ["agent_ui_mcp", "--host", "0.0.0.0", "--port", "9877", "--allow-remote"]
    with (
        patch.object(agent_ui_mcp, "create_agent_ui_mcp", return_value=server),
        patch.object(server, "run") as sdk_run,
        patch("uvicorn.run") as uv_run,
        patch.object(agent_ui_mcp.sys, "argv", argv),
    ):
        agent_ui_mcp.main()
    sdk_run.assert_not_called()
    app = uv_run.call_args.args[0]
    assert isinstance(app, BearerTokenMiddleware)
    assert uv_run.call_args.kwargs == {"host": "0.0.0.0", "port": 9877}
    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err


@pytest.mark.parametrize("allow_remote", [False, True])
def test_cli_refusal_never_echoes_the_token(monkeypatch, capsys, allow_remote):
    from types import SimpleNamespace
    from unittest.mock import patch

    from gaia import cli

    secret = "abc123-not-long-enough" if allow_remote else TOKEN
    monkeypatch.setenv("GAIA_AGENT_UI_MCP_TOKEN", secret)
    args = SimpleNamespace(
        backend="http://backend",
        stdio=False,
        host="192.168.1.20",
        port=9879,
        allow_remote=allow_remote,
    )
    with (
        patch("gaia.mcp.servers.agent_ui_mcp.create_agent_ui_mcp") as create,
        pytest.raises(SystemExit) as exc,
    ):
        cli.handle_mcp_serve(args)
    assert exc.value.code == 2
    create.assert_not_called()
    out = capsys.readouterr()
    assert secret not in out.out and secret not in out.err
    assert "GAIA_AGENT_UI_MCP_TOKEN" in out.err
