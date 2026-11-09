# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SidecarProxy: the HTTP transport onto one sidecar's ``/v1/<agent_id>/`` routes."""

import importlib.util

import pytest

pytestmark = pytest.mark.allow_network

from gaia.ui.sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.sidecar.proxy import SidecarProxy


class _Resp:
    def __init__(self, payload, status=200, *, raw_text=None, json_raises=False):
        self._payload, self.status_code = payload, status
        self.text = raw_text if raw_text is not None else str(payload)
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, payload, *, resp=None):
        self._payload = payload
        self._resp = resp  # pre-built _Resp to return (overrides payload)
        self.posts = []
        self.gets = []

    def _make(self):
        return self._resp if self._resp is not None else _Resp(self._payload)

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return self._make()

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, params))
        return self._make()


class _HeaderSession(_Session):
    """A fake session that also carries a mutable ``headers`` dict (like
    requests.Session) so the caller-auth header wiring can be asserted."""

    def __init__(self, payload=None, **kwargs):
        super().__init__(payload if payload is not None else {}, **kwargs)
        self.headers: dict = {}


def test_proxy_sets_bearer_auth_header_when_token_given():
    # #1706: the proxy replays the per-session token as a bearer header on every
    # request so the token-gated sidecar accepts UI-originated calls.
    sess = _HeaderSession()
    proxy = SidecarProxy(
        "http://127.0.0.1:9100", agent_id="gaia", session=sess, auth_token="tok-123"
    )
    assert proxy._auth_token == "tok-123"
    assert sess.headers.get("Authorization") == "Bearer tok-123"


def test_proxy_omits_auth_header_without_token():
    sess = _HeaderSession()
    SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    assert "Authorization" not in sess.headers


def test_default_timeout_from_env(monkeypatch):
    # Fix F: no explicit timeout → reads GAIA_GAIA_SIDECAR_TIMEOUT (default 300).
    monkeypatch.delenv("GAIA_GAIA_SIDECAR_TIMEOUT", raising=False)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=_Session({}))
    assert proxy.timeout == 300.0

    monkeypatch.setenv("GAIA_GAIA_SIDECAR_TIMEOUT", "120")
    proxy2 = SidecarProxy(
        "http://127.0.0.1:9100", agent_id="gaia", session=_Session({})
    )
    assert proxy2.timeout == 120.0


def test_explicit_timeout_overrides_env(monkeypatch):
    monkeypatch.setenv("GAIA_GAIA_SIDECAR_TIMEOUT", "999")
    proxy = SidecarProxy(
        "http://127.0.0.1:9100", agent_id="gaia", session=_Session({}), timeout=60.0
    )
    assert proxy.timeout == 60.0


def test_base_url_trailing_slash_normalized():
    sess = _Session({"ok": True})
    proxy = SidecarProxy("http://127.0.0.1:9100/", agent_id="gaia", session=sess)
    proxy.init()
    assert sess.gets[0][0] == "http://127.0.0.1:9100/v1/gaia/init"


def test_health_and_version_get_routes():
    sess = _Session({"status": "ok"})
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    assert proxy.health()["status"] == "ok"
    proxy.version()
    assert sess.gets == [
        ("http://127.0.0.1:9100/health", None),
        ("http://127.0.0.1:9100/version", None),
    ]


def test_init_get_route_returns_status_and_body_on_200():
    # #1888: init returns (status, body) — never raises on the contract statuses.
    body = {"ready": True, "lemonade": {"base_url": "http://127.0.0.1:8555/api/v1"}}
    sess = _Session(body)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    assert proxy.init() == (200, body)
    assert sess.gets == [("http://127.0.0.1:9100/v1/gaia/init", None)]


def test_init_503_not_ready_passes_body_through_without_raising():
    # 503 from /init is contract (full InitResponse + hint), not a transport
    # failure — it must NOT be flattened into a SidecarHTTPError.
    body = {"ready": False, "hint": "Lemonade Server not reachable"}
    sess = _Session(None, resp=_Resp(body, status=503))
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    assert proxy.init() == (503, body)


def test_init_unexpected_status_still_raises_loudly():
    # Anything outside the 200/503 contract (401 bad token, 404 old sidecar)
    # keeps the loud SidecarHTTPError boundary.
    err = _Resp({"detail": "Missing bearer token"}, status=401)
    sess = _Session(None, resp=err)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        proxy.init()
    assert ei.value.status_code == 401
    assert "bearer token" in ei.value.detail.lower()


class _StreamSession:
    def __init__(self, resp):
        self._resp = resp
        self.posts = []  # (url, kwargs)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self._resp


