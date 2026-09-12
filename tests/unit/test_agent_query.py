# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the CLI thin-client driver (``gaia.daemon.agent_query``) and the
``client.ensure_agent`` helper (V2-8, #2152).

These are Lemonade/daemon-free: the SSE parser and console renderer are pure, and
``ensure_agent`` is exercised with a stubbed ``requests`` + ``start_or_attach`` so
the token-discard custody invariant is asserted without a live daemon.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from gaia.daemon.agent_query import ConsoleRenderer, _iter_sse_events


class _FakeResponse:
    """Minimal stand-in for a streamed ``requests`` response."""

    def __init__(self, text: str):
        self._text = text

    def iter_lines(self, decode_unicode=True):
        # Mirror requests.iter_lines: split on newlines, no trailing empty.
        for line in self._text.split("\n"):
            yield line


def test_iter_sse_events_parses_canonical_frames():
    stream = (
        'data: {"type": "status", "message": "Processing..."}\n'
        "\n"
        'data: {"type": "token", "delta": "Hello "}\n'
        "\n"
        ": heartbeat\n"  # SSE comment line — ignored
        "\n"
        'data: {"type": "final", "answer": "Hello world"}\n'
        "\n"
    )
    events = list(_iter_sse_events(_FakeResponse(stream)))
    assert [e["type"] for e in events] == ["status", "token", "final"]
    assert events[-1]["answer"] == "Hello world"


def test_iter_sse_events_skips_unparseable_payload(caplog):
    stream = "data: not-json\n\n" 'data: {"type": "final", "answer": "ok"}\n\n'
    events = list(_iter_sse_events(_FakeResponse(stream)))
    # The bad frame is dropped (logged), the good one survives.
    assert [e["type"] for e in events] == ["final"]


def test_iter_sse_events_flushes_trailing_frame_without_blank_line():
    stream = 'data: {"type": "final", "answer": "eof"}'  # no terminating blank line
    events = list(_iter_sse_events(_FakeResponse(stream)))
    assert events == [{"type": "final", "answer": "eof"}]


def test_renderer_answer_on_stdout_progress_on_stderr():
    out, err = io.StringIO(), io.StringIO()
    r = ConsoleRenderer(out=out, err=err)
    r.render({"type": "status", "message": "working"})
    r.render({"type": "tool_call", "tool": "triage_inbox", "args": {}})
    r.render({"type": "tool_result", "tool": "triage_inbox", "data": {}})
    r.render({"type": "final", "answer": "Done."})

    # The answer is the ONLY thing on stdout (pipe-friendly parity).
    assert out.getvalue().strip() == "Done."
    # Progress lands on stderr.
    assert "working" in err.getvalue()
    assert "triage_inbox" in err.getvalue()


def test_renderer_streamed_tokens_are_not_reprinted_by_final():
    out, err = io.StringIO(), io.StringIO()
    r = ConsoleRenderer(out=out, err=err)
    r.render({"type": "token", "delta": "Hello "})
    r.render({"type": "token", "delta": "world"})
    r.render({"type": "final", "answer": "Hello world"})
    # Tokens printed live; the final only adds a trailing newline, not a reprint.
    assert out.getvalue() == "Hello world\n"


def test_renderer_error_goes_to_stderr_and_is_captured():
    out, err = io.StringIO(), io.StringIO()
    r = ConsoleRenderer(out=out, err=err)
    r.render({"type": "error", "detail": "boom", "source": "daemon_relay"})
    assert r.error_detail == "boom"
    assert "boom" in err.getvalue()
    assert "daemon_relay" in err.getvalue()
    assert out.getvalue() == ""


def test_renderer_unknown_event_is_visible():
    out, err = io.StringIO(), io.StringIO()
    r = ConsoleRenderer(out=out, err=err)
    r.render({"type": "mystery", "foo": 1})
    assert "unknown event" in err.getvalue()


def test_ensure_agent_discards_sidecar_token(monkeypatch):
    """ensure_agent returns the daemon instance and NEVER surfaces the sidecar
    bearer the ensure response carries."""
    from gaia.daemon import client
    from gaia.daemon.instance import DaemonInstance

    inst = DaemonInstance(
        pid=1234, port=5555, token="DAEMON-TOKEN", host="127.0.0.1", api_version="1.1"
    )
    monkeypatch.setattr(client, "start_or_attach", lambda *a, **k: inst)

    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"agent_id": "email", "token": "SIDECAR-SECRET", "pid": 99}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _Resp()

    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    got = client.ensure_agent("email")

    # Returns the daemon instance (base_url + daemon token), not the sidecar body.
    assert got is inst
    # Ensure was authed with the daemon token.
    assert captured["headers"]["Authorization"] == "Bearer DAEMON-TOKEN"
    assert captured["url"].endswith("/daemon/v1/agents/email/ensure")


def test_ensure_agent_raises_loud_on_non_200(monkeypatch):
    from gaia.daemon import client
    from gaia.daemon.errors import DaemonError
    from gaia.daemon.instance import DaemonInstance

    inst = DaemonInstance(pid=1, port=2, token="T", host="127.0.0.1", api_version="1.1")
    monkeypatch.setattr(client, "start_or_attach", lambda *a, **k: inst)

    class _Resp:
        status_code = 502

        def json(self):
            return {"detail": "sidecar failed to start"}

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())

    with pytest.raises(DaemonError) as exc:
        client.ensure_agent("email")
    assert "sidecar failed to start" in str(exc.value)


