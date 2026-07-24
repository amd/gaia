# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end tests for ``POST /v1/email/query`` — the canonical streaming
agent-loop surface (#2016).

This is the acceptance harness for the issue: it drives the REAL route + REAL
``SSEOutputHandler`` + REAL translation layer with a FAKE agent (injected via the
``build_query_agent`` seam) so the canonical wire is exercised without Lemonade or
Gmail. It asserts the event **SEQUENCE** (not merely a final string), that cancel
stops tool execution between steps, and that a confirmation-requiring step ends
the stream with the stateless D1 refusal.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from gaia_agent_email import export_openapi, query_routes


@pytest.fixture()
def app_client():
    return TestClient(export_openapi.build_app())


def _parse_sse(text: str):
    """Parse an SSE body into a list of canonical event dicts."""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


def _types(events):
    return [e["type"] for e in events]


def _req(query="Triage my inbox.", **extra):
    body = {"query": query, "run_id": str(uuid.uuid4()), "context": []}
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Fake agents (injected via the build_query_agent seam)
# ---------------------------------------------------------------------------


class _HappyFakeAgent:
    """Drives the handler through a realistic triage turn: status → tool → final."""

    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None
        self.seen_query = None
        self.seen_history = None

    def process_query(self, query, max_steps=None):
        self.seen_query = query
        self.seen_history = list(self.conversation_history)
        self.console.print_processing_start(query, 20, "fake-model")
        self.console.print_step_header(1, 20)
        self.console.print_tool_usage("triage_inbox")
        self.console.pretty_print_json({"max_messages": 10}, title="Arguments")
        self.console.pretty_print_json({"ok": True, "count": 5})
        self.console.print_tool_complete()
        self.console.print_final_answer("Triaged 5 emails.", streaming=False)
        return {"answer": "Triaged 5 emails."}


class _ConfirmFakeAgent:
    """Attempts a destructive tool that requires confirmation."""

    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 20, "fake-model")
        self.console.print_tool_usage("send_now")
        approved = self.console.confirm_tool_execution(
            "send_now", {"to": "a@b.com", "subject": "Hi", "body": "there"}
        )
        # /query's stateless stub cancels the run, so confirm returns False.
        self.console.print_final_answer(
            "Sent." if approved else "Not sent.", streaming=False
        )
        return {"answer": "Sent." if approved else "Not sent."}


class _CancelFakeAgent:
    """Emits one tool per step, waiting on the cancel event BETWEEN steps."""

    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None
        self.step1_reached = threading.Event()

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 5, "fake-model")
        for step in range(1, 4):
            if self._cancel_event is not None and self._cancel_event.is_set():
                self.console.print_final_answer(
                    "Stopped between steps.", streaming=False
                )
                return {"answer": "Stopped between steps."}
            self.console.print_step_header(step, 5)
            self.console.print_tool_usage(f"tool_{step}")
            self.console.pretty_print_json({}, title="Arguments")
            self.console.pretty_print_json({"ok": True})
            self.console.print_tool_complete()
            if step == 1:
                self.step1_reached.set()
            if self._cancel_event is not None:
                # Wait (bounded) so the test can cancel between steps.
                self._cancel_event.wait(timeout=5)
        self.console.print_final_answer("Completed all steps.", streaming=False)
        return {"answer": "Completed all steps."}


class _RaisingFakeAgent:
    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 20, "fake-model")
        raise RuntimeError("Lemonade Server is not reachable at http://localhost:13305")


# ---------------------------------------------------------------------------
# Happy path — the canonical event SEQUENCE
# ---------------------------------------------------------------------------


def test_query_streams_canonical_sequence(app_client, monkeypatch):
    fake = _HappyFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)

    resp = app_client.post("/v1/email/query", json=_req())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    # The SEQUENCE, not just the final string (acceptance requirement).
    assert _types(events) == ["status", "status", "tool_call", "tool_result", "final"]
    # Every event is one of the seven canonical types.
    assert all(
        e["type"]
        in {
            "status",
            "token",
            "tool_call",
            "tool_result",
            "needs_confirmation",
            "final",
            "error",
        }
        for e in events
    )
    tool_call = events[2]
    assert tool_call == {
        "type": "tool_call",
        "tool": "triage_inbox",
        "args": {"max_messages": 10},
    }
    assert events[3]["type"] == "tool_result" and events[3]["tool"] == "triage_inbox"
    # Exactly one terminal event, and it is last (spec §3).
    assert _types(events).count("final") + _types(events).count("error") == 1
    assert events[-1] == {"type": "final", "answer": "Triaged 5 emails."} or (
        events[-1]["type"] == "final" and events[-1]["answer"] == "Triaged 5 emails."
    )