def test_non_2xx_with_json_detail_raises_actionable_error():
    # The sidecar's actionable detail (e.g. Lemonade down) must survive, not be
    # flattened into a generic HTTPError.
    err = _Resp({"detail": "local LLM failed: Lemonade not reachable"}, status=502)
    sess = _Session(None, resp=err)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        proxy.skills()
    assert ei.value.status_code == 502
    assert "Lemonade not reachable" in ei.value.detail
    assert "/v1/gaia/skills" in str(ei.value)


def test_non_2xx_non_json_body_uses_text():
    err = _Resp(None, status=500, raw_text="Internal Server Error", json_raises=True)
    sess = _Session(None, resp=err)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        proxy.skills()
    assert ei.value.status_code == 500
    assert "Internal Server Error" in ei.value.detail


def test_respond_query_posts_request_id_and_value_to_run_scoped_route():
    # (#2595) delivering a needs_input answer must hit the run-scoped
    # /respond route with exactly {request_id, value} — not a generic payload
    # passthrough like the schema-2.1 routes above.
    envelope = {
        "run_id": "rid-1",
        "request_id": "req-1",
        "accepted": True,
        "status": "ok",
    }
    sess = _Session(envelope)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    out = proxy.respond_query("rid-1", "req-1", "gmail")
    assert out == envelope
    assert sess.posts[0] == (
        "http://127.0.0.1:9100/v1/gaia/query/rid-1/respond",
        {"request_id": "req-1", "value": "gmail"},
    )


def test_respond_query_raises_loudly_on_non_2xx():
    # A 409 (question no longer pending) must surface as SidecarHTTPError,
    # never be swallowed into a fake "accepted".
    sess = _Session({}, resp=_Resp({"detail": "no such pending question"}, status=409))
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    with pytest.raises(SidecarHTTPError) as exc_info:
        proxy.respond_query("rid-1", "req-stale", "gmail")
    assert exc_info.value.status_code == 409


# -- query_stream / cancel_query (#2109 query relay) -----------------------------


class _SSERespLines:
    """Fake ``requests`` streamed response exposing ``iter_lines()`` (SSE shape).

    Yields whole lines (some blank, per SSE
    double-newline framing).
    """

    def __init__(self, lines, status=200, *, payload=None):
        self.status_code = status
        self.headers = {"Content-Type": "text/event-stream"}
        self._lines = lines
        self._payload = payload
        self.text = str(payload) if payload is not None else ""
        self.closed = False

    def iter_lines(self):
        yield from self._lines

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def close(self):
        self.closed = True


def test_query_stream_posts_to_query_route_with_connect_and_read_timeout():
    # #2109: the connect timeout is fixed at 10s regardless of the general
    # sidecar timeout; the read timeout is the caller-supplied read_timeout.
    resp = _SSERespLines([b'data: {"type": "final", "answer": "ok"}'])
    sess = _StreamSession(resp)
    proxy = SidecarProxy(
        "http://127.0.0.1:9100", agent_id="gaia", session=sess, timeout=60.0
    )
    list(proxy.query_stream({"question": "hi"}, read_timeout=5.0))
    url, kwargs = sess.posts[0]
    assert url == "http://127.0.0.1:9100/v1/gaia/query"
    assert kwargs["json"] == {"question": "hi"}
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == (10.0, 5.0)


def test_query_stream_default_read_timeout_is_300():
    resp = _SSERespLines([b'data: {"type": "final", "answer": "ok"}'])
    sess = _StreamSession(resp)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    list(proxy.query_stream({}))
    assert sess.posts[0][1]["timeout"] == (10.0, 300.0)


def test_query_stream_non_2xx_raises_before_any_event_and_closes():
    # The
    # sidecar's actionable detail must survive, raised before any event escapes.
    resp = _SSERespLines(
        [b'data: {"type": "status", "message": "should not appear"}'],
        status=502,
        payload={"detail": "local LLM query failed: Lemonade not reachable"},
    )
    sess = _StreamSession(resp)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        list(proxy.query_stream({"question": "hi"}))
    assert ei.value.status_code == 502
    assert "Lemonade not reachable" in ei.value.detail
    assert resp.closed


def test_query_stream_yields_parsed_events_in_order_skipping_blank_lines():
    lines = [
        b'data: {"type": "status", "message": "hi"}',
        b"",
        b'data: {"type": "final", "answer": "done"}',
        b"",
    ]
    resp = _SSERespLines(lines)
    sess = _StreamSession(resp)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    events = list(proxy.query_stream({"question": "hi"}))
    assert events == [
        {"type": "status", "message": "hi"},
        {"type": "final", "answer": "done"},
    ]
    assert resp.closed


