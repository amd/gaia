"""Base transport interface for MCP protocol communication."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class MCPTransport(ABC):
    """Abstract base class for MCP transport implementations.

    Transport layer handles the communication protocol between GAIA and MCP servers.
    Different implementations support different connection methods (stdio, HTTP, etc.).
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the MCP server.

        Returns:
            bool: True if connection successful, False otherwise
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection to the MCP server."""
        ...

    @abstractmethod
    def send_request(
        self, method: str, params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request to the server.

        Args:
            method: The JSON-RPC method name (e.g., "initialize", "tools/list")
            params: Optional parameters dictionary

        Returns:
            dict: JSON-RPC response containing "result" or "error"

        Raises:
            RuntimeError: If connection is not established
            TimeoutError: If request times out
        """
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is currently connected.

        Returns:
            bool: True if connected, False otherwise
        """
        ...
