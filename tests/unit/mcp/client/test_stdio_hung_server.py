# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A hung or chatty MCP stdio server must not freeze the agent's turn.

Each test runs a real stub server (a tiny Python script) so the pipes,
blocking reads, and process lifecycle are the real ones.
"""

import json
import os
import re
import sys
import textwrap
import threading
import time

import pytest

from gaia.mcp.client.transports import stdio as stdio_module
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


@pytest.fixture(autouse=True)
def _no_stub_outlives_its_test():
    """A failed assertion can skip disconnect(); stop what this test started."""
    psutil = pytest.importorskip("psutil")
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    yield
    leftover = [
        p for p in psutil.Process().children(recursive=True) if p.pid not in before
    ]
    for proc in leftover:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue  # it exited on its own
    psutil.wait_procs(leftover, timeout=5)


def _stub(tmp_path, body):
    script = tmp_path / "stub_server.py"
    script.write_text(_PRELUDE + textwrap.indent(textwrap.dedent(body), "    "))
    return script


def _transport(script, timeout=30):
    t = StdioTransport(sys.executable, args=["-u", str(script)], timeout=timeout)
    t.name = "stub"
    assert t.connect()
    return t


def _written(path):
    """Whether a stub has finished writing *path* (it can exist while empty)."""
    return path.exists() and path.read_text().strip() != ""


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
    assert "stub (python" in message
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


def test_tool_calls_end_just_inside_the_agents_tool_limit(monkeypatch):
    from gaia.mcp.client.transports import stdio

    monkeypatch.delenv("GAIA_AGENT_TOOL_TIMEOUT", raising=False)
    assert stdio.tool_call_timeout() == 175
    # The user's own limit wins, and the transport still ends first.
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "600")
    assert stdio.tool_call_timeout() == 595


def test_disconnect_mid_request_reports_the_disconnect(tmp_path):
    script = _stub(tmp_path, "time.sleep(3600)")
    transport = _transport(script, timeout=30)
    threading.Timer(0.5, transport.disconnect).start()

    box, elapsed = _call_with_watchdog(lambda: transport.send_request("tools/list"))

    assert isinstance(box.get("exc"), RuntimeError), box
    assert "disconnected" in str(box["exc"]) or "died" in str(box["exc"])
    assert elapsed < 10


def test_the_error_label_names_the_server_by_name_and_executable():
    t = StdioTransport(sys.executable, args=["-u", "server.py"])
    t.name = "github"
    assert t.label == f"github ({os.path.basename(sys.executable)})"
    # A program that doesn't resolve is never echoed back.
    assert StdioTransport("C:/tools/npx.cmd", args=["x"]).label == "?"


def test_the_label_keeps_a_quoted_program_path_with_spaces(tmp_path):
    folder = tmp_path / "Program Files"
    folder.mkdir()
    program = folder / "node.exe"
    program.write_bytes(b"")
    assert StdioTransport(f'"{program}" server.js').label == "node.exe"
    assert StdioTransport(str(program), args=["s.js"]).label == "node.exe"


# Secrets in every shape a config can carry them: with / and \, =, spaces.
_SECRETS = ["Zq9vKe7wP2", "Zq9v/Ke7w\\P2", "Zq9v=Ke7w", "Zq9v Ke7w/P2"]


def _grams(secret, n=4):
    return {
        secret[i : i + n]
        for i in range(len(secret) - n + 1)
        if " " not in secret[i : i + n]
    }


def _configs(secret, exe, script):
    quoted = '"' + secret + '"'
    yield {"command": exe, "args": ["-u", script, "--api-key", secret]}
    yield {"command": exe, "args": ["-u", script, f"--token={secret}"]}
    yield {"command": exe, "args": ["-u", script], "env": {"TOKEN": secret}}
    yield {"command": f"API_KEY={quoted} {exe} -u {script}", "args": None}
    yield {"command": f"{exe} --api-key {quoted}", "args": ["srv"]}
    yield {"command": secret, "args": None}
    yield {"command": secret, "args": ["srv"]}
    yield {"command": f"{quoted} --flag", "args": None}
    yield {"command": f"{exe} -u {script} --api-key {quoted}", "args": None}
    # Rejected before launch: shell syntax, and an unbalanced quote.
    yield {"command": f"{exe} --api-key {quoted} ; echo", "args": None}
    yield {"command": f"{exe} --api-key '{secret}", "args": None}


# How the stub echoes its command line and env back. Each mode puts the
# secret across the point where GAIA cuts what it keeps.
_ECHO_STUB = r"""
import json, os, sys
argv = sys.argv[1:]
mode = argv[argv.index("--mode") + 1] if "--mode" in argv else "die"
echo = " ".join(sys.argv) + " " + os.environ.get("TOKEN", "")
# The token most likely to be the secret, to place across the cut.
skip = {"--mode", mode, "-u", sys.argv[0]}
tok = max([a for a in argv if a not in skip] + [os.environ.get("TOKEN", "")], key=len)

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

