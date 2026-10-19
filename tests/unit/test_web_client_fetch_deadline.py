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

from gaia.web.client import FetchDeadlineExceeded, WebClient

# The server is local; every test here talks to it over a loopback socket.
pytestmark = pytest.mark.allow_network

TRICKLE_INTERVAL = 0.05


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802 - http.server naming
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


@pytest.mark.parametrize("path", ["/slow", "/redirect"])
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


def test_a_fast_fetch_is_unaffected(server):
    response = _client(fetch_deadline=1).get(server + "/fast")
    assert response.text == "<html>ok</html>"


def test_deadline_is_a_requests_timeout_so_existing_handlers_catch_it():
    import requests

    assert issubclass(FetchDeadlineExceeded, requests.exceptions.Timeout)
