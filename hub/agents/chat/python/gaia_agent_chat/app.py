# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Deprecated standalone chat entry (``python -m gaia_agent_chat.app``).

``interactive_mode`` moved to :mod:`gaia_agent.interactive` and is re-exported
here for one release; this module is removed in the next release.
"""

import argparse
import os
import sys
from pathlib import Path

from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig
from gaia_agent.interactive import interactive_mode

from gaia.logger import get_logger

__all__ = ["interactive_mode", "main", "parse_args"]

logger = get_logger(__name__)


def parse_args():
    """Parse command line arguments.

    # dead relative to cli.py; see #2323 — the real `gaia chat` entry point is
    # src/gaia/cli.py, which builds its own ChatAgentConfig from argparse
    # directly. This function (and `main()` below) only run via the standalone
    # `python -m gaia_agent_chat.app` entry, which no shipped console_script
    # invokes. Left in place — removing it is a separate behavior change.
    """
    parser = argparse.ArgumentParser(
        description="Chat Agent with RAG and file search capabilities"
    )

    # LLM backend options
    parser.add_argument(
        "--use-claude", action="store_true", help="Use Claude API instead of local LLM"
    )
    parser.add_argument(
        "--use-chatgpt",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--claude-model",
        type=str,
        default="claude-sonnet-4-20250514",
        help="Claude model to use (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--model-id",
        type=str,
        default=None,
        help="Model ID for local LLM (default: Gemma-4-E4B-it-GGUF)",
    )

    # Agent configuration
    parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum conversation steps (default: 10)",
    )
    parser.add_argument(
        "--streaming", action="store_true", help="Enable streaming responses"
    )
    parser.add_argument(
        "--show-stats", action="store_true", help="Show performance statistics"
    )
    parser.add_argument(
        "--show-prompts", action="store_true", help="Display prompts sent to LLM"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Suppress agent reasoning output (silent mode)",
    )

    # RAG configuration
    parser.add_argument(
        "--index",
        "-i",
        type=str,
        metavar="PATH",
        help="Index a document before running (combine with --query for one-shot usage)",
    )
    parser.add_argument(
        "--documents", "-d", nargs="+", help="Documents to index for RAG"
    )
    parser.add_argument(
        "--watch", "-w", nargs="+", help="Directories to monitor for new documents"
    )
    parser.add_argument(
        "--chunk-size", type=int, default=500, help="Document chunk size (default: 500)"
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=100,
        help="Chunk overlap in characters (default: 100)",
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=5,
        help="Maximum chunks to retrieve (default: 5)",
    )
    parser.add_argument(
        "--use-llm-chunking",
        action="store_true",
        help="Use LLM-based semantic chunking for better context preservation (slower but more accurate)",
    )
    parser.add_argument(
        "--allowed-paths",
        nargs="+",
        help="Allowed directory paths for file operations (default: current directory)",
    )

    # Input/output
    parser.add_argument(
        "--query", "-q", type=str, help="Single query to execute (non-interactive mode)"
    )
    parser.add_argument("--output-dir", type=str, help="Directory for output files")
    parser.add_argument(
        "--list-tools", action="store_true", help="List available tools and exit"
    )

    args = parser.parse_args()
    if args.use_chatgpt:
        from gaia.llm.factory import REMOVED_PROVIDER_MESSAGE

        parser.error(REMOVED_PROVIDER_MESSAGE)
    return args


def main():
    """Main entry point."""
    try:
        from gaia.logger import configure
    except ImportError:  # older amd-gaia sets up logging on import; the pin allows it
        pass
    else:
        configure()
    args = parse_args()

    try:
        # Keep console output visible by default so users can see agent reasoning
        # Silent mode can be enabled with --silent flag or for single-query mode
        if args.silent:
            use_silent_mode = True
        elif args.query is not None:
            use_silent_mode = True  # Auto-silent for non-interactive queries
        else:
            use_silent_mode = False  # Show output in interactive mode

        # Create agent config
        config = ChatAgentConfig(
            use_claude=args.use_claude,
            claude_model=args.claude_model,
            model_id=args.model_id,
            max_steps=args.max_steps,
            show_prompts=args.show_prompts,
            output_dir=args.output_dir,
            streaming=args.streaming,
            show_stats=args.show_stats,
            silent_mode=use_silent_mode,
            debug=args.debug,
            rag_documents=args.documents,
            watch_directories=args.watch,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            max_chunks=args.max_chunks,
            use_llm_chunking=args.use_llm_chunking,
            allowed_paths=args.allowed_paths,
        )

        # Create agent with config
        agent = ChatAgent(config)

        # Create initial session if not loading one
        if not agent.current_session:
            agent.current_session = agent.session_manager.create_session()
            # Reset tool loader session state on new session
            try:
                if hasattr(agent, "tool_loader"):
                    agent.tool_loader.reset_session()
            except Exception as e:
                logger.debug("Tool loader session reset skipped: %s", e)
            logger.debug(f"Created new session: {agent.current_session.session_id}")

        # Index document if --index flag provided
        if args.index:
            index_path = args.index
            if not os.path.exists(index_path):
                print(f"❌ File not found: {index_path}")
                return 1

            print(f"📄 Indexing: {Path(index_path).name}")
            print("=" * 60)

            result = agent.rag.index_document(str(Path(index_path).absolute()))

            if result.get("success"):
                print("✅ INDEXING SUCCESSFUL")
                print(f"📁 File: {result.get('file_name')}")
                print(f"📄 Type: {result.get('file_type')}")
                print(f"💾 Size: {result.get('file_size_mb', 0):.2f} MB")
                if result.get("num_pages"):
                    print(f"📖 Pages: {result['num_pages']}")
                print(f"📦 Chunks: {result.get('num_chunks', 0)}")
                print("=" * 60)
            else:
                print(f"❌ Indexing failed: {result.get('error')}")
                return 1

        # List tools if requested
        if args.list_tools:
            agent.list_tools(verbose=True)
            return 0

        # Single query mode
        if args.query:
            result = agent.process_query(args.query)
            print(f"\n{result['result']}")

            if args.show_stats and result.get("duration"):
                print("\n📊 Stats:")
                print(f"  Duration: {result['duration']:.2f}s")
                print(f"  Steps: {result['steps_taken']}")
                print(f"  Tokens: {result.get('total_tokens', 0):,}")

            return 0 if result["status"] == "success" else 1

        # Interactive mode
        interactive_mode(agent)
        return 0

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        print(f"\n❌ Error: {e}")
        return 1
    finally:
        # Cleanup. Draining here rather than beside the one-shot return means
        # interactive, Ctrl-C and error exits land the last turn's facts too —
        # extraction now finishes after the answer.
        try:
            from gaia.agents.base.memory import drain_memory_extraction

            drain_memory_extraction(agent)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("memory extraction did not finish before exit: %s", e)
        try:
            agent.stop_watching()
        except Exception as e:  # pylint: disable=broad-except
            logger.debug("stop_watching() failed during cleanup: %s", e)


if __name__ == "__main__":
    sys.exit(main())
