# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""End-to-end relay-fidelity test for the email streaming producer (#2109) —
"the honest relay evidence".

Drives ``_stream_chat_impl`` for a REAL ``agent_type=email`` session against a
REAL in-memory ``ChatDatabase``, with ONLY the sidecar manager and
``relay_query`` monkeypatched. Everything else — the real
``SSEOutputHandler``, the real captured-steps loop, the real
``db.upsert_message`` / ``message_to_response`` round-trip — runs for real.

This is deliberately the heaviest test in the increment: it proves a
scripted UUID answer and a fixed render-map payload survive BYTE-IDENTICAL
through the full pipeline (SSE event -> captured_steps -> JSON in DB ->
pydantic round-trip), and — folded into the same run — that a render-less
tool_result never gains a ``render``/``data`` key at all (the Part 5
retention-cap guard, verified against the raw persisted dict rather than a
source-shape regex, since the same run makes that assertion directly
checkable).
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
import gaia.ui.email_sidecar.daemon_client as daemon_client_module
import gaia.ui.email_sidecar.relay as relay_module
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.utils import message_to_response


class _FakeProxy:
    def init(self):
        return 200, {}


class _FakeHandle:
    """Duck-typed stand-in for SidecarHandle: at a contract version that
    passes the /query relay's floor (2.4). Acquisition itself (starting the
    sidecar, if needed) is the daemon's job now — the handle is simply
    already resolved by the time ``_dispatch_email_query`` gets it."""

    api_version = "2.4"

    def proxy(self):
        return _FakeProxy()


async def _noop_retitle(**kwargs):
    """Suppress the fire-and-forget auto-title background task's real
    Lemonade HTTP call — irrelevant to relay fidelity and would otherwise
    leave a pending task pinned to a closed event loop at test teardown."""
    return None


def test_email_relay_fidelity_end_to_end(monkeypatch):
    tool_marker = str(uuid.uuid4())
    answer_text = str(uuid.uuid4())

    def _fake_relay_query(handler, proxy, *, query, context, model_id=None, **kw):
        # Mirrors the real canonical-vocabulary translation in relay.py's
        # _dispatch_one: a tool_call always precedes its tool_result, which
        # is what makes captured_steps non-empty by the time the
        # render-carrying tool_result arrives (see the guard
        # `elif event_type == "tool_result" and captured_steps:` in
        # _chat_helpers.py).
        handler._emit(
            {
                "type": "tool_start",
                "tool": "pre_scan_inbox",
                "detail": "Scanning inbox",
            }
        )
        handler._emit(
            {
                "type": "tool_result",
                "tool": "pre_scan_inbox",
                "render": "email_pre_scan",
                "summary": "scanned",
                "success": True,
                "data": {"kind": "email_pre_scan", "marker": tool_marker},
            }
        )
        # A second, render-less tool call — proves the retention cap: only
        # render-carrying tool_results gain a render/data key.
        handler._emit(
            {"type": "tool_start", "tool": "list_inbox", "detail": "Listing inbox"}
        )
        handler._emit(
            {
                "type": "tool_result",
                "tool": "list_inbox",
                "summary": "listed 3 messages",
                "success": True,
                "data": {"count": 3},
            }
        )
        handler._emit({"type": "answer", "content": answer_text})
        # NB: no signal_done() here — the real relay_query never signals; the
        # turn-level sentinel is owned by _run_agent's outer finally.

    monkeypatch.setattr(
        daemon_client_module, "acquire_handle", lambda agent_id="email": _FakeHandle()
    )
    monkeypatch.setattr(relay_module, "relay_query", _fake_relay_query)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )

    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="email")
    request = ChatRequest(
        session_id=session["id"],
        message="prescan please",
        stream=True,
        agent_type="email",
    )
    run = SimpleNamespace(handler=None)

    async def _drive():
        async for _ in _stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(_drive())

    messages = db.get_messages(session["id"])
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1, (
        f"Expected exactly one persisted assistant message, got "
        f"{len(assistant_messages)}: {assistant_messages}"
    )
    persisted = assistant_messages[0]

    # ── Fidelity claim 1: the answer text survives byte-identical ──────────
    assert persisted["content"] == answer_text

    # ── Fidelity claim 2: the render-map card survives the pydantic
    #    round-trip byte-identical (the full pipeline: SSE event ->
    #    captured_steps -> JSON in DB -> AgentStepResponse). ──────────────
    resp = message_to_response(persisted)
    assert resp.agent_steps is not None
    render_steps = [s for s in resp.agent_steps if s.render == "email_pre_scan"]
    assert len(render_steps) == 1, (
        f"Expected exactly one render=email_pre_scan step, got "
        f"{[s.render for s in resp.agent_steps]}"
    )
    assert render_steps[0].data == {"kind": "email_pre_scan", "marker": tool_marker}

    # ── Retention-cap claim: the render-less tool_result never gained a
    #    render/data key at all — checked against the RAW persisted dict
    #    (not the pydantic model, whose fields always exist with a None
    #    default) so a regression that sets them to None instead of
    #    omitting them entirely would still be caught. ────────────────────
    raw_steps = persisted["agent_steps"]
    list_inbox_steps = [s for s in raw_steps if s.get("tool") == "list_inbox"]
    assert len(list_inbox_steps) == 1
    list_inbox_step = list_inbox_steps[0]
    assert "render" not in list_inbox_step, (
        "A render-less tool_result must not gain a 'render' key at all — "
        f"got {list_inbox_step!r}"
    )
    assert "data" not in list_inbox_step, (
        "A render-less tool_result must not gain a 'data' key at all — "
        f"got {list_inbox_step!r}"
    )


