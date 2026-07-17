# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract tests for ``gaia.ui.email_sidecar.relay`` (#2109 query relay).

``relay.py`` does not exist yet — this file pins its designed public surface
(``relay.relay_query`` + the ``STREAM_ENDED_UNEXPECTEDLY`` constant) as a
red-phase TDD contract. Every test in this file is expected to fail at
collection with ``ModuleNotFoundError`` / ``ImportError`` until the module
lands.

Part 2 (unit): a hand-rolled fake proxy + fake handler drive ``relay_query``
with no real HTTP, pinning the event-shape table and the crash/cancel/timeout
control flow.

Part 3 (integration): a REAL uvicorn server hosting the REAL
``gaia_agent_email`` FastAPI app (via ``export_openapi.build_app()``) with the
``query_routes.build_query_agent`` seam swapped for a scripted fake agent —
the same pattern ``hub/agents/python/email/tests/test_query_route.py`` uses.
This proves the render-map card payload (``pretty_print_json`` ->
``SSEOutputHandler._render_card_payload`` -> the real
``CanonicalTranslator`` -> real HTTP -> ``EmailSidecarProxy.query_stream``)
survives end to end, with no test code hand-constructing a canonical event.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import uuid

import pytest

from gaia.ui.email_sidecar import relay  # noqa: E402 - expected to fail (red phase)
from gaia.ui.email_sidecar.errors import SidecarError  # noqa: E402

# ---------------------------------------------------------------------------
# Part 2 — unit tests: hand-rolled fakes, no real HTTP
# ---------------------------------------------------------------------------


class _FakeHandler:
    """Minimal stand-in for ``gaia.ui.sse_handler.SSEOutputHandler``.

    Mirrors the real handler's attribute names relay_query depends on:
    ``_emit``, ``cancelled`` (a ``threading.Event``), ``active_relay_response``,
    and ``signal_done()``.
    """

    def __init__(self) -> None:
        self.events: list = []
        self.cancelled = threading.Event()
        self.active_relay_response = None
        self.done_calls = 0

    def _emit(self, event):
        self.events.append(event)

    def signal_done(self):
        self.done_calls += 1


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
    """Fake ``EmailSidecarProxy`` driving ``relay_query`` from a scripted
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
        }

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
                    "tool": "archive_message",
                    "args": {"message_id": "m1"},
                },
                {"type": "final", "answer": "done"},
            )
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        types = [e["type"] for e in handler.events]
        assert types == ["tool_start", "tool_args", "status", "answer"]
        status_event = handler.events[2]
        assert "archive_message" in status_event["message"]
        assert "mailbox" in status_event["message"]

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
        assert handler.events == [{"type": "agent_error", "content": "Lemonade down"}]
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel

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
        assert relay.LEMONADE_CONNECTION_HINT in content

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
        assert relay.LEMONADE_CONNECTION_HINT in content

    def test_non_connection_error_detail_gets_no_hint(self):
        # A logic/validation error must pass through untouched — the hint is
        # for connection/timeout-shaped failures only.
        raw_detail = "tool 'summarize_thread' failed: KeyError: 'thread_id'"
        handler = _FakeHandler()
        proxy = _ScriptedProxy(
            _events({"type": "error", "detail": raw_detail, "status": 500})
        )
        relay.relay_query(handler, proxy, query="q", context=[])
        assert handler.events == [{"type": "agent_error", "content": raw_detail}]

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
        inner = {"kind": "email_pre_scan", "urgent": [], "actionable": []}
        answer = "Here you go.\n\n" + json.dumps(inner)
        proxy = _ScriptedProxy(_events({"type": "final", "answer": answer}))
        relay.relay_query(handler, proxy, query="q", context=[])
        content = handler.events[-1]["content"]
        assert "email_pre_scan" not in content
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
        assert agent_errors[0]["content"] == relay.STREAM_ENDED_UNEXPECTEDLY
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
        assert agent_errors[0]["content"] == relay.STREAM_ENDED_UNEXPECTEDLY
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
        assert agent_errors[0]["content"] == relay.STREAM_ENDED_UNEXPECTEDLY
        assert handler.done_calls == 0  # the caller owns the turn-level sentinel

    def test_exception_never_propagates_out_of_relay_query(self):
        handler = _FakeHandler()

        def _source():
            yield {"type": "status", "message": "hi"}
            raise RuntimeError("boom")

        proxy = _ScriptedProxy(_source)
        # The call below must complete without raising -- the assertion IS
        # that no exception escapes.
        relay.relay_query(handler, proxy, query="q", context=[])


# --- Orphaned-generation cleanup (#2158) -------------------------------------


class TestOrphanCancelOnCrash:
    """A run that ends without a terminal sidecar event (read-timeout, dropped
    connection, exhausted stream) leaves the sidecar's ``/v1/email/query``
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
        assert agent_errors[0]["content"] == relay.STREAM_ENDED_UNEXPECTEDLY


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


