# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""HTTP transport for MCP protocol communication."""

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from gaia.logger import get_logger

from .base import MCPTransport

logger = get_logger(__name__)


class HTTPTransport(MCPTransport):
    """HTTP-based transport for remote MCP servers.

    This transport communicates with MCP servers over HTTP using JSON-RPC.

    Args:
        url: HTTP URL of the MCP server (e.g., "http://localhost:8080")
        timeout: Request timeout in seconds (default: 30)
        debug: Enable debug logging (default: False)
    """

    def __init__(self, url: str, timeout: int = 30, debug: bool = False):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.debug = debug
        self._request_id = 0
        self._connected = False

    def connect(self) -> bool:
        """Mark the transport ready; HTTP has no session to open.

        The MCP handshake (``initialize`` then ``notifications/initialized``)
        is :meth:`MCPClient.connect`'s job, so an unreachable server surfaces
        there as an error naming the URL.

        Returns:
            bool: Always True
        """
        self._connected = True
        return True

    def disconnect(self) -> None:
        """Mark connection as closed."""
        self._connected = False
        logger.debug(f"Disconnected from {self.url}")

    def send_request(
        self, method: str, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request via HTTP POST.

        Args:
            method: JSON-RPC method name
            params: Optional parameters dictionary

        Returns:
            dict: JSON-RPC response

        Raises:
            RuntimeError: If connection failed
            TimeoutError: If request times out
            ValueError: If response is invalid JSON
        """
        # Build JSON-RPC request
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params or {},
        }
        self._request_id += 1

        if self.debug:
            logger.debug(f"Sending HTTP request: {json.dumps(request, indent=2)}")

        response_data = self._post(request)
        try:
            response_json = json.loads(response_data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}")

        if self.debug:
            logger.debug(f"Received response: {json.dumps(response_json, indent=2)}")

        return response_json

    def send_notification(self, method: str, params: Dict[str, Any] = None) -> None:
        """POST a JSON-RPC notification; the server answers 202 with no body.

        Raises:
            RuntimeError: If the request failed or the server answered the
                notification with a JSON-RPC error
            TimeoutError: If request times out
        """
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params

        if self.debug:
            logger.debug(f"Sending HTTP notification: {json.dumps(message, indent=2)}")

        body = self._post(message).strip()
        if not body:
            return
        try:
            reply = json.loads(body)
        except json.JSONDecodeError:
            reply = None
        if isinstance(reply, dict) and "error" in reply:
            error = reply["error"]
            detail = error.get("message") if isinstance(error, dict) else error
            raise RuntimeError(
                f"MCP server at {self.url} rejected '{method}': {detail}"
            )

    def _post(self, message: Dict[str, Any]) -> str:
        """POST one JSON-RPC message and return the response body as text."""
        req = urllib.request.Request(
            self.url,
            data=json.dumps(message).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise RuntimeError(f"HTTP request to {self.url} failed: {e}") from e
        except TimeoutError:
            raise TimeoutError(f"Request to {self.url} timed out after {self.timeout}s")
        except ConnectionError as e:
            # http.client.RemoteDisconnected escapes URLError - see do_open().
            raise RuntimeError(
                f"HTTP request to {self.url} dropped mid-flight: {e}. The MCP "
                f"server closed the connection - retry, or check it is still "
                f"running with: gaia mcp status"
            ) from e

    def is_connected(self) -> bool:
        """Check if HTTP connection is active.

        Returns:
            bool: True if connected
        """
        return self._connected
