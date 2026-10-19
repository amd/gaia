# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A server that trickles bytes must not keep a fetch alive forever.

The per-request ``timeout`` bounds each socket read, so a server that sends one
byte just inside it, again and again, kept a page fetch (up to the 10 MB cap)
or a download (up to 100 MB) running for days. Fetches and downloads now have
an overall deadline covering every redirect hop and the body.

Driven against a real local HTTP server that sends a byte every 50 ms.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from gaia.web.client import FetchDeadlineExceeded, WebClient

# The server is local; every test here talks to it over a loopback socket.
pytestmark = pytest.mark.allow_network

TRICKLE_INTERVAL = 0.05


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802 - http.server naming
        if self.path == "/slow-headers":
            self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Pad: ")
            try:
                while not self.server.stopping.is_set():
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(TRICKLE_INTERVAL)
            except OSError:
                pass
            return
        if self.path == "/silent":
            self.send_response(200)
            self.end_headers()
            self.server.stopping.wait(30)
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/slow")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if self.path == "/fast":
            body = b"<html>ok</html>"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.end_headers()
        try:
            while not self.server.stopping.is_set():
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(TRICKLE_INTERVAL)
        except OSError:
            pass  # the client hung up, which is what the test wants


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.stopping = threading.Event()
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.stopping.set()
    httpd.shutdown()
    httpd.server_close()


def _client(**kwargs):
    # A read timeout far above the trickle interval: only the deadline can stop it.
    return WebClient(timeout=10, rate_limit=0.001, **kwargs)


def _bounded(fn, limit=20):
    box = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as e:  # noqa: BLE001 - surfaced to the test
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    started = time.monotonic()
    t.start()
    t.join(limit)
    assert not t.is_alive(), f"still fetching after {limit}s"
    return box, time.monotonic() - started


@pytest.mark.parametrize("path", ["/slow", "/redirect", "/slow-headers"])
def test_trickling_page_fetch_stops_at_the_deadline(server, path):
    client = _client(fetch_deadline=1)
    box, elapsed = _bounded(lambda: client.get(server + path))

    error = box.get("error")
    assert isinstance(error, FetchDeadlineExceeded), box
    assert "did not finish within 1s" in str(error)
    assert elapsed < 10


def test_trickling_download_stops_and_leaves_no_partial_file(server, tmp_path):
    client = _client(download_deadline=1)
    box, _ = _bounded(
        lambda: client.download(server + "/slow", str(tmp_path), filename="f.bin")
    )

    assert isinstance(box.get("error"), FetchDeadlineExceeded), box
    assert list(tmp_path.iterdir()) == []


def test_a_silent_body_read_is_unblocked_at_the_deadline(server):
    """No bytes at all: the timer, not the 10 s read timeout, ends the read."""
    client = _client(fetch_deadline=1)
    box, elapsed = _bounded(lambda: client.get(server + "/silent"))
    assert isinstance(box.get("error"), FetchDeadlineExceeded), box
    assert elapsed < 8


def test_a_caller_timeout_is_capped_by_the_deadline(server):
    client = _client(fetch_deadline=1)
    box, elapsed = _bounded(lambda: client.get(server + "/silent", timeout=(5, 300)))
    assert isinstance(box.get("error"), FetchDeadlineExceeded), box
    assert elapsed < 8


def test_a_fast_fetch_is_unaffected(server):
    response = _client(fetch_deadline=1).get(server + "/fast")
    assert response.text == "<html>ok</html>"


def test_deadline_is_a_requests_timeout_so_existing_handlers_catch_it():
    import requests

    assert issubclass(FetchDeadlineExceeded, requests.exceptions.Timeout)


