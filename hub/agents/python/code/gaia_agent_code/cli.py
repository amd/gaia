# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""CLI for Code Agent."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


def _run_interactive_mode(agent, project_path, args, log):
    """Run the interactive REPL loop for the Code Agent."""
    while True:
        try:
            query = input("\ncode> ").strip()

            if query.lower() in ["exit", "quit"]:
                log.info("Goodbye!")
                break

            if query.lower() == "help":
                print("\nAvailable commands:")
                print("  Generate functions, classes, or tests")
                print("  Analyze Python files")
                print("  Validate Python syntax")
                print("  Lint and format code")
                print("  Edit files with diffs")
                print("  Search for code patterns")
                print("  Type 'exit' or 'quit' to end")
                continue

            if not query:
                continue

            # Process the query
            result = agent.process_query(
                query,
                workspace_root=project_path,
                max_steps=args.max_steps,
                trace=args.trace,
            )

            # Display result
            if not args.silent:
                if result.get("status") == "success":
                    log.info(f"\n✅ {result.get('result', 'Task completed')}")
                else:
                    log.error(f"\n❌ {result.get('result', 'Task failed')}")

        except KeyboardInterrupt:
            print("\n\nInterrupted. Type 'exit' to quit.")
            continue
        except Exception as e:
            log.error(f"Error processing query: {e}")
            if args.debug:
                import traceback

                traceback.print_exc()


