# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CLI entry point for GAIA OpenAI-compatible API server

This module provides command-line interface for managing the API server.

Usage:
    gaia api start [--host HOST] [--port PORT] [--background]
    gaia api status
    gaia api stop
"""

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def start_server(
    host: str = "localhost",
    port: int = 8080,
    background: bool = False,
    debug: bool = False,
    show_prompts: bool = False,
    streaming: bool = False,
    step_through: bool = False,
) -> None:
    """
    Start the API server.

    Args:
        host: Host to bind to (default: localhost)
        port: Port to bind to (default: 8080)
        background: Run in background if True, foreground otherwise
        debug: Enable debug logging
        show_prompts: Display prompts sent to LLM
        streaming: Enable real-time streaming of LLM responses
        step_through: Enable step-through debugging mode

    Example:
        >>> start_server("localhost", 8080, background=True)
        ✅ GAIA API server started in background (PID: 12345)
        >>> start_server("localhost", 8080, debug=True, show_prompts=True)
        ✅ GAIA API server started with debug mode enabled
    """
    # Set environment variables for agent configuration
    # These will be read by agent_registry.py when agents are instantiated
    if debug:
        os.environ["GAIA_API_DEBUG"] = "1"
    if show_prompts:
        os.environ["GAIA_API_SHOW_PROMPTS"] = "1"
    if streaming:
        os.environ["GAIA_API_STREAMING"] = "1"
    if step_through:
        os.environ["GAIA_API_STEP_THROUGH"] = "1"

    if background:
        # Start in background
        log_file = Path("gaia_api.log")
        with open(log_file, "w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "gaia.api.openai_server:app",
                    "--host",
                    host,
                    "--port",
                    str(port),
                ],
                stdout=f,
                stderr=f,
            )
        print(f"✅ GAIA API server started in background (PID: {proc.pid})")
        print(f"📝 Logs: {log_file}")
        print(f"🌐 URL: http://{host}:{port}")
        if debug or show_prompts or streaming or step_through:
            print("\n🐛 Debug features enabled:")
            if debug:
                print("  • Debug logging")
            if show_prompts:
                print("  • Show prompts")
            if streaming:
                print("  • LLM streaming")
            if step_through:
                print("  • Step-through mode")
        print("\nAvailable endpoints:")
        print(f"  • POST http://{host}:{port}/v1/chat/completions")
        print(f"  • GET  http://{host}:{port}/v1/models")
        print(f"  • GET  http://{host}:{port}/health")
    else:
        # Start in foreground
        import uvicorn

        print(f"🚀 Starting GAIA API server on http://{host}:{port}")
        if debug or show_prompts or streaming or step_through:
            print("\n🐛 Debug features enabled:")
            if debug:
                print("  • Debug logging")
            if show_prompts:
                print("  • Show prompts")
            if streaming:
                print("  • LLM streaming")
            if step_through:
                print("  • Step-through mode")
        print("\nAvailable endpoints:")
        print(f"  • POST http://{host}:{port}/v1/chat/completions")
        print(f"  • GET  http://{host}:{port}/v1/models")
        print(f"  • GET  http://{host}:{port}/health")
        print("\nPress Ctrl+C to stop\n")

        # Set uvicorn log level based on debug flag
        log_level = "debug" if debug else "info"
        uvicorn.run(
            "gaia.api.openai_server:app", host=host, port=port, log_level=log_level
        )


def check_status() -> None:
    """
    Check if API server is running.

    This will be implemented in a future version.
    For now, it just prints a message.
    """
    print("Status check not yet implemented")
    print("Try: curl http://localhost:8080/health")


def stop_server(port: int = 8080) -> None:
    """
    Stop the API server by finding and killing processes on the port.

    Args:
        port: Port number to stop server on (default: 8080)

    Returns:
        None
    """
    import platform
    import signal

    system = platform.system()

    try:
        if system == "Windows":
            # Windows: Use netstat to find PID, then taskkill to stop it
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            # Parse netstat output to find PIDs listening on the port
            pids = set()
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    # Line format: "  TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    12345"
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids.add(parts[-1])

            if pids:
                for pid in pids:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True,
                            timeout=5,
                            check=False,
                        )
                        print(f"🛑 Stopped API server process (PID: {pid})")
                    except (
                        subprocess.TimeoutExpired,
                        subprocess.CalledProcessError,
                    ) as e:
                        print(f"⚠️  Failed to stop PID {pid}: {e}")
                print("✅ API server stopped")
            else:
                print("ℹ️  No API server found running on port {port}")

        else:
            # Linux/Mac: Use lsof to find PID, then kill it
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            pids = result.stdout.strip().split("\n")
            pids = [pid for pid in pids if pid]  # Filter empty strings

            if pids:
                for pid in pids:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        print(f"🛑 Stopped API server process (PID: {pid})")
                    except (ProcessLookupError, ValueError) as e:
                        print(f"⚠️  Failed to stop PID {pid}: {e}")
                print("✅ API server stopped")
            else:
                print(f"ℹ️  No API server found running on port {port}")

    except FileNotFoundError as e:
        print(f"❌ Required command not found: {e}")
        print("To stop manually, find the process using the port:")
        if system == "Windows":
            print(f"  netstat -ano | findstr :{port}")
            print("  taskkill /F /PID <PID>")
        else:
            print(f"  lsof -ti :{port}")
            print("  kill -9 <PID>")
    except subprocess.TimeoutExpired:
        print(f"❌ Timeout while trying to stop server on port {port}")
    except Exception as e:
        print(f"❌ Error stopping server: {e}")


def main() -> None:
    """
    CLI entry point for API server commands.

    Example:
        $ gaia api start
        $ gaia api start --host 0.0.0.0 --port 8000 --background
        $ gaia api status
        $ gaia api stop
    """
    parser = argparse.ArgumentParser(description="GAIA OpenAI-compatible API server")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start API server")
    start_parser.add_argument(
        "--host", default="localhost", help="Host to bind to (default: localhost)"
    )
    start_parser.add_argument(
        "--port", type=int, default=8080, help="Port to bind to (default: 8080)"
    )
    start_parser.add_argument(
        "--background", action="store_true", help="Run in background"
    )
    start_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    start_parser.add_argument(
        "--show-prompts",
        action="store_true",
        help="Display prompts sent to LLM",
    )
    start_parser.add_argument(
        "--streaming",
        action="store_true",
        help="Enable real-time streaming of LLM responses",
    )
    start_parser.add_argument(
        "--step-through",
        action="store_true",
        help="Enable step-through debugging mode (pause at each agent step)",
    )

    # Status command
    subparsers.add_parser("status", help="Check server status")

    # Stop command
    stop_parser = subparsers.add_parser("stop", help="Stop server")
    stop_parser.add_argument(
        "--port", type=int, default=8080, help="Port number (default: 8080)"
    )

    args = parser.parse_args()

    if args.command == "start":
        start_server(
            args.host,
            args.port,
            args.background,
            getattr(args, "debug", False),
            getattr(args, "show_prompts", False),
            getattr(args, "streaming", False),
            getattr(args, "step_through", False),
        )
    elif args.command == "status":
        check_status()
    elif args.command == "stop":
        stop_server(getattr(args, "port", 8080))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