if mode == "die":  # stderr's kept tail is 2,000 characters
    sys.stderr.write(echo + "x" * 3000 + tok + "y" * (2000 - len(tok) // 2))
    sys.exit(2)
for line in sys.stdin:
    req = json.loads(line)
    if "id" not in req:
        continue
    if mode == "stdout":  # a non-JSON line is cut to 200 characters
        sys.stdout.write("z" * (200 - len(tok) // 2) + tok + " " + echo + "\n")
        sys.stdout.flush()
    elif mode == "reject":  # the server's own error reply to initialize
        send({"jsonrpc": "2.0", "id": req["id"],
              "error": {"code": -32603, "message": echo}})
    elif req["method"] == "initialize":  # echoed in metadata GAIA logs
        send({"jsonrpc": "2.0", "method": echo})
        send({"jsonrpc": "2.0", "id": echo, "result": {}})
        send({"jsonrpc": "2.0", "id": req["id"],
              "result": {"serverInfo": {"name": echo}, "note": echo}})
    elif mode == "result":  # a successful result, echoing as value and key
        send({"jsonrpc": "2.0", "id": req["id"],
              "result": {"content": [{"type": "text", "text": echo}], echo: echo}})
    else:  # tools/list or tools/call: a structured, non-string error
        send({"jsonrpc": "2.0", "id": req["id"],
              "error": {"code": -32603, "message": {"detail": echo}}})
"""


@pytest.mark.parametrize("mode", ["die", "stdout", "reject", "list", "call", "result"])
def test_no_part_of_a_secret_reaches_a_label_log_or_error(tmp_path, caplog, mode):
    """Property: no 4-character run of a secret ever reaches the server's
    label, GAIA's logs (at DEBUG, with debug on), an error, last_error, or a
    tool result, when the secret sits where config secrets sit: an env
    value, a KEY=VALUE, the value of a credential flag, or the program.

    The stub echoes its whole command line and env back the way a server
    that rejects its options does: on stderr as it dies, as a stray stdout
    line, in an error reply, in metadata, and in a structured tool error.
    """
    import logging

    from gaia.mcp.client.mcp_client import MCPClient

    # GAIA pins these loggers at INFO; the debug lines are under test too.
    for name in ("gaia.mcp.client.transports.stdio", "gaia.mcp.client.mcp_client"):
        caplog.set_level(logging.DEBUG, logger=name)
    script = tmp_path / "echo.py"
    script.write_text(_ECHO_STUB)
    exe = sys.executable.replace("\\", "/")
    checked = 0
    debug_lines = 0
    for secret in _SECRETS:
        for config in _configs(secret, exe, str(script).replace("\\", "/")):
            config = dict(config)
            if config["args"] is not None and config["command"] == exe:
                config["args"] = [*config["args"], "--mode", mode]
            elif config["args"] is None and config["command"].endswith(
                str(script).replace("\\", "/")
            ):
                config["command"] += f" --mode {mode}"
            caplog.clear()
            seen = []
            try:
                client = MCPClient.from_config(
                    "leaky",
                    {k: v for k, v in config.items() if v is not None},
                    timeout=5,
                    debug=True,
                )
                seen.append(client.transport.label)
                try:
                    if client.connect():
                        if mode == "list":
                            seen.append(repr(client.list_tools()))
                        elif mode in ("call", "result"):
                            # The result itself is the tool's; its debug log is GAIA's.
                            result = client.call_tool("x", {})
                            if mode == "call":
                                seen.append(repr(result))
                    seen.append(str(client.last_error))
                finally:
                    client.disconnect()
            except ValueError as e:  # an unparseable command line
                seen.append(str(e))
            seen.extend(r.getMessage() for r in caplog.records)
            debug_lines += sum(r.levelno == logging.DEBUG for r in caplog.records)
            text = "\n".join(seen)
            leaked = [g for g in _grams(secret) if g in text]
            assert not leaked, (config, leaked, text[:800])
            checked += 1
    assert checked == len(_SECRETS) * 11
    assert debug_lines, "no DEBUG record was captured, so debug logs went unchecked"


def test_ordinary_words_in_a_server_error_stay_readable(tmp_path):
    """Only secret values are removed; the rest of an error must read as sent."""
    from gaia.mcp.client.mcp_client import MCPClient

    script = tmp_path / "shipping_server.py"
    script.write_text(
        _PRELUDE
        + textwrap.indent(
            textwrap.dedent("""
        if req["method"] == "initialize":
            send({"jsonrpc": "2.0", "id": req["id"], "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": req["id"], "error": {
                "code": -32000,
                "message": "shipping lookup failed: python --stdio server"}})
        """),
            "    ",
        )
    )
    client = MCPClient.from_config(
        "ship",
        {
            "command": sys.executable,
            "args": ["-u", str(script), "--stdio", "--api-key", "Zq9vKe7wP2"],
        },
    )
    try:
        assert client.connect(), client.last_error
        result = client.call_tool("track_shipment", {})
    finally:
        client.disconnect()

    assert result == {"error": "shipping lookup failed: python --stdio server"}


def test_a_missing_program_named_in_the_os_error_is_not_logged(monkeypatch, caplog):
    import logging

    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "/opt/Zq9vKe7wP2/srv")

    monkeypatch.setattr(stdio_module.subprocess, "Popen", missing)
    caplog.set_level(logging.DEBUG)

    assert StdioTransport("/opt/Zq9vKe7wP2/srv", args=["x"]).connect() is False
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "FileNotFoundError" in logged
    assert "Zq9v" not in logged


def test_a_server_that_stops_reading_cannot_block_the_write(tmp_path):
    # Never reads stdin, so a request bigger than the pipe buffer can't land.
    script = _stub(tmp_path, "pass")
    script.write_text("import time\ntime.sleep(3600)\n")
    transport = _transport(script, timeout=2)
    try:
        box, elapsed = _call_with_watchdog(
            lambda: transport.send_request("big", {"blob": "x" * 2_000_000}),
            limit=15,
        )
    finally:
        transport.disconnect()

    assert isinstance(box.get("exc"), TimeoutError), box
    assert "stopped reading" in str(box["exc"])
    assert elapsed < 8
    assert not transport.is_connected()


def test_stop_ends_a_wait_behind_another_request(tmp_path):
    script = _stub(tmp_path, "time.sleep(3600)")
    transport = _transport(script, timeout=60)
    first = threading.Thread(
        target=lambda: _call_with_watchdog(
            lambda: transport.send_request("tools/list"), limit=20
        ),
        daemon=True,
    )
    first.start()
    time.sleep(0.5)  # the first request now holds the pipe
    cancel = threading.Event()

    def second():
        set_tool_cancel_event(cancel)
        try:
            return transport.send_request("tools/call", {"name": "x"})
        finally:
            set_tool_cancel_event(None)

    threading.Timer(0.5, cancel.set).start()
    try:
        box, elapsed = _call_with_watchdog(second)
    finally:
        transport.disconnect()

    assert isinstance(box.get("exc"), ToolCancelled), box
    assert elapsed < 5


def test_disconnect_stops_the_servers_children(tmp_path):
    psutil = pytest.importorskip("psutil")
    child_pid_file = tmp_path / "child.pid"
    script = tmp_path / "parent_server.py"
    # Like npx: the process we start runs the real server as a child.
    script.write_text(textwrap.dedent(f"""
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
            open({str(child_pid_file)!r}, "w").write(str(child.pid))
            time.sleep(3600)
            """))
    transport = _transport(script)
    deadline = time.monotonic() + 10
    while not _written(child_pid_file) and time.monotonic() < deadline:
        time.sleep(0.05)
    child_pid = int(child_pid_file.read_text())
    assert psutil.pid_exists(child_pid)

    transport.disconnect()

    deadline = time.monotonic() + 10
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid), "the server's child outlived disconnect"


def test_a_server_that_misses_the_connect_deadline_is_stopped(tmp_path, monkeypatch):
    from gaia.mcp.client import mcp_client_manager as mgr_mod

    psutil = pytest.importorskip("psutil")
    pid_file = tmp_path / "server.pid"
    script = tmp_path / "never_initializes.py"
    script.write_text(textwrap.dedent(f"""
            import os, time
            open({str(pid_file)!r}, "w").write(str(os.getpid()))
            time.sleep(3600)
            """))
    monkeypatch.setattr(mgr_mod, "_CONNECT_TIMEOUT_S", 1.0, raising=False)

    class _Config:
        def get_servers(self):
            return {"stuck": {"command": sys.executable, "args": ["-u", str(script)]}}

    manager = mgr_mod.MCPClientManager(config=_Config())
    manager.load_from_config()

    assert "timed out" in manager._failed["stuck"]
    deadline = time.monotonic() + 10
    while not _written(pid_file) and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid), "a server that missed the deadline kept running"


def test_tools_call_waits_for_the_agents_budget_not_the_default(tmp_path, monkeypatch):
    # 7 s agent limit less the 5 s margin: tools/call gets 2 s, others 0.5 s.
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "7")
    script = _stub(tmp_path, "time.sleep(3600)")
    transport = _transport(script, timeout=0.5)
    try:
        listed, listed_s = _call_with_watchdog(lambda: transport.send_request("ping"))
        called, called_s = _call_with_watchdog(
            lambda: transport.send_request("tools/call", {"name": "x"})
        )
    finally:
        transport.disconnect()

    assert isinstance(listed.get("exc"), TimeoutError)
    assert isinstance(called.get("exc"), TimeoutError)
    assert "within 2s" in str(called["exc"])
    assert listed_s < 1.5 and called_s >= 1.9


def test_children_of_an_exited_server_are_still_stopped(tmp_path):
    psutil = pytest.importorskip("psutil")
    child_pid_file = tmp_path / "child.pid"
    script = tmp_path / "parent_exits.py"
    # The launcher starts the real server, then exits on a signal from stdin.
    script.write_text(textwrap.dedent(f"""
            import subprocess, sys
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
            open({str(child_pid_file)!r}, "w").write(str(child.pid))
            sys.stdin.readline()
            """))
    transport = _transport(script)
    deadline = time.monotonic() + 10
    while not _written(child_pid_file) and time.monotonic() < deadline:
        time.sleep(0.05)
    child_pid = int(child_pid_file.read_text())
    # Children are recorded while the launcher runs; a real npx outlives this.
    time.sleep(4 * stdio_module._CHILD_SCAN_S)
    transport._process.stdin.write("bye\n")
    transport._process.stdin.flush()
    transport._process.wait(timeout=10)
    assert psutil.pid_exists(child_pid)

    transport.disconnect()

    deadline = time.monotonic() + 10
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid), "an exited server's child kept running"


def test_a_server_that_refuses_to_initialise_is_stopped(tmp_path):
    from gaia.mcp.client.mcp_client import MCPClient

    script = _stub(
        tmp_path,
        """
        send({"jsonrpc": "2.0", "id": req["id"],
              "error": {"code": -32603, "message": "bad config"}})
        """,
    )
    client = MCPClient.from_config(
        "refuses", {"command": sys.executable, "args": ["-u", str(script)]}
    )

    assert client.connect() is False
    assert "bad config" in client.last_error
    assert not client.transport.is_connected(), "a refused server kept running"


def test_disconnect_never_stops_a_process_it_did_not_see_the_server_start(
    tmp_path, monkeypatch
):
    """A process whose parent PID matches the exited server's is not ours.

    Windows reuses PIDs, so after the server exits that PID can belong to an
    unrelated program, and so can its children.
    """
    psutil = pytest.importorskip("psutil")
    script = tmp_path / "exits.py"
    script.write_text("import sys\nsys.stdin.readline()\n")
    transport = _transport(script)
    server_pid = transport._process.pid
    transport._process.stdin.write("bye\n")
    transport._process.stdin.flush()
    transport._process.wait(timeout=10)

    bystander = psutil.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    real_iter = psutil.process_iter

    def iter_with_reused_ppid(*args, **kwargs):
        for proc in real_iter(*args, **kwargs):
            if proc.pid == bystander.pid:
                proc.info = {"ppid": server_pid, "create_time": time.time()}
            yield proc

    monkeypatch.setattr(psutil, "process_iter", iter_with_reused_ppid)
    try:
        transport.disconnect()
        assert bystander.is_running(), "disconnect stopped a stranger's process"
    finally:
        if bystander.is_running():
            bystander.kill()
            bystander.wait(5)


def test_the_child_watcher_backs_off_once_the_tree_settles(tmp_path, monkeypatch):
    script = _stub(tmp_path, "time.sleep(3600)")
    sleeps = []
    real_sleep = time.sleep
    monkeypatch.setattr(
        stdio_module.time,
        "sleep",
        lambda s: (
            (
                sleeps.append(s)
                if threading.current_thread().name.startswith("mcp-children")
                else None
            ),
            real_sleep(min(s, 0.05)),
        ),
    )
    transport = _transport(script)
    try:
        real_sleep(1.5)  # many watcher rounds, each cut to 0.05 s
    finally:
        transport.disconnect()

    assert sleeps, "the watcher never ran"
    assert max(sleeps) > stdio_module._CHILD_SCAN_S
    assert max(sleeps) <= stdio_module._CHILD_SCAN_MAX_S


def test_a_server_ping_gets_an_empty_result(tmp_path):
    script = _stub(
        tmp_path,
        """
        if req.get("method") == "tools/list":
            send({"jsonrpc": "2.0", "id": "srv-ping", "method": "ping"})
            answer = json.loads(sys.stdin.readline())
            send({"jsonrpc": "2.0", "id": req["id"], "result": {"pong": answer}})
        """,
    )
    transport = _transport(script)
    try:
        box, _ = _call_with_watchdog(lambda: transport.send_request("tools/list"))
    finally:
        transport.disconnect()

    assert box["result"]["result"]["pong"] == {
        "jsonrpc": "2.0",
        "id": "srv-ping",
        "result": {},
    }


class _FakeClient:
    """An MCPClient stand-in whose connect() takes as long as a test needs."""

    def __init__(self, name, connect_s=0.0, connects=True):
        self.name = name
        self.connect_s = connect_s
        self.connects = connects
        self.last_error = None
        self.disconnected = threading.Event()

    def connect(self):
        time.sleep(self.connect_s)
        return self.connects

    def disconnect(self):
        self.disconnected.set()


class _OneServer:
    def get_servers(self):
        return {"srv": {"command": "unused"}}


def test_a_connect_finishing_at_the_deadline_is_kept(monkeypatch):
    """Done just after as_completed gave up, but before the timeout check."""
    from concurrent.futures import TimeoutError as FuturesTimeout
    from concurrent.futures import wait as futures_wait

    from gaia.mcp.client import mcp_client_manager as mgr_mod

    fake = _FakeClient("srv")
    monkeypatch.setattr(mgr_mod.MCPClient, "from_config", lambda *a, **k: fake)

    def times_out_after_everything_finished(futures, timeout=None):
        futures_wait(list(futures))
        raise FuturesTimeout()
        yield  # pragma: no cover - a generator, like the real one

    monkeypatch.setattr(mgr_mod, "as_completed", times_out_after_everything_finished)
    manager = mgr_mod.MCPClientManager(config=_OneServer())
    manager.load_from_config()

    assert manager._clients.get("srv") is fake, manager._failed
    assert not fake.disconnected.is_set()


def test_a_connect_that_finishes_after_being_given_up_stops_itself(monkeypatch):
    """Given up on before its process existed, it must not run unrecorded."""
    from concurrent.futures import TimeoutError as FuturesTimeout

    from gaia.mcp.client import mcp_client_manager as mgr_mod

    fake = _FakeClient("srv", connect_s=0.5)

    def slow_from_config(*args, **kwargs):
        time.sleep(0.3)  # the deadline lands before the client even exists
        return fake

    monkeypatch.setattr(mgr_mod.MCPClient, "from_config", slow_from_config)

    def times_out_at_once(futures, timeout=None):
        raise FuturesTimeout()
        yield  # pragma: no cover

    monkeypatch.setattr(mgr_mod, "as_completed", times_out_at_once)
    manager = mgr_mod.MCPClientManager(config=_OneServer())
    manager.load_from_config()

    assert "timed out" in manager._failed["srv"]
    assert fake.disconnected.wait(5), "a late connect kept its server running"
    assert "srv" not in manager._clients


def test_a_launch_that_cannot_start_its_readers_stops_the_server(tmp_path, monkeypatch):
    psutil = pytest.importorskip("psutil")
    script = _stub(tmp_path, "time.sleep(3600)")
    transport = StdioTransport(sys.executable, args=["-u", str(script)])
    launched = {}
    real_start = stdio_module.StdioTransport._start_readers

    def no_threads(self):
        launched["pid"] = self._process.pid
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(stdio_module.StdioTransport, "_start_readers", no_threads)

    with pytest.raises(RuntimeError, match="new thread"):
        transport.connect()
    assert real_start is not None
    pid = launched["pid"]
    deadline = time.monotonic() + 10
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid), "a failed launch left the server running"


def test_a_server_can_ask_for_a_longer_handshake(monkeypatch):
    from gaia.mcp.client import mcp_client_manager as mgr_mod
    from gaia.mcp.client.mcp_client import MCPClient, server_timeout

    assert server_timeout("s", {}, 30) == 30
    assert server_timeout("s", {"timeout": 120}, 30) == 120
    for bad in (0, -5, "120", True):
        with pytest.raises(ValueError, match="positive number of seconds"):
            server_timeout("s", {"timeout": bad}, 30)

    client = MCPClient.from_config("s", {"command": "npx", "timeout": 120})
    assert client.transport.timeout == 120

    # The manager's connect deadline stretches to the longest one asked for.
    waited = {}

    def record_deadline(futures, timeout=None):
        waited["timeout"] = timeout
        return iter(())

    class _Config:
        def get_servers(self):
            return {"slow": {"command": "unused", "timeout": 120}}

    monkeypatch.setattr(mgr_mod, "as_completed", record_deadline)
    monkeypatch.setattr(
        mgr_mod.MCPClient, "from_config", lambda *a, **k: _FakeClient("slow")
    )
    mgr_mod.MCPClientManager(config=_Config()).load_from_config()
    assert waited["timeout"] == 120


def test_a_launcher_that_dies_during_the_startup_check_has_its_child_stopped(
    tmp_path, monkeypatch
):
    psutil = pytest.importorskip("psutil")
    # A wide window, so a loaded machine's slow interpreter start still dies in it.
    monkeypatch.setattr(stdio_module, "_STARTUP_CHECK_S", 5.0)
    monkeypatch.setattr(stdio_module, "_STARTUP_CHECK_SLICES", 100)
    # Only the startup check may record the child; the watcher must not.
    monkeypatch.setattr(
        stdio_module.StdioTransport, "_watch_children", lambda self, proc: None
    )
    child_pid_file = tmp_path / "child.pid"
    script = tmp_path / "dies_fast.py"
    script.write_text(textwrap.dedent(f"""
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
            open({str(child_pid_file)!r}, "w").write(str(child.pid))
            time.sleep(0.5)  # recorded by the startup check, then gone
            sys.exit(1)
            """))
    transport = StdioTransport(sys.executable, args=["-u", str(script)])
    with pytest.raises(RuntimeError, match="died"):
        transport.connect()
    deadline = time.monotonic() + 10
    while not _written(child_pid_file) and time.monotonic() < deadline:
        time.sleep(0.05)
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 10
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid), "the dead launcher's child kept running"


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 1e9, 601, 10**400])
def test_a_timeout_that_could_never_end_is_refused(bad):
    from gaia.mcp.client.mcp_client import server_timeout

    with pytest.raises(ValueError, match="at most 600"):
        server_timeout("s", {"timeout": bad}, 30)


def test_one_bad_timeout_fails_only_its_own_server(monkeypatch):
    from gaia.mcp.client import mcp_client_manager as mgr_mod

    class _Config:
        def get_servers(self):
            return {
                "bad": {"command": "unused", "timeout": float("inf")},
                "huge": {"command": "unused", "timeout": 10**400},
                "good": {"command": "unused"},
            }

    monkeypatch.setattr(
        mgr_mod.MCPClient, "from_config", lambda name, *a, **k: _FakeClient(name)
    )
    manager = mgr_mod.MCPClientManager(config=_Config())
    manager.load_from_config()

    assert "good" in manager._clients
    assert "at most 600" in manager._failed["bad"]
    assert "at most 600" in manager._failed["huge"]


def test_ordinary_config_values_are_never_treated_as_secrets(tmp_path):
    long_path = str(tmp_path / ("deep" * 10) / "server_data")
    t = StdioTransport(
        sys.executable,
        args=[
            "-u",
            "srv.py",
            "--max-tokens",
            "4096",
            "-p",
            "3000",
            "--port=8080",
            "--transport=stdio",
            "LOG_LEVEL=info",
            "--author",
            "Alice",
            "--no-auth",
            "--monkey-patch",
            "true",
            "--data",
            long_path,
            "--max-tokens=4096",
            "--tokenizer=gpt2-xl",
            "--keyboard=dvorak",
            "--monkey-patch=true",
            "--author=Alice",
            "max_tokens=4096",
            "--token-limit=8000",
            "--primary-key-id",
            "pk_00042",
        ],
        env={
            "NODE_ENV": "production",
            "LOG_LEVEL": "debug",
            "AUTHOR": "Alice",
            "GIT_AUTHOR_NAME": "Alice Smith",
            "TOKENIZERS_PARALLELISM": "false",
            "API_KEY_PATH": "/srv/keys/api.pem",
            "MAX_TOKENS": "4096",
            "AUTH_MODE": "oauth2",
        },
    )
    text = (
        f"max tokens 4096 on port 3000 and 8080 via stdio at info by Alice; "
        f"production debug true {long_path} gpt2-xl dvorak Alice Smith "
        "8000 false oauth2 pk_00042 /srv/keys/api.pem"
    )
    assert t.redact(text) == text


@pytest.mark.parametrize(
    "args,env,secret,forms",
    [
        # A cp850 console: two bytes can decode to one character.
        (
            [],
            {"API_KEY": "Zq9vßüKe7w", "STUB_CODEC": "cp850", "STUB_ERRORS": "strict"},
            "Zq9vßüKe7w",
            ["Zq9vßüKe7w", "Zq9v�Ke7w"],
        ),
        # A cp866 console: two bytes can decode to one character.
        (
            [],
            {"API_KEY": "пароль2024Zq", "STUB_CODEC": "cp866", "STUB_ERRORS": "strict"},
            "пароль2024Zq",
            ["пароль2024Zq", "��\u0bab�2024Zq"],
        ),
        # A cp1251 console: two bytes can decode to one character.
        (
            [],
            {
                "API_KEY": "Zq9vпарольKe7w",
                "STUB_CODEC": "cp1251",
                "STUB_ERRORS": "strict",
            },
            "Zq9vпарольKe7w",
            ["Zq9vпарольKe7w", "Zq9v������Ke7w"],
        ),
        # One character as two escapes (a surrogate pair in ASCII JSON).
        (
            [],
            {"API_KEY": "Zq9v😀Ke7w", "STUB_ASCII": "1"},
            "Zq9v😀Ke7w",
            ["Zq9v😀Ke7w", "Zq9v\\\\ud83d\\\\ude00Ke7w"],
        ),
        # Uppercase hex in an escape.
        (
            [],
            {"API_KEY": "Zq9véKe7w", "STUB_ASCII": "1", "STUB_UPPER_HEX": "1"},
            "Zq9véKe7w",
            ["Zq9véKe7w", "Zq9v\\\\u00E9Ke7w"],
        ),
        # A backslash before a letter, escaped twice over.
        (
            [],
            {"API_KEY": "Zq9v\\Ke7wP2", "STUB_DOUBLE_JSON": "1"},
            "Zq9v\\Ke7wP2",
            ["Zq9v\\Ke7wP2", "Zq9v\\\\\\\\Ke7wP2"],
        ),
        # latin-1 has no euro sign: "?" in its place.
        (
            [],
            {"API_KEY": "p€ss1234", "STUB_CODEC": "latin-1", "STUB_ERRORS": "replace"},
            "p€ss1234",
            ["p€ss1234", "p?ss1234"],
        ),
        # ASCII with errors="replace".
        (
            [],
            {"API_KEY": "p€ss1234", "STUB_CODEC": "ascii", "STUB_ERRORS": "replace"},
            "p€ss1234",
            ["p€ss1234", "p?ss1234"],
        ),
        # cp437 (a DOS console), which isn't valid UTF-8.
        (
            [],
            {"API_KEY": "Zq9véKe7w", "STUB_CODEC": "cp437", "STUB_ERRORS": "strict"},
            "Zq9véKe7w",
            ["Zq9véKe7w", "Zq9v�Ke7w"],
        ),
        # A JSON string holding a JSON string: every escape doubled.
        (
            [],
            {"API_KEY": 'Zq9v"éKe7w', "STUB_DOUBLE_JSON": "1"},
            'Zq9v"éKe7w',
            ['Zq9v"éKe7w', 'Zq9v\\\\\\"\\\\u00e9Ke7w'],
        ),
        # stderr as Python's ASCII backslash escapes.
        (
            [],
            {
                "API_KEY": "Zq9vé😀Ke7w",
                "STUB_CODEC": "ascii",
                "STUB_ERRORS": "backslashreplace",
            },
            "Zq9vé😀Ke7w",
            ["Zq9vé😀Ke7w", "Zq9v\\xe9\\U0001f600Ke7w"],
        ),
        # A Node-style JSON line written in cp1252.
        (
            [],
            {"API_KEY": 'Zq9v"€Ke7w', "STUB_ASCII": "0", "STUB_JSON_CODEC": "cp1252"},
            'Zq9v"€Ke7w',
            ['Zq9v"€Ke7w', 'Zq9v\\"�Ke7w'],
        ),
        (
            ["--url", "https://bob:hunter2222@db.example/x"],
            {},
            "hunter2222",
            ["hunter2222"],
        ),
        (
            ["--header", "Authorization: Bearer Zq9vKe7wP2.tok"],
            {},
            "Zq9vKe7wP2.tok",
            ["Zq9vKe7wP2.tok"],
        ),
        # A non-object line written as Python's json writes it (ASCII-escaped).
        (
            [],
            {"GITHUB_TOKEN": 'Zq9v\\"Ke7wéP2', "STUB_ASCII": "1"},
            'Zq9v\\"Ke7wéP2',
            ['Zq9v\\"Ke7wéP2', 'Zq9v\\\\\\"Ke7w\\u00e9P2'],
        ),
        # ... and as Node's JSON.stringify does (no ASCII escapes).
        (
            [],
            {"GITHUB_TOKEN": 'Zq9v\\"Ke7wéP2', "STUB_ASCII": "0"},
            'Zq9v\\"Ke7wéP2',
            ['Zq9v\\"Ke7wéP2', 'Zq9v\\\\\\"Ke7wéP2'],
        ),
        # stderr in cp1252, which has a euro sign; latin-1 has none.
        (
            [],
            {"API_KEY": "p€ss1234", "STUB_ERRORS": "strict"},
            "p€ss1234",
            ["p€ss1234", "p�ss1234"],
        ),
        # A euro sign cp1252 holds, then an emoji it can't, written as an escape ...
        (
            [],
            {"API_KEY": "Zq9v€😀Ke7w", "STUB_ERRORS": "backslashreplace"},
            "Zq9v€😀Ke7w",
            ["Zq9v€😀Ke7w", "Zq9v�\\U0001f600Ke7w"],
        ),
        # ... or replaced with "?".
        (
            [],
            {"API_KEY": "Zq9v☃Ke7w", "STUB_ERRORS": "replace"},
            "Zq9v☃Ke7w",
            ["Zq9v☃Ke7w", "Zq9v?Ke7w"],
        ),
    ],
    ids=[
        "cp850",
        "cp866",
        "cp1251",
        "surrogate-pair-json",
        "uppercase-hex",
        "backslash-in-json-in-json",
        "latin1-euro",
        "ascii-replace",
        "cp437",
        "json-in-json",
        "ascii-backslash",
        "json-in-cp1252",
        "url-userinfo",
        "bearer-header",
        "json-ascii",
        "json-utf8",
        "cp1252-euro",
        "emoji-backslash",
        "snowman-replace",
    ],
)
def test_known_secrets_are_cleaned_in_every_form_they_arrive_in(
    tmp_path, args, env, secret, forms
):
    """Raw, JSON-escaped in a non-object line, as a dict key, and as a code
    page writes it on stderr."""
    from gaia.mcp.client.mcp_client import MCPClient

    script = tmp_path / "echo_forms.py"
    script.write_text(textwrap.dedent("""
        import json, os, sys
        secret = sys.argv[-1]
        ascii_only = os.environ.get("STUB_ASCII", "1") == "1"
        errors = os.environ.get("STUB_ERRORS", "replace")
        codec = os.environ.get("STUB_CODEC", "cp1252")
        json_codec = os.environ.get("STUB_JSON_CODEC", "utf-8")
        double = os.environ.get("STUB_DOUBLE_JSON") == "1"
        upper = os.environ.get("STUB_UPPER_HEX") == "1"
        for line in sys.stdin:
            req = json.loads(line)
            if req["method"] == "initialize":
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                                             "result": {}}) + "\\n")
            elif req["method"] == "tools/list":  # a non-object JSON line
                # UTF-8 bytes, as Node writes them whatever the console code page.
                line = json.dumps("leaked " + secret, ensure_ascii=ascii_only)
                if double:  # a JSON string holding a JSON string
                    line = json.dumps(line)
                if upper:  # uppercase hex, as some encoders write it
                    import re
                    line = re.sub(r"u([0-9a-f]{4})", lambda m: "u" + m.group(1).upper(), line)
                sys.stdout.flush()
                sys.stdout.buffer.write((line + "\\n").encode(json_codec))
            else:  # a dict-key echo in an error, then cp1252 bytes on stderr
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                    "error": {"code": 1, "message": {secret: secret}}}) + "\\n")
                sys.stdout.flush()
                sys.stderr.buffer.write(secret.encode(codec, errors))
                sys.stderr.flush()
                sys.exit(3)
            sys.stdout.flush()
        """))
    client = MCPClient.from_config(
        "forms",
        {
            "command": sys.executable,
            "args": ["-u", str(script), *args, secret],
            "env": env,
        },
    )
    seen = []
    try:
        assert client.connect(), client.last_error
        try:
            client.list_tools()
        except ValueError as e:
            seen.append(str(e))
        seen.append(repr(client.call_tool("x", {})))
        time.sleep(0.5)
        seen.append(client.transport._read_stderr())
    finally:
        client.disconnect()

    text = "\n".join(seen)
    assert "<redacted>" in text
    for form in forms:
        assert form not in text, (form, text)
    # Runs of letters and digits read the same in every encoding, repr included.
    for run in re.findall(r"[A-Za-z0-9]{4,}", secret):
        for i in range(len(run) - 3):
            assert run[i : i + 4] not in text, (run[i : i + 4], text)


def test_a_validation_error_never_echoes_a_secret_from_the_tool_schema(
    tmp_path, caplog
):
    """A -32602 rejection adds the server's own inputSchema to the error."""
    import logging

    from gaia.mcp.client.mcp_client import MCPClient

    secret = "Zq9vKe7wP2secret"
    script = tmp_path / "schema_echo.py"
    script.write_text(
        _PRELUDE
        + textwrap.indent(
            textwrap.dedent("""
        conf = " ".join(sys.argv)
        if req["method"] == "initialize":
            send({"jsonrpc": "2.0", "id": req["id"], "result": {}})
        elif req["method"] == "tools/list":
            send({"jsonrpc": "2.0", "id": req["id"], "result": {"tools": [
                {"name": "x", "inputSchema": {"description": conf,
                                              conf: {"type": "string"}}}]}})
        else:
            send({"jsonrpc": "2.0", "id": req["id"],
                  "error": {"code": -32602, "message": "bad params"}})
        """),
            "    ",
        )
    )
    caplog.set_level(logging.DEBUG, logger="gaia.mcp.client.mcp_client")
    client = MCPClient.from_config(
        "schema",
        {"command": sys.executable, "args": ["-u", str(script), "--api-key", secret]},
    )
    try:
        assert client.connect(), client.last_error
        result = client.call_tool("x", {})
    finally:
        client.disconnect()

    text = repr(result) + "\n".join(r.getMessage() for r in caplog.records)
    assert "input validation failed" in result["error"]
    assert secret not in text


