#!/usr/bin/env python
#
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA MCP Bridge - HTTP Native Implementation
No WebSockets, just clean HTTP + JSON-RPC for maximum compatibility
"""

import io
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict
from urllib.parse import urlparse

# Add GAIA to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from gaia.llm import create_client  # pylint: disable=wrong-import-position
from gaia.logger import get_logger  # pylint: disable=wrong-import-position

# pylint: enable=wrong-import-position

logger = get_logger(__name__)

# Global verbose flag for request logging
VERBOSE = False

# Environment variable used to hand the bridge its auth token without exposing
# it in the process command line.
AUTH_TOKEN_ENV_VAR = "GAIA_MCP_AUTH_TOKEN"

# Paths reachable without credentials even when a token is configured. /health
# returns only liveness plus agent/tool counts, so orchestrator probes and
# `gaia mcp status` keep working. Matching is exact — every other path,
# including unknown ones, is authenticated. (CORS preflight is also exempt, but
# via do_OPTIONS: browsers never send Authorization on a preflight.)
PUBLIC_PATHS = frozenset({"/health"})


class GAIAMCPBridge:
    """HTTP-native MCP Bridge for GAIA - no WebSockets needed!"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        base_url: str = None,
        verbose: bool = False,
        auth_token: str = None,
    ):
        self.host = host
        self.port = port
        self.base_url = base_url or "http://localhost:13305/api/v1"
        self.auth_token = auth_token or None
        self.agents = {}
        self.tools = {}
        self.llm_client = None
        self.chat_sdk = None
        self.verbose = verbose
        self.chat_sdk = None  # Lazy initialized in _execute_chat
        global VERBOSE
        VERBOSE = verbose

        # Initialize on creation
        self._initialize_agents()
        self._register_tools()

    def _initialize_agents(self):
        """Initialize all GAIA agents."""
        try:
            # LLM agent
            self.agents["llm"] = {
                "module": "gaia.apps.llm.app",
                "function": "main",
                "description": "Direct LLM interaction",
                "capabilities": ["query", "stream", "model_selection"],
            }

            # Chat agent
            self.agents["chat"] = {
                "module": "gaia.chat.app",
                "function": "main",
                "description": "Interactive chat",
                "capabilities": ["conversation", "history", "context_management"],
            }

            logger.info(f"Initialized {len(self.agents)} agents")

        except Exception as e:
            logger.error(f"Agent initialization error: {e}")

    def _register_tools(self):
        """Register available tools."""
        # Load from mcp.json if available
        try:
            mcp_config_path = os.path.join(os.path.dirname(__file__), "mcp.json")
            if os.path.exists(mcp_config_path):
                with open(mcp_config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    tools_config = config.get("tools", {})
                    # Convert tool config to proper MCP format with name field
                    self.tools = {}
                    for tool_name, tool_data in tools_config.items():
                        self.tools[tool_name] = {
                            "name": tool_name,
                            "description": tool_data.get("description", ""),
                            "servers": tool_data.get("servers", []),
                            "parameters": tool_data.get("parameters", {}),
                        }
                    logger.info(f"Loaded {len(self.tools)} tools from mcp.json")
        except Exception as e:
            logger.warning(f"Could not load mcp.json: {e}")

        if "gaia.chat" not in self.tools:
            self.tools["gaia.chat"] = {
                "name": "gaia.chat",
                "description": "Conversational chat with context",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            }

        if "gaia.query" not in self.tools:
            self.tools["gaia.query"] = {
                "name": "gaia.query",
                "description": "Direct LLM queries (no conversation context)",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            }

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool and return results."""
        try:
            if tool_name == "gaia.query":
                return self._execute_query(arguments)
            elif tool_name == "gaia.chat":
                return self._execute_chat(arguments)
            else:
                return {"error": f"Tool not implemented: {tool_name}"}
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return {"error": str(e)}

    def _execute_query(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute LLM query."""
        if not self.llm_client:
            self.llm_client = create_client("lemonade", base_url=self.base_url)

        response = self.llm_client.generate(
            prompt=args.get("query", ""),
            model=args.get("model"),
            max_tokens=args.get("max_tokens", 500),
        )

        return {"success": True, "result": response}

    def _execute_chat(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute chat interaction with conversation context."""
        try:
            from gaia.chat.sdk import AgentConfig, AgentSDK

            # Initialize chat SDK if not already done
            if self.chat_sdk is None:
                # AgentSDK uses the global LLM configuration, not a base_url
                config = AgentConfig()
                self.chat_sdk = AgentSDK(config=config)

            # Get the query
            query = args.get("query", "")

            # Send message and get response
            chat_response = self.chat_sdk.send(query)

            # Extract the text response
            if hasattr(chat_response, "text"):
                response = chat_response.text
            elif hasattr(chat_response, "content"):
                response = chat_response.content
            else:
                response = str(chat_response)

            return {"success": True, "result": response}
        except Exception as e:
            logger.error(f"Chat execution error: {e}")
            return {"success": False, "error": str(e)}


class MCPHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for MCP protocol."""

    def __init__(self, *args, bridge: GAIAMCPBridge = None, **kwargs):
        self.bridge = bridge or GAIAMCPBridge()
        super().__init__(*args, **kwargs)

    def log_request_details(self, method, path, body=None):
        """Log incoming request details if verbose mode is enabled."""
        if VERBOSE:
            client_addr = self.client_address[0] if self.client_address else "unknown"
            logger.info(f"MCP Request: {method} {path} from {client_addr}")
            if body:
                logger.debug(f"Request body: {json.dumps(body, indent=2)}")

    def _check_auth(self):
        """Classify the request's credentials.

        Returns ``None`` when the request may proceed, otherwise an
        ``(http_status, message)`` pair to send back.
        """
        if not self.bridge.auth_token:
            return None

        header = self.headers.get("Authorization", "")
        if not header:
            return 401, "Missing Authorization header. Expected: Bearer <token>"

        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not presented:
            return 401, "Malformed Authorization header. Expected: Bearer <token>"

        # compare_digest keeps the check constant-time so a network caller
        # can't recover the token byte-by-byte from response timing. Compare as
        # bytes — the str form rejects non-ASCII input with a TypeError.
        if not secrets.compare_digest(
            presented.encode("utf-8", "surrogateescape"),
            self.bridge.auth_token.encode("utf-8", "surrogateescape"),
        ):
            return 403, "Invalid authentication token"

        return None

    def _drain_request_body(self):
        """Consume any pending request body so the client can read our reply."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return
        while length > 0:
            chunk = self.rfile.read(min(length, 65536))
            if not chunk:
                break
            length -= len(chunk)

    def _reject_unauthenticated(self, path):
        """Send 401/403 for a credential-less request. True when rejected."""
        if path in PUBLIC_PATHS:
            return False

        failure = self._check_auth()
        if failure is None:
            return False

        status, message = failure
        client_addr = self.client_address[0] if self.client_address else "unknown"
        logger.warning(
            "Rejected unauthenticated MCP request: %s %s from %s (%s)",
            self.command,
            path,
            client_addr,
            message,
        )
        self._drain_request_body()
        self.send_json(status, {"error": message})
        return True

    def do_GET(self):
        """Handle GET requests."""
        self.log_request_details("GET", self.path)
        parsed = urlparse(self.path)

        if self._reject_unauthenticated(parsed.path):
            return

        if parsed.path == "/health":
            self.send_json(
                200,
                {
                    "status": "healthy",
                    "service": "GAIA MCP Bridge (HTTP)",
                    "agents": len(self.bridge.agents),
                    "tools": len(self.bridge.tools),
                },
            )
        elif parsed.path == "/tools" or parsed.path == "/v1/tools":
            self.send_json(200, {"tools": list(self.bridge.tools.values())})
        elif parsed.path == "/status":
            # Comprehensive status endpoint with all details
            agents_info = {}
            for name, agent in self.bridge.agents.items():
                agents_info[name] = {
                    "description": agent.get("description", ""),
                    "capabilities": agent.get("capabilities", []),
                    "type": "class" if "class" in agent else "module",
                }

            tools_info = {}
            for name, tool in self.bridge.tools.items():
                tools_info[name] = {
                    "description": tool.get("description", ""),
                    "inputSchema": tool.get("inputSchema", {}),
                }

            self.send_json(
                200,
                {
                    "status": "healthy",
                    "service": "GAIA MCP Bridge (HTTP)",
                    "version": "2.0.0",
                    "host": self.bridge.host,
                    "port": self.bridge.port,
                    "llm_backend": self.bridge.base_url,
                    "agents": agents_info,
                    "tools": tools_info,
                    "endpoints": {
                        "health": "GET /health - Health check",
                        "status": "GET /status - Detailed status (this endpoint)",
                        "tools": "GET /tools - List available tools",
                        "chat": "POST /chat - Interactive chat",
                        "llm": "POST /llm - Direct LLM queries",
                        "jsonrpc": "POST / - JSON-RPC endpoint",
                    },
                },
            )
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        """Handle POST requests - main MCP endpoint."""
        parsed = urlparse(self.path)

        # Authenticate before the body is read or any tool runs.
        if self._reject_unauthenticated(parsed.path):
            return

        content_length = int(self.headers.get("Content-Length", 0))
        ctype = self.headers.get("content-type", "")

        if ctype.startswith("application/json") and content_length > 0:
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                self.log_request_details("POST", self.path, data)
            except json.JSONDecodeError:
                self.log_request_details("POST", self.path)
                logger.error("Invalid JSON in request body")
                self.send_json(400, {"error": "Invalid JSON"})
                return
        else:
            data = {}
            self.log_request_details("POST", self.path)

        # Handle different endpoints
        if parsed.path in ["/", "/v1/messages", "/rpc"]:
            # JSON-RPC endpoint
            self.handle_jsonrpc(data)
        elif parsed.path == "/chat":
            # Direct chat endpoint for conversations
            result = self.bridge.execute_tool("gaia.chat", data)
            self.send_json(200 if result.get("success") else 500, result)
        elif parsed.path == "/llm":
            # Direct LLM endpoint (no conversation context)
            result = self.bridge.execute_tool("gaia.query", data)
            self.send_json(200 if result.get("success") else 500, result)
        else:
            self.send_json(404, {"error": "Not found"})

    def handle_jsonrpc(self, data):
        """Handle JSON-RPC requests."""
        # Validate that data is a dict (JSON-RPC requires an object)
        if not isinstance(data, dict):
            self.send_json(
                400,
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32600,
                        "message": "Invalid Request: expected JSON object",
                    },
                    "id": None,
                },
            )
            return
        # Validate JSON-RPC
        if "jsonrpc" not in data or data["jsonrpc"] != "2.0":
            self.send_json(
                400,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": "Invalid Request"},
                    "id": data.get("id"),
                },
            )
            return

        method = data.get("method")
        params = data.get("params", {})
        request_id = data.get("id")

        # Route methods
        if method == "initialize":
            result = {
                "protocolVersion": "1.0.0",
                "serverInfo": {"name": "GAIA MCP Bridge", "version": "2.0.0"},
                "capabilities": {"tools": True, "resources": True, "prompts": True},
            }
        elif method == "tools/list":
            result = {"tools": list(self.bridge.tools.values())}
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            tool_result = self.bridge.execute_tool(tool_name, arguments)
            result = {"content": [{"type": "text", "text": json.dumps(tool_result)}]}
        else:
            self.send_json(
                400,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": request_id,
                },
            )
            return

        # Send response
        self.send_json(200, {"jsonrpc": "2.0", "result": result, "id": request_id})

    def do_OPTIONS(self):
        """Handle OPTIONS for CORS."""
        self.log_request_details("OPTIONS", self.path)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def send_sse_headers(self):
        """Send standard headers for Server-Sent Events."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def stream_sse(self, iterator):
        """Stream SSE data from an iterator of chunk dicts."""
        for chunk in iterator:
            if chunk.get("is_complete"):
                data_out = json.dumps(
                    {"event": "complete", "performance": chunk.get("performance", {})}
                )
            else:
                data_out = json.dumps({"text": chunk.get("text", "")})
            self.wfile.write(f"data: {data_out}\n\n".encode("utf-8"))
            self.wfile.flush()

    def send_json(self, status, data):
        """Send JSON response."""
        if VERBOSE:
            logger.info(f"MCP Response: Status {status}")
            logger.debug(f"Response body: {json.dumps(data, indent=2)}")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        """Override to control standard HTTP logging."""
        # In verbose mode, skip the built-in HTTP logging since we have custom logging
        if VERBOSE:
            # We already log detailed info in log_request_details and send_json
            pass
        elif "/health" not in args[0]:
            # In non-verbose mode, skip health checks but log everything else
            super().log_message(format, *args)


def resolve_bind_host(host, authenticated=False):
    """Map the requested host to the address the socket actually binds.

    "localhost" must never widen beyond loopback. On non-Windows it resolves to
    127.0.0.1 (Python may otherwise bind ::1, which curl can't reach by
    default). Binding all interfaces requires the caller to pass a wildcard
    address explicitly, and is logged — as a warning when no auth token is
    configured, since then anyone on the network can invoke the bridge's tools.
    """
    if host == "localhost" and sys.platform != "win32":
        return "127.0.0.1"
    if host in ("0.0.0.0", "::"):  # nosec B104 - explicit caller opt-in only
        if authenticated:
            logger.info(
                "MCP bridge binding to ALL network interfaces (%s) with "
                "authentication enabled.",
                host,
            )
        else:
            logger.warning(
                "MCP bridge binding to ALL network interfaces (%s). The bridge is "
                "UNAUTHENTICATED - anyone on the network can invoke its tools. "
                "Pass --auth-token, or use --host localhost unless network "
                "exposure is intentional.",
                host,
            )
    return host


def start_server(
    host="localhost", port=8765, base_url=None, verbose=False, auth_token=None
):
    """Start the HTTP MCP server."""
    # Fix Windows Unicode
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    auth_token = auth_token or os.environ.get(AUTH_TOKEN_ENV_VAR) or None
    bind_host = resolve_bind_host(host, authenticated=bool(auth_token))

    logger.info(f"Creating MCP bridge for {host}:{port}")

    # Create bridge with verbose flag
    bridge = GAIAMCPBridge(host, port, base_url, verbose=verbose, auth_token=auth_token)

    # Create handler with bridge
    def handler(*args, **kwargs):
        return MCPHTTPHandler(*args, bridge=bridge, **kwargs)

    # Start server - use bind_host for actual socket binding
    logger.info(f"Creating HTTP server on {bind_host}:{port}")
    try:
        server = HTTPServer((bind_host, port), handler)
        logger.info(
            f"HTTP server created successfully, listening on {bind_host}:{port}"
        )
    except Exception as e:
        logger.error(f"Failed to create HTTP server: {e}")
        raise

    print("=" * 60, flush=True)
    print("🚀 GAIA MCP Bridge - HTTP Native")
    print("=" * 60)
    print(f"Server: http://{host}:{port}")
    print(f"LLM Backend: {bridge.base_url}")
    print(f"Agents: {list(bridge.agents.keys())}")
    print(f"Tools: {list(bridge.tools.keys())}")
    if bridge.auth_token:
        print("Auth: 🔒 Bearer token required (/health stays public)")
    else:
        print("Auth: ⚠️  none - every endpoint is open to any client that can reach it")
    if verbose:
        print("\n🔍 Verbose Mode: ENABLED")
        print("   All requests will be logged to console and gaia.log")
        logger.info("MCP Bridge started in VERBOSE mode - all requests will be logged")
    print("\n📍 Endpoints:")
    print(f"  GET  http://{host}:{port}/health     - Health check")
    print(
        f"  GET  http://{host}:{port}/status      - Detailed status with agents & tools"
    )
    print(f"  GET  http://{host}:{port}/tools      - List tools")
    print(f"  POST http://{host}:{port}/           - JSON-RPC")
    print(f"  POST http://{host}:{port}/chat       - Chat (with context)")
    print(f"  POST http://{host}:{port}/llm        - Direct LLM (no context)")
    print("\n🔧 Usage Examples:")
    print(
        '  Chat: curl -X POST http://localhost:8765/chat -d \'{"query":"Hello GAIA!"}\''
    )
    print('  n8n: HTTP Request → POST /chat → {"query": "..."}')
    print("  MCP: JSON-RPC to / with method: tools/call")
    print("=" * 60)
    print("\nPress Ctrl+C to stop\n", flush=True)

    logger.info(f"Starting serve_forever() on {bind_host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✅ Server stopped")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GAIA MCP Bridge - HTTP Native")
    parser.add_argument("--host", default="localhost", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    parser.add_argument(
        "--base-url", default="http://localhost:13305/api/v1", help="LLM server URL"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging for all requests"
    )
    parser.add_argument(
        "--auth-token",
        help=(
            "Require 'Authorization: Bearer <token>' on every request except "
            f"/health. Defaults to ${AUTH_TOKEN_ENV_VAR}."
        ),
    )

    args = parser.parse_args()
    start_server(
        args.host, args.port, args.base_url, args.verbose, auth_token=args.auth_token
    )


if __name__ == "__main__":
    main()