# ---------------------------------------------------------------------------
# Part 3 — integration: REAL uvicorn + REAL SSEOutputHandler + REAL
# CanonicalTranslator, with a scripted fake agent injected via the
# ``query_routes.build_query_agent`` seam.
# ---------------------------------------------------------------------------

_HAS_UVICORN = importlib.util.find_spec("uvicorn") is not None
_HAS_EMAIL_AGENT = importlib.util.find_spec("gaia_agent_email") is not None
_HAS_REQUESTS = importlib.util.find_spec("requests") is not None

pytestmark_integration = pytest.mark.skipif(
    not (_HAS_UVICORN and _HAS_EMAIL_AGENT and _HAS_REQUESTS),
    reason=(
        "uvicorn, requests, and gaia-agent-email must all be importable for "
        "the real-server /query integration tests"
    ),
)


def _free_port() -> int:
    from gaia.daemon.sidecars.manager import find_free_port

    return find_free_port()


@pytest.fixture(scope="module")
def live_email_app():
    """Boot the REAL ``gaia_agent_email`` FastAPI app on a real TCP port.

    Mirrors ``hub/agents/python/email/tests/test_query_route.py``'s
    ``export_openapi.build_app()`` pattern, but run for real over HTTP via
    uvicorn in a background thread rather than FastAPI's in-process
    ``TestClient`` -- this is what proves the wire (SSE framing, socket
    timing) rather than just the ASGI call path.
    """
    if not (_HAS_UVICORN and _HAS_EMAIL_AGENT and _HAS_REQUESTS):
        pytest.skip("uvicorn / requests / gaia_agent_email not importable")

    import requests
    import uvicorn
    from gaia_agent_email import export_openapi

    app = export_openapi.build_app()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    reachable = False
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{base_url}/v1/email/health", timeout=0.5)
            if resp.status_code == 200:
                reachable = True
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.05)
    if not reachable:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("email sidecar test server never became reachable")

    yield base_url

    server.should_exit = True
    thread.join(timeout=10)


# --- Fake agent scripts (injected via query_routes.build_query_agent) ------


class _PreScanFakeAgent:
    """One tool call: ``pre_scan_inbox`` returning a well-formed card envelope."""

    def __init__(self):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 20, "fake-model")
        self.console.print_tool_usage("pre_scan_inbox")
        self.console.pretty_print_json(
            {
                "ok": True,
                "data": {
                    "kind": "email_pre_scan",
                    "urgent": [],
                    "actionable": [{"message_id": "m1", "subject": "Q3 review"}],
                    "informational_count": 0,
                    "suggested_archives": [],
                    "suggested_drafts": [],
                    "preferences_applied": {},
                    "totals": {},
                },
            }
        )
        self.console.print_final_answer("Here's your inbox pre-scan.", streaming=False)
        return {"answer": "Here's your inbox pre-scan."}


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
        self.console.print_final_answer(
            "Sent." if approved else "Not sent.", streaming=False
        )
        return {"answer": "Sent." if approved else "Not sent."}


