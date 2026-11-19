# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A client that resets its connection must not leave a GAIA server deaf.

On Windows a connection reset while it waits in the listen backlog fails the
next accept with WinError 64, and asyncio's proactor loop answers by closing
the listening socket: the process lives on, but nothing can connect to it.
These tests use real sockets against real servers, because the failure lives
in the socket layer a mock replaces.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from gaia.listener_guard import (
    keep_listening_after_client_resets,
    listener_is_guarded,
)

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "win32", reason="the proactor accept loop is Windows-only"
    ),
    pytest.mark.allow_network,
]

_RESETS = 8
_LOOP_BUSY_S = 0.4


def _reset_queued_connections(loop: asyncio.AbstractEventLoop, port: int) -> None:
    """Reset connections that are still waiting for the server to accept them."""
    busy = threading.Event()

    def stay_busy():
        busy.set()
        time.sleep(_LOOP_BUSY_S)

    loop.call_soon_threadsafe(stay_busy)
    assert busy.wait(5), "the server's event loop never ran"
    # The first connection takes the accept already armed; the rest queue.
    first = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        for _ in range(_RESETS):
            queued = socket.create_connection(("127.0.0.1", port), timeout=5)
            queued.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            queued.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0)
            )
            queued.close()
    finally:
        first.close()
    time.sleep(_LOOP_BUSY_S + 0.3)


class _Hello(asyncio.Protocol):
    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self.transport.write(b"hello")
        self.transport.close()


class _BareServer:
    """A plain asyncio server on its own proactor loop, in a thread."""

    def __init__(self, guarded: bool):
        self.loop = asyncio.ProactorEventLoop()
        self.errors: list = []
        self.loop.set_exception_handler(lambda _loop, ctx: self.errors.append(ctx))
        if guarded:
            assert keep_listening_after_client_resets(self.loop) is True
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(5)

    def _run(self):
        async def serve():
            self.server = await self.loop.create_server(_Hello, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
            self._ready.set()

        self.loop.run_until_complete(serve())
        self.loop.run_forever()

    def ask(self) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(b"hi")
            return s.recv(16)

    def close(self):
        self.loop.call_soon_threadsafe(self.server.close)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)
        self.loop.close()


@pytest.fixture
def bare_server():
    servers = []

    def start(guarded: bool) -> _BareServer:
        servers.append(_BareServer(guarded))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def test_a_guarded_listener_still_answers_after_queued_connections_are_reset(
    bare_server,
):
    server = bare_server(guarded=True)
    assert server.ask() == b"hello"

    _reset_queued_connections(server.loop, server.port)

    assert server.ask() == b"hello"
    assert server.server.sockets, "the listening socket was closed"
    assert server.errors == []


def test_the_reset_this_guards_against_is_what_closes_an_unguarded_listener(
    bare_server,
):
    """Pins the repro: without the guard the same resets leave nothing listening."""
    server = bare_server(guarded=False)

    _reset_queued_connections(server.loop, server.port)

    messages = [ctx.get("message") for ctx in server.errors]
    if "Accept failed on a socket" not in messages:
        pytest.skip("this Python no longer closes the listener on a reset accept")
    with pytest.raises(OSError):
        server.ask()


def test_an_accept_failure_that_is_not_a_reset_still_fails_the_accept():
    loop = asyncio.ProactorEventLoop()
    try:
        calls = []

        def failing_accept(listener):
            calls.append(listener)
            future = loop.create_future()
            future.set_exception(OSError(24, "Too many open files"))
            return future

        loop._proactor.accept = failing_accept
        keep_listening_after_client_resets(loop)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            future = loop._proactor.accept(listener)
            loop.run_until_complete(asyncio.sleep(0))
            with pytest.raises(OSError, match="Too many open files"):
                future.result()
        assert len(calls) == 1, "a failure the guard does not own was retried"
    finally:
        loop.close()


def test_a_selector_loop_needs_no_guard():
    loop = asyncio.SelectorEventLoop()
    try:
        assert keep_listening_after_client_resets(loop) is False
        assert listener_is_guarded(loop) is False
    finally:
        loop.close()


def test_arming_twice_wraps_accept_once():
    loop = asyncio.ProactorEventLoop()
    try:
        keep_listening_after_client_resets(loop)
        armed = loop._proactor.accept
        keep_listening_after_client_resets(loop)
        assert loop._proactor.accept is armed
    finally:
        loop.close()


# ── The servers GAIA ships ─────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, f"test server never started on port {port}"
    return server, thread, port


def _assert_survives_resets(app, path: str) -> None:
    server, thread, port = _serve(app)
    try:
        loop = server.servers[0].get_loop()
        assert listener_is_guarded(loop), "the app's startup did not arm the guard"
        _reset_queued_connections(loop, port)
        # Any HTTP answer proves a new connection was accepted.
        response = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=10)
        assert response.status_code < 500
    finally:
        server.should_exit = True
        thread.join(15)


def test_the_agent_ui_backend_survives_reset_connections():
    from gaia.ui.server import create_app

    _assert_survives_resets(create_app(db_path=":memory:"), "/api/health")


def test_the_daemon_survives_reset_connections():
    from gaia.daemon.app import create_app

    app = create_app(token="tok-reset", port=1, pid=1, started_at=0.0)
    _assert_survives_resets(app, "/daemon/v1/status")


def test_the_agent_sidecar_survives_reset_connections(monkeypatch):
    pytest.importorskip("gaia_agent")
    from gaia_agent import caller_auth
    from gaia_agent import server as server_mod

    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)
    try:
        _assert_survives_resets(server_mod.build_app(), "/health")
    finally:
        caller_auth.reset()
