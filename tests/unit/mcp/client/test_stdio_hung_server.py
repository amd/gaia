# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A hung or chatty MCP stdio server must not freeze the agent's turn.

Each test runs a real stub server (a tiny Python script) so the pipes,
blocking reads, and process lifecycle are the real ones.
"""

import os
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


def test_no_part_of_a_secret_reaches_a_label_log_or_error(tmp_path, caplog):
    """Property: no 4-character run of a secret in a server's command, args or
    env ever reaches its label, GAIA's logs, or an error raised to a caller.

    The stub echoes its whole command line and env to stderr and exits, the
    way a server that rejects its options does.
    """
    import logging

    script = tmp_path / "echo_and_die.py"
    script.write_text(
        "import os, sys\n"
        "sys.stderr.write(' '.join(sys.argv) + ' ' + os.environ.get('TOKEN', ''))\n"
        "sys.exit(2)\n"
    )
    exe = sys.executable.replace("\\", "/")
    caplog.set_level(logging.DEBUG)
    checked = 0
    for secret in _SECRETS:
        for config in _configs(secret, exe, str(script).replace("\\", "/")):
            caplog.clear()
            seen = []
            try:
                t = StdioTransport(
                    config["command"], args=config["args"], env=config.get("env")
                )
                seen.append(t.label)
                try:
                    if t.connect():
                        t.send_request("tools/list", timeout=5)
                except Exception as e:  # noqa: BLE001 — the text is under test
                    seen.append(str(e))
                finally:
                    t.disconnect()
            except ValueError as e:  # an unparseable command line
                seen.append(str(e))
            seen.extend(r.getMessage() for r in caplog.records)
            text = "\n".join(seen)
            leaked = [g for g in _grams(secret) if g in text]
            assert not leaked, (config, leaked, text[:500])
            checked += 1
    assert checked == len(_SECRETS) * 9


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