def test_a_timer_firing_after_the_fetch_ends_leaves_the_next_request_alone():
    """cancel() cannot stop a timer callback already running."""
    import threading as _threading

    from gaia.web import client as client_mod

    class FakeSocket:
        shut = False

        def shutdown(self, _how):
            self.shut = True

    with client_mod._Deadline(60, "http://example.test", "fetch") as deadline:
        pass
    next_request = FakeSocket()
    with client_mod._SOCKETS_LOCK:
        client_mod._THREAD_SOCKETS[_threading.get_ident()] = next_request
    try:
        deadline._fire()
    finally:
        with client_mod._SOCKETS_LOCK:
            client_mod._THREAD_SOCKETS.pop(_threading.get_ident(), None)
    assert next_request.shut is False
    assert not deadline.fired.is_set()


def test_a_timer_that_runs_late_still_reports_the_deadline(server, monkeypatch):
    """On a loaded machine the read timeout, cut to the time left, can fail a
    moment before the timer thread runs; that is still the deadline."""
    from gaia.web import client as client_mod

    on_time = client_mod._Deadline._fire

    def late(self):
        time.sleep(0.3)
        on_time(self)

    monkeypatch.setattr(client_mod._Deadline, "_fire", late)
    box, _ = _bounded(lambda: _client(fetch_deadline=1).get(server + "/silent"))
    assert isinstance(box.get("error"), FetchDeadlineExceeded), box


def test_a_deadline_that_passes_during_dns_still_bounds_the_body(server, monkeypatch):
    """No socket existed when the timer fired; the reads after it must stop."""
    from gaia.web import client as client_mod

    resolve = client_mod.PinnedIPAdapter._resolve_first_ip

    def slow_dns(self, host, port):
        time.sleep(1.3)
        return resolve(self, host, port)

    monkeypatch.setattr(client_mod.PinnedIPAdapter, "_resolve_first_ip", slow_dns)
    box, elapsed = _bounded(lambda: _client(fetch_deadline=1).get(server + "/slow"))
    assert isinstance(box.get("error"), FetchDeadlineExceeded), box
    assert elapsed < 8


class _TrickledHeaders(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802 - http.server naming
        # One header line, a byte at a time: http.client's 100-header cap never
        # ends it, and its 64 KB line cap would take an hour.
        self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Pad: ")
        try:
            while not self.server.stopping.is_set():
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(TRICKLE_INTERVAL)
        except OSError:
            pass


def test_a_proxy_that_trickles_headers_stops_at_the_deadline(server):
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), _TrickledHeaders)
    proxy.stopping = threading.Event()
    proxy.daemon_threads = True
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{proxy.server_address[1]}"
    try:
        box, elapsed = _bounded(
            lambda: _client(fetch_deadline=1).get(
                server + "/fast", proxies={"http": url}
            )
        )
    finally:
        proxy.stopping.set()
        proxy.shutdown()
        proxy.server_close()
    assert isinstance(box.get("error"), FetchDeadlineExceeded), box
    assert elapsed < 8


@pytest.fixture
def trickled_tls(monkeypatch):
    """Accepts TLS, then sends its handshake a byte at a time."""
    import socket

    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    listener.settimeout(0.5)
    stop = threading.Event()

    def handle(conn):
        try:
            conn.recv(4096)  # ClientHello
            conn.sendall(b"\x16\x03\x03\x40\x00")  # a 16 KB handshake record
            while not stop.is_set():
                conn.sendall(b"\x00")
                time.sleep(TRICKLE_INTERVAL)
        except OSError:
            pass

    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield f"https://127.0.0.1:{listener.getsockname()[1]}/"
    stop.set()
    listener.close()


def test_a_trickled_tls_handshake_stops_at_the_deadline(trickled_tls):
    box, elapsed = _bounded(lambda: _client(fetch_deadline=1).get(trickled_tls))
    assert isinstance(box.get("error"), FetchDeadlineExceeded), box
    assert elapsed < 8


class _Compressed(BaseHTTPRequestHandler):
    """A gzip body that inflates far past the size cap, with no charset."""

    body = b""

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802 - http.server naming
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(self.body)
        except OSError:
            pass