class _StallFakeAgent:
    """Emits one status then stalls well past a short client read_timeout."""

    def __init__(self, stall_seconds: float = 2.0):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None
        self._stall_seconds = stall_seconds

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 5, "fake-model")
        time.sleep(self._stall_seconds)
        self.console.print_final_answer("done eventually", streaming=False)
        return {"answer": "done eventually"}


class _TimingFakeAgent:
    """Two status events with a real sleep between them, to prove the SSE
    wire is not buffered/batched."""

    def __init__(self, gap_seconds: float = 0.4):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None
        self._gap_seconds = gap_seconds

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 5, "fake-model")
        self.console.print_info("first")
        time.sleep(self._gap_seconds)
        self.console.print_info("second")
        self.console.print_final_answer("done", streaming=False)
        return {"answer": "done"}


class _CancelParkFakeAgent:
    """One tool step, then parks with a periodic heartbeat so a client-side
    cancellation (relayed to the server via a real ``POST .../cancel``) is
    noticed promptly instead of waiting out one long silent block."""

    def __init__(self, park_seconds: float = 8.0):
        self.conversation_history = []
        self.console = None
        self._cancel_event = None
        self._park_seconds = park_seconds

    def process_query(self, query, max_steps=None):
        self.console.print_processing_start(query, 5, "fake-model")
        self.console.print_tool_usage("tool_1")
        self.console.pretty_print_json({}, title="Arguments")
        self.console.pretty_print_json({"ok": True})
        self.console.print_tool_complete()

        deadline = time.monotonic() + self._park_seconds
        while time.monotonic() < deadline:
            if self._cancel_event is not None and self._cancel_event.is_set():
                break
            self.console.print_info("still working...")
            time.sleep(0.1)
        self.console.print_final_answer("Completed.", streaming=False)
        return {"answer": "Completed."}


# --- (a) Pre-scan happy path -- THE key render-map proof --------------------


class TestPreScanCardSurvivesRealPipeline:
    pytestmark = pytestmark_integration

    def test_raw_query_stream_carries_the_render_card(
        self, live_email_app, monkeypatch
    ):
        from gaia_agent_email import query_routes

        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        fake = _PreScanFakeAgent()
        monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
        proxy = EmailSidecarProxy(live_email_app)
        events = list(
            proxy.query_stream(
                {"query": "prescan please", "run_id": str(uuid.uuid4()), "context": []}
            )
        )
        tool_results = [e for e in events if e.get("type") == "tool_result"]
        assert len(tool_results) == 1
        tr = tool_results[0]
        assert tr["tool"] == "pre_scan_inbox"
        assert tr["render"] == "email_pre_scan"
        assert tr["data"]["kind"] == "email_pre_scan"
        assert tr["data"]["actionable"][0]["message_id"] == "m1"

    def test_relay_query_carries_the_render_card(self, live_email_app, monkeypatch):
        from gaia_agent_email import query_routes

        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        fake = _PreScanFakeAgent()
        monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
        proxy = EmailSidecarProxy(live_email_app)
        handler = _FakeHandler()
        relay.relay_query(handler, proxy, query="prescan please", context=[])
        tool_results = [e for e in handler.events if e.get("type") == "tool_result"]
        assert len(tool_results) == 1
        tr = tool_results[0]
        assert tr["render"] == "email_pre_scan"
        assert tr["data"]["kind"] == "email_pre_scan"


# --- (b) Send-class confirmation --------------------------------------------


