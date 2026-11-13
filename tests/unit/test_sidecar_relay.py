# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract tests for ``gaia.ui.sidecar.relay`` (#2109 query relay).

A hand-rolled fake proxy + fake handler drive ``relay_query`` with no real
HTTP, pinning the event-shape table and the crash/cancel/timeout control flow.
"""

from __future__ import annotations

import json
import threading
import uuid

import pytest

pytestmark = pytest.mark.allow_network

from gaia.daemon.sidecars.errors import (  # noqa: E402
    SidecarError,
    SidecarHTTPError,
)
from gaia.llm.surface_hints import APP_SERVER_DOWN_HINT  # noqa: E402
from gaia.ui.sidecar import relay  # noqa: E402
from gaia.ui.sse_handler import MODEL_RETAKE_FAILED_MESSAGE  # noqa: E402
from gaia.ui.turn_summary import TurnLedger  # noqa: E402

# ---------------------------------------------------------------------------
# Part 2 — unit tests: hand-rolled fakes, no real HTTP
# ---------------------------------------------------------------------------


class _FakeHandler:
    """Minimal stand-in for ``gaia.ui.sse_handler.SSEOutputHandler``.

    Mirrors the real handler's attribute names relay_query depends on:
    ``_emit``, ``cancelled`` (a ``threading.Event``), ``active_relay_response``,
    ``signal_done()``, ``turn_ledger``, the ``record_tool_*`` turn-state hooks,
    and the question / model-slot hooks a ``needs_input`` drives (no slot here,
    so stepping away is a no-op).
    """

    def __init__(self) -> None:
        self.events: list = []
        self.cancelled = threading.Event()
        self.turn_ledger = TurnLedger()
        self.active_relay_response = None
        self.done_calls = 0
        self.relay_question = None
        self.step_away_result = False
        self.retake_result = True

    def _emit(self, event):
        self.events.append(event)

    def set_relay_question(self, request_id):
        self.relay_question = request_id

    def set_relay_confirmation(self, *_args):
        pass

    def record_tool_started(self, tool_name):
        self.turn_ledger.tool_started(tool_name)

    def record_tool_args(self, args):
        self.turn_ledger.tool_args(args)

    def record_tool_result(self, data, success):
        self.turn_ledger.tool_result(data, success)

    def signal_done(self):
        self.done_calls += 1

    def step_away_from_model(self):
        return self.step_away_result

    def come_back_to_model(self):
        if self.retake_result is False:
            self._emit(
                {
                    "type": "agent_error",
                    "content": MODEL_RETAKE_FAILED_MESSAGE,
                    "fatal": True,
                }
            )
            self.cancelled.set()
        return self.retake_result


class _RaisingIterator:
    """Iterator that yields nothing and raises ``exc`` on the first ``next()``.

    Used instead of a generator function with unreachable code after a
    top-of-body ``raise`` (which would need a dangling ``yield`` to stay a
    generator function).
    """

    def __init__(self, exc):
        self._exc = exc

    def __iter__(self):
        return self

    def __next__(self):
        raise self._exc


class _ScriptedProxy:
    """Fake ``SidecarProxy`` driving ``relay_query`` from a scripted
    event source — no real HTTP.

    ``event_source`` is a zero-arg callable returning an iterable/iterator of
    canonical event dicts (may raise mid-iteration).
    """

    def __init__(self, event_source, *, on_response_obj=None, cancel_raises=None):
        self._event_source = event_source
        self.on_response_obj = (
            on_response_obj if on_response_obj is not None else object()
        )
        self.cancel_raises = cancel_raises
        self.query_stream_calls: list = []
        self.cancel_calls: list = []

    def query_stream(self, body, *, read_timeout=300.0, on_response=None):
        self.query_stream_calls.append(
            {"body": dict(body), "read_timeout": read_timeout}
        )
        if on_response is not None:
            on_response(self.on_response_obj)
        yield from self._event_source()

    def cancel_query(self, run_id):
        self.cancel_calls.append(run_id)
        if self.cancel_raises is not None:
            raise self.cancel_raises


def _events(*evs):
    """Return a zero-arg callable yielding a fixed list of events — the
    common case for ``_ScriptedProxy(event_source)``."""

    def _source():
        return iter(evs)

    return _source


# --- Request body construction -------------------------------------------


class TestRelayQueryRequestBody:
    def test_builds_body_with_query_run_id_context(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "ok"}))
        ctx = [{"role": "user", "content": "hi"}]
        relay.relay_query(handler, proxy, query="hello", context=ctx, run_id="rid-1")
        assert proxy.query_stream_calls[0]["body"] == {
            "query": "hello",
            "run_id": "rid-1",
            "context": ctx,
            "can_answer_questions": True,
        }

    def test_declares_can_answer_questions_true(self):
        # (#2595 review) the sidecar's own QueryRequest.can_answer_questions
        # defaults to False -- omitting this field silently makes ask()
        # refuse every mid-run question, even
        # though this relay DOES render needs_input and answer it. A
        # response-mocking test can never catch a missing outgoing field
        # like this; assert the request body itself carries it.
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "ok"}))
        relay.relay_query(handler, proxy, query="q", context=[])
        assert proxy.query_stream_calls[0]["body"]["can_answer_questions"] is True

    def test_includes_model_and_max_steps_when_given(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "ok"}))
        relay.relay_query(
            handler,
            proxy,
            query="q",
            context=[],
            model_id="gemma-4-e4b",
            max_steps=5,
            run_id="rid-2",
        )
        body = proxy.query_stream_calls[0]["body"]
        assert body["model"] == "gemma-4-e4b"
        assert body["max_steps"] == 5

    def test_omits_model_when_falsy_and_max_steps_when_none(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "ok"}))
        relay.relay_query(
            handler,
            proxy,
            query="q",
            context=[],
            model_id="",
            max_steps=None,
            run_id="rid-3",
        )
        body = proxy.query_stream_calls[0]["body"]
        assert "model" not in body
        assert "max_steps" not in body

    def test_run_id_minted_via_uuid4_when_omitted(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "ok"}))
        relay.relay_query(handler, proxy, query="q", context=[])
        body = proxy.query_stream_calls[0]["body"]
        # Must not raise -- a valid UUIDv4-parseable string.
        uuid.UUID(body["run_id"])

    def test_read_timeout_passed_through_to_query_stream(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "ok"}))
        relay.relay_query(handler, proxy, query="q", context=[], read_timeout=42.0)
        assert proxy.query_stream_calls[0]["read_timeout"] == 42.0

    def test_default_read_timeout_is_300(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "ok"}))
        relay.relay_query(handler, proxy, query="q", context=[])
        assert proxy.query_stream_calls[0]["read_timeout"] == 300.0


# --- active_relay_response cross-thread wiring -----------------------------


class TestActiveRelayResponseWiring:
    def test_active_relay_response_set_during_run_and_reset_after(self):
        handler = _FakeHandler()
        marker = object()
        captured = {}

        def _source_with_probe():
            captured["during"] = handler.active_relay_response
            yield {"type": "status", "message": "hi"}
            yield {"type": "final", "answer": "done"}

        proxy = _ScriptedProxy(_source_with_probe, on_response_obj=marker)
        relay.relay_query(handler, proxy, query="q", context=[])

        # on_response fired before the generator's own body ran, so the probe
        # (which runs at the top of the scripted generator) already observed it.
        assert captured["during"] is marker
        assert handler.active_relay_response is None

    def test_active_relay_proxy_and_run_id_set_during_run_and_reset_after(self):
        # (#2595) a later POST /api/chat/user-input must be able to find
        # where to deliver a needs_input answer while the run is live, and
        # never after it ends.
        handler = _FakeHandler()
        captured = {}

        def _source_with_probe():
            captured["proxy_during"] = handler.active_relay_proxy
            captured["run_id_during"] = handler.active_relay_run_id
            yield {"type": "final", "answer": "done"}

        proxy = _ScriptedProxy(_source_with_probe)
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-9")

        assert captured["proxy_during"] is proxy
        assert captured["run_id_during"] == "rid-9"
        assert handler.active_relay_proxy is None
        assert handler.active_relay_run_id is None


# --- Canonical event -> UI event shape table --------------------------------


class TestCanonicalEventShapes:
    def test_status_event_passthrough(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "status", "message": "Thinking..."},
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events[0] == {"type": "status", "message": "Thinking..."}

    def test_token_with_truthy_delta_emits_chunk(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "token", "delta": "Hi"},
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events[0] == {"type": "chunk", "content": "Hi"}

    def test_token_empty_delta_is_dropped(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "token", "delta": ""},
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        chunk_events = [e for e in handler.events if e["type"] == "chunk"]
        assert chunk_events == []

    def test_token_missing_delta_is_dropped(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "token"},
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        chunk_events = [e for e in handler.events if e["type"] == "chunk"]
        assert chunk_events == []

    def test_tool_call_emits_tool_start_then_tool_args(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "tool_call",
                    "tool": "search_inbox",
                    "args": {"q": "invoices"},
                },
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        start, args_event = handler.events[0], handler.events[1]
        assert start["type"] == "tool_start"
        assert start["tool"] == "search_inbox"
        assert isinstance(start["detail"], str) and start["detail"]
        assert args_event["type"] == "tool_args"
        assert args_event["tool"] == "search_inbox"
        assert args_event["args"] == {"q": "invoices"}
        assert isinstance(args_event["detail"], str) and args_event["detail"]

    def test_mutating_tool_call_emits_extra_visibility_status_line(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "tool_call",
                    "tool": "write_file",
                    "args": {"file_path": "notes.txt"},
                },
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        types = [e["type"] for e in handler.events]
        assert types == ["tool_start", "tool_args", "status", "answer"]
        status_event = handler.events[2]
        assert "write_file" in status_event["message"]
        assert "local change" in status_event["message"]

    def test_read_only_tool_call_does_not_emit_extra_status_line(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "tool_call", "tool": "pre_scan_inbox", "args": {}},
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        types = [e["type"] for e in handler.events]
        assert types == ["tool_start", "tool_args", "answer"]

    def test_tool_result_without_render_key(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "tool_result",
                    "tool": "pre_scan_inbox",
                    "data": {"kind": "x"},
                },
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        result_event = handler.events[0]
        assert result_event["type"] == "tool_result"
        assert result_event["tool"] == "pre_scan_inbox"
        assert result_event["success"] is True
        assert result_event["data"] == {"kind": "x"}
        assert result_event["summary"]
        assert result_event["summary"] != "Done"
        assert "render" not in result_event

    def test_tool_result_with_render_key_passthrough(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "tool_result",
                    "tool": "pre_scan_inbox",
                    "data": {"kind": "email_pre_scan"},
                    "render": "email_pre_scan",
                },
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        result_event = handler.events[0]
        assert result_event["render"] == "email_pre_scan"
        assert result_event["data"] == {"kind": "email_pre_scan"}

    def test_needs_confirmation_emitted_and_loop_continues(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "needs_confirmation",
                    "run_id": "r1",
                    "action": "send_now",
                    "summary": "send to a@b.com",
                },
                {"type": "final", "answer": "Not sent."},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        types = [e["type"] for e in handler.events]
        assert types == ["needs_confirmation", "answer"]
        assert handler.events[0] == {
            "type": "needs_confirmation",
            "action": "send_now",
            "summary": "send to a@b.com",
        }

    def test_needs_input_emitted_with_full_payload_and_loop_continues(self):
        # (#2595) unlike needs_confirmation, needs_input must NOT terminate
        # the relay loop -- the sidecar run stays blocked mid-stream until an
        # answer arrives, so a `final`/`error` after it is still expected.
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "needs_input",
                    "request_id": "req-1",
                    "question": "Which mailbox?",
                    "options": [
                        {"value": "gmail", "label": "Gmail", "description": ""},
                    ],
                    "allow_free_text": True,
                    "sensitive": False,
                    "timeout_seconds": 240,
                },
                {"type": "final", "answer": "Used Gmail."},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        types = [e["type"] for e in handler.events]
        assert types == ["needs_input", "answer"]
        assert handler.events[0] == {
            "type": "needs_input",
            "request_id": "req-1",
            "question": "Which mailbox?",
            "options": [{"value": "gmail", "label": "Gmail", "description": ""}],
            "allow_free_text": True,
            "sensitive": False,
            "timeout_seconds": 240,
        }

    def test_needs_input_defaults_missing_optional_fields(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "needs_input", "request_id": "req-2", "question": "Proceed?"},
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        event = handler.events[0]
        assert event["options"] == []
        assert event["allow_free_text"] is True
        assert event["sensitive"] is False

    def test_final_emits_answer_and_terminates(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "All done."}))
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events == [{"type": "answer", "content": "All done."}]
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel

    def test_error_event_emits_agent_error_verbatim(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events({"type": "error", "detail": "Lemonade down", "status": 500})
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events == [
            {"type": "agent_error", "content": "Lemonade down", "fatal": True}
        ]
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel

    def test_a_final_marked_failed_reaches_the_ui_marked_failed(self):
        """A sidecar turn a model failure ended must not be recorded as done."""
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "token", "delta": "I'll start by loading the skill."},
                {"type": "final", "answer": "The model stopped.", "failed": True},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events[-1] == {
            "type": "answer",
            "content": "The model stopped.",
            "failed": True,
        }

    def test_connection_shaped_error_detail_gains_actionable_hint_appended(self):
        # The sidecar emits str(exc) verbatim, so the most common consumer
        # failure (Lemonade Server down) arrives as a raw urllib3/requests
        # repr. The relay APPENDS an actionable hint — it must never replace
        # or truncate the original text.
        raw_detail = (
            "local LLM query failed: HTTPConnectionPool(host='localhost', "
            "port=13305): Max retries exceeded with url: "
            "/api/v1/chat/completions (Caused by NewConnectionError("
            "'<urllib3.connection.HTTPConnection object at 0x104a4b910>: "
            "Failed to establish a new connection: [Errno 61] Connection "
            "refused'))"
        )
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events({"type": "error", "detail": raw_detail, "status": 500})
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert len(handler.events) == 1
        content = handler.events[0]["content"]
        assert handler.events[0]["type"] == "agent_error"
        assert content.startswith(
            raw_detail
        ), "the original error text must be preserved verbatim at the front"
        assert content != raw_detail, "no hint was appended"
        assert relay.GAIA_PROFILE.lemonade_hint in content

    def test_timeout_shaped_error_detail_gains_actionable_hint_appended(self):
        raw_detail = (
            "local LLM query failed: HTTPConnectionPool(host='localhost', "
            "port=13305): Read timed out. (read timeout=300)"
        )
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events({"type": "error", "detail": raw_detail, "status": 500})
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        content = handler.events[0]["content"]
        assert content.startswith(raw_detail)
        assert relay.GAIA_PROFILE.lemonade_hint in content

    def test_non_connection_error_detail_gets_no_hint(self):
        # A logic/validation error must pass through untouched — the hint is
        # for connection/timeout-shaped failures only.
        raw_detail = "tool 'summarize_thread' failed: KeyError: 'thread_id'"
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events({"type": "error", "detail": raw_detail, "status": 500})
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events == [
            {"type": "agent_error", "content": raw_detail, "fatal": True}
        ]

    def test_unrecognized_type_emits_status_naming_the_type(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "something_unrecognized", "foo": "bar"},
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        status_event = handler.events[0]
        assert status_event["type"] == "status"
        assert "something_unrecognized" in status_event["message"]

    def test_full_event_sequence_ordered_types(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events(
                {"type": "status", "message": "Starting"},
                {"type": "tool_call", "tool": "pre_scan_inbox", "args": {}},
                {
                    "type": "tool_result",
                    "tool": "pre_scan_inbox",
                    "data": {"kind": "email_pre_scan"},
                    "render": "email_pre_scan",
                },
                {"type": "token", "delta": "Hi "},
                {"type": "token", "delta": "there."},
                {"type": "final", "answer": "Hi there."},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        types = [e["type"] for e in handler.events]
        assert types == [
            "status",
            "tool_start",
            "tool_args",
            "tool_result",
            "chunk",
            "chunk",
            "answer",
        ]


# --- Echo-strip: embedded render-card JSON in the final answer -------------


class TestFinalAnswerEchoStrip:
    def test_embedded_render_card_json_is_stripped_from_final_answer(self):
        handler = _FakeHandler()
        inner = {"kind": "table", "columns": [], "rows": []}
        answer = "Here you go.\n\n" + json.dumps(inner)
        proxy = _ScriptedProxy(_events({"type": "final", "answer": answer}))
        relay.relay_query(handler, proxy, query="q", context=[])
        content = handler.events[-1]["content"]
        assert "table" not in content
        assert '"kind"' not in content
        assert "Here you go." in content

    def test_plain_answer_with_no_embedded_card_passes_through_unchanged(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events({"type": "final", "answer": "Plain prose, nothing embedded."})
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events[-1]["content"] == "Plain prose, nothing embedded."


# --- Crash mid-stream / no-terminal-event handling --------------------------


class TestStreamFailureHandling:
    def test_crash_after_some_events_emits_stream_ended_unexpectedly(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "status", "message": "hi"}
            raise RuntimeError("socket closed")

        proxy = _ScriptedProxy(_source)
        # Must not raise.
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == relay.GAIA_PROFILE.stream_ended_text()
        # Ends the turn: the chat is recorded failed, not done.
        assert agent_errors[0]["fatal"] is True
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel
        assert handler.active_relay_response is None

    def test_crash_with_zero_events_before_raise(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(RuntimeError("connection reset"))
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == relay.GAIA_PROFILE.stream_ended_text()
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel

    def test_generator_exhausted_without_terminal_event(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "status", "message": "hi"}
            # Generator ends -- no final/error was ever yielded.

        proxy = _ScriptedProxy(_source)
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == relay.GAIA_PROFILE.stream_ended_text()
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel

    def test_crash_while_a_question_is_open_clears_it(self):
        """Otherwise the chat keeps showing "waiting on you" after the run died."""
        handler = _FakeHandler()

        def _source():
            yield {"type": "needs_input", "request_id": "req-9", "question": "Which?"}
            raise RuntimeError("socket closed")

        relay.relay_query(handler, _ScriptedProxy(_source), query="q", context=[])
        assert handler.relay_question is None

    def test_exception_never_propagates_out_of_relay_query(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "status", "message": "hi"}
            raise RuntimeError("boom")

        proxy = _ScriptedProxy(_source)
        # The call below must complete without raising -- the assertion IS
        # that no exception escapes.
        relay.relay_query(handler, proxy, query="q", context=[])


# --- Sidecar HTTP-error detail surfacing (#2419) ----------------------------


class TestSidecarHTTPErrorDetail:
    """A non-404 ``SidecarHTTPError`` carries the sidecar's own actionable
    ``detail`` (e.g. the zero-connector 502). The relay must surface that
    detail instead of masking it as the generic crash message."""

    def test_non_404_http_error_with_detail_surfaces_detail(self):
        handler = _FakeHandler()
        detail = (
            "Failed to start the agent for this query: No model downloaded "
            "— download one in Settings → Models "
            "before chatting."
        )
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(
                SidecarHTTPError(502, detail, path="/v1/gaia/query")
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == detail

    def test_connection_shaped_http_error_detail_gains_hint(self, monkeypatch):
        monkeypatch.setattr(
            "gaia.llm.lemonade_launcher.gaia_runs_lemonade", lambda *a: True
        )
        handler = _FakeHandler()
        detail = "502 local LLM triage failed: connection refused"
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(
                SidecarHTTPError(502, detail, path="/v1/gaia/query")
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == (
            f"{detail}{relay.GAIA_PROFILE.lemonade_hint} {APP_SERVER_DOWN_HINT}"
        )
        assert "gaia " not in agent_errors[0]["content"]

    def test_a_detail_that_names_the_setup_repair_gains_no_hint(self):
        from gaia.llm.surface_hints import AGENT_UI_SETUP_HINT

        handler = _FakeHandler()
        detail = (
            "GAIA's AI engine couldn't start: lemond timed out. Sending the "
            f"message again won't help until it is repaired. {AGENT_UI_SETUP_HINT}"
        )
        self._assert_left_alone(detail)

    def test_a_detail_that_already_says_to_resend_gains_no_hint(self):
        self._assert_left_alone(
            "The local model server at localhost:13305 isn't answering (connection "
            "refused). Start the model server, then send the message again."
        )

    def test_a_server_the_user_runs_gets_its_start_instruction(self, monkeypatch):
        from gaia.llm import lemonade_launcher

        start = "Start the Lemonade app from Applications, then retry."
        monkeypatch.setattr(lemonade_launcher, "gaia_runs_lemonade", lambda *a: False)
        monkeypatch.setattr(
            lemonade_launcher,
            "describe_start_hint",
            lambda ctx_size=None: lemonade_launcher.StartHint(instruction=start),
        )
        detail = "local LLM triage failed: connection refused"
        content = relay._augment_error_detail(detail)
        assert content == f"{detail}{relay.GAIA_PROFILE.lemonade_hint} {start}"

    def _assert_left_alone(self, detail):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(
                SidecarHTTPError(503, detail, path="/v1/gaia/query")
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert agent_errors[0]["content"] == detail

    @pytest.mark.parametrize("path", ["/v1/gaia/query", "/v1/gaia/query/"])
    def test_404_http_error_still_shows_version_upgrade_message(self, path):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(SidecarHTTPError(404, "Not Found", path=path))
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == relay.GAIA_PROFILE.version_upgrade_message

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/gaia/query/r1/tool_decision",
            "/v1/gaia/query/r1/respond",
            "/v1/gaia/sessions/s1/internet",
            # Another agent's /query says nothing about this one's version.
            "/v1/email/query",
            "",
        ],
    )
    def test_a_404_from_another_route_is_not_called_an_old_agent(self, path):
        """Only a missing /query proves an old binary; a finished run 404s too."""
        handler = _FakeHandler()
        detail = "No run 'r1' is in flight. It may have already finished."
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(SidecarHTTPError(404, detail, path=path))
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        [agent_error] = [e for e in handler.events if e["type"] == "agent_error"]
        assert agent_error["content"] != relay.GAIA_PROFILE.version_upgrade_message
        assert "out of date" not in agent_error["content"]
        assert detail in agent_error["content"]

    def test_detail_less_http_error_falls_back_to_stream_ended(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(SidecarHTTPError(503, "  ", path="/v1/gaia/query"))
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == relay.GAIA_PROFILE.stream_ended_text()


# --- Model-retake failure after an answered prompt (#6019) -------------------


class TestModelRetakeFailure:
    def test_retake_failure_preserves_named_terminal_error(self):
        handler = _FakeHandler()
        handler.step_away_result = True
        handler.retake_result = False
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "needs_confirmation",
                    "confirm_id": "confirm-retake",
                    "action": "send_message",
                    "summary": "Send it?",
                },
                {"type": "token", "delta": "discarded"},
                {"type": "final", "answer": "must not be discarded"},
            )
        )

        relay.relay_query(
            handler,
            proxy,
            query="q",
            context=[],
            run_id="rid-retake",
            answers_confirmations=True,
        )

        errors = [e for e in handler.events if e.get("type") == "agent_error"]
        assert errors == [
            {
                "type": "agent_error",
                "content": MODEL_RETAKE_FAILED_MESSAGE,
                "fatal": True,
            }
        ]
        assert not [
            e
            for e in handler.events
            if e.get("type") == "status" and e.get("message") == "Cancelled."
        ]
        assert not [e for e in handler.events if e.get("type") == "answer"]
        assert proxy.cancel_calls == ["rid-retake"]

    def test_user_stop_during_retake_stays_a_quiet_cancel(self):
        handler = _FakeHandler()
        handler.step_away_result = True
        handler.retake_result = None
        handler.cancelled.set()
        proxy = _ScriptedProxy(_events({"type": "token", "delta": "discarded"}))

        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-stop")

        assert not [e for e in handler.events if e.get("type") == "agent_error"]
        assert any(
            e.get("type") == "status" and e.get("message") == "Cancelled."
            for e in handler.events
        )
        assert proxy.cancel_calls == ["rid-stop"]

    def test_late_user_stop_after_model_regain_emits_cancelled(self):
        class _LateStopHandler(_FakeHandler):
            def come_back_to_model(self):
                self.cancelled.set()
                return None

        handler = _LateStopHandler()
        handler.step_away_result = True
        proxy = _ScriptedProxy(
            _events(
                {
                    "type": "needs_confirmation",
                    "confirm_id": "confirm-late-stop",
                    "action": "send_message",
                    "summary": "Send it?",
                },
                {"type": "token", "delta": "discarded"},
            )
        )

        relay.relay_query(
            handler,
            proxy,
            query="q",
            context=[],
            run_id="rid-late-stop",
            answers_confirmations=True,
        )

        assert not [e for e in handler.events if e.get("type") == "agent_error"]
        assert [
            e
            for e in handler.events
            if e.get("type") == "status" and e.get("message") == "Cancelled."
        ] == [{"type": "status", "message": "Cancelled."}]
        assert proxy.cancel_calls == ["rid-late-stop"]


# --- Orphaned-generation cleanup (#2158) -------------------------------------


class TestOrphanCancelOnCrash:
    """A run that ends without a terminal sidecar event (read-timeout, dropped
    connection, exhausted stream) leaves the sidecar's ``/v1/gaia/query``
    generation decoding on the single GPU slot unless the relay cancels it —
    #2158's multi-query death-spiral."""

    def test_stream_raise_without_terminal_event_calls_cancel_query(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "status", "message": "hi"}
            raise RuntimeError("read timed out")

        proxy = _ScriptedProxy(_source)
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-t1")
        assert proxy.cancel_calls == ["rid-t1"]

    def test_stream_raise_with_zero_events_calls_cancel_query(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            lambda: _RaisingIterator(RuntimeError("connection reset"))
        )
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-t2")
        assert proxy.cancel_calls == ["rid-t2"]

    def test_stream_exhausted_without_terminal_event_calls_cancel_query(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "status", "message": "hi"}
            # Generator ends -- no final/error was ever yielded.

        proxy = _ScriptedProxy(_source)
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-t3")
        assert proxy.cancel_calls == ["rid-t3"]

    def test_terminal_final_does_not_call_cancel_query(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(_events({"type": "final", "answer": "done"}))
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-t4")
        assert proxy.cancel_calls == []

    def test_terminal_error_does_not_call_cancel_query(self):
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events({"type": "error", "detail": "Lemonade down", "status": 500})
        )
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-t5")
        assert proxy.cancel_calls == []

    def test_user_cancel_still_calls_cancel_query_exactly_once(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "tool_call", "tool": "pre_scan_inbox", "args": {}}
            handler.cancelled.set()

        proxy = _ScriptedProxy(_source)
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-t6")
        assert proxy.cancel_calls == ["rid-t6"]

    def test_crash_path_swallows_cancel_query_error_and_still_emits(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "status", "message": "hi"}
            raise RuntimeError("read timed out")

        proxy = _ScriptedProxy(_source, cancel_raises=SidecarError("cancel failed"))
        # Must not raise even though proxy.cancel_query raises SidecarError.
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-t7")
        assert proxy.cancel_calls == ["rid-t7"]
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == relay.GAIA_PROFILE.stream_ended_text()