class _ScriptedQueryProxy:
    """Fake sidecar proxy whose /query stream is a fixed canonical script —
    used with the REAL ``relay_query`` (nothing in the relay is patched)."""

    def __init__(self, events):
        self._events = list(events)

    def init(self):
        return 200, {}

    def query_stream(self, body, *, read_timeout=300.0, on_response=None):
        if on_response is not None:
            on_response(_ClosableResp())
        return iter(self._events)

    def cancel_query(self, run_id):  # pragma: no cover - not hit in this test
        return None


class _ClosableResp:
    def close(self):
        pass


class _ScriptedHandle(_FakeHandle):
    def __init__(self, events):
        self._scripted_proxy = _ScriptedQueryProxy(events)

    def proxy(self):
        return self._scripted_proxy


def test_card_echo_only_answer_never_persists_raw_echoed_json(monkeypatch):
    """#2109 inherited-defect fix: a ``final`` whose echo-stripped answer is
    legitimately empty (the model's whole answer was the render-card JSON
    echo) must OVERRIDE the chunk-accumulated text — the old
    ``if answer_content:`` guard kept the raw echoed JSON as the persisted
    assistant message. Empty is a valid answer."""
    marker = str(uuid.uuid4())
    echoed_card = '{"kind": "email_pre_scan", "marker": "' + marker + '", "total": 3}'
    events = [
        # The model streams its card echo as tokens (chunk noise)...
        {"type": "token", "delta": echoed_card[:20]},
        {"type": "token", "delta": echoed_card[20:]},
        # ...and the final answer is the same echo, which the relay's
        # echo-strip cleans down to the empty string.
        {"type": "final", "answer": echoed_card},
    ]
    monkeypatch.setattr(
        daemon_client_module,
        "acquire_handle",
        lambda agent_id="email": _ScriptedHandle(events),
    )
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )

    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="email")
    request = ChatRequest(
        session_id=session["id"],
        message="prescan please",
        stream=True,
        agent_type="email",
    )
    run = SimpleNamespace(handler=None)

    async def _drive():
        async for _ in _stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(_drive())

    messages = db.get_messages(session["id"])
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert marker not in assistant[0]["content"], (
        "the raw echoed render-card JSON leaked into the persisted assistant "
        f"message: {assistant[0]['content']!r}"
    )


def test_email_turn_pushes_exactly_one_done_sentinel(monkeypatch):
    """The turn-level exactly-once sentinel contract, driven through the REAL
    ``relay_query`` (#2109 inherited-defect fix: relay_query used to call
    ``signal_done()`` itself AND ``_run_agent``'s outer ``finally`` fired a
    second one — masked because the consumer breaks on the first ``None``,
    leaving a stray sentinel in the queue)."""
    events = [
        {"type": "token", "delta": "Hi"},
        {"type": "final", "answer": "Hi there"},
    ]
    monkeypatch.setattr(
        daemon_client_module,
        "acquire_handle",
        lambda agent_id="email": _ScriptedHandle(events),
    )
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )

    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="email")
    request = ChatRequest(
        session_id=session["id"],
        message="hello",
        stream=True,
        agent_type="email",
    )
    run = SimpleNamespace(handler=None)

    async def _drive():
        async for _ in _stream_chat_impl(run, db, session, request):
            pass

    asyncio.run(_drive())

    # The consumer loop already broke on the first None sentinel, and
    # _cleanup_stream joined the producer — so anything still in the queue is
    # a stray. A second signal_done would have left a second None here.
    import queue as _queue

    leftovers = []
    while True:
        try:
            leftovers.append(run.handler.event_queue.get_nowait())
        except _queue.Empty:
            break
    stray_sentinels = [e for e in leftovers if e is None]
    assert stray_sentinels == [], (
        f"expected exactly one done sentinel per turn (consumed by the "
        f"stream loop); found {len(stray_sentinels)} stray sentinel(s) left "
        f"in the queue — signal_done was called more than once"
    )

    # Sanity: the turn itself completed normally.
    messages = db.get_messages(session["id"])
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["content"] == "Hi there"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
