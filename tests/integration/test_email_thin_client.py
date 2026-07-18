# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration test: ``gaia email`` is a thin client over the daemon (V2-8, #2152).

Exercises the ACTUAL CLI command a user runs (``python -m gaia.cli email -q ...``,
per CLAUDE.md testing philosophy) against a FAKE daemon that stands in for the
real one: it answers ``/daemon/v1/status``, ``/daemon/v1/agents/email/ensure``,
and streams the canonical SSE contract on ``/v1/email/query``.

The fake daemon runs in the pytest process (a threaded HTTP server) and records
every inbound request, so the test can prove the thin-client custody invariant:

  * ``gaia email`` ensures + streams through the daemon relay,
  * presenting ONLY the daemon client token — it never learns or presents the
    sidecar bearer that ``ensure`` returns.

State is isolated under a tmp ``GAIA_DAEMON_HOME`` so the run never touches (or
spawns) the user's real daemon: instance.json is written pointing at THIS process
(pid alive + token-authed status probe succeeds → ``start_or_attach`` attaches
instead of spawning).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gaia.daemon import instance
from gaia.daemon.constants import DAEMON_API_VERSION, SERVICE_ID
from gaia.daemon.instance import DaemonInstance

_REPO_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"
)

# Distinct tokens: the DAEMON client token the CLI is allowed to present, and the
# SIDECAR bearer the ensure response leaks — the CLI must NEVER present the latter.
_DAEMON_TOKEN = "daemon-client-token-abc123"
_SIDECAR_TOKEN = "sidecar-bearer-SECRET-must-not-leak"

_FINAL_ANSWER = "Triaged 5 emails: 2 urgent, 3 newsletters."


class _Recorder:
    """Thread-safe capture of the requests the fake daemon received."""

    def __init__(self) -> None:
        self.requests: list = []
        self._lock = threading.Lock()

    def add(self, method: str, path: str, auth: str, body: bytes) -> None:
        with self._lock:
            self.requests.append(
                {"method": method, "path": path, "auth": auth, "body": body}
            )

    def find(self, path_contains: str):
        with self._lock:
            return [r for r in self.requests if path_contains in r["path"]]


def _make_handler(recorder: _Recorder, ensure_status: int, sse_frames: list):
    """Build a BaseHTTPRequestHandler class bound to this test's fixtures."""

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 (the default): the server closes the connection at the end of
        # each response, so the SSE client reads the body until EOF without us
        # having to chunk it.
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args):  # silence per-request stderr noise
            pass

        def _auth(self) -> str:
            return self.headers.get("Authorization", "")

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(length) if length else b""

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            body = self._read_body()
            recorder.add("GET", self.path, self._auth(), body)
            if self.path.startswith("/daemon/v1/status"):
                self._json(
                    200,
                    {
                        "service": SERVICE_ID,
                        "api_version": DAEMON_API_VERSION,
                        "pid": os.getpid(),
                        "port": self.server.server_address[1],
                        "host": "127.0.0.1",
                        "started_at": 0.0,
                        "uptime_seconds": 1.0,
                    },
                )
                return
            self._json(404, {"detail": f"no route {self.path}"})

        def do_POST(self):
            body = self._read_body()
            recorder.add("POST", self.path, self._auth(), body)

            if self.path.endswith("/agents/email/ensure"):
                if ensure_status != 200:
                    self._json(
                        ensure_status,
                        {"detail": "the email sidecar failed its health check."},
                    )
                    return
                # The ensure body carries the sidecar bearer — the CLI must
                # discard it and never present it on the relay call below.
                self._json(
                    200,
                    {
                        "agent_id": "email",
                        "state": "running",
                        "mode": "user",
                        "pid": 4242,
                        "port": 59999,
                        "base_url": "http://127.0.0.1:59999",
                        "api_version": "1",
                        "agent_version": "1.0.0",
                        "started_at": 0.0,
                        "token": _SIDECAR_TOKEN,
                    },
                )
                return

            if self.path.endswith("/v1/email/query"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for frame in sse_frames:
                    self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return

            if self.path.endswith("/cancel"):
                self._json(200, {"run_id": "x", "cancelled": True, "status": "ok"})
                return

            self._json(404, {"detail": f"no route {self.path}"})

    return Handler


@pytest.fixture()
def fake_daemon(tmp_path, monkeypatch):
    """Start a fake daemon in-process and register it in a tmp GAIA_DAEMON_HOME.

    Yields (recorder, configure) where ``configure`` lets a test set the ensure
    status and the SSE frames BEFORE it runs the CLI.
    """
    home = tmp_path / "host"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(home))

    recorder = _Recorder()
    # Mutable config the closure reads at request time.
    cfg = {
        "ensure_status": 200,
        "sse_frames": [
            {"type": "status", "message": "Processing..."},
            {"type": "tool_call", "tool": "triage_inbox", "args": {}},
            {"type": "tool_result", "tool": "triage_inbox", "data": {"count": 5}},
            {"type": "token", "delta": _FINAL_ANSWER},
            {"type": "final", "answer": _FINAL_ANSWER},
        ],
    }

    # Bind an ephemeral loopback port; build the handler bound to live cfg.
    def handler_factory(*args, **kwargs):
        return _make_handler(recorder, cfg["ensure_status"], cfg["sse_frames"])(
            *args, **kwargs
        )

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Register instance.json pointing at THIS process (pid alive) + the fake port.
    instance.write_instance(
        DaemonInstance(
            pid=os.getpid(),
            port=port,
            token=_DAEMON_TOKEN,
            host="127.0.0.1",
            api_version=DAEMON_API_VERSION,
            service=SERVICE_ID,
            started_at=0.0,
        )
    )

    def configure(*, ensure_status=None, sse_frames=None):
        if ensure_status is not None:
            cfg["ensure_status"] = ensure_status
        if sse_frames is not None:
            cfg["sse_frames"] = sse_frames

    try:
        yield recorder, configure
    finally:
        server.shutdown()
        server.server_close()