# --- Cancellation mid-stream -------------------------------------------------


class TestCancellationMidStream:
    def test_cancelled_mid_stream_calls_cancel_query_not_stream_ended(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "tool_call", "tool": "pre_scan_inbox", "args": {}}
            handler.cancelled.set()
            # Stream ends abruptly after cancellation -- no final/error.

        proxy = _ScriptedProxy(_source)
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-9")
        assert proxy.cancel_calls == ["rid-9"]
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert agent_errors == []

    def test_cancelled_mid_stream_swallows_cancel_query_error(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "tool_call", "tool": "pre_scan_inbox", "args": {}}
            handler.cancelled.set()

        proxy = _ScriptedProxy(_source, cancel_raises=SidecarError("cancel failed"))
        # Must not raise even though proxy.cancel_query raises SidecarError.
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel
        assert handler.active_relay_response is None


class _ClosableResp:
    """Response stand-in whose ``close()`` calls are observable."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class TestEarlyCancelRace:
    """A cancel that lands BEFORE the response is registered must not leave
    the relay parked in a blocking read for the full read_timeout: the
    registration hook is the last point that can observe the raced-ahead
    cancelled flag, so it closes the just-registered response immediately
    (the router's ``close_active_relay_response`` was a no-op at cancel time
    — ``active_relay_response`` was still None)."""

    def test_registration_closes_response_when_cancel_raced_ahead(self):
        handler = _FakeHandler()
        resp = _ClosableResp()

        def _source():
            # The cancel raced ahead: by the time the proxy fires on_response
            # (which _ScriptedProxy does before yielding), the flag is set.
            yield {"type": "status", "message": "never consumed"}
            yield {"type": "final", "answer": "done"}

        handler.cancelled.set()
        proxy = _ScriptedProxy(_source, on_response_obj=resp)
        relay.relay_query(handler, proxy, query="q", context=[], run_id="rid-race")

        assert resp.closed is True, (
            "on_response must close the response when handler.cancelled was "
            "already set at registration time — otherwise the relay blocks "
            "in the next socket read until read_timeout"
        )
        # The normal cancel tail still runs.
        assert proxy.cancel_calls == ["rid-race"]

    def test_registration_does_not_close_response_when_not_cancelled(self):
        handler = _FakeHandler()
        resp = _ClosableResp()
        proxy = _ScriptedProxy(
            _events({"type": "final", "answer": "ok"}), on_response_obj=resp
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert resp.closed is False
