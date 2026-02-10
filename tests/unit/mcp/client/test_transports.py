# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for MCP transport implementations."""

import json
from io import StringIO
from unittest.mock import Mock, patch

import pytest

from gaia.mcp.client.transports.stdio import StdioTransport


class TestStdioTransport:
    """Test stdio transport with mocked subprocess."""

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_connect_starts_subprocess(self, mock_popen):
        """Test that connect() starts a subprocess."""
        mock_process = Mock()
        mock_process.poll.return_value = None  # Process is alive
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        result = transport.connect()

        assert result is True
        mock_popen.assert_called_once()
        assert transport.is_connected()

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_connect_twice_returns_true(self, mock_popen):
        """Test that connecting when already connected returns True."""
        mock_process = Mock()
        mock_process.poll.return_value = None  # Process is alive
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport.connect()
        result = transport.connect()  # Second connect

        assert result is True
        # Should only call Popen once
        assert mock_popen.call_count == 1

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_disconnect_terminates_process(self, mock_popen):
        """Test that disconnect() terminates the subprocess."""
        mock_process = Mock()
        mock_process.poll.return_value = None  # Process alive
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport.connect()
        transport.disconnect()

        mock_process.terminate.assert_called_once()
        assert not transport.is_connected()

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_send_request_sends_json_rpc(self, mock_popen):
        """Test that send_request() sends properly formatted JSON-RPC."""
        mock_process = Mock()
        mock_process.poll.return_value = None  # Process alive
        mock_process.stdin = Mock()
        mock_process.stdout = StringIO(
            '{"jsonrpc": "2.0", "id": 0, "result": {"status": "ok"}}\n'
        )
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport.connect()

        response = transport.send_request("test_method", {"param": "value"})

        # Check request was written
        written_data = mock_process.stdin.write.call_args[0][0]
        request = json.loads(written_data.strip())

        assert request["jsonrpc"] == "2.0"
        assert request["method"] == "test_method"
        assert request["params"] == {"param": "value"}
        assert "id" in request

        # Check response
        assert response["result"]["status"] == "ok"

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_send_request_without_connection_raises_error(self, mock_popen):
        """Test that send_request() raises error when not connected."""
        transport = StdioTransport("test command")

        with pytest.raises(RuntimeError, match="Transport not connected"):
            transport.send_request("test_method")

    @patch("gaia.mcp.client.transports.stdio.time.sleep")
    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_send_request_when_process_died_raises_error(self, mock_popen, mock_sleep):
        """Test that send_request() raises error if process dies after connect."""
        mock_process = Mock()
        # Process alive during connect, dead by send_request
        mock_process.poll.side_effect = [None, 1, 1]
        mock_process.returncode = 1
        mock_process.pid = 1234
        mock_process.stderr = StringIO("")
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport.connect()

        with pytest.raises(RuntimeError, match="process died"):
            transport.send_request("test_method")

    @patch("gaia.mcp.client.transports.stdio.time.sleep")
    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_is_connected_returns_false_when_process_exits(
        self, mock_popen, mock_sleep
    ):
        """Test that is_connected() returns False when process exits."""
        mock_process = Mock()
        # connect() calls poll() once for early crash check, then is_connected() calls poll()
        mock_process.poll.side_effect = [
            None,
            None,
            None,
            0,
        ]  # connect, alive, alive, dead
        mock_process.pid = 1234
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport.connect()

        assert transport.is_connected() is True
        assert transport.is_connected() is True
        assert transport.is_connected() is False

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_debug_mode_logs_requests(self, mock_popen):
        """Test that debug mode logs request/response details."""
        mock_process = Mock()
        mock_process.poll.return_value = None
        mock_process.stdin = Mock()
        mock_process.stdout = StringIO('{"jsonrpc": "2.0", "id": 0, "result": {}}\n')
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command", debug=True)
        transport.connect()

        # This should trigger debug logging
        with patch("gaia.mcp.client.transports.stdio.logger") as mock_logger:
            transport.send_request("test_method")
            # Verify debug was called at least once
            assert mock_logger.debug.called

    @patch("gaia.mcp.client.transports.stdio.shutil.which", side_effect=lambda cmd: cmd)
    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_stdio_transport_accepts_args_list(self, mock_popen, _):
        """Test that StdioTransport accepts separate args list."""
        mock_process = Mock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        transport = StdioTransport(
            "npx", args=["-y", "@modelcontextprotocol/server-github"]
        )
        result = transport.connect()

        assert result is True
        # Verify command was built from command + args
        call_args = mock_popen.call_args
        assert call_args[0][0] == ["npx", "-y", "@modelcontextprotocol/server-github"]
        # Should not use shell=True when using args list
        assert call_args[1]["shell"] is False

    @patch("gaia.mcp.client.transports.stdio.shutil.which", side_effect=lambda cmd: cmd)
    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_stdio_transport_builds_command_from_args(self, mock_popen, _):
        """Test that command is built correctly from command + args."""
        mock_process = Mock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        transport = StdioTransport("python", args=["-m", "mcp_server", "--debug"])
        transport.connect()

        call_args = mock_popen.call_args
        assert call_args[0][0] == ["python", "-m", "mcp_server", "--debug"]

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    @patch(
        "gaia.mcp.client.transports.stdio.os.environ",
        {"PATH": "/usr/bin", "HOME": "/home/user"},
    )
    def test_stdio_transport_passes_env_to_popen(self, mock_popen):
        """Test that env vars are passed to subprocess."""
        mock_process = Mock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        transport = StdioTransport(
            "npx",
            args=["-y", "server"],
            env={"GITHUB_TOKEN": "ghp_xxx", "DEBUG": "true"},
        )
        transport.connect()

        call_args = mock_popen.call_args
        passed_env = call_args[1]["env"]
        assert passed_env["GITHUB_TOKEN"] == "ghp_xxx"
        assert passed_env["DEBUG"] == "true"

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    @patch(
        "gaia.mcp.client.transports.stdio.os.environ",
        {"PATH": "/usr/bin", "HOME": "/home/user"},
    )
    def test_stdio_transport_merges_env_with_system(self, mock_popen):
        """Test that config env merges with system environment."""
        mock_process = Mock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        transport = StdioTransport(
            "npx", args=["-y", "server"], env={"API_KEY": "secret123"}
        )
        transport.connect()

        call_args = mock_popen.call_args
        passed_env = call_args[1]["env"]
        # System env should be preserved
        assert passed_env["PATH"] == "/usr/bin"
        assert passed_env["HOME"] == "/home/user"
        # Config env should be merged
        assert passed_env["API_KEY"] == "secret123"

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_stdio_transport_legacy_command_string_still_works(self, mock_popen):
        """Test backward compat: command string without args uses shell=True."""
        mock_process = Mock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        transport = StdioTransport("npx -y @modelcontextprotocol/server-github")
        transport.connect()

        call_args = mock_popen.call_args
        # Legacy string command uses shell=True
        assert call_args[0][0] == "npx -y @modelcontextprotocol/server-github"
        assert call_args[1]["shell"] is True

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_stdio_transport_empty_args_treated_as_none(self, mock_popen):
        """Test that empty args list is treated same as None."""
        mock_process = Mock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        transport = StdioTransport("echo test", args=[])
        transport.connect()

        call_args = mock_popen.call_args
        # Empty args should use shell mode like legacy
        assert call_args[1]["shell"] is True

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_read_stderr_from_dead_process(self, mock_popen):
        """Test that _read_stderr captures output from a dead process."""
        mock_process = Mock()
        mock_process.poll.return_value = 1  # Process is dead
        mock_process.stderr = StringIO("Error: something went wrong\n")
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport._process = mock_process

        stderr = transport._read_stderr()
        assert "something went wrong" in stderr

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_read_stderr_skips_live_process(self, mock_popen):
        """Test that _read_stderr returns empty for a live process."""
        mock_process = Mock()
        mock_process.poll.return_value = None  # Process is alive
        mock_process.stderr = StringIO("should not be read")
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport._process = mock_process

        stderr = transport._read_stderr()
        assert stderr == ""

    def test_format_exit_code_with_signal(self):
        """Test that _format_exit_code translates signal numbers to names."""
        # -11 is SIGSEGV on Unix
        result = StdioTransport._format_exit_code(-11)
        assert "-11" in result
        assert "SIGSEGV" in result

    def test_format_exit_code_with_sigkill(self):
        """Test _format_exit_code for SIGKILL (-9)."""
        result = StdioTransport._format_exit_code(-9)
        assert "-9" in result
        assert "SIGKILL" in result

    def test_format_exit_code_positive(self):
        """Test _format_exit_code for normal exit codes (no signal name)."""
        result = StdioTransport._format_exit_code(1)
        assert result == "1"

    def test_format_exit_code_none(self):
        """Test _format_exit_code for None."""
        result = StdioTransport._format_exit_code(None)
        assert result == "None"

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_process_died_error_includes_signal_name(self, mock_popen):
        """Test that _process_died_error includes signal name and stderr."""
        mock_process = Mock()
        mock_process.returncode = -11
        mock_process.poll.return_value = -11
        mock_process.stderr = StringIO("Segmentation fault (core dumped)\n")
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport._process = mock_process

        error = transport._process_died_error()
        msg = str(error)
        assert "SIGSEGV" in msg
        assert "Segmentation fault" in msg
        assert "Hint:" in msg
        assert "uvx cache clean" in msg

    @patch("gaia.mcp.client.transports.stdio.time.sleep")
    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_connect_detects_immediate_crash(self, mock_popen, mock_sleep):
        """Test that connect() detects process dying immediately after start."""
        mock_process = Mock()
        # poll returns None first (for Popen), then -11 after sleep
        mock_process.poll.return_value = -11
        mock_process.returncode = -11
        mock_process.pid = 12345
        mock_process.stderr = StringIO("fatal error\n")
        mock_popen.return_value = mock_process

        transport = StdioTransport("bad-command", args=["--crash"])

        with pytest.raises(RuntimeError, match="process died"):
            transport.connect()

        # Process reference should be cleaned up
        assert transport._process is None