def test_ensure_agent_rejects_prehistoric_daemon(monkeypatch):
    """A daemon predating the agents/relay control plane fails loud (v1.1 floor)."""
    from gaia.daemon import client
    from gaia.daemon.errors import DaemonVersionError
    from gaia.daemon.instance import DaemonInstance

    old = DaemonInstance(pid=1, port=2, token="T", host="127.0.0.1", api_version="1.0")
    monkeypatch.setattr(client, "start_or_attach", lambda *a, **k: old)

    with pytest.raises(DaemonVersionError):
        client.ensure_agent("email")


# ---------------------------------------------------------------------------
# run_query's payload — session_id parity with model/max_steps (#2829)
# ---------------------------------------------------------------------------


def _stub_ensure(monkeypatch, base_url="http://127.0.0.1:9999"):
    """Stub client.ensure_agent so run_query never touches a real daemon."""
    from gaia.daemon import agent_query
    from gaia.daemon.instance import DaemonInstance

    inst = DaemonInstance(
        pid=1, port=9999, token="T", host="127.0.0.1", api_version="1.1"
    )
    monkeypatch.setattr(agent_query.client, "ensure_agent", lambda agent_id: inst)
    return inst


def _stub_stream_post(monkeypatch, captured, frames=None):
    """Stub requests.post to capture the JSON payload and return the given SSE
    frames (one 'final' by default), without opening a real connection."""
    import requests

    payloads = frames or ['{"type": "final", "answer": "ok"}']

    class _Resp:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_lines(self, decode_unicode=True):
            for frame in payloads:
                yield f"data: {frame}"
                yield ""

    def _fake_post(url, headers=None, json=None, stream=None, timeout=None):
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(requests, "post", _fake_post)


def test_run_query_omits_session_id_when_none(monkeypatch):
    from gaia.daemon.agent_query import run_query

    _stub_ensure(monkeypatch)
    captured: dict = {}
    _stub_stream_post(monkeypatch, captured)

    run_query("email", "hi")

    assert "session_id" not in captured["json"]


def test_run_query_sends_session_id_when_given(monkeypatch):
    from gaia.daemon.agent_query import run_query

    _stub_ensure(monkeypatch)
    captured: dict = {}
    _stub_stream_post(monkeypatch, captured)

    run_query("email", "hi", session_id="conv-123")

    assert captured["json"]["session_id"] == "conv-123"


def test_run_query_without_trace_writes_no_file(monkeypatch, tmp_path):
    """No --trace -> nothing on disk, and no trace_path on the outcome."""
    from gaia.daemon.agent_query import run_query

    monkeypatch.chdir(tmp_path)
    _stub_ensure(monkeypatch)
    _stub_stream_post(monkeypatch, {})

    outcome = run_query("email", "hi")

    assert outcome.trace_path is None
    assert list(tmp_path.glob("*.json")) == []


def test_run_query_trace_writes_full_event_stream(monkeypatch, tmp_path):
    """--trace lands a JSON file in the CWD holding the request and every
    canonical event the sidecar emitted (issue #3345)."""
    import json

    from gaia.daemon.agent_query import run_query

    monkeypatch.chdir(tmp_path)
    _stub_ensure(monkeypatch)
    _stub_stream_post(
        monkeypatch,
        {},
        frames=[
            '{"type": "status", "message": "Processing..."}',
            '{"type": "tool_call", "tool": "triage_inbox", "args": {"limit": 5}}',
            '{"type": "tool_result", "tool": "triage_inbox", "data": {"count": 5}}',
            '{"type": "final", "answer": "Done."}',
        ],
    )

    outcome = run_query("email", "triage my inbox", trace=True)

    written = list(tmp_path.glob("email_trace_*.json"))
    assert len(written) == 1
    assert outcome.trace_path == str(written[0])

    doc = json.loads(written[0].read_text(encoding="utf-8"))
    assert doc["agent_id"] == "email"
    assert doc["request"]["query"] == "triage my inbox"
    assert doc["terminal_type"] == "final"
    assert doc["final_answer"] == "Done."
    assert [e["type"] for e in doc["events"]] == [
        "status",
        "tool_call",
        "tool_result",
        "final",
    ]


def test_run_query_trace_captures_stream_with_no_terminal_event(monkeypatch, tmp_path):
    """A sidecar that dies mid-run still leaves the events it did emit on disk —
    the trace is most useful exactly when the run failed."""
    import json

    from gaia.daemon.agent_query import run_query

    monkeypatch.chdir(tmp_path)
    _stub_ensure(monkeypatch)
    _stub_stream_post(
        monkeypatch,
        {},
        frames=['{"type": "tool_call", "tool": "list_emails"}'],
    )

    outcome = run_query("email", "hi", trace=True)

    assert outcome.exit_code == 1
    doc = json.loads(Path(outcome.trace_path).read_text(encoding="utf-8"))
    assert doc["terminal_type"] is None
    # The CLI-synthesized terminal error is recorded alongside the real events.
    assert [e["type"] for e in doc["events"]] == ["tool_call", "error"]
