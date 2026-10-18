# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A hung or chatty MCP stdio server must not freeze the agent's turn.

Each test runs a real stub server (a tiny Python script) so the pipes,
blocking reads, and process lifecycle are the real ones.
"""

import sys
import textwrap
import threading
import time

import pytest

from gaia.mcp.client.transports.stdio import StdioTransport
from gaia.tool_cancellation import ToolCancelled, set_tool_cancel_event

# Every stub reads JSON-RPC lines from stdin and acts per request.
_PRELUDE = textwrap.dedent("""
    import json, sys, time
    def send(msg):
        sys.stdout.write(json.dumps(msg) + "\\n")
        sys.stdout.flush()
    for line in sys.stdin:
        req = json.loads(line)
        if "id" not in req:  # a notification, e.g. notifications/cancelled
            continue
    """)


def _stub(tmp_path, body):
    script = tmp_path / "stub_server.py"
    script.write_text(_PRELUDE + textwrap.indent(textwrap.dedent(body), "    "))
    return script


def _transport(script, timeout=30):
    t = StdioTransport(sys.executable, args=["-u", str(script)], timeout=timeout)
    assert t.connect()
    return t


def _call_with_watchdog(fn, limit=10.0):
    """Run *fn* in a thread so a regression fails the test instead of hanging it."""
    box = {}

    def run():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 — inspected by the test
            box["exc"] = exc

    started = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(limit)
    assert not worker.is_alive(), f"send_request still blocked after {limit}s"
    return box, time.monotonic() - started


def test_server_that_never_replies_times_out_with_its_name(tmp_path):
    script = _stub(tmp_path, "time.sleep(3600)")
    transport = _transport(script, timeout=1)
    try:
        box, elapsed = _call_with_watchdog(lambda: transport.send_request("tools/list"))
    finally:
        transport.disconnect()

    assert isinstance(box.get("exc"), TimeoutError), box
    assert elapsed < 5
    message = str(box["exc"])
    assert "python" in message and "stub_server.py" in message
    assert "tools/list" in message
    assert "1s" in message


def test_notification_before_the_reply_is_skipped(tmp_path):
    script = _stub(
        tmp_path,
        """
        send({"jsonrpc": "2.0", "method": "notifications/message",
              "params": {"level": "info", "data": "warming up"}})
        send({"jsonrpc": "2.0", "id": req["id"], "result": {"tools": []}})
        """,
    )
    transport = _transport(script)
    try:
        box, _ = _call_with_watchdog(lambda: transport.send_request("tools/list"))
    finally:
        transport.disconnect()

    assert box.get("result") == {"jsonrpc": "2.0", "id": 0, "result": {"tools": []}}


def test_late_reply_to_a_timed_out_request_is_not_taken_as_the_next_answer(
    tmp_path,
):
    script = _stub(
        tmp_path,
        """
        if req["method"] == "slow":
            time.sleep(1.5)
        send({"jsonrpc": "2.0", "id": req["id"], "result": {"for": req["method"]}})
        """,
    )
    transport = _transport(script, timeout=0.5)
    try:
        with pytest.raises(TimeoutError):
            transport.send_request("slow")
        transport.timeout = 5
        box, _ = _call_with_watchdog(lambda: transport.send_request("fast"))
    finally:
        transport.disconnect()

    assert box.get("result", {}).get("result") == {"for": "fast"}


def test_server_request_gets_an_error_reply_instead_of_deadlocking(tmp_path):
    script = _stub(
        tmp_path,
        """
        if req.get("method") == "tools/list":
            send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list"})
            answer = json.loads(sys.stdin.readline())
            send({"jsonrpc": "2.0", "id": req["id"],
                  "result": {"client_said": answer.get("error", {}).get("code")}})
        """,
    )
    transport = _transport(script)
    try:
        box, _ = _call_with_watchdog(lambda: transport.send_request("tools/list"))
    finally:
        transport.disconnect()

    assert box.get("result", {}).get("result") == {"client_said": -32601}


def test_a_server_flooding_stderr_still_gets_its_reply_read(tmp_path):
    # An undrained stderr pipe fills (~64 KB) and blocks the server mid-write.
    script = _stub(
        tmp_path,
        """
        sys.stderr.write("x" * 1_000_000)
        sys.stderr.flush()
        send({"jsonrpc": "2.0", "id": req["id"], "result": {}})
        """,
    )
    transport = _transport(script)
    try:
        box, _ = _call_with_watchdog(lambda: transport.send_request("tools/list"))
    finally:
        transport.disconnect()

    assert box.get("result") == {"jsonrpc": "2.0", "id": 0, "result": {}}


def test_cancelled_tool_call_stops_waiting_on_a_hung_server(tmp_path):
    script = _stub(tmp_path, "time.sleep(3600)")
    transport = _transport(script, timeout=60)
    cancel = threading.Event()

    def call():
        set_tool_cancel_event(cancel)
        try:
            return transport.send_request("tools/call", {"name": "x"})
        finally:
            set_tool_cancel_event(None)

    threading.Timer(0.5, cancel.set).start()
    try:
        box, elapsed = _call_with_watchdog(call)
    finally:
        transport.disconnect()

    assert isinstance(box.get("exc"), ToolCancelled), box
    assert elapsed < 5


def test_server_exit_mid_request_reports_its_stderr(tmp_path):
    script = _stub(
        tmp_path,
        """
        sys.stderr.write("fatal: token expired\\n")
        sys.exit(3)
        """,
    )
    transport = _transport(script)
    try:
        box, _ = _call_with_watchdog(lambda: transport.send_request("tools/list"))
    finally:
        transport.disconnect()

    assert isinstance(box.get("exc"), RuntimeError), box
    assert "exit code: 3" in str(box["exc"])
    assert "token expired" in str(box["exc"])


def test_a_parse_error_without_an_id_is_reported_not_waited_out(tmp_path):
    script = _stub(
        tmp_path,
        """
        send({"jsonrpc": "2.0", "id": None,
              "error": {"code": -32700, "message": "Parse error"}})
        """,
    )
    transport = _transport(script, timeout=30)
    try:
        box, elapsed = _call_with_watchdog(lambda: transport.send_request("tools/list"))
    finally:
        transport.disconnect()

    assert isinstance(box.get("exc"), RuntimeError), box
    assert "Parse error" in str(box["exc"])
    assert elapsed < 5


def test_a_timed_out_request_is_cancelled_on_the_server(tmp_path):
    log = tmp_path / "seen.jsonl"
    # Logs every line it reads, notifications included, and never replies.
    script = tmp_path / "silent_logger.py"
    script.write_text(textwrap.dedent(f"""
            import sys
            for line in sys.stdin:
                with open({str(log)!r}, "a") as f:
                    f.write(line)
            """))
    transport = _transport(script, timeout=0.5)
    try:
        with pytest.raises(TimeoutError):
            transport.send_request("slow")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "cancelled" not in log.read_text():
            time.sleep(0.05)
    finally:
        transport.disconnect()

    import json as _json

    seen = [_json.loads(x) for x in log.read_text().splitlines()]
    assert seen[-1]["method"] == "notifications/cancelled"
    assert seen[-1]["params"]["requestId"] == seen[0]["id"]


def test_tool_calls_get_the_agents_tool_budget_not_the_handshake_default():
    t = StdioTransport("npx", args=["-y", "@scope/server"])
    # No process: only the budget arithmetic is under test.
    from gaia.mcp.client.transports import stdio

    assert t.timeout < stdio.TOOL_CALL_TIMEOUT_S
    assert stdio.TOOL_CALL_TIMEOUT_S == 180


def test_disconnect_mid_request_reports_the_disconnect(tmp_path):
    script = _stub(tmp_path, "time.sleep(3600)")
    transport = _transport(script, timeout=30)
    threading.Timer(0.5, transport.disconnect).start()

    box, elapsed = _call_with_watchdog(lambda: transport.send_request("tools/list"))

    assert isinstance(box.get("exc"), RuntimeError), box
    assert "disconnected" in str(box["exc"]) or "died" in str(box["exc"])
    assert elapsed < 10


def test_the_error_label_names_the_server_without_its_arguments():
    t = StdioTransport(
        "C:/tools/npx.cmd", args=["-y", "@scope/server", "--token", "s3cret"]
    )
    assert t.label == "npx.cmd @scope/server"
    legacy = StdioTransport("uvx my-server --api-key=s3cret")
    assert "s3cret" not in legacy.label