def test_a_reused_pid_is_never_taken_for_the_server():
    """Only a process created after this launch can be the server we track."""
    t = StdioTransport(sys.executable, args=["-c", "pass"])
    t._started_at = time.time()
    # This test's own process existed long before the "launch".
    assert t._own_process(os.getpid()) is None
    t._started_at = 0.0
    assert t._own_process(os.getpid()) is not None


@pytest.mark.parametrize(
    "args,env,secret",
    [
        ([], {"AUTHORIZATION": "Basic Zq9vKe7w"}, "Basic Zq9vKe7w"),
        ([], {"PROXY_AUTHORIZATION": "Zq9vKe7wP2"}, "Zq9vKe7wP2"),
        (["--accessToken", "Zq9vKe7wP2"], {}, "Zq9vKe7wP2"),
        (["--clientSecret=Zq9vKe7wP2"], {}, "Zq9vKe7wP2"),
        (["https://Zq9vKe7wP2@api.example/v1"], {}, "Zq9vKe7wP2"),
    ],
    ids=[
        "authorization-env",
        "proxy-authorization-env",
        "camel-flag",
        "camel-flag-equals",
        "user-only-userinfo",
    ],
)
def test_contract_secret_positions_are_cleaned(args, env, secret):
    t = StdioTransport(sys.executable, args=["-u", "srv.py", *args], env=env)
    assert secret not in t.redact(f"failed: {secret} rejected")


