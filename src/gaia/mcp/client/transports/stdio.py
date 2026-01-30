"""Stdio transport for MCP protocol communication via subprocess."""

import json
import subprocess
from typing import Any, Dict, Optional

from gaia.logger import get_logger

from .base import MCPTransport

logger = get_logger(__name__)


class StdioTransport(MCPTransport):
    """Stdio-based transport using subprocess for MCP servers.

    This transport launches MCP servers as subprocesses and communicates
    via stdin/stdout using JSON-RPC messages.

    Args:
        command: Shell command to start the MCP server (e.g., "npx server" or "python -m server")
        timeout: Request timeout in seconds (default: 30)
        debug: Enable debug logging (default: False)
    """

    def __init__(self, command: str, timeout: int = 30, debug: bool = False):
        self.command = command
        self.timeout = timeout
        self.debug = debug
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0

    def connect(self) -> bool:
        """Launch the MCP server subprocess.

        Returns:
            bool: True if process started successfully
        """
        if self._process is not None:
            logger.warning("Transport already connected")
            return True

        try:
            if self.debug:
                logger.debug(f"Starting MCP server with command: {self.command}")

            self._process = subprocess.Popen(
                self.command,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )

            logger.debug(f"MCP server process started (PID: {self._process.pid})")
            return True

        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            self._process = None
            return False

    def disconnect(self) -> None:
        """Terminate the MCP server subprocess."""
        if self._process is None:
            return

        try:
            logger.debug(f"Terminating MCP server process (PID: {self._process.pid})")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Process did not terminate, killing...")
                self._process.kill()
                self._process.wait()

            logger.debug("MCP server process terminated")

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

        finally:
            self._process = None

    def send_request(
        self, method: str, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request via stdin and read response from stdout.

        Args:
            method: JSON-RPC method name
            params: Optional parameters dictionary

        Returns:
            dict: JSON-RPC response

        Raises:
            RuntimeError: If not connected or process died
            TimeoutError: If request times out
            ValueError: If response is invalid JSON
        """
        if self._process is None:
            raise RuntimeError("Transport not connected")

        # Check if process is still alive
        if self._process.poll() is not None:
            raise RuntimeError(
                f"MCP server process died (exit code: {self._process.returncode})"
            )

        # Build JSON-RPC request
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        self._request_id += 1

        if self.debug:
            logger.debug(f"Sending request: {json.dumps(request, indent=2)}")

        try:
            # Send request
            request_json = json.dumps(request) + "\n"
            self._process.stdin.write(request_json)
            self._process.stdin.flush()

            # Read response
            try:
                response_line = self._process.stdout.readline()
                if not response_line:
                    raise RuntimeError("Server closed connection")

                response = json.loads(response_line)

                if self.debug:
                    logger.debug(f"Received response: {json.dumps(response, indent=2)}")

                return response

            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON response: {e}")

        except BrokenPipeError:
            raise RuntimeError("Server closed connection unexpectedly")

    def is_connected(self) -> bool:
        """Check if the subprocess is running.

        Returns:
            bool: True if process is alive
        """
        return self._process is not None and self._process.poll() is None
