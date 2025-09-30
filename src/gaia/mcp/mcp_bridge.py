#!/usr/bin/env python
"""
GAIA MCP Bridge - HTTP Native Implementation
No WebSockets, just clean HTTP + JSON-RPC for maximum compatibility
"""

import json
import logging
import os
import sys
from typing import Dict, Any, Optional, List
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import threading
import uuid

# Add GAIA to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from gaia.agents.jira.agent import JiraAgent
from gaia.agents.blender.agent import BlenderAgent
from gaia.llm.llm_client import LLMClient
from gaia.logger import get_logger

logger = get_logger(__name__)

# Global verbose flag for request logging
VERBOSE = False


class GAIAMCPBridge:
    """HTTP-native MCP Bridge for GAIA - no WebSockets needed!"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        base_url: str = None,
        verbose: bool = False,
    ):
        self.host = host
        self.port = port
        self.base_url = base_url or "http://localhost:8000/api/v0"
        self.agents = {}
        self.tools = {}
        self.llm_client = None
        self.verbose = verbose
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

            # Blender agent
            try:
                self.agents["blender"] = {
                    "class": BlenderAgent,
                    "description": "3D content creation",
                    "capabilities": ["3d_modeling", "scene_manipulation", "rendering"],
                }
            except ImportError:
                logger.warning("Blender agent not available")

            # Jira agent - THE KEY ADDITION
            try:
                from gaia.agents.jira.agent import JiraAgent

                self.agents["jira"] = {
                    "class": JiraAgent,
                    "description": "Natural language Jira orchestration",
                    "capabilities": ["search", "create", "update", "bulk_operations"],
                    "init_params": {
                        "model_id": "Qwen3-Coder-30B-A3B-Instruct-GGUF",
                        "silent_mode": True,
                        "debug": False,
                    },
                }
                logger.info("✅ Jira agent registered")
            except ImportError as e:
                logger.warning(f"Jira agent not available: {e}")

            logger.info(f"Initialized {len(self.agents)} agents")

        except Exception as e:
            logger.error(f"Agent initialization error: {e}")

    def _register_tools(self):
        """Register available tools."""
        # Load from mcp.json if available
        try:
            mcp_config_path = os.path.join(os.path.dirname(__file__), "mcp.json")
            if os.path.exists(mcp_config_path):
                with open(mcp_config_path, "r") as f:
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

        # Ensure core tools are registered
        if "gaia.jira" not in self.tools:
            self.tools["gaia.jira"] = {
                "name": "gaia.jira",
                "description": "Natural language Jira operations",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": ["query", "create", "update"],
                        },
                    },
                },
            }

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
            if tool_name == "gaia.jira":
                return self._execute_jira(arguments)
            elif tool_name == "gaia.query":
                return self._execute_query(arguments)
            elif tool_name == "gaia.chat":
                return self._execute_chat(arguments)
            elif tool_name == "gaia.blender.create":
                return self._execute_blender(arguments)
            else:
                return {"error": f"Tool not implemented: {tool_name}"}
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return {"error": str(e)}

    def _execute_jira(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Jira operations."""
        query = args.get("query", "")

        # Get or create agent
        agent_config = self.agents.get("jira")
        if not agent_config:
            return {"error": "Jira agent not available"}

        # Lazy initialization
        if "instance" not in agent_config:
            agent_class = agent_config["class"]
            init_params = agent_config.get("init_params", {})
            agent_config["instance"] = agent_class(**init_params)

            # Initialize Jira config discovery
            try:
                config = agent_config["instance"].initialize()
                logger.info(
                    f"Jira initialized: {len(config.get('projects', []))} projects found"
                )
            except Exception as e:
                logger.warning(f"Jira config discovery failed: {e}")

        agent = agent_config["instance"]

        # Execute query
        result = agent.process_query(query, output_to_file=False)

        return {
            "success": True,
            "result": result.get("final_answer", ""),
            "steps_taken": result.get("steps_taken", 0),
            "conversation": result.get("conversation", []),
        }

    def _execute_query(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute LLM query."""
        if not self.llm_client:
            self.llm_client = LLMClient(base_url=self.base_url)

        response = self.llm_client.generate(
            prompt=args.get("query", ""),
            model=args.get("model"),
            max_tokens=args.get("max_tokens", 500),
        )

        return {"success": True, "result": response}

    def _execute_chat(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute chat interaction with conversation context."""
        try:
            from gaia.chat.sdk import ChatSDK, ChatConfig

            # Initialize chat SDK if not already done
            if not hasattr(self, "chat_sdk"):
                # ChatSDK uses the global LLM configuration, not a base_url
                config = ChatConfig()
                self.chat_sdk = ChatSDK(config=config)

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

    def _execute_blender(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Blender operations."""
        # Implementation would go here
        return {"success": True, "result": "Blender operation completed"}


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

    def do_GET(self):
        """Handle GET requests."""
        self.log_request_details("GET", self.path)
        parsed = urlparse(self.path)

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
                        "jira": "POST /jira - Jira operations",
                        "llm": "POST /llm - Direct LLM queries",
                        "jsonrpc": "POST / - JSON-RPC endpoint",
                    },
                },
            )
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        """Handle POST requests - main MCP endpoint."""
        content_length = int(self.headers.get("Content-Length", 0))

        if content_length > 0:
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

        parsed = urlparse(self.path)

        # Handle different endpoints
        if parsed.path in ["/", "/v1/messages", "/rpc"]:
            # JSON-RPC endpoint
            self.handle_jsonrpc(data)
        elif parsed.path == "/chat":
            # Direct chat endpoint for conversations
            result = self.bridge.execute_tool("gaia.chat", data)
            self.send_json(200 if result.get("success") else 500, result)
        elif parsed.path == "/jira":
            # Direct Jira endpoint for convenience
            result = self.bridge.execute_tool("gaia.jira", data)
            self.send_json(200 if result.get("success") else 500, result)
        elif parsed.path == "/llm":
            # Direct LLM endpoint (no conversation context)
            result = self.bridge.execute_tool("gaia.query", data)
            self.send_json(200 if result.get("success") else 500, result)
        else:
            self.send_json(404, {"error": "Not found"})

    def handle_jsonrpc(self, data):
        """Handle JSON-RPC requests."""
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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


def start_server(host="localhost", port=8765, base_url=None, verbose=False):
    """Start the HTTP MCP server."""
    import io

    # Fix Windows Unicode
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    # Create bridge with verbose flag
    bridge = GAIAMCPBridge(host, port, base_url, verbose=verbose)

    # Create handler with bridge
    handler = lambda *args, **kwargs: MCPHTTPHandler(*args, bridge=bridge, **kwargs)

    # Start server
    server = HTTPServer((host, port), handler)

    print("=" * 60)
    print("🚀 GAIA MCP Bridge - HTTP Native")
    print("=" * 60)
    print(f"Server: http://{host}:{port}")
    print(f"LLM Backend: {bridge.base_url}")
    print(f"Agents: {list(bridge.agents.keys())}")
    print(f"Tools: {list(bridge.tools.keys())}")
    if verbose:
        print(f"\n🔍 Verbose Mode: ENABLED")
        print(f"   All requests will be logged to console and gaia.log")
        logger.info("MCP Bridge started in VERBOSE mode - all requests will be logged")
    print("\n📍 Endpoints:")
    print(f"  GET  http://{host}:{port}/health     - Health check")
    print(
        f"  GET  http://{host}:{port}/status      - Detailed status with agents & tools"
    )
    print(f"  GET  http://{host}:{port}/tools      - List tools")
    print(f"  POST http://{host}:{port}/           - JSON-RPC")
    print(f"  POST http://{host}:{port}/chat       - Chat (with context)")
    print(f"  POST http://{host}:{port}/jira       - Direct Jira")
    print(f"  POST http://{host}:{port}/llm        - Direct LLM (no context)")
    print("\n🔧 Usage Examples:")
    print(
        '  Chat: curl -X POST http://localhost:8765/chat -d \'{"query":"Hello GAIA!"}\''
    )
    print(
        '  Jira: curl -X POST http://localhost:8765/jira -d \'{"query":"show my issues"}\''
    )
    print('  n8n: HTTP Request → POST /chat → {"query": "..."}')
    print("  MCP: JSON-RPC to / with method: tools/call")
    print("=" * 60)
    print("\nPress Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✅ Server stopped")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GAIA MCP Bridge - HTTP Native")
    parser.add_argument("--host", default="localhost", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    parser.add_argument(
        "--base-url", default="http://localhost:8000/api/v0", help="LLM server URL"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging for all requests"
    )

    args = parser.parse_args()
    start_server(args.host, args.port, args.base_url, args.verbose)