def test_overlapping_secrets_leave_no_piece_of_either():
    t = StdioTransport(
        sys.executable, args=["--token", "abcdefgh", "--api-key", "efghijkl"]
    )
    assert t.redact("xx abcdefghijkl yy") == "xx <redacted> yy"


def test_values_under_four_characters_are_not_matched():
    t = StdioTransport(sys.executable, args=["--token", "abc"])
    assert t.redact("abc stays: a short value would erase ordinary words") == (
        "abc stays: a short value would erase ordinary words"
    )


def test_a_null_id_error_reply_is_cleaned(tmp_path):
    """A parse-error reply carries no id; its message is the server's text."""
    from gaia.mcp.client.mcp_client import MCPClient

    secret = "Zq9vKe7wP2nullid"
    script = tmp_path / "null_id.py"
    script.write_text(
        _PRELUDE
        + textwrap.indent(
            textwrap.dedent("""
        send({"jsonrpc": "2.0", "id": None,
              "error": {"code": -32700, "message": " ".join(sys.argv)}})
        """),
            "    ",
        )
    )
    client = MCPClient.from_config(
        "nullid",
        {"command": sys.executable, "args": ["-u", str(script), "--api-key", secret]},
    )
    try:
        assert client.connect() is False
    finally:
        client.disconnect()

    assert "rejected 'initialize'" in client.last_error
    assert secret not in client.last_error


