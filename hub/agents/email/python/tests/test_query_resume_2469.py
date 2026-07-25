# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The mid-run question resume seam — ``needs_input`` + ``/respond`` (#2469).

Before this, ``/v1/email/query`` could pause but never continue: a step that
needed something from the user emitted an event and then deliberately killed the
run. These tests hold the other half of the contract in place.

The resume path is exercised against a REAL uvicorn server on an ephemeral port,
not the in-process TestClient: the whole point is that a SECOND request lands
while the FIRST one's stream is still open and blocked on a worker thread.
Serialising them through a test transport would prove nothing.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid

import httpx
import pytest
from gaia_agent_email import export_openapi, query_routes


class _AskingFakeAgent:
    """Asks one question mid-run and reports what came back."""

    QUESTION = "Which mailbox should I connect?"

    def __init__(self, allow_free_text: bool = False):
        self.conversation_history = []
        self.console = None
        self.can_answer_questions = False  # the route sets it from the request
        self._cancel_event = None
        self.allow_free_text = allow_free_text
        self.answer = None
        self.asked = threading.Event()

    def process_query(self, query, max_steps=None):
        from gaia_agent_email import question as q

        self.console.print_processing_start(query, 20, "fake-model")
        self.asked.set()
        try:
            self.answer = q.ask(
                self,
                self.QUESTION,
                options=(
                    q.Option("google", "Gmail", "A gmail.com or Workspace account."),
                    q.Option("microsoft", "Outlook", "An outlook.com account."),
                ),
                allow_free_text=self.allow_free_text,
                timeout_seconds=10,
            )
        except q.InputUnansweredError as exc:
            self.console.print_error(str(exc))
            return {"status": "failed", "result": str(exc)}
        self.console.print_final_answer(f"Connecting {self.answer}.", streaming=False)
        return {"status": "success", "answer": f"Connecting {self.answer}."}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LiveServer:
    """A real uvicorn server for the sidecar app, on an ephemeral port."""

    def __init__(self):
        import uvicorn

        self.port = _free_port()
        config = uvicorn.Config(
            export_openapi.build_app(),
            host="127.0.0.1",
            port=self.port,
            log_level="error",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if getattr(self.server, "started", False):
                return self
            time.sleep(0.02)
        raise RuntimeError("the test sidecar server never started")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture()
def live_server():
    with _LiveServer() as srv:
        yield srv


def _stream_events(client, base, body, on_event):
    """POST /query and hand each canonical event to ``on_event``.

    ``on_event`` may return ``"stop"`` to close the stream early. Returns the
    full list of events seen.
    """
    seen = []
    with client.stream("POST", f"{base}/v1/email/query", json=body) as resp:
        assert resp.status_code == 200, resp.read()
        for line in resp.iter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            event = json.loads(line[len("data:") :].strip())
            seen.append(event)
            if on_event(event) == "stop":
                break
    return seen


def test_answered_question_resumes_the_same_run(live_server, monkeypatch):
    """The headline: a run that asks a question and gets an answer completes."""
    fake = _AskingFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **kw: fake)

    run_id = str(uuid.uuid4())
    body = {
        "query": "Set up my mailbox.",
        "run_id": run_id,
        "context": [],
        "can_answer_questions": True,
    }

    with httpx.Client(timeout=30.0) as client:

        def answer(event):
            if event["type"] != "needs_input":
                return None
            # The question is answerable: it names itself and where to reply.
            assert event["run_id"] == run_id
            assert event["question"] == _AskingFakeAgent.QUESTION
            assert event["respond_url"] == f"/v1/email/query/{run_id}/respond"
            assert [o["value"] for o in event["options"]] == ["google", "microsoft"]
            assert event["options"][0]["description"]
            assert event["allow_free_text"] is False

            resp = client.post(
                f"{live_server.base}/v1/email/query/{run_id}/respond",
                json={"request_id": event["request_id"], "value": "microsoft"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["accepted"] is True
            return None

        events = _stream_events(client, live_server.base, body, answer)

    types = [e["type"] for e in events]
    assert "needs_input" in types, types
    # The run CONTINUED on the same stream and terminated normally.
    assert types[-1] == "final", types
    assert events[-1]["answer"] == "Connecting microsoft."
    assert fake.answer == "microsoft"


def test_label_is_accepted_as_the_answer(live_server, monkeypatch):
    """A client that echoes the label instead of the value still resolves."""
    fake = _AskingFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **kw: fake)

    run_id = str(uuid.uuid4())
    with httpx.Client(timeout=30.0) as client:

        def answer(event):
            if event["type"] == "needs_input":
                client.post(
                    f"{live_server.base}/v1/email/query/{run_id}/respond",
                    json={"request_id": event["request_id"], "value": "Gmail"},
                )
            return None

        events = _stream_events(
            client,
            live_server.base,
            {"query": "Set up.", "run_id": run_id, "context": [], "can_answer_questions": True},
            answer,
        )

    assert events[-1]["type"] == "final"
    assert fake.answer == "google"


def test_unanswered_question_fails_loudly_instead_of_hanging(live_server, monkeypatch):
    """Nothing answers → the run ends with an error, not an open socket."""
    fake = _AskingFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **kw: fake)

    run_id = str(uuid.uuid4())
    started = time.monotonic()
    with httpx.Client(timeout=60.0) as client:
        events = _stream_events(
            client,
            live_server.base,
            {"query": "Set up.", "run_id": run_id, "context": [], "can_answer_questions": True},
            lambda e: None,
        )
    elapsed = time.monotonic() - started

    types = [e["type"] for e in events]
    assert "needs_input" in types, types
    assert types[-1] == "error", types
    assert "No answer to" in events[-1]["detail"]
    # The ask timeout is 10s; the stream must not outlive it by much.
    assert elapsed < 40, f"the run hung for {elapsed:.1f}s"


