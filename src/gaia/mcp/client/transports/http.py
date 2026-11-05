# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""HTTP transport for MCP protocol communication."""

import http.client
import ipaddress
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, TypeVar

from gaia.logger import get_logger

from ..redaction import redact
from .base import MCPTransport

logger = get_logger(__name__)

T = TypeVar("T")


class MCPHTTPError(RuntimeError):
    """An HTTP failure talking to a remote MCP server, with its status if any.

    ``status`` is the HTTP status code, or None when no response came back.
    Callers tell "bad token" (401) from "no such endpoint" (404) by it rather
    than by parsing the message. ``refused`` marks a request GAIA itself
    stopped (a redirect, a public peer over plain http); its message says why.
    """

    def __init__(
        self, message: str, status: Optional[int] = None, refused: bool = False
    ):
        super().__init__(message)
        self.status = status
        self.refused = refused


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would re-send our headers to the target.

    The headers carry a bearer token, and a redirect can name any host or
    downgrade to plain http, so a 3xx is reported instead of followed.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MCPHTTPError(
            f"MCP server at {req.full_url} answered with a redirect (HTTP "
            f"{code}) to {newurl}. GAIA does not follow redirects, so the "
            f"access token is never sent to another address. Use the address "
            f"the server redirects to.",
            status=code,
            refused=True,
        )


# Tailscale and other overlay networks hand out CGNAT addresses for LAN peers.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


def is_private_address(address: str) -> bool:
    """Whether ``address`` is on this machine or a private network."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or (ip.version == 4 and ip in _SHARED_ADDRESS_SPACE)
    )


class _PrivatePeerConnection(http.client.HTTPConnection):
    """Plain-http connection that refuses to send anything to a public address.

    Checked on the connected socket, not on a lookup made earlier, so a DNS
    answer that changes between the check and the request cannot slip the
    token to a public host.
    """

    def connect(self):
        super().connect()
        peer = self.sock.getpeername()[0]
        if not is_private_address(peer):
            self.sock.close()
            raise MCPHTTPError(
                f"{self.host} resolved to the public address {peer}. GAIA only "
                f"sends an access token over plain http to an address on this "
                f"PC or your local network. Use the server's https address.",
                refused=True,
            )


class _PrivatePeerHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PrivatePeerConnection, req)


_OPENER = urllib.request.build_opener(_RefuseRedirects())
# No proxy either: a proxy would be the peer, and the LAN needs none.
_PRIVATE_HTTP_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _PrivatePeerHTTPHandler(), _RefuseRedirects()
)


class HTTPTransport(MCPTransport):
    """HTTP-based transport for remote MCP servers.

    Speaks MCP's streamable HTTP transport: every message is a POST, the
    server answers with JSON or an SSE stream, and a session id it hands out
    on ``initialize`` is sent back on every later request.

    Args:
        url: HTTP URL of the MCP server (e.g., "http://localhost:8080/mcp")
        timeout: Request timeout in seconds (default: 30)
        debug: Enable debug logging (default: False)
        headers: Extra headers sent on every request, such as
            ``Authorization``. Never logged.
        private_http_only: Refuse to send a plain-http request to anything but
            a private, loopback or link-local address. https is unaffected.
    """

    def __init__(
        self,
        url: str,
        timeout: int = 30,
        debug: bool = False,
        headers: Optional[Dict[str, str]] = None,
        private_http_only: bool = False,
    ):
        self.url = url.rstrip("/")
        self.extra_headers: Dict[str, str] = dict(headers or {})
        self._opener = (
            _PRIVATE_HTTP_OPENER
            if private_http_only and self.url.lower().startswith("http://")
            else _OPENER
        )
        self.timeout = timeout
        self.debug = debug
        self._request_id = 0
        self._connected = False
        self.session_id: Optional[str] = None
        self.protocol_version: Optional[str] = None

    def redact_value(self, value: Any) -> Any:
        """*value* with every header secret (and its bare token) removed."""
        secrets = []
        for header in self.extra_headers.values():
            secrets.append(header)
            scheme, _, token = header.partition(" ")
            if token and scheme.lower() in ("bearer", "basic", "token"):
                secrets.append(token)
        if not secrets:
            return value
        if isinstance(value, str):
            return redact(value, secrets)
        if isinstance(value, dict):
            return {
                (redact(k, secrets) if isinstance(k, str) else k): self.redact_value(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.redact_value(v) for v in value]
        return value

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
            with self._opener.open(req, timeout=self.timeout):
                pass
        except MCPHTTPError as e:
            logger.warning(f"Could not end the MCP session at {self.url}: {e}")
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

        response_json = self._post(
            request, lambda response: self._read_reply(response, request["id"])
        )

        if self.debug:
            logger.debug(f"Received response: {json.dumps(response_json, indent=2)}")

        if method == "initialize":
            result = response_json.get("result")
            if isinstance(result, dict) and isinstance(
                result.get("protocolVersion"), str
            ):
                self.protocol_version = result["protocolVersion"]

        return response_json

    def _read_reply(self, response: Any, request_id: int) -> Dict[str, Any]:
        """Read the JSON-RPC reply to ``request_id`` from a POST response."""
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            return self._reply_from_sse(response, request_id)
        try:
            return json.loads(response.read().decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}")

    def _reply_from_sse(self, response: Any, request_id: int) -> Dict[str, Any]:
        """Return the message answering ``request_id`` from an SSE stream.

        Reads one event at a time and stops at the reply, since the server
        may keep the stream open after it. The stream can carry the server's
        own notifications before the reply, so those are skipped. An error
        with a null id is the server refusing the request itself, so it is
        returned.
        """
        data_lines = []
        while True:
            raw = response.readline()
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data_lines.append(line[5:].removeprefix(" "))
            if line and raw:
                continue
            # A blank line ends an event; so does the end of the stream.
            message = self._sse_message("\n".join(data_lines))
            data_lines = []
            if message is not None and (
                message.get("id") == request_id
                or (message.get("id") is None and "error" in message)
            ):
                return message
            if not raw:
                break
        raise ValueError(
            f"MCP server at {self.url} closed the SSE stream without "
            f"answering request {request_id}. Resuming a stream with "
            f"Last-Event-ID is not supported yet."
        )

    def _sse_message(self, data: str) -> Optional[Dict[str, Any]]:
        """Parse one SSE event's data; None for events that carry no message."""
        if not data:
            return None
        try:
            message = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in SSE event from {self.url}: {e}")
        return message if isinstance(message, dict) else None

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

        body = self._post(
            message, lambda response: response.read().decode("utf-8")
        ).strip()
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
        headers = dict(self.extra_headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, message: Dict[str, Any], read: Callable[[Any], T]) -> T:
        """POST one JSON-RPC message and return ``read(response)``."""
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
            with self._opener.open(req, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                return read(response)
        except urllib.error.HTTPError as e:
            if e.code == 404 and self.session_id:
                self.session_id = None
                self.protocol_version = None
                self._connected = False
                raise MCPHTTPError(
                    f"MCP server at {self.url} no longer knows this session. "
                    f"Reconnect to start a new one.",
                    status=404,
                ) from e
            raise MCPHTTPError(
                f"HTTP request to {self.url} failed: HTTP {e.code} {e.reason}",
                status=e.code,
            ) from e
        except urllib.error.URLError as e:
            raise MCPHTTPError(f"HTTP request to {self.url} failed: {e.reason}") from e
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