@pytest.mark.parametrize(
    "name",
    [
        "API_KEY_PRIMARY",
        "API_KEY_2",
        "OPENAI_API_KEY_V2",
        "SECRET_KEY_BASE",
        "STRIPE_SECRET_KEY_LIVE",
        "CLIENT_SECRET_VALUE",
        "PRIVATE_KEY_PEM",
        "API_TOKENS",
        "SECRETS",
        "NO_PROXY_TOKEN",
        "PROXY_AUTHORIZATION",
        "NPM_AUTH",
    ],
)
def test_every_shape_of_secret_name_is_cleaned(name):
    secret = "Zq9vKe7wP2value"
    t = StdioTransport(
        sys.executable, args=["-u", "srv.py", f"{name}={secret}"], env={name: secret}
    )
    assert secret not in t.redact(f"failed: {secret}")
    t = StdioTransport(sys.executable, args=["-u", "srv.py"], env={name: secret})
    assert secret not in t.redact(f"failed: {secret}")


def test_a_switch_off_flag_takes_no_value():
    """--no-auth and --disable-auth are switches; what follows is an argument."""
    t = StdioTransport(
        sys.executable,
        args=[
            "-u",
            "srv.py",
            "--no-auth",
            "workspace_alpha",
            "--disable-auth",
            "beta_dir",
        ],
    )
    text = "serving workspace_alpha and beta_dir"
    assert t.redact(text) == text