def test_answer_for_an_unknown_run_is_rejected(live_server):
    """A run that does not exist cannot be answered — 404, not a silent no-op."""
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{live_server.base}/v1/email/query/{uuid.uuid4()}/respond",
            json={"request_id": "whatever", "value": "yes"},
        )
    assert resp.status_code == 404
    assert "no in-flight run" in resp.json()["detail"].lower()


def test_stale_request_id_is_rejected(live_server, monkeypatch):
    """An answer to a question the run is not waiting on is a 409, not applied."""
    fake = _AskingFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **kw: fake)

    run_id = str(uuid.uuid4())
    with httpx.Client(timeout=30.0) as client:

        def answer(event):
            if event["type"] != "needs_input":
                return None
            stale = client.post(
                f"{live_server.base}/v1/email/query/{run_id}/respond",
                json={"request_id": "a-question-from-another-run", "value": "google"},
            )
            assert stale.status_code == 409, stale.text
            assert "not waiting on" in stale.json()["detail"]
            # The real one still works, so the rejection was targeted.
            ok = client.post(
                f"{live_server.base}/v1/email/query/{run_id}/respond",
                json={"request_id": event["request_id"], "value": "google"},
            )
            assert ok.status_code == 200
            return None

        events = _stream_events(
            client,
            live_server.base,
            {"query": "Set up.", "run_id": run_id, "context": [], "can_answer_questions": True},
            answer,
        )

    assert events[-1]["type"] == "final"
    assert fake.answer == "google"


def test_a_caller_that_cannot_answer_gets_an_error_not_a_pause(live_server, monkeypatch):
    """The default is "I can't answer", and it must fail fast, not park.

    This is the regression guard for the one-shot / CLI surfaces: they cannot
    render a question, so an agent that asks one there would sit silent until
    the 240s timeout — indistinguishable from a hang.
    """
    fake = _AskingFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **kw: fake)

    started = time.monotonic()
    with httpx.Client(timeout=30.0) as client:
        events = _stream_events(
            client,
            live_server.base,
            # can_answer_questions omitted → defaults to False.
            {"query": "Set up.", "run_id": str(uuid.uuid4()), "context": []},
            lambda e: None,
        )
    elapsed = time.monotonic() - started

    types = [e["type"] for e in events]
    assert "needs_input" not in types, "a caller that cannot answer must not be asked"
    assert types[-1] in ("final", "error"), types
    assert elapsed < 15, f"it paused for {elapsed:.1f}s instead of failing fast"


def test_stream_heartbeats_while_a_question_is_pending(live_server, monkeypatch):
    """The paused stream keeps talking, so a client watchdog does not abandon it."""
    fake = _AskingFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **kw: fake)

    run_id = str(uuid.uuid4())
    saw_heartbeat = False
    with httpx.Client(timeout=60.0) as client:
        with client.stream(
            "POST",
            f"{live_server.base}/v1/email/query",
            json={"query": "Set up.", "run_id": run_id, "context": [], "can_answer_questions": True},
        ) as resp:
            for raw in resp.iter_lines():
                if raw.startswith(":"):
                    saw_heartbeat = True
                    break
    assert saw_heartbeat, "a run parked on a question sent no keepalive"