class TestConfirmationSequenceRealPipeline:
    pytestmark = pytestmark_integration

    def test_raw_query_stream_needs_confirmation_then_final(
        self, live_email_app, monkeypatch
    ):
        from gaia_agent_email import query_routes

        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        fake = _ConfirmFakeAgent()
        monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
        proxy = EmailSidecarProxy(live_email_app)
        events = list(
            proxy.query_stream(
                {
                    "query": "send a reply to bob",
                    "run_id": str(uuid.uuid4()),
                    "context": [],
                }
            )
        )
        types = [e["type"] for e in events]
        assert "needs_confirmation" in types
        idx = types.index("needs_confirmation")
        assert types[idx + 1] == "final"
        assert events[-1]["answer"]

    def test_relay_query_needs_confirmation_then_answer_terminates_fast(
        self, live_email_app, monkeypatch
    ):
        from gaia_agent_email import query_routes

        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        fake = _ConfirmFakeAgent()
        monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
        proxy = EmailSidecarProxy(live_email_app)
        handler = _FakeHandler()
        start = time.monotonic()
        relay.relay_query(handler, proxy, query="send a reply to bob", context=[])
        elapsed = time.monotonic() - start
        types = [e["type"] for e in handler.events]
        assert "needs_confirmation" in types
        idx = types.index("needs_confirmation")
        assert types[idx + 1] == "answer"
        assert elapsed < 5.0


# --- (c) / (d) Read-timeout mid-stream (stall) -------------------------------


class TestReadTimeoutMidStream:
    pytestmark = pytestmark_integration

    def test_relay_read_timeout_emits_stream_ended_unexpectedly(
        self, live_email_app, monkeypatch
    ):
        from gaia_agent_email import query_routes

        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        fake = _StallFakeAgent(stall_seconds=2.0)
        monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
        proxy = EmailSidecarProxy(live_email_app)
        handler = _FakeHandler()
        start = time.monotonic()
        relay.relay_query(
            handler, proxy, query="stall please", context=[], read_timeout=0.3
        )
        elapsed = time.monotonic() - start
        agent_errors = [e for e in handler.events if e["type"] == "agent_error"]
        assert len(agent_errors) == 1
        assert agent_errors[0]["content"] == relay.STREAM_ENDED_UNEXPECTEDLY
        assert elapsed < 5.0


# --- (e) Cancel park pattern -------------------------------------------------


class TestCancelParkPattern:
    pytestmark = pytestmark_integration

    def test_relay_reacts_to_handler_cancelled_and_calls_cancel_query(
        self, live_email_app, monkeypatch
    ):
        from gaia_agent_email import query_routes

        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        fake = _CancelParkFakeAgent(park_seconds=8.0)
        monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)

        class _SpyProxy(EmailSidecarProxy):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.cancel_calls: list = []

            def cancel_query(self, run_id):
                self.cancel_calls.append(run_id)
                return super().cancel_query(run_id)

        proxy = _SpyProxy(live_email_app)
        handler = _FakeHandler()
        run_id = str(uuid.uuid4())
        result: dict = {}

        def _run():
            start = time.monotonic()
            relay.relay_query(
                handler,
                proxy,
                query="park please",
                context=[],
                run_id=run_id,
                read_timeout=15.0,
            )
            result["elapsed"] = time.monotonic() - start

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # Wait until the relay has emitted something (proves step 1 ran)
        # before triggering cancellation.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not handler.events:
            time.sleep(0.02)
        assert handler.events, "relay never emitted any event before timeout"

        handler.cancelled.set()
        thread.join(timeout=8.0)

        assert not thread.is_alive(), "relay_query did not return after cancellation"
        assert proxy.cancel_calls == [run_id]
        assert result["elapsed"] < 5.0


# --- Streaming-truth timing assertion (real socket, no hand-waving) --------


class TestStreamingIsNotBuffered:
    pytestmark = pytestmark_integration

    def test_inter_arrival_gap_reflects_real_server_side_sleep(
        self, live_email_app, monkeypatch
    ):
        from gaia_agent_email import query_routes

        from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

        fake = _TimingFakeAgent(gap_seconds=0.4)
        monkeypatch.setattr(query_routes, "build_query_agent", lambda **k: fake)
        proxy = EmailSidecarProxy(live_email_app)

        timestamps = []
        for event in proxy.query_stream(
            {"query": "time me please", "run_id": str(uuid.uuid4()), "context": []},
            read_timeout=10.0,
        ):
            if event.get("type") == "status" and event.get("message") in (
                "first",
                "second",
            ):
                timestamps.append(time.monotonic())

        assert len(timestamps) == 2
        gap = timestamps[1] - timestamps[0]
        assert gap >= 0.8 * 0.4
