# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Socket-level tests for the Blender MCP client.

Uses a fake persistent-connection server (no real Blender required) to
verify that ``send_command`` does not deadlock against a server that
keeps the connection open after responding — matching the real Blender
addon's behaviour.

Regression test for issue #1022: the client looped on ``recv()`` until
the server sent FIN, but the Blender addon never closes the connection
on its own, leading to a mutual-recv deadlock.
"""

import json
import socket
import threading
import time

import pytest

from gaia.mcp.blender_mcp_client import MCPClient, MCPError


class _PersistentServer:
    """Fake server mimicking the Blender addon's connection model.

    Accepts a single connection, reads one JSON command, sends a JSON
    response via ``sendall``, and then *keeps the socket open* — the
    same behaviour as ``SimpleBlenderMCPServer._handle_client`` in
    ``src/gaia/mcp/blender_mcp_server.py``. A correctly-implemented
    client must therefore stop reading as soon as it has parsed a
    complete JSON response, rather than waiting for FIN.
    """

    def __init__(self, response: dict):
        self.response = response
        self.host = "127.0.0.1"
        self.port = 0
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self.received_command = None

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._sock.settimeout(2.0)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            client, _ = self._sock.accept()
        except socket.timeout:
            return
        client.settimeout(2.0)
        try:
            buffer = b""
            while not self._stop.is_set():
                try:
                    chunk = client.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                try:
                    self.received_command = json.loads(buffer.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue
            try:
                client.sendall(json.dumps(self.response).encode("utf-8"))
            except OSError:
                return
            # Crucially: do NOT close. Hold the socket open exactly like
            # the real Blender addon, which is waiting for the next
            # command on the same connection.
            while not self._stop.is_set():
                time.sleep(0.05)
        finally:
            try:
                client.close()
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=1.0)


@pytest.fixture
def persistent_server():
    server = _PersistentServer(
        response={
            "status": "success",
            "result": {"object_count": 0, "message": "Scene cleared successfully"},
        }
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_send_command_does_not_hang_with_persistent_server(persistent_server):
    """Regression test for #1022.

    The client must not loop on ``recv()`` waiting for a FIN that the
    server never sends. We run the call inside a *daemon* thread so the
    test fails fast (rather than hanging the suite) if the deadlock
    comes back — daemon threads are abandoned when pytest exits, even
    if blocked on ``recv()``.
    """
    client = MCPClient(host=persistent_server.host, port=persistent_server.port)
    outcome: dict = {}

    def _call():
        try:
            outcome["response"] = client.send_command("clear_scene")
        except BaseException as exc:  # noqa: BLE001 — propagate to assertion
            outcome["error"] = exc

    worker = threading.Thread(target=_call, daemon=True)
    worker.start()
    worker.join(timeout=5.0)

    if worker.is_alive():
        pytest.fail(
            "send_command hung against a persistent-connection server. "
            "This is the #1022 regression — the client is waiting for "
            "the server to send FIN, but the Blender addon keeps its "
            "side of the connection open."
        )
    if "error" in outcome:
        raise outcome["error"]

    response = outcome["response"]
    assert response["status"] == "success"
    assert response["result"]["message"] == "Scene cleared successfully"
    assert persistent_server.received_command == {
        "type": "clear_scene",
        "params": {},
    }


def test_send_command_raises_on_connection_refused():
    """Connection-refused errors must surface as actionable MCPError."""
    client = MCPClient(host="127.0.0.1", port=1)  # almost certainly nothing here
    with pytest.raises(MCPError) as exc_info:
        client.send_command("clear_scene")
    assert "Connection refused" in str(exc_info.value)
