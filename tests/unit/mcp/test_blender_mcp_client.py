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
    # Bind a socket to grab a free port, then close it; subsequent
    # connect() to that port should reliably get ECONNREFUSED across
    # platforms and CI sandboxes.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    client = MCPClient(host="127.0.0.1", port=free_port)
    with pytest.raises(MCPError) as exc_info:
        client.send_command("clear_scene", timeout=2.0)
    assert "Connection refused" in str(exc_info.value)


class _ChunkedServer:
    """Like _PersistentServer but writes the response in two send() calls
    with a small delay, to exercise the client's incremental-parse loop."""

    def __init__(
        self, response: dict, split_after: int = 30, gap_seconds: float = 0.05
    ):
        self.response = response
        self.split_after = split_after
        self.gap_seconds = gap_seconds
        self.host = "127.0.0.1"
        self.port = 0
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

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
                    json.loads(buffer.decode("utf-8"))
                    break
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            # ensure_ascii=False so non-ASCII content lands on the wire as
            # raw multi-byte UTF-8, letting tests deliberately split the
            # payload mid-codepoint to exercise the client's
            # UnicodeDecodeError tolerance.
            payload = json.dumps(self.response, ensure_ascii=False).encode("utf-8")
            try:
                client.sendall(payload[: self.split_after])
                time.sleep(self.gap_seconds)
                client.sendall(payload[self.split_after :])
            except OSError:
                return
            # Hold the connection open like the real Blender addon.
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


def test_send_command_assembles_chunked_response():
    """The incremental-parse loop must reconstruct a JSON response that
    arrives across multiple recv() chunks (TCP segmentation)."""
    response_dict = {
        "status": "success",
        "result": {"object_count": 0, "message": "Scene cleared 🧼"},
    }
    # Split halfway through the 4-byte UTF-8 encoding of the emoji to
    # exercise the UnicodeDecodeError tolerance specifically. If we just
    # picked an arbitrary offset it would land in pure ASCII (the bulk of
    # the JSON) and never reach the multi-byte boundary, leaving that code
    # path uncovered.
    # Match the wire format _ChunkedServer will emit (ensure_ascii=False
    # keeps the emoji as raw 4-byte UTF-8 instead of escaping to \uXXXX).
    payload = json.dumps(response_dict, ensure_ascii=False).encode("utf-8")
    emoji_bytes = "🧼".encode("utf-8")
    assert len(emoji_bytes) == 4
    emoji_start = payload.index(emoji_bytes)
    mid_emoji = emoji_start + 2  # split inside the emoji, mid-codepoint

    server = _ChunkedServer(
        response=response_dict,
        split_after=mid_emoji,
        gap_seconds=0.05,
    )
    server.start()
    try:
        client = MCPClient(host=server.host, port=server.port)
        outcome: dict = {}

        def _call():
            try:
                outcome["response"] = client.send_command("clear_scene", timeout=5.0)
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc

        worker = threading.Thread(target=_call, daemon=True)
        worker.start()
        worker.join(timeout=5.0)

        if worker.is_alive():
            pytest.fail("send_command hung against a chunked-response server")
        if "error" in outcome:
            raise outcome["error"]

        response = outcome["response"]
        assert response["status"] == "success"
        # The emoji must have been reconstructed across the mid-codepoint
        # split — proves the UnicodeDecodeError tolerance is exercised.
        assert response["result"]["message"] == "Scene cleared 🧼"
    finally:
        server.stop()


def test_send_command_surfaces_enhanced_error_message():
    """When the server returns ``{"status": "error", "message": ...}``,
    the client must run the message through ``_enhance_error_message``
    before raising — so a bare NameError ("name 'foo' is not defined")
    becomes a more actionable message for the LLM/user."""
    server = _PersistentServer(
        response={
            "status": "error",
            "message": "name 'undefined_var' is not defined",
        }
    )
    server.start()
    try:
        client = MCPClient(host=server.host, port=server.port)
        outcome: dict = {}

        def _call():
            try:
                client.send_command("execute_code", {"code": "undefined_var + 1"})
            except BaseException as exc:  # noqa: BLE001
                outcome["error"] = exc

        worker = threading.Thread(target=_call, daemon=True)
        worker.start()
        worker.join(timeout=5.0)

        if worker.is_alive():
            pytest.fail("send_command hung against an error-response server")
        assert "error" in outcome, "expected MCPError, got nothing"
        err = outcome["error"]
        assert isinstance(err, MCPError)
        # _enhance_error_message rewrites NameError-shaped messages
        # into a friendlier, actionable form.
        assert "undefined_var" in str(err)
        assert "Make sure to declare it before use" in str(err)
    finally:
        server.stop()


def test_send_command_raises_on_premature_close():
    """If the server closes before sending a full JSON response, the
    client must surface a clear MCPError rather than returning garbage."""

    class _PrematureCloseServer:
        def __init__(self):
            self.host = "127.0.0.1"
            self.port = 0
            self._sock = None
            self._thread = None

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
            try:
                # Send a fragment that cannot parse as JSON, then close.
                client.sendall(b'{"status": "succ')
                client.close()
            except OSError:
                pass

        def stop(self):
            try:
                self._sock.close()
            except OSError:
                pass

    server = _PrematureCloseServer()
    server.start()
    try:
        client = MCPClient(host=server.host, port=server.port)
        with pytest.raises(MCPError) as exc_info:
            client.send_command("clear_scene", timeout=3.0)
        assert "Connection closed" in str(exc_info.value)
    finally:
        server.stop()