_SECRET_WORD_ROWS = [
    "key",
    "keys",
    "apikey",
    "token",
    "tokens",
    "secret",
    "secrets",
    "password",
    "passwords",
    "passwd",
    "pwd",
    "passphrase",
    "credential",
    "credentials",
    "auth",
    "authorization",
    "authtoken",
    "apitoken",
    "pass",
    "pat",
    "pgpassword",
]
_QUALIFIER_ROWS = [
    "id",
    "mode",
    "limit",
    "file",
    "path",
    "type",
    "url",
    "name",
    "size",
    "count",
    "length",
    "region",
    "parallelism",
    "layout",
    "sort",
    "partition",
    "cache",
    "enabled",
    "ttl",
    "endpoint",
    "provider",
    "uri",
    "timeout",
    "required",
    "method",
    "scope",
    "prefix",
    "through",
    "attempt",
    "attempts",
    "age",
    "days",
    "retry",
    "retries",
    "expiry",
    "expires",
    "lifetime",
    "budget",
]
_COUNT_ROWS = ["max", "min", "num", "first"]
_KEY_KIND_ROWS = ["sort", "partition", "primary", "cache", "foreign", "composite"]


@pytest.mark.parametrize("word", _SECRET_WORD_ROWS)
def test_each_secret_word_names_a_secret(word):
    from gaia.mcp.client.transports.stdio import _is_secret_flag, _is_secret_name

    assert _is_secret_name(f"SERVICE_{word.upper()}")
    assert _is_secret_flag(f"--service-{word}")


@pytest.mark.parametrize("word", _QUALIFIER_ROWS)
def test_each_qualifier_makes_a_secret_word_ordinary(word):
    from gaia.mcp.client.transports.stdio import _is_secret_name

    assert not _is_secret_name(f"API_TOKEN_{word.upper()}")


@pytest.mark.parametrize("word", _COUNT_ROWS)
def test_each_count_prefix_makes_a_secret_word_ordinary(word):
    from gaia.mcp.client.transports.stdio import _is_secret_flag, _is_secret_name

    assert not _is_secret_name(f"{word.upper()}_TOKENS")
    assert not _is_secret_flag(f"--{word}-tokens")


@pytest.mark.parametrize("word", _KEY_KIND_ROWS)
def test_a_database_key_is_not_a_credential(word):
    from gaia.mcp.client.transports.stdio import _is_secret_name

    assert not _is_secret_name(f"TABLE_{word.upper()}_KEY")
    assert _is_secret_name("TABLE_API_KEY")


