# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Docker MCP Server Launcher
Starts an MCP server for the Docker agent
"""

from gaia.agents.docker.agent import DockerAgent
from gaia.mcp.agent_mcp_server import MCP_DEFAULT_HOST, MCP_DEFAULT_PORT, AgentMCPServer


def start_docker_mcp(
    port: int = None,
    host: str = None,
    verbose: bool = False,
    model_id: str = None,
    silent_mode: bool = True,
):
    """
    Start the Docker MCP server.

    Args:
        port: Port to listen on (default: 8080)
        host: Host to bind to (default: localhost)
        verbose: Enable verbose logging
        model_id: LLM model ID to use
        silent_mode: Suppress agent console output (default: True for MCP)
    """
    # Prepare agent parameters
    agent_params = {
        "silent_mode": silent_mode,
    }

    if model_id:
        agent_params["model_id"] = model_id

    # Create and start MCP server
    server = AgentMCPServer(
        agent_class=DockerAgent,
        name="GAIA Docker MCP",
        port=port or MCP_DEFAULT_PORT,
        host=host or MCP_DEFAULT_HOST,
        verbose=verbose,
        agent_params=agent_params,
    )

    server.start()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GAIA Docker MCP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=MCP_DEFAULT_PORT,
        help=f"Port to listen on (default: {MCP_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        default=MCP_DEFAULT_HOST,
        help=f"Host to bind to (default: {MCP_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--model-id",
        help="LLM model ID to use (default: Qwen3-Coder-30B-A3B-Instruct-GGUF)",
    )

    args = parser.parse_args()

    start_docker_mcp(
        port=args.port,
        host=args.host,
        verbose=args.verbose,
        model_id=args.model_id,
    )
