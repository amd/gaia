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

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_send_request_when_process_died_raises_error(self, mock_popen):
        """Test that send_request() raises error if process died."""
        mock_process = Mock()
        mock_process.poll.return_value = 1  # Process exited with code 1
        mock_popen.return_value = mock_process

        transport = StdioTransport("test command")
        transport.connect()

        with pytest.raises(RuntimeError, match="process died"):
            transport.send_request("test_method")

    @patch("gaia.mcp.client.transports.stdio.subprocess.Popen")
    def test_is_connected_returns_false_when_process_exits(self, mock_popen):
        """Test that is_connected() returns False when process exits."""
        mock_process = Mock()
        mock_process.poll.side_effect = [None, None, 0]  # Alive, alive, then dead
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