@pytest.mark.parametrize(
    "name,secret",
    [
        ("DISABLE_AUTH", False),
        ("ENABLE_AUTH", False),
        ("NO_AUTH", False),
        ("--no-auth", False),
        ("--disable-auth", False),
        ("NO_PROXY_TOKEN", True),
        ("APIToken", True),
        ("JWTSecret", True),
        ("DBPassword", True),
        ("API_KEY2", True),
        ("TOKEN2", True),
        ("apiKey2", True),
        ("V2TOKEN", True),
        ("PGPASSWORD", True),
        ("AUTH_REQUIRED", False),
        ("AUTH_METHOD", False),
        ("TOKEN_SCOPE", False),
        ("KEY_PREFIX", False),
        ("PASS_THROUGH", False),
        ("FIRST_PASS", False),
        ("FIRST_PASSWORD", True),
        ("MAX_SECRET", True),
        ("MAX_PASSWORD_ATTEMPTS", False),
        ("MAX_SECRET_AGE", False),
        ("TOKEN_RETRIES", False),
        ("API_KEY_DAYS", False),
        ("TOKEN_EXPIRY", False),
        ("TOKEN_LIFETIME", False),
        ("TOKEN_BUDGET", False),
        ("AUTH_RETRY", False),
        ("MAX_PASSWORD_ATTEMPT", False),
    ],
)
def test_name_splitting_and_switches(name, secret):
    from gaia.mcp.client.transports.stdio import _is_secret_flag, _is_secret_name

    check = _is_secret_flag if name.startswith("-") else _is_secret_name
    assert check(name) is secret


def test_redaction_stays_linear_on_long_non_ascii_secrets():
    """A 200-character non-ASCII run against 1 MB must not backtrack."""
    cases = [
        ("\u5bc6" * 200 + "Zq9v1234", ("\u4e2d\u6587" * 500_000)[:1_000_000]),
        ("Zq9v" + "\u5bc6" * 200 + "Ke7w", ("Zq9v" + "\u4e2d" * 900) * 1100),
        ("\u5bc6\u7801" * 6 + "Zq9v1234", "\u4e2d" * 1_000_000),
        # A core that starts with a backslash or a quote, against a long run
        # of backslashes: every position could start a match.
        ("\\\\Zq9vKe7w", "\\" * 1_000_000),
        ('"Zq9vKe7w', "\\" * 1_000_000),
        ('\u00e9"Zq9vKe7w', "\\" * 1_000_000),
        # A backslash run in the secret, then another character: the run
        # must not be split between two pieces of the pattern.
        ("\\Zq9vKe7w", "\\" * 1_000_000),
        ("Zq9v\\\u00e9Ke7w", ("Zq9v" + "\\" * 50_000) * 20),
        ("Zq9v\\Ke7wP2", ("Zq9v" + "\\" * 50_000) * 20),
    ]
    for secret, text in cases:
        t = StdioTransport(sys.executable, args=["--api-key", secret])
        started = time.perf_counter()
        t.redact(text)
        took = time.perf_counter() - started
        assert took < 0.25, f"{took:.2f}s for a {len(secret)}-character secret"


def test_a_mostly_non_ascii_secret_never_erases_unrelated_text():
    """Too few ASCII anchors: match it exactly, not as a loose pattern."""
    t = StdioTransport(
        sys.executable, args=["--api-key", "\u043f\u0430\u0440\u043e\u043b\u044c1"]
    )
    text = "unrelated ??????1 and \u0442\u0435\u043a\u0441\u04421 stay"
    assert t.redact(text) == text
    assert "<redacted>" in t.redact("leaked \u043f\u0430\u0440\u043e\u043b\u044c1")


def test_a_boolean_value_is_never_a_secret():
    t = StdioTransport(
        sys.executable,
        args=["--token", "true", "--api-key=false"],
        env={"API_KEY": "yes"},
    )
    text = "verbose: true, cache: false, retry: yes"
    assert t.redact(text) == text


@pytest.mark.parametrize(
    "secret,arrived",
    [
        ("пароль2024Zq", "пароль2024Zq"),
        ("пароль2024Zq", "\u043f\u0430\u0440\u043e\u043b\u044c2024Zq"),
        ("Zq2024пароль", "Zq2024пароль"),
        ("Zq2024пароль", "Zq2024??????"),
    ],
    ids=["leading-raw", "leading-escaped", "trailing-raw", "trailing-replaced"],
)
def test_a_secret_starting_or_ending_in_non_ascii_goes_whole(secret, arrived):
    t = StdioTransport(sys.executable, args=["--api-key", secret])
    assert t.redact(f"x {arrived} y") == "x <redacted> y"


def test_a_secret_starting_with_a_backslash_or_quote_is_still_found():
    for secret, arrived in [
        ("\\\\Zq9vKe7w", "\\\\\\\\\\\\\\\\Zq9vKe7w"),
        ('"Zq9vKe7w', '\\\\\\"Zq9vKe7w'),
    ]:
        t = StdioTransport(sys.executable, args=["--api-key", secret])
        assert t.redact(f"x {arrived} y") == "x <redacted> y", (secret, arrived)


def test_stderr_lines_are_cleaned_whole_then_capped(tmp_path):
    """A huge line can't stall redaction, and the cap can't cut a secret."""
    secret = "Zq9vKe7wP2capped"
    script = tmp_path / "huge_line.py"
    # The secret straddles the cap, inside one 200 KB line.
    script.write_text(
        "import sys\n"
        f"sys.stderr.write('x' * (8192 - 8) + {secret!r} + 'y' * 200_000 + '\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(4)\n"
    )
    transport = StdioTransport(
        sys.executable, args=["-u", str(script), "--api-key", secret]
    )
    try:
        # Dies at once, but a loaded machine can let it outlast connect()'s check.
        assert transport.connect()
        transport._process.wait(timeout=30)
        stderr = str(transport._process_died_error())
    except RuntimeError as died:
        stderr = str(died)
    finally:
        transport.disconnect()
    assert "Zq9v" not in stderr and "capped" not in stderr
    assert "[line cut]" in stderr
    assert len(stderr) < 9000


