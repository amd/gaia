# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deleting a chat mid-email-turn stops the turn and returns promptly (#4140).

An email-relay turn parks in a blocking socket read that the cancelled flag
alone can't interrupt, and the delete waits on the session lock that turn
holds. Every cancel route must also force the parked read closed.
"""

import asyncio
import http.server
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base.memory_store import MemoryStore
from gaia.ui._chat_helpers import _active_sse_handlers
from gaia.ui.database import ChatDatabase
from gaia.ui.sidecar.proxy import EmailSidecarProxy
from gaia.ui.routers import sessions as sessions_router
from gaia.ui.run_manager import Run, run_manager
from gaia.ui.server import create_app
from gaia.ui.sse_handler import SSEOutputHandler

PARK_SECONDS = 30.0
PROMPT_SECONDS = 10.0


class _RecordingHandler(SSEOutputHandler):
    def __init__(self):
        super().__init__()
        self.closed = 0

    def close_active_relay_response(self) -> None:
        self.closed += 1
        super().close_active_relay_response()


@pytest.fixture(autouse=True)
def _clean_runs():
    run_manager._runs.clear()
    yield
    run_manager._runs.clear()


@pytest.fixture
def silent_sidecar():
    """A sidecar that sends one event, then goes silent until released."""
    release = threading.Event()

    class _Park(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _chunk(self, data: bytes):
            self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
            self.wfile.flush()

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._chunk(b'data: {"type": "status", "message": "started"}\n\n')
            release.wait(timeout=PARK_SECONDS)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Park)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    release.set()
    server.shutdown()
    server.server_close()


def _park_relay_read(url, handler):
    """Open a relay stream on *handler* and drain it on a thread, like the producer."""
    proxy = EmailSidecarProxy(url)
    gen = proxy.query_stream(
        {"query": "park"},
        read_timeout=PARK_SECONDS,
        on_response=lambda r: setattr(handler, "active_relay_response", r),
    )
    first = threading.Event()
    done = threading.Event()

    def _drain():
        try:
            for _ in gen:
                first.set()
        except Exception:  # pylint: disable=broad-except
            pass  # The forced close surfaces as a read error; that is the point.
        finally:
            done.set()

    threading.Thread(target=_drain, daemon=True).start()
    assert first.wait(5.0), "relay never received the sidecar's first event"
    return done


@pytest.mark.allow_network
def test_run_manager_cancel_unblocks_a_parked_relay_read(silent_sidecar):
    handler = _RecordingHandler()
    run = Run("sess-cancel")
    run.handler = handler
    run_manager._runs["sess-cancel"] = run
    done = _park_relay_read(silent_sidecar, handler)

    t0 = time.monotonic()
    assert run_manager.cancel("sess-cancel") is True

    assert done.wait(PROMPT_SECONDS), "parked relay read survived the cancel"
    assert time.monotonic() - t0 < PROMPT_SECONDS
    assert handler.cancelled.is_set()
    assert handler.closed == 1


def test_cancel_without_relay_is_harmless():
    handler = _RecordingHandler()
    run = Run("sess-idle")
    run.handler = handler
    run_manager._runs["sess-idle"] = run

    assert run_manager.cancel("sess-idle") is True
    assert handler.cancelled.is_set()
    assert handler.closed == 1


def test_cancel_unknown_session_returns_false():
    assert run_manager.cancel("nope") is False


@pytest.mark.allow_network
def test_delete_mid_relay_turn_returns_promptly(silent_sidecar, tmp_path, monkeypatch):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    monkeypatch.setattr(sessions_router, "get_memory_store", lambda: store)
    db = ChatDatabase(str(tmp_path / "chat.db"))
    sid = db.create_session()["id"]
    handler = _RecordingHandler()
    run = Run(sid)
    run.handler = handler
    run_manager._runs[sid] = run
    done = _park_relay_read(silent_sidecar, handler)
    locks: dict = {}
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_locks=locks))
    )

    async def _scenario():
        holding = asyncio.Event()

        async def _turn():
            # The turn holds the chat's lock until its relay read returns.
            async with locks.setdefault(sid, asyncio.Lock()):
                holding.set()
                await asyncio.to_thread(done.wait, PARK_SECONDS)
            run_manager._runs.pop(sid, None)

        turn = asyncio.create_task(_turn())
        await holding.wait()
        t0 = time.monotonic()
        await asyncio.wait_for(
            sessions_router.delete_session(sid, request, db), PARK_SECONDS * 2
        )
        elapsed = time.monotonic() - t0
        await turn
        return elapsed

    try:
        elapsed = asyncio.run(_scenario())
    finally:
        store.close()
        db.close()

    assert elapsed < PROMPT_SECONDS, f"delete took {elapsed:.1f}s"
    assert handler.closed == 1
    assert db_session_gone(tmp_path, sid)


def db_session_gone(tmp_path, sid):
    check = ChatDatabase(str(tmp_path / "chat.db"))
    try:
        return check.get_session(sid) is None
    finally:
        check.close()


@pytest.mark.parametrize("route", ["delete", "chat_cancel", "run_manager"])
def test_every_cancel_route_closes_the_relay(route, tmp_path, monkeypatch):
    """The three cancel entry points must not drift apart again."""
    store = MemoryStore(db_path=tmp_path / "memory.db")
    monkeypatch.setattr(sessions_router, "get_memory_store", lambda: store)
    client = TestClient(create_app(db_path=str(tmp_path / "chat.db")))
    sid = client.post("/api/sessions", json={}).json()["id"]
    handler = _RecordingHandler()
    run = Run(sid)
    run.handler = handler
    run_manager._runs[sid] = run

    try:
        if route == "delete":
            assert client.delete(f"/api/sessions/{sid}").status_code == 200
        elif route == "chat_cancel":
            _active_sse_handlers[sid] = handler
            try:
                resp = client.post("/api/chat/cancel", json={"session_id": sid})
            finally:
                _active_sse_handlers.pop(sid, None)
            assert resp.status_code == 200
        else:
            assert run_manager.cancel(sid) is True
    finally:
        store.close()

    assert handler.cancelled.is_set()
    assert handler.closed == 1
