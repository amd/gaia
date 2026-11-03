# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""HTTP transport for MCP protocol communication."""

import http.client
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from gaia.logger import get_logger

from .base import MCPTransport

logger = get_logger(__name__)


class HTTPTransport(MCPTransport):
    """HTTP-based transport for remote MCP servers.

    Speaks MCP's streamable HTTP transport: every message is a POST, the
    server answers with JSON or an SSE stream, and a session id it hands out
    on ``initialize`` is sent back on every later request.

    Args:
        url: HTTP URL of the MCP server (e.g., "http://localhost:8080/mcp")
        timeout: Request timeout in seconds (default: 30)
        debug: Enable debug logging (default: False)
    """

    def __init__(self, url: str, timeout: int = 30, debug: bool = False):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.debug = debug
        self._request_id = 0
        self._connected = False
        self.session_id: Optional[str] = None
        self.protocol_version: Optional[str] = None

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
        """End the server session, if one was opened, and mark closed."""
        if self.session_id:
            self._end_session()
        self.session_id = None
        self.protocol_version = None
        self._connected = False
        logger.debug(f"Disconnected from {self.url}")

    def _end_session(self) -> None:
        """DELETE the session so the server can free it."""
        req = urllib.request.Request(self.url, headers=self._headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
        except urllib.error.HTTPError as e:
            # 405: the server doesn't let clients end sessions. 404: already gone.
            if e.code not in (404, 405):
                logger.warning(
                    f"MCP server at {self.url} refused to end the session: "
                    f"HTTP {e.code}"
                )
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
        ) as e:
            logger.warning(f"Could not end the MCP session at {self.url}: {e}")

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

        if method == "initialize":
            # A new session starts without the old one's headers.
            self.session_id = None
            self.protocol_version = None

        content_type, body = self._post(request)
        if "text/event-stream" in content_type:
            response_json = self._reply_from_sse(body, request["id"])
        else:
            try:
                response_json = json.loads(body)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON response: {e}")

        if self.debug:
            logger.debug(f"Received response: {json.dumps(response_json, indent=2)}")

        if method == "initialize":
            result = response_json.get("result")
            if isinstance(result, dict) and isinstance(
                result.get("protocolVersion"), str
            ):
                self.protocol_version = result["protocolVersion"]

        return response_json

    def _reply_from_sse(self, body: str, request_id: int) -> Dict[str, Any]:
        """Return the message answering ``request_id`` from an SSE stream.

        The stream can carry the server's own notifications before the reply,
        so those are skipped. An error with a null id is the server refusing
        the request itself, so it is returned.
        """
        for event in body.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[5:].removeprefix(" ")
                for line in event.split("\n")
                if line.startswith("data:")
            )
            if not data:
                continue
            try:
                message = json.loads(data)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in SSE event from {self.url}: {e}")
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id:
                return message
            if message.get("id") is None and "error" in message:
                return message
        raise ValueError(
            f"MCP server at {self.url} closed the SSE stream without "
            f"answering request {request_id}. Resuming a stream with "
            f"Last-Event-ID is not supported yet."
        )

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

        body = self._post(message)[1].strip()
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

    def _headers(self) -> Dict[str, str]:
        headers = {}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, message: Dict[str, Any]) -> Tuple[str, str]:
        """POST one JSON-RPC message; return the reply's content type and body."""
        req = urllib.request.Request(
            self.url,
            data=json.dumps(message).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **self._headers(),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                content_type = response.headers.get("Content-Type", "")
                return content_type, response.read().decode("utf-8")
        except urllib.error.URLError as e:
            if getattr(e, "code", None) == 404 and self.session_id:
                self.session_id = None
                self.protocol_version = None
                raise RuntimeError(
                    f"MCP server at {self.url} no longer knows this session. "
                    f"Reconnect to start a new one."
                ) from e
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
