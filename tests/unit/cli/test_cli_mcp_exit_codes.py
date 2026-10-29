# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A failed `gaia mcp ...` command must exit non-zero.

Every MCP handler used to print ``❌ ...`` and return, so `gaia mcp status`
against a stopped bridge exited 0 and scripts or CI gating on it saw success.
These drive the real ``gaia.cli.main`` entry point because the exit code is the
thing under test.
"""

import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gaia import cli

# Reserved and never listening, so connecting to it is refused.
CLOSED_PORT = "1"


def _run_main(monkeypatch, *argv):
    """Run ``gaia <argv>`` through ``main()``; return its exit status."""
    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else code
    return 0


@pytest.fixture(autouse=True)
def _bypass_proxies(monkeypatch):
    """Send the CLI's loopback requests to the fixtures, not a configured proxy."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")


@pytest.fixture
def fake_bridge():
    """A local bridge that is healthy and answers tools/call with ``reply``."""
    state = {"reply": {}}

    class _Handler(BaseHTTPRequestHandler):
        def _send(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # pylint: disable=invalid-name
            self._send({"status": "healthy"})

        def do_POST(self):  # pylint: disable=invalid-name
            self.rfile.read(int(self.headers["Content-Length"]))
            self._send(state["reply"])

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def serve(reply):
        state["reply"] = reply
        return str(server.server_address[1])

    yield serve
    server.shutdown()
    server.server_close()


@pytest.fixture
def in_tmp(monkeypatch, tmp_path):
    """Run from an empty directory with an empty GAIA home (no PID file, no config)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    return tmp_path


@pytest.fixture
def stub_bridge(monkeypatch):
    """Stand in for ``gaia.mcp.mcp_bridge`` so `mcp start` never binds a port."""
    module = types.ModuleType("gaia.mcp.mcp_bridge")
    module.start_server = lambda **_kwargs: None
    monkeypatch.setitem(sys.modules, "gaia.mcp.mcp_bridge", module)
    return module


def _rpc_text(payload):
    return {"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}


class TestFailuresExitOne:
    @pytest.mark.allow_network
    def test_status_with_no_bridge_running(self, monkeypatch, capsys):
        code = _run_main(
            monkeypatch, "mcp", "status", "--host", "127.0.0.1", "--port", CLOSED_PORT
        )
        assert code == 1
        assert "❌ MCP server is not accessible" in capsys.readouterr().out

    @pytest.mark.allow_network
    def test_test_with_no_bridge_running(self, monkeypatch, capsys):
        code = _run_main(
            monkeypatch, "mcp", "test", "--host", "127.0.0.1", "--port", CLOSED_PORT
        )
        assert code == 1
        assert "❌ Cannot connect to MCP server" in capsys.readouterr().out

    @pytest.mark.allow_network
    def test_agent_with_no_bridge_running(self, monkeypatch, capsys):
        code = _run_main(
            monkeypatch,
            "mcp",
            "agent",
            "hello",
            "--host",
            "127.0.0.1",
            "--port",
            CLOSED_PORT,
        )
        assert code == 1
        assert "❌ Cannot connect to MCP server" in capsys.readouterr().out

    @pytest.mark.allow_network
    @pytest.mark.parametrize("action", ["test", "agent"])
    def test_json_rpc_error_from_a_healthy_bridge(
        self, action, monkeypatch, capsys, fake_bridge
    ):
        port = fake_bridge({"error": {"code": -32602, "message": "Unknown tool"}})
        argv = ["mcp", action] + (["hello"] if action == "agent" else [])
        code = _run_main(monkeypatch, *argv, "--host", "127.0.0.1", "--port", port)
        assert code == 1
        assert "Unknown tool" in capsys.readouterr().out

    @pytest.mark.allow_network
    def test_agent_workflow_step_error_exits_after_showing_every_step(
        self, monkeypatch, capsys, fake_bridge
    ):
        port = fake_bridge(
            _rpc_text(
                {
                    "agent_results": {
                        "workflow_results": {
                            "step_1": {"description": "first", "error": "it broke"},
                            "step_2": {"description": "second", "tool": "t"},
                        }
                    }
                }
            )
        )
        code = _run_main(
            monkeypatch, "mcp", "agent", "hello", "--host", "127.0.0.1", "--port", port
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "❌ ERROR: it broke" in out
        assert "step_2: second" in out

    def test_bare_mcp_with_no_action(self, monkeypatch, capsys):
        assert _run_main(monkeypatch, "mcp") == 1
        assert "❌ No MCP action specified" in capsys.readouterr().out

    def test_unknown_action(self, capsys):
        # argparse rejects unknown verbs itself; this guards the handler's own branch.
        with pytest.raises(SystemExit) as exc:
            cli.handle_mcp_command(types.SimpleNamespace(mcp_action="bogus"))
        assert exc.value.code == 1
        assert "❌ Unknown MCP action: bogus" in capsys.readouterr().out

    def test_stop_with_no_pid_file(self, monkeypatch, capsys, in_tmp):
        assert _run_main(monkeypatch, "mcp", "stop") == 1
        assert "❌ No MCP bridge PID file found" in capsys.readouterr().out

    def test_stop_that_fails(self, monkeypatch, capsys, in_tmp):
        pid_file = in_tmp / "gaia.mcp.pid"
        pid_file.write_text("4242", encoding="utf-8")
        monkeypatch.setattr(cli.sys, "platform", "win32")

        def run(cmd, **_kwargs):
            if cmd[0] == "taskkill":
                raise cli.subprocess.CalledProcessError(1, cmd)
            return types.SimpleNamespace(stdout="python.exe 4242")

        monkeypatch.setattr(cli.subprocess, "run", run)

        assert _run_main(monkeypatch, "mcp", "stop") == 1
        assert "❌ Failed to stop process 4242" in capsys.readouterr().out
        assert not pid_file.exists()

    def test_start_without_mcp_extras(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "aiohttp", None)
        assert _run_main(monkeypatch, "mcp", "start", "--no-lemonade-check") == 1
        assert "❌ Error: MCP dependencies not installed" in capsys.readouterr().out

    def test_start_when_lemonade_is_not_ready(self, monkeypatch, stub_bridge):
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
        monkeypatch.setattr(
            cli, "initialize_lemonade_for_agent", lambda **_kwargs: (False, None)
        )
        assert _run_main(monkeypatch, "mcp", "start") == 1

    @pytest.mark.allow_network
    def test_background_start_when_already_running(
        self, monkeypatch, capsys, stub_bridge, fake_bridge, in_tmp
    ):
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
        port = fake_bridge({})
        code = _run_main(
            monkeypatch,
            "mcp",
            "start",
            "--background",
            "--no-lemonade-check",
            "--host",
            "127.0.0.1",
            "--port",
            port,
        )
        assert code == 1
        assert "❌ MCP bridge is already running" in capsys.readouterr().out

    def test_start_error(self, monkeypatch, capsys, stub_bridge):
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))

        def boom(**_kwargs):
            raise OSError("address in use")

        stub_bridge.start_server = boom
        assert _run_main(monkeypatch, "mcp", "start", "--no-lemonade-check") == 1
        assert "❌ Error starting MCP bridge: address in use" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "action, module",
        [
            ("serve", "gaia.mcp.servers.agent_ui_mcp"),
            ("tui", "gaia.mcp.servers.tui_mcp"),
        ],
    )
    def test_server_that_cannot_load(self, action, module, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, module, None)
        assert _run_main(monkeypatch, "mcp", action) == 1
        assert "❌ Error: Could not load" in capsys.readouterr().out

    def test_tools_for_an_unknown_server(self, monkeypatch, capsys, in_tmp):
        config = in_tmp / "mcp_servers.json"
        config.write_text('{"mcpServers": {}}', encoding="utf-8")
        code = _run_main(monkeypatch, "mcp", "tools", "nosuch", "--config", str(config))
        assert code == 1
        assert "❌ MCP server 'nosuch' not found" in capsys.readouterr().out

    def test_test_client_for_an_unknown_server(self, monkeypatch, capsys, in_tmp):
        assert _run_main(monkeypatch, "mcp", "test-client", "nosuch") == 1
        assert "❌ MCP server 'nosuch' not found" in capsys.readouterr().out


class TestNonFailuresStayZero:
    def test_list_with_no_servers_configured(self, monkeypatch, capsys, in_tmp):
        config = in_tmp / "mcp_servers.json"
        config.write_text('{"mcpServers": {}}', encoding="utf-8")
        assert _run_main(monkeypatch, "mcp", "list", "--config", str(config)) == 0
        assert "No MCP servers configured" in capsys.readouterr().out

    @pytest.mark.allow_network
    def test_status_of_a_healthy_bridge(self, monkeypatch, capsys, fake_bridge):
        port = fake_bridge({})
        code = _run_main(
            monkeypatch, "mcp", "status", "--host", "127.0.0.1", "--port", port
        )
        assert code == 0
        assert "✅ MCP server is fully operational" in capsys.readouterr().out

    @pytest.mark.allow_network
    def test_test_that_gets_a_reply(self, monkeypatch, capsys, fake_bridge):
        port = fake_bridge(_rpc_text({"response": "hi there"}))
        code = _run_main(
            monkeypatch, "mcp", "test", "--host", "127.0.0.1", "--port", port
        )
        assert code == 0
        assert "💬 Response: hi there" in capsys.readouterr().out

    def test_stop_when_the_process_is_already_gone(self, monkeypatch, in_tmp):
        pid_file = in_tmp / "gaia.mcp.pid"
        pid_file.write_text("4242", encoding="utf-8")
        monkeypatch.setattr(cli.sys, "platform", "linux")

        def gone(_pid, _sig):
            raise ProcessLookupError

        monkeypatch.setattr(cli.os, "kill", gone)

        assert _run_main(monkeypatch, "mcp", "stop") == 0
        assert not pid_file.exists()

    def test_start_stopped_with_ctrl_c(self, monkeypatch, capsys, stub_bridge):
        monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))

        def interrupted(**_kwargs):
            raise KeyboardInterrupt

        stub_bridge.start_server = interrupted
        assert _run_main(monkeypatch, "mcp", "start", "--no-lemonade-check") == 0
        assert "✅ MCP bridge stopped" in capsys.readouterr().out