def test_the_read_order_puts_the_body_through_the_capped_read(monkeypatch):
    """A test pins the read order: the body goes through the bounded read
    before charset detection looks at it (real detection, nothing mocked)."""
    import tracemalloc
    import zlib

    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    packer = zlib.compressobj(9, zlib.DEFLATED, 31)
    block = b"a" * (1 << 20)
    _Compressed.body = b"".join(packer.compress(block) for _ in range(40))
    _Compressed.body += packer.flush()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Compressed)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = WebClient(timeout=10, rate_limit=0.001, max_response_size=1 << 20)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="exceeds max size"):
            client.get(f"http://127.0.0.1:{httpd.server_address[1]}/")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        httpd.shutdown()
        httpd.server_close()
    assert peak < 16 * (1 << 20)


def test_a_blocked_address_is_not_reported_as_a_slow_server(server, monkeypatch):
    """Only transport failures become the deadline; an SSRF block found after
    the deadline passed must still say it was blocked."""
    from gaia.web import client as client_mod

    def blocked(self, host, port):
        time.sleep(1.3)
        raise ValueError(f"Blocked: {host} resolves to private/reserved IP")

    monkeypatch.setattr(client_mod.PinnedIPAdapter, "_resolve_first_ip", blocked)
    box, _ = _bounded(lambda: _client(fetch_deadline=1).get(server + "/fast"))
    error = box.get("error")
    assert isinstance(error, ValueError) and "Blocked" in str(error), box


def test_a_read_failing_just_before_the_deadline_counts_as_the_deadline():
    """The read timeout is cut to the time left, so it can fire a tick before
    the deadline does."""
    from gaia.web import client as client_mod

    with client_mod._Deadline(60, "http://example.test", "fetch") as deadline:
        deadline._at = time.monotonic() + 0.1  # a tick from the deadline
        error = requests.exceptions.ReadTimeout("read timed out")
        assert client_mod._is_deadline_failure(error, deadline)
        deadline._at = time.monotonic() + 5
        assert not client_mod._is_deadline_failure(error, deadline)


def test_the_rate_limit_wait_is_not_charged_to_the_deadline(server):
    """The wait between two fetches to one host is GAIA's own pacing."""
    client = WebClient(timeout=10, rate_limit=1.5, fetch_deadline=1)
    client.get(server + "/fast")
    response = client.get(server + "/fast")  # waits ~1.5 s first
    assert response.text == "<html>ok</html>"


@pytest.fixture
def socks5_relay(monkeypatch):
    """A minimal SOCKS5 relay (no auth, CONNECT only)."""
    import socket
    import struct

    pytest.importorskip("socks", reason="SOCKS proxies need PySocks")
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    hits = []

    def pipe(src, dst):
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (src, dst):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def handle(conn):
        conn.recv(262)
        conn.sendall(b"\x05\x00")
        header = conn.recv(4)
        if header[3] == 1:
            host = socket.inet_ntoa(conn.recv(4))
        else:
            host = conn.recv(conn.recv(1)[0]).decode()
        (port,) = struct.unpack(">H", conn.recv(2))
        hits.append((host, port))
        upstream = socket.create_connection((host, port))
        conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
        threading.Thread(target=pipe, args=(conn, upstream), daemon=True).start()
        pipe(upstream, conn)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)

    def accept():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    yield f"socks5://127.0.0.1:{listener.getsockname()[1]}", hits
    listener.close()


def test_fetches_through_a_socks_proxy_still_work(server, socks5_relay):
    proxy, hits = socks5_relay
    response = _client(fetch_deadline=5).get(
        server + "/fast", proxies={"http": proxy, "https": proxy}
    )
    assert response.text == "<html>ok</html>"
    assert hits, "the request did not go through the proxy"


def test_a_socks_proxied_trickle_still_stops_at_the_deadline(server, socks5_relay):
    proxy, _ = socks5_relay
    box, elapsed = _bounded(
        lambda: _client(fetch_deadline=1).get(
            server + "/slow-headers", proxies={"http": proxy}
        )
    )
    assert isinstance(box.get("error"), FetchDeadlineExceeded), box
    assert elapsed < 8
