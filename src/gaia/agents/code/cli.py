# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""CLI for Code Agent."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from gaia.agents.code.agent import CodeAgent

logger = logging.getLogger(__name__)
console = Console()


def _print_header():
    """Print a styled header for the CLI."""
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]GAIA Code Agent[/bold cyan]\n"
            "[dim]AI-powered code generation and analysis[/dim]",
            border_style="cyan",
        )
    )
    console.print()


def _add_common_args(parser):
    """Add common arguments to a parser."""
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
        help="Lemonade server URL (default: http://localhost:8000/api/v1)",
    )
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
    parser.add_argument(
        "--no-lemonade-check",
        action="store_true",
        help="Skip Lemonade server initialization check",
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
        from gaia.agents.code.agent import CodeAgent  # noqa: F401
        CODE_AVAILABLE = True
    except ImportError:
        CODE_AVAILABLE = False

    if not CODE_AVAILABLE:
        log.error("Code agent is not available. Please check your installation.")
        return 1

    # Get base_url from args or environment
    base_url = args.base_url
    if base_url is None:
        base_url = os.getenv("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")

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
        # Import RoutingAgent for intelligent language detection
        from gaia.agents.routing.agent import RoutingAgent

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
            return 0

    except Exception as e:
        log.error(f"Error initializing Code agent: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1


def cmd_init(args):
    """Initialize Code agent by downloading and loading required models."""
    import time

    from gaia.llm.lemonade_client import LemonadeClient

    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]Code Agent Setup[/bold cyan]\n"
            "[dim]Downloading and loading required models[/dim]",
            border_style="cyan",
        )
    )
    console.print()

    # Required models for Code agent
    code_model = "Qwen3-Coder-30B-A3B-Instruct-GGUF"  # Default code model

    REQUIRED_CONTEXT_SIZE = 32768

    # Step 1: Check Lemonade server and context size
    console.print("[bold]Step 1:[/bold] Checking Lemonade server...")
    try:
        client = LemonadeClient(model=code_model)
        health = client.health_check()
        if health.get("status") == "ok":
            console.print("  [green]✓[/green] Lemonade server is running")

            # Check context size
            context_size = health.get("context_size", 0)
            if context_size >= REQUIRED_CONTEXT_SIZE:
                console.print(
                    f"  [green]✓[/green] Context size: [cyan]{context_size:,}[/cyan] tokens (recommended: {REQUIRED_CONTEXT_SIZE:,})"
                )
            elif context_size > 0:
                console.print(
                    f"  [yellow]⚠[/yellow] Context size: [yellow]{context_size:,}[/yellow] tokens"
                )
                console.print(
                    f"    [yellow]Warning:[/yellow] Context size should be at least [cyan]{REQUIRED_CONTEXT_SIZE:,}[/cyan] for reliable code generation"
                )
                console.print(
                    "    [dim]To fix: Right-click Lemonade tray → Settings → Context Size → 32768[/dim]"
                )
            else:
                console.print(
                    "  [dim]Context size: Not reported (will check after model load)[/dim]"
                )
        else:
            console.print("  [red]✗[/red] Lemonade server not responding")
            console.print()
            console.print("[yellow]Please start Lemonade server first:[/yellow]")
            console.print("  1. Open Lemonade from the system tray")
            console.print("  2. Or run: [cyan]lemonade-server[/cyan]")
            return 1
    except Exception as e:
        console.print(f"  [red]✗[/red] Cannot connect to Lemonade: {e}")
        console.print()
        console.print("[yellow]Please start Lemonade server first:[/yellow]")
        console.print("  1. Open Lemonade from the system tray")
        console.print("  2. Or run: [cyan]lemonade-server[/cyan]")
        return 1

    # Step 2: Check required models
    console.print()
    console.print("[bold]Step 2:[/bold] Checking required models...")

    try:
        models_response = client.list_models()
        available_models = models_response.get("data", [])
        downloaded_model_ids = [m.get("id", "") for m in available_models]

        # Check code model
        is_downloaded = code_model in downloaded_model_ids
        if is_downloaded:
            console.print(f"  [green]✓[/green] Code model: [cyan]{code_model}[/cyan]")
        else:
            console.print(
                f"  [dim]○[/dim] Code model: [cyan]{code_model}[/cyan] [dim](not downloaded)[/dim]"
            )

    except Exception as e:
        console.print(f"  [red]✗[/red] Failed to check models: {e}")
        return 1

    # Step 3: Load code model
    console.print()
    console.print("[bold]Step 3:[/bold] Loading code model...")
    console.print(f"  Loading: [cyan]{code_model}[/cyan]...")

    try:
        start_time = time.time()
        client.load_model(code_model, timeout=1800, auto_download=True)
        elapsed = time.time() - start_time
        console.print(f"  [green]✓[/green] Model loaded ({elapsed:.1f}s)")
    except Exception as e:
        error_msg = str(e)
        if "being used by another process" in error_msg:
            console.print("  [yellow]![/yellow] File locked, try again later")
        elif "not found" in error_msg.lower() or "does not exist" in error_msg.lower():
            console.print("  [yellow]![/yellow] Model not available in registry")
        else:
            console.print(f"  [yellow]![/yellow] {error_msg[:50]}...")
        return 1

    # Step 4: Verify model is ready
    console.print()
    console.print("[bold]Step 4:[/bold] Verifying model is ready...")

    try:
        health = client.health_check()
        final_context_size = health.get("context_size", 0)

        model_ready = client.check_model_loaded(code_model)

        if model_ready:
            console.print("  [green]✓[/green] Code model: Ready")
        else:
            console.print("  [yellow]![/yellow] Code model: Will load on first use")

        # Report context size
        if final_context_size >= REQUIRED_CONTEXT_SIZE:
            console.print(
                f"  [green]✓[/green] Context size: [cyan]{final_context_size:,}[/cyan] tokens"
            )
        elif final_context_size > 0:
            console.print(
                f"  [yellow]⚠[/yellow] Context size: [yellow]{final_context_size:,}[/yellow] tokens (need {REQUIRED_CONTEXT_SIZE:,})"
            )

    except Exception as e:
        console.print(f"  [yellow]![/yellow] Could not verify: {e}")

    # Success summary
    console.print()
    console.print(
        Panel.fit(
            f"[bold green]✓ Code Agent initialized[/bold green]\n\n"
            f"[green]✓[/green] Model: [cyan]{code_model}[/cyan]\n"
            f"[green]✓[/green] Context: {final_context_size:,} tokens\n\n"
            "[dim]You can now run:[/dim]\n"
            "  [cyan]gaia-code \"your query\"[/cyan]  - Generate code\n"
            "  [cyan]gaia-code --interactive[/cyan]  - Interactive mode\n"
            "  [cyan]gaia-code --list-tools[/cyan]  - List available tools",
            border_style="green",
        )
    )
    console.print()

    # Context size warning if needed
    if 0 < final_context_size < REQUIRED_CONTEXT_SIZE:
        console.print(
            Panel.fit(
                "[yellow]⚠️  Context Size Warning[/yellow]\n\n"
                f"Current context size ({final_context_size:,}) may be too small for complex code generation.\n\n"
                "[bold]To fix:[/bold]\n"
                "  1. Right-click Lemonade tray icon → Settings\n"
                "  2. Set Context Size to [cyan]32768[/cyan]\n"
                "  3. Click Apply and restart the model",
                border_style="yellow",
            )
        )
        console.print()

    return 0


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="GAIA Code Agent - AI-powered code generation and analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a Python function
  gaia-code "Generate a Python function to calculate factorial"

  # Analyze a Python file
  gaia-code "Analyze the code in /path/to/file.py"

  # Interactive mode
  gaia-code --interactive

  # Initialize and setup models
  gaia-code init
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Run command (default)
    parser_run = subparsers.add_parser(
        "run",
        help="Run code agent with a query",
        add_help=False,
    )
    parser_run.add_argument(
        "query",
        nargs="?",
        help="Code operation query (e.g., 'Generate a function to sort a list')",
    )
    parser_run.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode for multiple queries",
    )
    parser_run.add_argument(
        "--path",
        "-p",
        type=str,
        default=None,
        help="Project directory path. Creates directory if it doesn't exist.",
    )
    parser_run.add_argument(
        "--step-through",
        action="store_true",
        help="Enable step-through debugging mode (pause at each agent step)",
    )
    parser_run.add_argument(
        "--list-tools",
        action="store_true",
        help="List all available tools and exit",
    )
    _add_common_args(parser_run)
    parser_run.set_defaults(func=cmd_run)

    # Init command
    parser_init = subparsers.add_parser(
        "init",
        help="Download and setup required models",
    )
    parser_init.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser_init.set_defaults(func=cmd_init)

    # Parse args - if no command specified, treat as 'run' with query
    args, unknown = parser.parse_known_args()

    # If no command and there are unknown args, treat first unknown as query for 'run'
    if not args.command and unknown:
        # Reconstruct args for 'run' command
        run_args = ["run"] + unknown + sys.argv[1:]
        args = parser.parse_args(run_args)
    elif not args.command:
        # No command and no args - show help
        parser.print_help()
        return 0

    # Configure logging - WARNING by default, DEBUG with --debug flag
    if getattr(args, "debug", False):
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
        return args.func(args)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if getattr(args, "debug", False):
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