def _child_env(home_env) -> dict:
    env = os.environ.copy()
    env["GAIA_DAEMON_HOME"] = home_env
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _REPO_SRC + (os.pathsep + existing if existing else "")
    # Force UTF-8 I/O so the CLI's emoji/ellipsis output round-trips on Windows
    # (the default console codec is cp1252, which cannot encode ❌ / … / 🔧).
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_email(*cli_args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "gaia.cli", "email", "--no-lemonade-check", *cli_args],
        env=_child_env(os.environ["GAIA_DAEMON_HOME"]),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_email_streams_through_daemon_relay(fake_daemon):
    """Golden path: ``gaia email -q`` ensures + streams the answer via the relay."""
    recorder, _ = fake_daemon

    r = _run_email("-q", "Triage my inbox")

    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    # The agent's answer lands on stdout (pipe-friendly, parity with the old path).
    assert _FINAL_ANSWER in r.stdout

    # It went through the daemon: an ensure AND a relayed /v1/email/query happened.
    assert recorder.find("/agents/email/ensure"), "ensure was never called"
    query_reqs = recorder.find("/v1/email/query")
    # The plain query, not the cancel sub-path.
    query_only = [q for q in query_reqs if q["path"].endswith("/v1/email/query")]
    assert query_only, "the relayed /v1/email/query was never called"


def test_thin_client_presents_only_the_daemon_token(fake_daemon):
    """Custody invariant: the CLI presents the DAEMON token, never the sidecar bearer."""
    recorder, _ = fake_daemon

    r = _run_email("-q", "Triage my inbox")
    assert r.returncode == 0, r.stderr

    ensure = recorder.find("/agents/email/ensure")[0]
    query = [
        q
        for q in recorder.find("/v1/email/query")
        if q["path"].endswith("/v1/email/query")
    ][0]

    # Both the ensure and the relayed query authenticate with the DAEMON token.
    assert ensure["auth"] == f"Bearer {_DAEMON_TOKEN}"
    assert query["auth"] == f"Bearer {_DAEMON_TOKEN}"

    # The sidecar bearer that `ensure` leaked must appear in NO outgoing request —
    # the thin client never learns or presents it.
    for req in recorder.requests:
        assert _SIDECAR_TOKEN not in req["auth"]
        assert _SIDECAR_TOKEN not in (req["body"] or b"").decode("utf-8", "replace")

    # The relayed query carries a host-minted run_id + the pushed (empty) context.
    payload = json.loads(query["body"])
    assert payload["query"] == "Triage my inbox"
    assert isinstance(payload["run_id"], str) and payload["run_id"]
    assert payload["context"] == []


def test_ensure_failure_is_loud_not_a_fallback(fake_daemon):
    """Lemonade/sidecar failure at ensure → loud actionable error, no silent fallback."""
    recorder, configure = fake_daemon
    configure(ensure_status=502)

    r = _run_email("-q", "Triage my inbox")

    assert r.returncode == 1
    # Actionable: names what failed and points at the sidecar.
    assert "sidecar" in r.stderr.lower()
    # It did NOT fall back to an in-process run: no /query was attempted.
    assert not [
        q
        for q in recorder.find("/v1/email/query")
        if q["path"].endswith("/v1/email/query")
    ]


def test_stream_without_terminal_event_fails_loud(fake_daemon):
    """A stream that ends without a final/error is a failure, surfaced loudly."""
    _, configure = fake_daemon
    configure(
        sse_frames=[
            {"type": "status", "message": "Processing..."},
            {"type": "token", "delta": "partial..."},
            # No terminal final/error — a crashed sidecar mid-run.
        ]
    )

    r = _run_email("-q", "Triage my inbox")

    assert r.returncode == 1
    assert "terminal" in r.stderr.lower()