@pytest.mark.parametrize(
    "secret,arrived",
    [
        ("Zq9v&<>Ke7w", "Zq9v\\u0026\\u003c\\u003eKe7w"),  # Go's JSON
        ("Zq9v/Ke7wP2", "Zq9v\\/Ke7wP2"),  # JSON's optional \/
        ("Zq9v\tKe7w\nP2", "Zq9v\\tKe7w\\nP2"),  # short escapes
        ("Zq9v'Ke7wP2", "Zq9v\\'Ke7wP2"),  # Python repr
        ("Zq9v'Ke7wP2", "Zq9v\\x27Ke7wP2"),
        ("Zq9v<Ke7wP2", "Zq9v\\u003CKe7wP2"),  # uppercase hex
        ("Zq9v\\\u00e9Ke7w", "Zq9v\\\\\\u00e9Ke7w"),  # backslash, then an escape
        ("Zq9v\\Ke7wP2", "Zq9v\\\\\\\\Ke7wP2"),  # backslash, escaped twice
        ("Zq9v\vKe7wP2", "Zq9v\\vKe7wP2"),  # C's \v, \0, \e
        ("Zq9v\0Ke7wP2", "Zq9v\\0Ke7wP2"),
        ("Zq9v\x1bKe7wP2", "Zq9v\\eKe7wP2"),
        ("Zq9v'Ke7wP2", "Zq9v\\U00000027Ke7wP2"),  # eight-digit escape
        ("Zq9vKe7w\U0001f600", "Zq9vKe7w\\U0001F600"),
        ("Zq9vKe7w\U0001f600", "Zq9vKe7w\\ud83d\\ude00"),  # surrogate pair
        ("Zq9v\\Ke7wP2", "Zq9v\\x5cKe7wP2"),  # backslash as a hex escape
        ("Zq9v\\Ke7wP2", "Zq9v\\u005CKe7wP2"),
        ("Zq9v\\Ke7wP2", "Zq9v\\u005c\\u005cKe7wP2"),
        ("Zq9vKe7w\u00e9", "Zq9vKe7w\\xc3\\xa9"),  # UTF-8 bytes, as repr(bytes)
        # The secret's own backslash beside what backslashreplace wrote.
        ("Zq9v\\xcEKe7w\u20ac", "Zq9v\\xcEKe7w\\u20ac"),
    ],
    ids=[
        "go-json",
        "json-slash",
        "short-escapes",
        "repr-quote",
        "x-escape",
        "upper-hex",
        "backslash-escape",
        "double-backslash",
        "vertical-tab",
        "nul",
        "esc",
        "eight-digit",
        "eight-digit-upper",
        "surrogate-pair",
        "backslash-as-x",
        "backslash-as-u",
        "backslash-twice-as-u",
        "utf8-bytes",
        "own-backslash-and-backslashreplace",
    ],
)
def test_escaped_forms_of_a_secret_are_redacted_too(secret, arrived):
    t = StdioTransport(sys.executable, args=["--api-key", secret])
    assert t.redact(f"x {arrived} y") == "x <redacted> y", (secret, arrived)


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_a_secret_json_escaped_up_to_three_times_is_redacted(layers):
    secret = 'Zq9v"Ke7w\\P2\u00e9\tx'
    arrived = secret
    for _ in range(layers):
        arrived = json.dumps(arrived)[1:-1]
    t = StdioTransport(sys.executable, args=["--api-key", secret])
    assert t.redact(f"x {arrived} y") == "x <redacted> y", arrived


def test_a_secret_repeating_through_the_text_is_redacted_whole():
    t = StdioTransport(sys.executable, args=["--api-key", "abababab"])
    assert t.redact("x " + "ab" * 10 + "a y") == "x <redacted>a y"
    t = StdioTransport(sys.executable, args=["--api-key", "a" * 40])
    started = time.perf_counter()
    assert t.redact("a" * 1_000_000) == "<redacted>"
    # In one step, not once per character.
    assert time.perf_counter() - started < 0.2


def test_redaction_stays_fast_on_dense_escapes():
    """Text built so every position could start an escaped secret."""
    backslash = "\\"
    cases = [
        ("Zq9vKe7w\u20ac\U0001f600", "Zq9vKe7w\u20ac"),
        ("Zq9vKe7wP2" * 20, "Zq9vKe7wP2" * 20),
        ("x41x41x41x41", "x41x41x41x4"),
        ("0" * 8, "0" * 7),
    ]
    for secret, near in cases:
        t = StdioTransport(sys.executable, args=["--api-key", secret])
        for text in (
            "".join(backslash * 3 + c for c in near) * (1_000_000 // (4 * len(near))),
            (backslash * 50 + near[0]) * (1_000_000 // 51),
            "".join(backslash + "u%04x" % ord(c) for c in near)
            * (1_000_000 // (6 * len(near))),
        ):
            started = time.perf_counter()
            t.redact(text)
            took = time.perf_counter() - started
            # Generous: this guards against blow-ups, not absolute speed.
            assert took < 2.0, f"{took:.2f}s on {text[:20]!r}"


def _stderr_of(tmp_path, body: str, *args: str) -> str:
    """The stderr a server that writes *body* and dies leaves in its error."""
    script = tmp_path / "write_stderr.py"
    script.write_text(
        "import sys\n"
        f"sys.stderr.write({body!r})\n"
        "sys.stderr.flush()\n"
        "sys.exit(4)\n"
    )
    transport = StdioTransport(sys.executable, args=["-u", str(script), *args])
    try:
        # Dies at once, but a loaded machine can let it outlast connect()'s check.
        assert transport.connect()
        transport._process.wait(timeout=30)
        return str(transport._process_died_error())
    except RuntimeError as died:
        return str(died)
    finally:
        transport.disconnect()


def test_a_long_escaped_secret_across_the_stderr_cap_goes_whole(tmp_path):
    """The secret's written form runs far past the cap: it is still read whole."""
    secret = "Zq9vKe7wP2" * 70
    written = "".join("\\\\u%04x" % ord(c) for c in secret)
    assert len(written) > 4096
    stderr = _stderr_of(
        tmp_path, "x" * (8192 - 14) + written + "\n", "--api-key", secret
    )
    assert "u005a" not in stderr and "<redacted>" in stderr
    assert "[line cut]" in stderr


def test_stderr_is_cut_before_it_is_cleaned(tmp_path):
    """Redacting first would shrink the line and pull in a secret the read cut."""
    import random

    pick = random.Random(5)
    secret = "".join(pick.choice("abcdefghijkmnpqrstuvwxyz") for _ in range(500))
    stderr = _stderr_of(tmp_path, secret * 49 + "\n", "--api-key", secret)
    assert secret[:40] not in stderr and "<redacted>" in stderr


def test_a_stderr_line_of_exactly_the_cap_is_kept_whole(tmp_path):
    assert "[line cut]" not in _stderr_of(tmp_path, "x" * 8192 + "\n")
    assert "[line cut]" not in _stderr_of(tmp_path, "x" * 8192)
    assert "[line cut]" in _stderr_of(tmp_path, "x" * 8193 + "\n")


def test_ordinary_escapes_beside_a_secret_shape_stay(tmp_path):
    t = StdioTransport(sys.executable, args=["--api-key", "Zq9v&Ke7wP2"])
    text = "a \\u0026 b \\/ c \\t d Zq9v and Ke7wP2 apart"
    assert t.redact(text) == text


def test_a_stderr_line_with_no_end_does_not_grow_memory(tmp_path):
    """A server streaming one endless line must not be buffered whole."""
    psutil = pytest.importorskip("psutil")
    script = tmp_path / "endless_line.py"
    script.write_text(
        "import sys, time\n"
        "chunk = 'x' * 1_000_000\n"
        "for _ in range(50):\n"
        "    sys.stderr.write(chunk)\n"
        "sys.stderr.write('\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )
    me = psutil.Process()
    before = me.memory_info().rss
    transport = StdioTransport(sys.executable, args=["-u", str(script)])
    assert transport.connect()
    peak = before
    try:
        # Until the reader has taken in all 50 MB: the newline is read last.
        deadline = time.monotonic() + 60
        settled_at = None
        while time.monotonic() < deadline:
            peak = max(peak, me.memory_info().rss)
            size = len(transport._stderr_tail)
            if size and settled_at is None:
                settled_at = time.monotonic()
            if settled_at and time.monotonic() - settled_at > 3:
                break
            time.sleep(0.05)
        kept = "".join(transport._stderr_tail)
    finally:
        transport.disconnect()

    assert "[line cut]" in kept
    assert len(kept) < 9000
    assert peak - before < 20 * 1024 * 1024, f"grew {(peak - before) >> 20} MB"