def test_query_stream_closes_response_when_exhausted():
    resp = _SSERespLines([b'data: {"type": "final", "answer": "done"}'])
    sess = _StreamSession(resp)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    assert list(proxy.query_stream({})) == [{"type": "final", "answer": "done"}]
    assert resp.closed


def test_query_stream_malformed_json_line_raises_loudly_and_stops():
    # A malformed SSE data line must raise loudly, not be swallowed into a
    # placeholder event -- and the generator must not yield anything further.
    lines = [
        b'data: {"type": "status", "message": "hi"}',
        b"",
        b"data: {not json",
    ]
    resp = _SSERespLines(lines)
    sess = _StreamSession(resp)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    gen = proxy.query_stream({"question": "hi"})
    first = next(gen)
    assert first == {"type": "status", "message": "hi"}
    with pytest.raises((SidecarError, ValueError)):
        next(gen)
    # Once the exception has propagated out of the generator it is closed --
    # no placeholder event, no silent resumption.
    with pytest.raises(StopIteration):
        next(gen)


def test_query_stream_posts_and_registers_response_eagerly_at_call_time():
    # Cross-thread cancel plumbing: on_response hands the live response object
    # to the caller (so a cancel from another thread can call resp.close()).
    # It must fire EAGERLY at call time — right after the POST returns, before
    # any line iteration — not lazily on first next(). A lazy registration
    # widens the early-cancel race: a cancel landing before the first
    # iteration would find active_relay_response still None, and the forced
    # close would be a no-op (#2109 inherited-defect fix).
    lines = [
        b'data: {"type": "status", "message": "hi"}',
        b"",
        b'data: {"type": "final", "answer": "done"}',
    ]
    resp = _SSERespLines(lines)
    sess = _StreamSession(resp)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)

    calls = []
    gen = proxy.query_stream({"question": "hi"}, on_response=calls.append)

    # The POST and the registration both happened at call time.
    assert len(sess.posts) == 1
    assert calls == [resp]
    assert hasattr(calls[0], "close")

    events = list(gen)
    assert events == [
        {"type": "status", "message": "hi"},
        {"type": "final", "answer": "done"},
    ]
    # Consuming the stream must not invoke the callback again.
    assert len(calls) == 1


def test_cancel_query_posts_to_correct_url_and_returns_none_on_2xx():
    sess = _Session({"ok": True})
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    result = proxy.cancel_query("run-123")
    assert result is None
    assert sess.posts[0][0] == "http://127.0.0.1:9100/v1/gaia/query/run-123/cancel"


def test_cancel_query_404_is_benign_and_returns_none():
    # The run already finished by the time the cancel lands -- not an error.
    err = _Resp({"detail": "run not found"}, status=404)
    sess = _Session(None, resp=err)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    result = proxy.cancel_query("run-already-done")
    assert result is None


def test_cancel_query_other_non_2xx_raises_sidecar_http_error():
    err = _Resp({"detail": "internal error cancelling run"}, status=500)
    sess = _Session(None, resp=err)
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    with pytest.raises(SidecarHTTPError) as ei:
        proxy.cancel_query("run-xyz")
    assert ei.value.status_code == 500
    assert "cancelling run" in ei.value.detail


class _TimeoutRecordingSession(_Session):
    """Fake session that also records the ``timeout=`` kwarg per POST, and can
    raise a transport exception instead of answering."""

    def __init__(self, payload, *, post_raises=None, **kwargs):
        super().__init__(payload, **kwargs)
        self.post_timeouts = []
        self._post_raises = post_raises

    def post(self, url, json=None, timeout=None):
        self.post_timeouts.append(timeout)
        if self._post_raises is not None:
            raise self._post_raises
        return super().post(url, json=json, timeout=timeout)


def test_cancel_query_uses_short_dedicated_timeout_not_general_timeout():
    # A hung sidecar must not make Cancel as slow as not cancelling: the
    # cancel POST is best-effort cleanup (the forced response close already
    # unblocked the relay's reader), so it gets its own short timeout instead
    # of the general 300s sidecar timeout.
    sess = _TimeoutRecordingSession({"ok": True})
    proxy = SidecarProxy(
        "http://127.0.0.1:9100", agent_id="gaia", session=sess, timeout=300.0
    )
    proxy.cancel_query("run-1")
    assert sess.post_timeouts == [10.0]


