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


class _ConnectionErrorFakeAgent:
    """Raises a realistic ``requests`` ConnectionError — the raw urllib3 repr a
    user actually sees when Lemonade is down, NOT a hand-written friendly
    string (issue #2139 acceptance)."""

    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 20, "fake-model")
        import requests

        raise requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='localhost', port=8000): Max retries "
            "exceeded with url: /api/v1/chat/completions (Caused by "
            "NewConnectionError('<urllib3.connection.HTTPConnection object at "
            "0x10a>: Failed to establish a new connection: [Errno 61] "
            "Connection refused'))"
        )


class _BuiltinConnRefusedFakeAgent:
    """Raises a builtin ``ConnectionRefusedError`` (an OS-level transport error,
    not a friendly string) — classified by type, not string shape."""

    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 20, "fake-model")
        raise ConnectionRefusedError(61, "Connection refused")


class _UnrelatedErrorFakeAgent:
    """Raises an error that has nothing to do with connectivity — it must pass
    through verbatim, never masked behind Lemonade copy (issue #2139)."""

    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 20, "fake-model")
        raise ValueError("triage produced malformed JSON at row 4")


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


def _assert_actionable_lemonade_detail(detail: str) -> None:
    """The three-part actionable contract (#2139): what failed, what to do,
    where to look."""
    lower = detail.lower()
    assert "lemonade server is not reachable" in lower  # what failed
    # what to do — start it (either remediation is acceptable copy).
    assert "lemonade-server serve" in lower or "gaia init" in lower
    assert "amd-gaia.ai/docs/guides/email" in lower  # where to look


def test_lemonade_down_connection_error_gets_actionable_detail(app_client, monkeypatch):
    """A realistic requests ConnectionError → actionable guidance, with the raw
    exception appended for debugging (not replacing it)."""
    fake = _ConnectionErrorFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
    resp = app_client.post("/v1/email/query", json=_req())
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["status"] == 500
    detail = events[-1]["detail"]
    _assert_actionable_lemonade_detail(detail)
    # The original exception text is preserved for debugging — appended, never
    # dropped (the guidance leads, the raw repr trails).
    assert "Technical details:" in detail
    assert "Connection refused" in detail
    assert detail.lower().index("not reachable") < detail.index("Technical details:")


def test_lemonade_down_builtin_connection_error_gets_actionable_detail(
    app_client, monkeypatch
):
    """A builtin ConnectionRefusedError is classified by TYPE (its str carries
    no 'Lemonade' token), proving detection isn't just substring luck."""
    fake = _BuiltinConnRefusedFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
    resp = app_client.post("/v1/email/query", json=_req())
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    _assert_actionable_lemonade_detail(events[-1]["detail"])
    assert "Connection refused" in events[-1]["detail"]


def test_unrelated_error_passes_through_unmasked(app_client, monkeypatch):
    """A non-connectivity failure is surfaced verbatim — never rewritten as a
    Lemonade message (no silent masking of unrelated bugs)."""
    fake = _UnrelatedErrorFakeAgent()
    monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
    resp = app_client.post("/v1/email/query", json=_req())
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["status"] == 500
    assert events[-1]["detail"] == "triage produced malformed JSON at row 4"
    assert "Lemonade" not in events[-1]["detail"]


# ---------------------------------------------------------------------------
# Classification helper — pure-function coverage (no TestClient)
# ---------------------------------------------------------------------------


def test_terminal_error_detail_classifies_wrapped_connection_cause():
    """A transport error hidden behind ``raise ... from`` is still classified
    unreachable — the cause chain is walked, not just ``str(exc)``."""
    try:
        raise ConnectionRefusedError(61, "Connection refused")
    except ConnectionRefusedError as cause:
        wrapped = RuntimeError("triage tool failed")
        wrapped.__cause__ = cause

    assert query_routes._is_lemonade_unreachable(wrapped) is True
    detail = query_routes._terminal_error_detail(wrapped)
    _assert_actionable_lemonade_detail(detail)
    # The wrapper's own message is preserved in the appended technical details.
    assert "triage tool failed" in detail


def test_terminal_error_detail_leaves_unrelated_errors_verbatim():
    exc = ValueError("some unrelated parse failure")
    assert query_routes._is_lemonade_unreachable(exc) is False
    assert query_routes._terminal_error_detail(exc) == "some unrelated parse failure"


def test_timeout_is_not_classified_as_lemonade_down():
    """A timeout means up-but-slow, or a *different* host (the Gmail/Outlook
    backends use httpx with their own timeouts) — never a not-running local
    Lemonade, which refuses instantly. Such errors must pass through verbatim so
    the user isn't told to restart Lemonade when Gmail is merely slow (#2139)."""

    class _ReadTimeout(Exception):
        """Stands in for httpx.ReadTimeout — its repr carries 'timeout'."""

    for exc in (
        _ReadTimeout("The read operation timed out"),
        _ReadTimeout(""),  # empty str → class-name fallback still says nothing Lemonade
        TimeoutError("timed out"),
        RuntimeError("Gmail API call: connect timeout after 15s"),
        RuntimeError("host is unreachable via the proxy"),
    ):
        assert query_routes._is_lemonade_unreachable(exc) is False, exc
        # And the actionable Lemonade copy is NOT prepended.
        assert "Lemonade" not in query_routes._terminal_error_detail(exc)


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
