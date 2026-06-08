# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Framework-provided generic server — the 5-interface standard for agents.

Every Agent Hub package should be runnable through the same five interface
modes (the gaia-bash PR #985 pattern): an interactive **TUI**, a one-shot
**CLI** (``--prompt``), a **pipe** mode (stdin → stdout), an OpenAI-compatible
**REST API server** (``--api``), and an **MCP stdio server** (``--mcp``).

Agents should not re-implement any of that.  This module wraps *any*
:class:`~gaia.agents.base.agent.Agent` instance and exposes it through all five
interfaces, reusing the existing REST schemas (:mod:`gaia.api.schemas`) and MCP
tool conventions (:mod:`gaia.mcp`).  An agent entry point becomes::

    from gaia.agents.base.server import run_agent_cli
    from my_pkg.agent import MyAgent

    def main() -> int:
        return run_agent_cli(MyAgent())

Which interfaces a package supports is declared in its ``gaia-agent.yaml``
``interfaces`` block (parsed into :class:`gaia.hub.manifest.Interfaces`).  When
a manifest is supplied, requesting a disabled interface fails loudly with an
actionable error instead of silently doing something unexpected.

Spec: ``docs/spec/agent-hub-restructure.mdx`` (Key Decision #5 — framework
generic server; ``interfaces`` block).
"""

import argparse
import json
import sys
import time
import uuid
from typing import IO, Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.api_agent import ApiAgent
from gaia.logger import get_logger

logger = get_logger(__name__)

# The five interface modes a package may declare in its manifest. These names
# match :data:`gaia.hub.manifest.VALID_INTERFACES` and the ``interfaces:`` keys.
INTERFACE_TUI = "tui"
INTERFACE_CLI = "cli"
INTERFACE_PIPE = "pipe"
INTERFACE_API = "api_server"
INTERFACE_MCP = "mcp_server"

# MCP protocol version we advertise in ``initialize``. Matches the revision the
# MCP Python SDK targets; clients negotiate down if they need an older one.
_MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-Schema type for each registry parameter type (see base/tools.py). The
# registry stores a coarse type tag per parameter; anything unrecognized maps to
# an open string so the client still gets a usable schema.
_REGISTRY_TYPE_TO_JSON = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


class InterfaceNotSupportedError(RuntimeError):
    """Raised when an interface mode is requested that the manifest disables.

    The message names *what* was requested, *what* the package declares, and
    *where* to change it (the ``interfaces`` block), per the fail-loudly rule.
    """


class AgentServer:
    """Serve a single :class:`Agent` instance through the 5 standard interfaces.

    The wrapper is interface-agnostic: it takes a fully-constructed agent and
    adds REST, MCP, pipe, CLI, and TUI front-ends around it. It never re-runs
    LLM logic itself — every mode funnels into ``agent.process_query`` (or, for
    MCP ``tools/call``, the agent's tool execution path).

    Args:
        agent: The agent instance to expose. Already constructed so the caller
            controls model, device, silent_mode, etc.
        manifest: Optional parsed ``gaia-agent.yaml``. When provided, its
            ``interfaces`` block gates which modes are allowed — a disabled mode
            raises :class:`InterfaceNotSupportedError`. When ``None`` (agent run
            directly, no package), all interfaces are permitted.
        model_id: OpenAI-compatible model id for the REST surface. Defaults to
            the agent's :meth:`ApiAgent.get_model_id` (or ``gaia-<classname>``).
        name: Human-readable server name (banners, MCP ``serverInfo``).
    """

    def __init__(
        self,
        agent: Agent,
        *,
        manifest: Any = None,
        model_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        if not isinstance(agent, Agent):
            raise TypeError(
                f"AgentServer requires a gaia Agent instance, got "
                f"{type(agent).__name__}. Construct the agent first, then wrap "
                f"it: AgentServer(MyAgent())."
            )
        self.agent = agent
        self.manifest = manifest
        self.model_id = model_id or self._default_model_id(agent)
        self.name = name or agent.__class__.__name__

    # ------------------------------------------------------------------
    # Interface gating (fail loudly)
    # ------------------------------------------------------------------

    @staticmethod
    def _default_model_id(agent: Agent) -> str:
        if isinstance(agent, ApiAgent):
            return agent.get_model_id()
        cls = agent.__class__.__name__
        base = cls[:-5].lower() if cls.endswith("Agent") else cls.lower()
        return f"gaia-{base}"

    def _ensure_interface(self, interface: str) -> None:
        """Raise if *interface* is disabled by the manifest's ``interfaces``.

        With no manifest the agent is being run standalone, so every interface
        is allowed. With a manifest, the declared block is authoritative.
        """
        if self.manifest is None:
            return
        interfaces = getattr(self.manifest, "interfaces", None)
        if interfaces is None:
            return
        if not getattr(interfaces, interface, False):
            agent_id = getattr(self.manifest, "id", self.name)
            raise InterfaceNotSupportedError(
                f"Agent '{agent_id}' does not support the '{interface}' "
                f"interface. Its gaia-agent.yaml 'interfaces' block declares "
                f"this mode disabled. Enable it by setting "
                f"'interfaces.{interface}: true' in the manifest, or run a "
                f"supported mode."
            )

    # ------------------------------------------------------------------
    # Tool introspection (shared by REST /v1/tools and MCP tools/list)
    # ------------------------------------------------------------------

    def _tool_definitions(self) -> List[Dict[str, Any]]:
        """Return MCP-style tool definitions for this agent.

        For an :class:`~gaia.agents.base.mcp_agent.MCPAgent` the agent's own
        ``get_mcp_tool_definitions`` is authoritative. For a plain agent the
        definitions are synthesized from its tool registry so any agent — not
        just MCP-aware ones — gets a usable schema.
        """
        from gaia.agents.base.mcp_agent import MCPAgent

        if isinstance(self.agent, MCPAgent):
            return list(self.agent.get_mcp_tool_definitions())

        definitions: List[Dict[str, Any]] = []
        for name, info in self.agent.get_tools_info().items():
            properties: Dict[str, Any] = {}
            required: List[str] = []
            for param_name, param_info in info.get("parameters", {}).items():
                json_type = _REGISTRY_TYPE_TO_JSON.get(
                    param_info.get("type", "string"), "string"
                )
                properties[param_name] = {"type": json_type}
                if param_info.get("required"):
                    required.append(param_name)
            definitions.append(
                {
                    "name": name,
                    "description": (info.get("description") or "").strip(),
                    "inputSchema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }
            )
        return definitions

    def _execute_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Execute one tool by name, routing through the agent's own path."""
        from gaia.agents.base.mcp_agent import MCPAgent

        if isinstance(self.agent, MCPAgent):
            return self.agent.execute_mcp_tool(name, arguments)
        return self.agent._execute_tool(name, arguments)  # noqa: SLF001

    def _prompt_definitions(self) -> List[Dict[str, Any]]:
        """Return MCP ``prompts/list`` entries.

        MCP-aware agents provide their own prompts. Otherwise the manifest's
        ``conversation_starters`` become simple, argument-less prompts so a
        client still sees the package's suggested entry points.
        """
        from gaia.agents.base.mcp_agent import MCPAgent

        if isinstance(self.agent, MCPAgent):
            prompts = self.agent.get_mcp_prompts()
            if prompts:
                return list(prompts)

        starters = getattr(self.manifest, "conversation_starters", None) or []
        return [
            {"name": f"starter_{i}", "description": text}
            for i, text in enumerate(starters)
        ]

    # ------------------------------------------------------------------
    # Result extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_content(result: Any) -> str:
        """Pull human-readable text out of an agent ``process_query`` result."""
        if isinstance(result, dict):
            return str(result.get("result", result.get("response", result)))
        return str(result)

    def _run_query(self, prompt: str) -> str:
        return self._extract_content(self.agent.process_query(prompt))

    # ------------------------------------------------------------------
    # CLI mode (--prompt)
    # ------------------------------------------------------------------

    def run_cli(self, prompt: str) -> str:
        """Run a single query and return its text (also printed to stdout)."""
        self._ensure_interface(INTERFACE_CLI)
        content = self._run_query(prompt)
        print(content)
        return content

    # ------------------------------------------------------------------
    # Pipe mode (stdin -> stdout)
    # ------------------------------------------------------------------

    def run_pipe(
        self, stdin: Optional[IO[str]] = None, stdout: Optional[IO[str]] = None
    ) -> str:
        """Read the whole of *stdin* as one prompt, write the answer to *stdout*.

        Pipe mode is the Unix-composable interface: ``echo "..." | agent --pipe``.
        The entire input stream is one prompt; the agent's answer is written as
        plain text (no banner, no prompt echo) so it can feed the next command.
        """
        self._ensure_interface(INTERFACE_PIPE)
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout

        prompt = stdin.read().strip()
        if not prompt:
            raise ValueError(
                "Pipe mode received empty stdin. Pipe a prompt into the agent, "
                'e.g. `echo "summarize this" | agent --pipe`.'
            )
        content = self._run_query(prompt)
        stdout.write(content)
        if not content.endswith("\n"):
            stdout.write("\n")
        stdout.flush()
        return content

    # ------------------------------------------------------------------
    # MCP stdio mode (--mcp)
    # ------------------------------------------------------------------

    def handle_mcp_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Dispatch one JSON-RPC 2.0 MCP request, returning the response.

        Returns ``None`` for notifications (requests without an ``id``), which
        per JSON-RPC must not produce a response. Supported methods:
        ``initialize``, ``tools/list``, ``tools/call``, ``prompts/list``,
        ``ping``. Unknown methods get a JSON-RPC ``-32601`` error.
        """
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        # Notifications (no id) are fire-and-forget — never answer them.
        if request_id is None and method != "ping":
            return None

        try:
            if method == "initialize":
                result: Dict[str, Any] = {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}, "prompts": {}},
                    "serverInfo": {"name": self.name, "version": "1.0.0"},
                }
            elif method == "tools/list":
                result = {"tools": self._tool_definitions()}
            elif method == "tools/call":
                result = self._handle_tools_call(params)
            elif method == "prompts/list":
                result = {"prompts": self._prompt_definitions()}
            elif method == "ping":
                result = {}
            else:
                return self._jsonrpc_error(
                    request_id, -32601, f"Method not found: {method}"
                )
        except Exception as exc:  # noqa: BLE001 - boundary: JSON-RPC error out
            logger.error("MCP method %s failed: %s", method, exc, exc_info=True)
            return self._jsonrpc_error(request_id, -32603, str(exc))

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _handle_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if not name:
            raise ValueError("tools/call requires a 'name' parameter")
        arguments = params.get("arguments") or {}
        tool_result = self._execute_tool(name, arguments)

        is_error = (
            isinstance(tool_result, dict) and tool_result.get("status") == "error"
        )
        if isinstance(tool_result, str):
            text = tool_result
        else:
            text = json.dumps(tool_result, default=str)
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    @staticmethod
    def _jsonrpc_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def run_mcp(
        self, stdin: Optional[IO[str]] = None, stdout: Optional[IO[str]] = None
    ) -> None:
        """Serve MCP over newline-delimited JSON-RPC on stdin/stdout.

        Reads one JSON object per line, dispatches via
        :meth:`handle_mcp_request`, and writes each response as one JSON line.
        Blank lines are skipped; malformed JSON yields a JSON-RPC parse error.
        The loop ends at EOF. stdout carries *only* JSON-RPC — diagnostics go to
        stderr — so a desktop MCP client's parser is never corrupted.
        """
        self._ensure_interface(INTERFACE_MCP)
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout

        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                self._write_mcp(stdout, self._jsonrpc_error(None, -32700, str(exc)))
                continue
            response = self.handle_mcp_request(request)
            if response is not None:
                self._write_mcp(stdout, response)

    @staticmethod
    def _write_mcp(stdout: IO[str], message: Dict[str, Any]) -> None:
        stdout.write(json.dumps(message) + "\n")
        stdout.flush()

    # ------------------------------------------------------------------
    # REST API mode (--api --port)
    # ------------------------------------------------------------------

    def build_api_app(self):
        """Build a single-agent OpenAI-compatible FastAPI app.

        Endpoints: ``POST /v1/chat/completions`` (non-streaming + SSE),
        ``GET /v1/models``, ``GET /v1/tools``, ``GET /health``. Reuses the REST
        schemas from :mod:`gaia.api.schemas` so the surface matches the shared
        API server exactly. FastAPI is imported lazily so pipe/CLI/MCP modes
        don't pay for the web stack.
        """
        self._ensure_interface(INTERFACE_API)

        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import StreamingResponse

        from gaia.api.schemas import (
            ChatCompletionChoice,
            ChatCompletionRequest,
            ChatCompletionResponse,
            ChatCompletionResponseMessage,
            ModelInfo,
            ModelListResponse,
            UsageInfo,
        )

        app = FastAPI(
            title=f"GAIA {self.name} API",
            description=f"OpenAI-compatible API for the {self.name} agent",
            version="1.0.0",
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        def _estimate_tokens(text: str) -> int:
            if isinstance(self.agent, ApiAgent):
                return self.agent.estimate_tokens(text)
            return len(text) // 4

        def _last_user_message(messages) -> Optional[str]:
            return next(
                (m.content for m in reversed(messages) if m.role == "user"), None
            )

        @app.get("/health")
        async def health_check():
            return {"status": "ok", "service": f"gaia-{self.model_id}"}

        @app.get("/v1/models")
        async def list_models() -> ModelListResponse:
            info = (
                self.agent.get_model_info()
                if isinstance(self.agent, ApiAgent)
                else {"max_input_tokens": 8192, "max_output_tokens": 4096}
            )
            return ModelListResponse(
                object="list",
                data=[
                    ModelInfo(
                        id=self.model_id,
                        object="model",
                        created=int(time.time()),
                        owned_by="amd-gaia",
                        description=info.get("description"),
                        max_input_tokens=info.get("max_input_tokens"),
                        max_output_tokens=info.get("max_output_tokens"),
                    )
                ],
            )

        @app.get("/v1/tools")
        async def list_tools():
            return {"tools": self._tool_definitions()}

        @app.post("/v1/chat/completions")
        async def create_chat_completion(request: ChatCompletionRequest):
            if request.model != self.model_id:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Model '{request.model}' not found. This server serves "
                        f"'{self.model_id}'."
                    ),
                )
            user_message = _last_user_message(request.messages)
            if not user_message:
                raise HTTPException(
                    status_code=400, detail="No user message found in messages array"
                )

            if request.stream:
                return StreamingResponse(
                    self._sse_stream(user_message),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )

            content = self._run_query(user_message)
            prompt_tokens = _estimate_tokens(user_message)
            completion_tokens = _estimate_tokens(content)
            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
                object="chat.completion",
                created=int(time.time()),
                model=self.model_id,
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatCompletionResponseMessage(
                            role="assistant", content=content
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=UsageInfo(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                ),
            )

        return app

    def _sse_stream(self, prompt: str):
        """Minimal OpenAI-compatible SSE: role chunk, content chunk, done."""
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        def _chunk(delta: Dict[str, Any], finish_reason=None) -> str:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": self.model_id,
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish_reason}
                ],
            }
            return f"data: {json.dumps(payload)}\n\n"

        yield _chunk({"role": "assistant", "content": ""})
        content = self._run_query(prompt)
        yield _chunk({"content": content})
        yield _chunk({}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    def run_api(self, host: str = "localhost", port: int = 8000) -> None:
        """Build the API app and serve it with uvicorn (blocking)."""
        self._ensure_interface(INTERFACE_API)
        import uvicorn

        app = self.build_api_app()
        print(
            f"🚀 Serving {self.name} on http://{host}:{port} (model: {self.model_id})"
        )
        uvicorn.run(app, host=host, port=port)

    # ------------------------------------------------------------------
    # TUI mode (default)
    # ------------------------------------------------------------------

    def run_tui(self) -> None:
        """Interactive read-eval-print loop over the agent.

        The framework default: a minimal REPL so any agent is usable
        interactively without shipping its own loop. Agents that want a richer
        TUI still can — this is the floor, not a ceiling.
        """
        self._ensure_interface(INTERFACE_TUI)
        print(f"{self.name} — interactive mode. Type 'exit' or Ctrl-D to quit.\n")
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if prompt.lower() in ("exit", "quit"):
                break
            if not prompt:
                continue
            print(self._run_query(prompt))


def build_arg_parser(prog: Optional[str] = None) -> argparse.ArgumentParser:
    """Build the standard 5-interface argument parser shared by agent entries."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run this agent via one of the 5 standard interfaces.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--prompt", metavar="TEXT", help="CLI mode: run a single query and exit"
    )
    mode.add_argument(
        "--pipe", action="store_true", help="Pipe mode: read prompt from stdin"
    )
    mode.add_argument(
        "--api", action="store_true", help="API server mode (OpenAI-compatible REST)"
    )
    mode.add_argument(
        "--mcp", action="store_true", help="MCP stdio server mode (JSON-RPC)"
    )
    parser.add_argument(
        "--host", default="localhost", help="API host (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="API port (default: 8000)"
    )
    return parser


def run_agent_cli(
    agent: Agent,
    argv: Optional[List[str]] = None,
    *,
    manifest: Any = None,
    prog: Optional[str] = None,
) -> int:
    """Dispatch an agent into the 5-interface standard from argv.

    The single entry point an agent package wires to its console script::

        def main() -> int:
            return run_agent_cli(MyAgent())

    Mode selection (mutually exclusive; default is TUI):
        ``--prompt TEXT`` → CLI, ``--pipe`` → pipe, ``--api [--host --port]`` →
        REST server, ``--mcp`` → MCP stdio server, none → interactive TUI.

    Args:
        agent: The constructed agent instance to serve.
        argv: Argument list (defaults to ``sys.argv[1:]``).
        manifest: Optional parsed ``gaia-agent.yaml`` whose ``interfaces`` block
            gates the allowed modes (unsupported mode → actionable error).
        prog: Program name for ``--help`` output.

    Returns:
        Process exit code (0 on success, 1 on an interface/usage error).
    """
    parser = build_arg_parser(prog=prog)
    args = parser.parse_args(argv)
    server = AgentServer(agent, manifest=manifest)

    try:
        if args.prompt is not None:
            server.run_cli(args.prompt)
        elif args.pipe:
            server.run_pipe()
        elif args.api:
            server.run_api(host=args.host, port=args.port)
        elif args.mcp:
            server.run_mcp()
        else:
            server.run_tui()
    except InterfaceNotSupportedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
