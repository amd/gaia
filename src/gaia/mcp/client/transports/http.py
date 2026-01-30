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
        """Test connection to the HTTP server.

        Returns:
            bool: True if server is reachable
        """
        try:
            if self.debug:
                logger.debug(f"Testing connection to {self.url}")

            # Try a simple health check or initialize request
            response = self.send_request(
                "initialize",
                {
                    "protocolVersion": "1.0.0",
                    "clientInfo": {"name": "GAIA", "version": "1.0.0"},
                    "capabilities": {},
                },
            )

            self._connected = "result" in response
            if self._connected:
                logger.debug(f"Connected to HTTP MCP server at {self.url}")

            return self._connected

        except Exception as e:
            logger.error(f"Failed to connect to {self.url}: {e}")
            self._connected = False
            return False

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

        try:
            # Prepare HTTP request
            req = urllib.request.Request(
                self.url,
                data=json.dumps(request).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            # Send request and read response
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                response_data = response.read().decode("utf-8")
                response_json = json.loads(response_data)

                if self.debug:
                    logger.debug(
                        f"Received response: {json.dumps(response_json, indent=2)}"
                    )

                return response_json

        except urllib.error.URLError as e:
            raise RuntimeError(f"HTTP request failed: {e}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}")
        except TimeoutError:
            raise TimeoutError(f"Request to {self.url} timed out after {self.timeout}s")

    def is_connected(self) -> bool:
        """Check if HTTP connection is active.

        Returns:
            bool: True if connected
        """
        return self._connected