def test_cancel_query_timeout_is_swallowed_not_raised():
    import requests

    sess = _TimeoutRecordingSession(
        {"ok": True}, post_raises=requests.exceptions.Timeout("read timed out")
    )
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    # Must not raise: the reader is already unblocked; a hung sidecar can't
    # honour the cancel anyway.
    assert proxy.cancel_query("run-hung") is None


def test_cancel_query_connection_error_is_swallowed_not_raised():
    import requests

    sess = _TimeoutRecordingSession(
        {"ok": True},
        post_raises=requests.exceptions.ConnectionError("connection refused"),
    )
    proxy = SidecarProxy("http://127.0.0.1:9100", agent_id="gaia", session=sess)
    # A dead sidecar has nothing left to cancel — best-effort, never fatal.
    assert proxy.cancel_query("run-dead") is None


@pytest.mark.skipif(
    importlib.util.find_spec("requests") is None, reason="requests not installed"
)
def test_cross_thread_close_unblocks_parked_read_promptly_real_socket():
    """The cancel design's core mechanism, proven against a REAL socket.

    The relay's cancel path assumes the router-side forced close
    (``SSEOutputHandler.close_active_relay_response``) wakes a reader thread
    parked in a blocking socket read promptly. Every other cancel test
    drives that path through mocks or a heartbeat-emitting server (where the
    between-events cancelled check fires instead) — this one parks a real
    HTTP response silently, fires the production closer cross-thread, and
    bounds both how long the closer takes (it must not block on the parked
    reader's buffered-IO lock — a bare ``resp.close()`` does) and how long
    the reader takes to unblock.
    """
    import http.server
    import threading
    import time

    park = threading.Event()  # set → the server handler stops parking

    class _SilentParkHandler(http.server.BaseHTTPRequestHandler):
        # HTTP/1.1 + chunked framing — the same wire shape uvicorn streams
        # for the real sidecar's SSE. (A plain HTTP/1.0 unchunked body would
        # park the CLIENT in a buffered read waiting to fill its chunk size,
        # failing the test for the wrong reason.)
        protocol_version = "HTTP/1.1"

        def _write_chunk(self, data: bytes):
            self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
            self.wfile.write(data + b"\r\n")
            self.wfile.flush()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._write_chunk(b'data: {"type": "status", "message": "started"}\n\n')
            # Park SILENTLY — no heartbeats, so the client's only way out
            # before the 30s read_timeout is the cross-thread forced close.
            park.wait(timeout=30.0)

        def log_message(self, *args):  # noqa: D102 - silence test output
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SilentParkHandler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        proxy = SidecarProxy(f"http://127.0.0.1:{port}", agent_id="gaia")
        captured: dict = {}
        events: list = []
        outcome: dict = {}
        first_event_seen = threading.Event()

        gen = proxy.query_stream(
            {"query": "park"},
            read_timeout=30.0,
            on_response=lambda r: captured.setdefault("resp", r),
        )
        # Eager registration (the early-cancel-race contract): the live
        # response is already in hand before any iteration.
        assert "resp" in captured

        def _consume():
            try:
                for ev in gen:
                    events.append(ev)
                    first_event_seen.set()
                outcome["end"] = "exhausted"
            except Exception as exc:  # noqa: BLE001 - recording, not hiding
                outcome["end"] = f"raised:{type(exc).__name__}"
            outcome["t_done"] = time.monotonic()

        worker = threading.Thread(target=_consume, daemon=True)
        worker.start()

        assert first_event_seen.wait(
            timeout=5.0
        ), "never received the first SSE event from the real server"

        # The PRODUCTION closer, exactly as the /api/chat/cancel route and the
        # streaming generator's orphan cleanup invoke it.
        from gaia.ui.sse_handler import SSEOutputHandler

        handler = SSEOutputHandler()
        handler.active_relay_response = captured["resp"]

        t_close = time.monotonic()
        handler.close_active_relay_response()
        t_closer_done = time.monotonic()
        worker.join(timeout=5.0)

        assert t_closer_done - t_close < 1.0, (
            f"the forced close itself blocked for {t_closer_done - t_close:.2f}s "
            "— it must return immediately (the cancel HTTP route calls it on "
            "the event loop)"
        )
        assert not worker.is_alive(), (
            "the forced close did NOT unblock the parked read within 5s — "
            "the relay cancel design's core assumption is broken on this "
            "platform"
        )
        assert (
            outcome["t_done"] - t_close < 3.0
        ), f"unblocking took {outcome['t_done'] - t_close:.2f}s — not prompt"
        assert events == [{"type": "status", "message": "started"}]
    finally:
        park.set()
        server.shutdown()
        server_thread.join(timeout=5.0)
