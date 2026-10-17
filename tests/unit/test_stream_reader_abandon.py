# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An abandoned model-stream reader must exit and close its connection on every
OS, wherever its read is blocked.

A raw TCP server that never sends what the client waits for: no headers, no
body after the headers, or half of a chunk. The reader is abandoned by a stall,
by Stop, and by an exception in the consumer. Each case must leave no
``gaia-model-stream`` thread and no open socket to the server, seen both from
psutil and from the server, which must read the client's hang-up.
"""

import socket
import threading
import time

import httpx
import psutil
import pytest

from gaia.llm.retry import StreamCancelledError, StreamStalledError, model_retry_notices
from gaia.llm.stream_watchdog import (
    ByteActivity,
    StallLimits,
    activity_http_client,
    watch_stream,
)

pytestmark = pytest.mark.allow_network

IDLE = 1.0
#: How long the server keeps a connection open unless the client hangs up.
HOLD = 60.0
#: An abandoned reader must be gone well inside this, on any OS.
EXIT_WITHIN = 2.0

HEADERS = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/event-stream\r\n"
    b"Transfer-Encoding: chunked\r\n\r\n"
)
FRAME = b'data: {"n": 1}\n\n'


def _chunk(payload: bytes) -> bytes:
    return f"{len(payload):x}\r\n".encode() + payload + b"\r\n"


def never_answers(conn):
    pass


def headers_then_silence(conn):
    conn.sendall(HEADERS)


def frame_then_silence(conn):
    conn.sendall(HEADERS + _chunk(FRAME))


def half_a_chunk(conn):
    """A chunk announced as 100 bytes of which 10 arrive: the reader is inside
    the chunk body when it is abandoned."""
    conn.sendall(HEADERS + _chunk(FRAME) + b"64\r\n" + b"x" * 10)


class SilentServer:
    """Accepts one connection, runs *behaviour*, then waits for the client to
    hang up; ``client_hung_up`` is set once it has."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.client_hung_up = threading.Event()
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        conn, _ = self.listener.accept()
        with conn:
            conn.settimeout(HOLD)
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(65536)
            self.behaviour(conn)
            try:
                while conn.recv(65536):
                    pass
            except OSError:
                pass  # A reset is a hang-up too.
            self.client_hung_up.set()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.listener.close()


#: States in which this side has not closed its socket yet.
_STILL_OPEN = {psutil.CONN_SYN_SENT, psutil.CONN_ESTABLISHED, psutil.CONN_CLOSE_WAIT}


def _open_to(port):
    """This process's TCP sockets to *port* that it has not closed."""
    proc = psutil.Process()
    connections = getattr(proc, "net_connections", proc.connections)(kind="tcp")
    return [
        c
        for c in connections
        if c.raddr and c.raddr.port == port and c.status in _STILL_OPEN
    ]


def _readers():
    return [t for t in threading.enumerate() if t.name == "gaia-model-stream"]


def _stream(port, activity, limits):
    client = activity_http_client(activity)

    def produce():
        try:
            with client.stream("POST", f"http://127.0.0.1:{port}/v1/x", json={}) as r:
                for line in r.iter_lines():
                    if line:
                        yield line
        finally:
            client.close()

    return watch_stream(
        produce, limits=limits, activity=activity, is_output=lambda line: True
    )


def _assert_clean(server):
    end = time.monotonic() + EXIT_WITHIN
    while _readers() and time.monotonic() < end:
        time.sleep(0.02)
    assert not _readers(), "the abandoned reader thread is still alive"
    assert server.client_hung_up.wait(EXIT_WITHIN), "the server never saw a hang-up"
    assert not _open_to(server.port), "a socket to the server is still open"


@pytest.fixture(autouse=True)
def _no_reader_before():
    assert not _readers(), "a reader leaked from an earlier test"


HOSTED = StallLimits(first_output=IDLE, idle=IDLE, remote=True)
#: Stop must not depend on the stall limit, so it is out of reach here.
FAR = StallLimits(first_output=HOLD, idle=HOLD, remote=False)


@pytest.mark.parametrize(
    "behaviour", [never_answers, headers_then_silence, half_a_chunk]
)
def test_a_stall_ends_the_reader_and_its_socket(behaviour):
    with SilentServer(behaviour) as server:
        started = time.monotonic()
        with pytest.raises(StreamStalledError):
            list(_stream(server.port, ByteActivity(), HOSTED))
        assert time.monotonic() - started < IDLE + 2
        _assert_clean(server)


@pytest.mark.parametrize(
    "behaviour", [never_answers, headers_then_silence, half_a_chunk]
)
def test_stop_ends_the_reader_and_its_socket(behaviour):
    stop_at = time.monotonic() + 0.5
    with SilentServer(behaviour) as server:
        with model_retry_notices(lambda n: None, lambda: time.monotonic() > stop_at):
            with pytest.raises(StreamCancelledError):
                list(_stream(server.port, ByteActivity(), FAR))
        _assert_clean(server)


@pytest.mark.parametrize("behaviour", [frame_then_silence, half_a_chunk])
def test_a_consumer_exception_ends_the_reader_and_its_socket(behaviour):
    """The consumer fails while the reader is blocked waiting for more."""
    with SilentServer(behaviour) as server:
        with pytest.raises(RuntimeError, match="consumer broke"):
            for _ in _stream(server.port, ByteActivity(), FAR):
                time.sleep(0.3)  # Let the reader block on the next read.
                raise RuntimeError("consumer broke")
        _assert_clean(server)


def test_a_consumer_that_walks_away_ends_the_reader_and_its_socket():
    """``close()`` on the half-read generator, as a caller breaking out does."""
    with SilentServer(frame_then_silence) as server:
        stream = _stream(server.port, ByteActivity(), FAR)
        assert next(stream) == FRAME.decode().strip()
        time.sleep(0.3)
        stream.close()
        _assert_clean(server)


def test_an_abort_before_connecting_never_opens_a_socket():
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        activity = ByteActivity()
        activity.abort()
        client = activity_http_client(activity)
        with pytest.raises(httpx.ConnectError):
            client.post(f"http://127.0.0.1:{port}/v1/x", json={})
        client.close()
        listener.settimeout(0.2)
        with pytest.raises(socket.timeout):
            listener.accept()


def test_a_live_stream_is_not_cut_by_the_short_read_slices():
    """Reads wait in short slices; a reply slower than one slice must still
    arrive whole."""
    payloads = [FRAME, b'data: {"n": 2}\n\n', b'data: {"n": 3}\n\n']

    def slow(conn):
        conn.sendall(HEADERS)
        for payload in payloads:
            time.sleep(0.6)
            conn.sendall(_chunk(payload))
        conn.sendall(b"0\r\n\r\n")

    with SilentServer(slow) as server:
        lines = list(_stream(server.port, ByteActivity(), FAR))
    assert lines == [p.decode().strip() for p in payloads]


def test_the_read_timeout_still_fires_when_nothing_aborts():
    """Slicing must not turn the HTTP client's own read timeout into forever."""
    with SilentServer(headers_then_silence) as server:
        client = activity_http_client(ByteActivity())
        started = time.monotonic()
        with pytest.raises(httpx.ReadTimeout):
            with client.stream(
                "POST",
                f"http://127.0.0.1:{server.port}/v1/x",
                json={},
                timeout=httpx.Timeout(5.0, read=0.8),
            ) as r:
                r.read()
        client.close()
        took = time.monotonic() - started
    assert 0.7 < took < 3, f"read timeout fired after {took:.1f}s"