def cmd_run(args):
    """Run the Code Agent with a query."""
    from gaia.logger import get_logger

    log = get_logger(__name__)

    # Set logging level to DEBUG if --debug flag is used
    if args.debug:
        from gaia.logger import log_manager

        # Set root logger level first to ensure all handlers process DEBUG messages
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Update all existing loggers that start with "gaia"
        for logger_name in list(log_manager.loggers.keys()):
            if logger_name.startswith("gaia"):
                log_manager.loggers[logger_name].setLevel(logging.DEBUG)

        # Set default level for future loggers
        log_manager.set_level("gaia", logging.DEBUG)

        # Also ensure all handlers have DEBUG level
        for handler in root_logger.handlers:
            handler.setLevel(logging.DEBUG)

    # Check if code agent is available
    try:
        from gaia_agent_code.agent import CodeAgent  # noqa: F401

        CODE_AVAILABLE = True
    except ImportError:
        CODE_AVAILABLE = False

    if not CODE_AVAILABLE:
        log.error("Code agent is not available. Please check your installation.")
        return 1

    # Get base_url from args or environment
    base_url = args.base_url
    if base_url is None:
        base_url = os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")

    # Initialize Lemonade with code agent profile (32768 context)
    # Skip for remote servers (e.g., devtunnel URLs), external APIs, or --no-lemonade-check
    is_local = "localhost" in base_url or "127.0.0.1" in base_url
    skip_lemonade = args.no_lemonade_check
    if is_local and not skip_lemonade:
        from gaia.cli import initialize_lemonade_for_agent

        success, _ = initialize_lemonade_for_agent(
            agent="code",
            skip_if_external=True,
            use_claude=args.use_claude,
            use_chatgpt=args.use_chatgpt,
        )
        if not success:
            return 1

    try:
        # Import RoutingAgent for intelligent language detection. It ships as
        # the standalone gaia-agent-routing wheel (#1102), declared as a
        # dependency of this package.
        from gaia_agent_routing.agent import RoutingAgent

        # Handle --path argument
        project_path = args.path if hasattr(args, "path") else None
        if project_path:
            project_path = Path(project_path).expanduser().resolve()
            # Create directory if it doesn't exist
            project_path.mkdir(parents=True, exist_ok=True)
            project_path = str(project_path)
            log.debug(f"Using project path: {project_path}")

        # Get the query to analyze
        query = args.query if hasattr(args, "query") and args.query else None

        # Use RoutingAgent to determine language and project type
        if query:
            # Prepare agent configuration from CLI args
            agent_config = {
                "silent_mode": args.silent,
                "debug": args.debug,
                "show_prompts": args.show_prompts,
                "max_steps": args.max_steps,
                "use_claude": args.use_claude,
                "use_chatgpt": args.use_chatgpt,
                "streaming": args.stream,
                "base_url": args.base_url,
                "skip_lemonade": args.no_lemonade_check,
            }

            # Single query mode - use routing with configuration
            router = RoutingAgent(**agent_config)
            agent = router.process_query(query)
        else:
            # Interactive mode - start with default Python agent
            # User can still benefit from routing per query
            agent = CodeAgent(
                silent_mode=args.silent,
                debug=args.debug,
                show_prompts=args.show_prompts,
                max_steps=args.max_steps,
                use_claude=args.use_claude,
                use_chatgpt=args.use_chatgpt,
                streaming=args.stream,
                base_url=args.base_url,
                skip_lemonade=args.no_lemonade_check,
            )

        # Handle list tools option
        if args.list_tools:
            agent.list_tools(verbose=True)
            return 0

        # Handle interactive mode
        if args.interactive:
            log.info("🤖 Code Agent Interactive Mode")
            log.info("Type 'exit' or 'quit' to end the session")
            log.info("Type 'help' for available commands\n")

            _run_interactive_mode(agent, project_path, args, log)
            return 0

        # Single query mode
        elif query:
            result = agent.process_query(
                query,
                workspace_root=project_path,
                max_steps=args.max_steps,
                trace=args.trace,
                step_through=args.step_through,
            )

            # Output result
            if args.silent:
                # In silent mode, output only JSON
                print(json.dumps(result, indent=2))
            else:
                # Display formatted result
                agent.display_result("Code Operation Result", result)

            return 0 if result.get("status") == "success" else 1

        else:
            # Default to interactive mode when no query provided
            log.info("Starting Code Agent interactive mode (type 'help' for commands)")

            _run_interactive_mode(agent, project_path, args, log)
            return 0

    except Exception as e:
        log.error(f"Error initializing Code agent: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1


def _build_index_parser():
    """Build the argparse parser for ``gaia-code index``."""
    parser = argparse.ArgumentParser(
        prog="gaia-code index",
        description="Index a code repository for semantic search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Index current directory
  gaia-code index

  # Index a specific repository
  gaia-code index --repo /path/to/repo

  # Search the index
  gaia-code index search "how does the agent handle errors"

  # Restrict search to source code chunks
  gaia-code index search "auth flow" --scope code --top-k 5

  # Show index status
  gaia-code index status

  # Clear the index
  gaia-code index clear

  # Interactive code Q&A (CodeAgent + code_index tools)
  gaia-code index chat
        """,
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Path to repository root (default: current directory)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=5000,
        help="Maximum number of files to index (default: 5000)",
    )
    parser.add_argument(
        "--model",
        default="nomic-embed-text-v2-moe-GGUF",
        help="Embedding model to use (default: nomic-embed-text-v2-moe-GGUF)",
    )
    # Shared LLM / Lemonade flags (used by `chat`)
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Use Claude API instead of local Lemonade server (for `chat`)",
    )
    parser.add_argument(
        "--use-chatgpt",
        action="store_true",
        help="Use ChatGPT/OpenAI API instead of local Lemonade server (for `chat`)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Lemonade server URL (default: http://localhost:8000/api/v1)",
    )
    parser.add_argument(
        "--no-lemonade-check",
        action="store_true",
        help="Skip Lemonade server initialization check",
    )

    sub = parser.add_subparsers(dest="index_action", help="Index action")

    search_p = sub.add_parser("search", help="Search the index")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument(
        "--scope",
        choices=["all", "code"],
        default="all",
        help="Scope of search (default: all)",
    )
    search_p.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of results to return (default: 10)",
    )

    sub.add_parser("status", help="Show index status")
    sub.add_parser("clear", help="Clear the index")
    sub.add_parser("chat", help="Interactive code Q&A (CodeAgent + code_index tools)")

    return parser


def cmd_index(argv):
    """Handle ``gaia-code index [...]`` (semantic code search).

    Args:
        argv: Remaining CLI args after the leading ``index`` token
              (e.g. ``sys.argv[2:]``).

    Returns:
        Process exit code (0 = success, non-zero = failure).
    """
    try:
        from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK
    except ImportError:
        print("code_index dependencies missing. Install with: pip install -e '.[rag]'")
        return 1

    parser = _build_index_parser()
    args = parser.parse_args(argv)

    repo_path = os.path.abspath(args.repo)
    config = CodeIndexConfig(
        repo_path=repo_path,
        max_files=args.max_files,
        embedding_model=args.model,
    )
    sdk = CodeIndexSDK(config)

    action = args.index_action

    if action == "chat":
        try:
            from gaia_agent_code.agent import CodeAgent
        except ImportError:
            print(
                "code_index dependencies missing. "
                "Install with: pip install -e '.[rag]'"
            )
            return 1

        if not args.no_lemonade_check:
            from gaia.cli import initialize_lemonade_for_agent

            success, _ = initialize_lemonade_for_agent(
                agent="code",
                skip_if_external=True,
                use_claude=args.use_claude,
                use_chatgpt=args.use_chatgpt,
                base_url=args.base_url,
            )
            if not success:
                return 1

        agent = CodeAgent(
            repo_path=repo_path,
            code_index_config=config,
            use_claude=args.use_claude,
            use_chatgpt=args.use_chatgpt,
            base_url=args.base_url,
            skip_lemonade=args.no_lemonade_check,
        )
        print("=== Code Index Chat ===")
        print(f"Repository: {repo_path}")
        print("Ask questions about the codebase. Type 'exit' or 'quit' to stop.\n")
        while True:
            try:
                query = input("You: ")
                if query.lower() in ["exit", "quit", "q"]:
                    break
                if query.strip():
                    agent.process_query(query)
            except KeyboardInterrupt:
                print("\nExiting.")
                break
            except Exception as e:
                print(f"Error: {e}")
        return 0

    if action == "status":
        status = sdk.get_status()
        if not status.get("indexed"):
            print(f"No index found for {repo_path}")
            print("Run 'gaia-code index' to build the index.")
        else:
            print(f"Repository: {status['repo_path']}")
            print(f"Embedding model: {status.get('embedding_model', 'unknown')}")
            print(f"Total chunks: {status.get('total_chunks', 0)}")
            print(f"  Code chunks: {status.get('code_chunks', 0)}")
            print(f"Files tracked: {status.get('files_tracked', 0)}")
        return 0

    if action == "clear":
        sdk.clear_index()
        print(f"Index cleared for {repo_path}")
        return 0

    if action == "search":
        results = sdk.search(args.query, scope=args.scope, top_k=args.top_k)
        if not results:
            print("No results found. Run 'gaia-code index' first.")
            return 0
        for i, r in enumerate(results, 1):
            chunk = r.chunk
            print(f"\n{'=' * 60}")
            print(f"Result {i} (score: {r.score:.4f}, type: {r.result_type})")
            if hasattr(chunk, "file_path"):
                line = f":{chunk.start_line}" if hasattr(chunk, "start_line") else ""
                print(f"File: {chunk.file_path}{line}")
            if hasattr(chunk, "symbol_name") and chunk.symbol_name:
                print(
                    f"Symbol: {chunk.symbol_name} "
                    f"({getattr(chunk, 'symbol_type', '')})"
                )
            print(f"\n{chunk.content[:400]}")
        return 0

    # Default action: build the index
    print(f"Indexing repository: {repo_path}")
    try:
        result = sdk.index_repository()
        print("\nIndexing complete:")
        print(f"  Files indexed: {result.files_indexed}")
        print(f"  Chunks created: {result.chunks_created}")
        return 0
    except Exception as e:
        print(f"Indexing failed: {e}")
        return 1


def main():
    """Main CLI entry point."""
    # Dispatch `gaia-code index ...` before the main argparse runs, since the
    # main parser has a positional `query` that would otherwise swallow
    # "index" as a free-form query string.
    if len(sys.argv) > 1 and sys.argv[1] == "index":
        return cmd_index(sys.argv[2:])

    parser = argparse.ArgumentParser(
        description="GAIA Code Agent - AI-powered code generation and analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate code with a query
  gaia-code "Build me a todo tracking app using typescript"

  # Work in a specific directory
  gaia-code "Build me a todo app" --path ~/src/todo-app

  # Interactive mode
  gaia-code --interactive
  gaia-code -i

  # List available tools
  gaia-code --list-tools

  # Use external LLM APIs
  gaia-code "Build an app" --use-claude
  gaia-code "Build an app" --use-chatgpt

  # Debug mode
  gaia-code "Build an app" --debug

  # Semantic code search (see `gaia-code index --help`)
  gaia-code index --repo .
  gaia-code index search "how does auth work" --scope code
  gaia-code index status
  gaia-code index chat
        """,
    )

    # Positional argument - the code query
    parser.add_argument(
        "query",
        nargs="?",
        help="Code operation query (e.g., 'Build me a todo app')",
    )

    # Mode flags
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode for multiple queries",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List all available tools and exit",
    )

    # Project configuration
    parser.add_argument(
        "--path",
        "-p",
        type=str,
        default=None,
        help="Project directory path. Creates directory if it doesn't exist.",
    )

    # Debug and output options
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--silent",
        "-s",
        action="store_true",
        help="Silent mode - suppress console output, return JSON only",
    )
    parser.add_argument(
        "--step-through",
        action="store_true",
        help="Enable step-through debugging mode (pause at each agent step)",
    )
    parser.add_argument(
        "--show-prompts",
        action="store_true",
        help="Display prompts sent to LLM",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Save conversation trace to JSON file",
    )

    # LLM backend options
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Use Claude API instead of local Lemonade server",
    )
    parser.add_argument(
        "--use-chatgpt",
        action="store_true",
        help="Use ChatGPT/OpenAI API instead of local Lemonade server",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Lemonade server URL (default: http://localhost:13305/api/v1)",
    )
    parser.add_argument(
        "--no-lemonade-check",
        action="store_true",
        help="Skip Lemonade server initialization check",
    )

    # Agent configuration
    parser.add_argument(
        "--max-steps",
        type=int,
        default=100,
        help="Maximum conversation steps (default: 100)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Enable streaming responses",
    )

    # Parse args
    args = parser.parse_args()

    # Configure logging - WARNING by default, DEBUG with --debug flag
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        # Suppress logs from gaia modules for cleaner output
        logging.basicConfig(level=logging.WARNING)
        for logger_name in ["gaia", "gaia.llm", "gaia.agents"]:
            logging.getLogger(logger_name).setLevel(logging.WARNING)

    # Run command
    try:
        return cmd_run(args)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