def test_query_pushes_context_as_history(app_client, monkeypatch):
    fake = _HappyFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
    ctx = [
        {"role": "user", "content": "earlier turn"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    resp = app_client.post("/v1/email/query", json=_req(context=ctx))
    assert resp.status_code == 200
    _parse_sse(resp.text)  # drain
    # Context is pushed into the agent's conversation history (spec §2.4).
    assert fake.seen_history == ctx


# ---------------------------------------------------------------------------
# Confirmation stub (D1) — needs_confirmation then a final refusal
# ---------------------------------------------------------------------------


def test_confirmation_step_ends_with_needs_confirmation_then_final_refusal(
    app_client, monkeypatch
):
    fake = _ConfirmFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)

    resp = app_client.post("/v1/email/query", json=_req(query="Send a reply to Bob."))
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = _types(events)

    assert "needs_confirmation" in types
    nc = events[types.index("needs_confirmation")]
    assert nc["action"] == "send_now"
    assert "confirm_url" not in nc  # stateless stop-and-hand-off (D1)
    # The run ends with a plain-language refusal — no internal REST contract or
    # architecture jargon leaked to the chat user (issue #2404).
    assert events[-1]["type"] == "final"
    answer = events[-1]["answer"]
    assert "confirmation" in answer.lower()
    assert "/v1/email" not in answer
    assert "D1" not in answer
    assert "POST" not in answer
    # The gated tool never actually "sent".
    assert "Sent." not in answer


# ---------------------------------------------------------------------------
# Cancel — stops tool execution between steps
# ---------------------------------------------------------------------------


def test_cancel_stops_tool_execution_between_steps(monkeypatch):
    fake = _CancelFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)

    # Two clients share the process-global run registry: one streams, one cancels.
    streamer = TestClient(export_openapi.build_app())
    canceller = TestClient(export_openapi.build_app())
    run_id = str(uuid.uuid4())
    collected = {}

    def _stream():
        resp = streamer.post(
            "/v1/email/query",
            json={"query": "do work", "run_id": run_id, "context": []},
        )
        collected["text"] = resp.text

    t = threading.Thread(target=_stream, daemon=True)
    t.start()

    # Wait until the first step ran, then cancel between step 1 and step 2.
    assert fake.step1_reached.wait(timeout=10), "agent never reached step 1"
    cancel = canceller.post(f"/v1/email/query/{run_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["cancelled"] is True

    t.join(timeout=10)
    events = _parse_sse(collected["text"])
    tool_calls = [e["tool"] for e in events if e["type"] == "tool_call"]
    # Step 1's tool ran; step 2's tool did NOT — execution stopped between steps.
    assert "tool_1" in tool_calls
    assert "tool_2" not in tool_calls
    assert events[-1]["type"] == "final"
    assert "Stopped between steps." in events[-1]["answer"]


def test_cancel_unknown_run_id_is_404(app_client):
    resp = app_client.post(f"/v1/email/query/{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Error path — a failed run ends with a terminal error event
# ---------------------------------------------------------------------------


def test_run_failure_ends_with_terminal_error(app_client, monkeypatch):
    fake = _RaisingFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
    resp = app_client.post("/v1/email/query", json=_req())
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["status"] == 500
    assert "Lemonade" in events[-1]["detail"]


# ---------------------------------------------------------------------------
# Request validation (fail loud, before the stream)
# ---------------------------------------------------------------------------


def test_missing_run_id_is_422(app_client):
    resp = app_client.post("/v1/email/query", json={"query": "hi", "context": []})
    assert resp.status_code == 422


def test_non_uuid_run_id_is_422(app_client):
    resp = app_client.post(
        "/v1/email/query", json={"query": "hi", "run_id": "not-a-uuid", "context": []}
    )
    assert resp.status_code == 422


def test_empty_query_is_422(app_client):
    resp = app_client.post("/v1/email/query", json=_req(query=""))
    assert resp.status_code == 422


def test_unknown_field_is_rejected(app_client):
    body = _req()
    body["bogus"] = 1
    resp = app_client.post("/v1/email/query", json=body)
    assert resp.status_code == 422


def test_non_lemonade_provider_is_400(app_client, monkeypatch):
    fake = _HappyFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
    resp = app_client.post("/v1/email/query", json=_req(provider="claude"))
    assert resp.status_code == 400
    assert "local inference only" in resp.json()["detail"]
