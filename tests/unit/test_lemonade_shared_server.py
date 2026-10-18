# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The LLM client on a Lemonade other apps share: a server that is busy loading
someone else's model must read as busy, never hang GAIA or misreport the failure.

Every server here is a local fake; nothing touches a real Lemonade.
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.llm.retry import model_retry_notices

# Local loopback fakes only.
pytestmark = pytest.mark.allow_network

MODEL = "Gemma-4-E4B-it-GGUF"


class StalledServer:
    """Accepts every connection and never answers: Lemonade mid-load, as seen
    from a client whose request landed behind it."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}/api/v1"
        self._held = []
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                continue
            self._held.append(conn)

    def close(self):
        self._stop.set()
        for conn in self._held:
            conn.close()
        self._sock.close()


class FakeLemonade:
    """A minimal Lemonade: /health, /models, /load, /chat/completions.

    ``health_delay`` stalls /health; ``chat_errors`` is a queue of
    ``(status, body)`` replies served before chat succeeds.
    """

    def __init__(self, ctx_size=65536):
        self.calls = []
        self.health_delay = 0.0
        self.chat_errors = []
        self.loaded = [
            {
                "model_name": MODEL,
                "type": "llm",
                "status": "ready",
                "recipe": "llamacpp",
                "recipe_options": {"ctx_size": ctx_size},
                "max_context_window": 131072,
            }
        ]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split("?")[0]
                fake.calls.append(("GET", path))
                if path.endswith("/health"):
                    if fake.health_delay:
                        time.sleep(fake.health_delay)
                    self._reply(200, {"status": "ok", "all_models_loaded": fake.loaded})
                elif path.endswith("/models"):
                    self._reply(200, {"data": [{"id": MODEL, "downloaded": True}]})
                else:
                    self._reply(404, {"error": "not found"})

            def do_POST(self):
                path = self.path.split("?")[0]
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                fake.calls.append(("POST", path))
                if path.endswith("/chat/completions"):
                    if fake.chat_errors:
                        status, body = fake.chat_errors.pop(0)
                        self._reply(status, body)
                        return
                    self._reply(
                        200,
                        {
                            "choices": [
                                {
                                    "message": {"role": "assistant", "content": "hi"},
                                    "finish_reason": "stop",
                                }
                            ]
                        },
                    )
                elif path.endswith("/load"):
                    self._reply(200, {"status": "success"})
                else:
                    self._reply(404, {"error": "not found"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/api/v1"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def count(self, method, suffix):
        return sum(1 for m, p in self.calls if m == method and p.endswith(suffix))

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("GAIA_BROKER_URL", raising=False)
    monkeypatch.setattr(lc, "resolve_ctx_size", lambda *a, **k: 65536)
    LemonadeManager.reset()
    yield tmp_path
    LemonadeManager.reset()


@pytest.fixture
def stalled():
    server = StalledServer()
    yield server
    server.close()


@pytest.fixture
def fake():
    server = FakeLemonade()
    yield server
    server.close()


def _run_bounded(fn, n, limit):
    """Run *fn* on *n* threads; return their results, or fail if any is still
    running after *limit* seconds."""
    results = [None] * n
    errors = []

    def worker(i):
        try:
            results[i] = fn()
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)
    ]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.0, limit - (time.monotonic() - started)))
    hung = sum(t.is_alive() for t in threads)
    assert not hung, f"{hung} of {n} callers still blocked after {limit}s"
    return results, errors, time.monotonic() - started


# ── B1: a stalled /health must not freeze agent construction ─────────────


def test_ensure_ready_reports_a_stalled_server_as_busy(isolated_home, stalled):
    """Two agents built against a server that accepts and never answers both
    come back within the probe timeouts instead of waiting 15 minutes."""
    results, errors, elapsed = _run_bounded(
        lambda: LemonadeManager.ensure_ready(
            base_url=stalled.base_url, model=MODEL, min_context_size=32768
        ),
        n=2,
        limit=30,
    )
    assert not errors, errors
    # Busy is not "ready": the next caller checks again.
    assert results == [True, True]
    assert not LemonadeManager.is_initialized()


def test_get_status_flags_busy_not_down(isolated_home, stalled):
    client = lc.LemonadeClient(base_url=stalled.base_url, verbose=False)
    started = time.monotonic()
    status = client.get_status()
    assert time.monotonic() - started < 15
    assert status.busy and not status.running
    assert "did not answer" in status.error


def test_health_check_default_is_a_short_probe(isolated_home, stalled):
    client = lc.LemonadeClient(base_url=stalled.base_url, verbose=False)
    started = time.monotonic()
    with pytest.raises(lc.LemonadeUnresponsiveError):
        client.health_check()
    assert time.monotonic() - started < 15


# ── B6: per-call status probes are short and cached ──────────────────────


def test_each_chat_is_one_short_probe_and_no_catalog_read(isolated_home, fake):
    client = lc.LemonadeClient(base_url=fake.base_url, verbose=False)
    for _ in range(3):
        client.chat_completions(MODEL, [{"role": "user", "content": "hi"}])
    assert fake.count("POST", "/chat/completions") == 3
    assert fake.count("GET", "/health") == 3
    assert fake.count("GET", "/models") == 0
    assert fake.count("POST", "/load") == 0


def test_a_reload_by_someone_else_is_seen_on_the_next_request(isolated_home, fake):
    """Another app evicts the model and Lemonade reloads it at its own 32K
    default (#1030): the very next request notices and restores the window."""
    client = lc.LemonadeClient(base_url=fake.base_url, verbose=False)
    client.chat_completions(MODEL, [{"role": "user", "content": "hi"}])
    fake.loaded[0]["recipe_options"]["ctx_size"] = 32768
    client.chat_completions(MODEL, [{"role": "user", "content": "hi"}])
    assert fake.count("POST", "/load") == 1


@pytest.fixture
def quick_probes(monkeypatch):
    monkeypatch.setattr(lc, "PROBE_TIMEOUT", (1.0, 1.0))
    monkeypatch.setattr(lc, "_BUSY_POLL_SECONDS", 0.5)


def test_first_request_waits_for_a_busy_server(isolated_home, fake, quick_probes):
    """/health stalls while another app loads a model, then answers: the
    request waits for it, says so, and never sends a load."""
    fake.health_delay = 60
    threading.Timer(3, lambda: setattr(fake, "health_delay", 0)).start()
    client = lc.LemonadeClient(base_url=fake.base_url, verbose=False)
    notices = []

    def chat():
        with model_retry_notices(notices.append):
            return client.chat_completions(MODEL, [{"role": "user", "content": "hi"}])

    results, errors, elapsed = _run_bounded(
        chat,
        n=1,
        limit=40,
    )
    assert not errors, errors
    assert results[0]["choices"][0]["message"]["content"] == "hi"
    assert fake.count("POST", "/load") == 0
    assert notices and "busy" in notices[0].reason


def test_a_server_that_never_answers_fails_loudly(
    isolated_home, fake, quick_probes, monkeypatch
):
    fake.health_delay = 60
    monkeypatch.setenv("GAIA_LLM_LOAD_WAIT", "3")
    client = lc.LemonadeClient(base_url=fake.base_url, verbose=False)
    results, errors, elapsed = _run_bounded(
        lambda: client.chat_completions(MODEL, [{"role": "user", "content": "hi"}]),
        n=1,
        limit=40,
    )
    assert len(errors) == 1 and isinstance(errors[0], lc.LemonadeUnresponsiveError)
    assert "GAIA_LLM_LOAD_WAIT" in str(errors[0])
    assert fake.count("POST", "/chat/completions") == 0


def test_load_wait_zero_fails_at_once(isolated_home, fake, quick_probes, monkeypatch):
    fake.health_delay = 60
    monkeypatch.setenv("GAIA_LLM_LOAD_WAIT", "0")
    client = lc.LemonadeClient(base_url=fake.base_url, verbose=False)
    results, errors, elapsed = _run_bounded(
        lambda: client.chat_completions(MODEL, [{"role": "user", "content": "hi"}]),
        n=1,
        limit=20,
    )
    assert len(errors) == 1 and isinstance(errors[0], lc.LemonadeUnresponsiveError)
    assert elapsed < 5


# ── A busy server is never mistaken for a stopped one ────────────────────


def test_launch_server_does_not_take_over_a_busy_server(isolated_home, stalled):
    client = lc.LemonadeClient(base_url=stalled.base_url, verbose=False)
    with (
        patch.object(client, "_classify_port_listeners") as classify,
        patch.object(client, "_stop_listeners") as stop,
    ):
        client.launch_server()
    classify.assert_not_called()
    stop.assert_not_called()


def test_initialize_does_not_start_over_a_busy_server(isolated_home, stalled):
    client = lc.LemonadeClient(base_url=stalled.base_url, verbose=False)
    with (
        patch.object(client, "_check_lemonade_installed", return_value=True),
        patch.object(client, "launch_server") as launch,
    ):
        status = client.initialize(quiet=True)
    assert status.busy
    launch.assert_not_called()


def test_auto_start_does_not_launch_over_a_busy_server(isolated_home, stalled):
    with patch.object(lc.LemonadeClient, "launch_server") as launch:
        lc.create_lemonade_client(
            host="127.0.0.1", port=stalled.port, auto_start=True, verbose=False
        )
    launch.assert_not_called()
