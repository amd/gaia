#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Code Agent Application.

Provides a command-line interface for the Code Agent.
"""

import argparse
import json
import logging
import os
import sys

# Add parent directory to path for imports
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# pylint: disable=wrong-import-position
from gaia.agents.code.agent import CodeAgent  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main(args=None):
    """Main entry point for the Code Agent CLI."""
    parser = argparse.ArgumentParser(
        description="GAIA Code Agent - Intelligent code assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a Python function
  gaia code "Generate a Python function to calculate factorial"
  
  # Analyze a Python file
  gaia code "Analyze the code in /path/to/file.py"
  
  # Validate Python syntax
  gaia code "Check if this is valid Python: def hello() print('hi')"
  
  # Generate a test class
  gaia code "Create a unittest test class for a Calculator module"
  
  # Interactive mode
  gaia code --interactive
        """,
    )

    parser.add_argument(
        "query",
        nargs="?",
        help="Code operation query (e.g., 'Generate a function to sort a list')",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode for multiple queries",
    )
    parser.add_argument(
        "--silent",
        "-s",
        action="store_true",
        help="Silent mode - suppress console output, return JSON only",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--show-prompts", action="store_true", help="Display prompts sent to LLM"
    )
    parser.add_argument(
        "--debug-prompts",
        action="store_true",
        help="Include prompts in conversation history (for JSON output)",
    )
    parser.add_argument("--output", "-o", help="Output file for results (JSON format)")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=100,
        help="Maximum conversation steps (default: 100)",
    )
    parser.add_argument(
        "--list-tools", action="store_true", help="List all available tools and exit"
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
        "--show-stats",
        action="store_true",
        help="Display LLM performance statistics (tokens, timing)",
    )

    args = parser.parse_args(args)

    # Configure logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        # Initialize the agent
        agent = CodeAgent(
            silent_mode=args.silent,
            debug=args.debug,
            show_prompts=args.show_prompts,
            debug_prompts=args.debug_prompts,
            max_steps=args.max_steps,
            use_claude=args.use_claude,
            use_chatgpt=args.use_chatgpt,
            show_stats=args.show_stats,
        )

        # List tools if requested
        if args.list_tools:
            agent.list_tools(verbose=True)
            return 0

        # Interactive mode
        if args.interactive:
            print("🤖 Code Agent Interactive Mode")
            print("Type 'exit' or 'quit' to end the session")
            print("Type 'help' for available commands\n")

            while True:
                try:
                    query = input("\ncode> ").strip()

                    if query.lower() in ["exit", "quit"]:
                        print("Goodbye!")
                        break

                    if query.lower() == "help":
                        print("\nAvailable commands:")
                        print("  Generate functions, classes, or tests")
                        print("  Analyze Python files")
                        print("  Validate Python syntax")
                        print("  Search for code patterns")
                        print("  Type 'exit' or 'quit' to end")
                        continue

                    if not query:
                        continue

                    # Process the query
                    result = agent.process_query(
                        query,
                        max_steps=args.max_steps,
                        output_to_file=bool(args.output),
                        filename=args.output,
                    )

                    # Display result in interactive mode
                    if not args.silent:
                        if result.get("status") == "success":
                            print(f"\n✅ {result.get('result', 'Task completed')}")
                        else:
                            print(f"\n❌ {result.get('result', 'Task failed')}")

                except KeyboardInterrupt:
                    print("\n\nInterrupted. Type 'exit' to quit.")
                    continue
                except Exception as e:
                    logger.error(f"Error processing query: {e}")
                    if args.debug:
                        import traceback

                        traceback.print_exc()

        # Single query mode
        elif args.query:
            result = agent.process_query(
                args.query,
                max_steps=args.max_steps,
                output_to_file=bool(args.output),
                filename=args.output,
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
            # Default to interactive mode when no query is provided
            print("🤖 Code Agent Interactive Mode")
            print("Type 'exit' or 'quit' to end the session")
            print("Type 'help' for available commands\n")

            while True:
                try:
                    query = input("\ncode> ").strip()

                    if query.lower() in ["exit", "quit"]:
                        print("Goodbye!")
                        break

                    if query.lower() == "help":
                        print("\nAvailable commands:")
                        print("  Generate functions, classes, or tests")
                        print("  Analyze Python files")
                        print("  Validate Python syntax")
                        print("  Search for code patterns")
                        print("  Type 'exit' or 'quit' to end")
                        continue

                    if not query:
                        continue

                    # Process the query
                    result = agent.process_query(
                        query,
                        max_steps=args.max_steps,
                        output_to_file=bool(args.output),
                        filename=args.output,
                    )

                    # Display result in interactive mode
                    if not args.silent:
                        if result.get("status") == "success":
                            print(f"\n✅ {result.get('result', 'Task completed')}")
                        else:
                            print(f"\n❌ {result.get('result', 'Task failed')}")

                except KeyboardInterrupt:
                    print("\n\nInterrupted. Type 'exit' to quit.")
                    continue
                except Exception as e:
                    logger.error(f"Error processing query: {e}")
                    if args.debug:
                        import traceback

                        traceback.print_exc()

            return 0

    except KeyboardInterrupt:
        print("\n\nOperation cancelled.")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
