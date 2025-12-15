# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Chat Agent Application - Interactive chat with RAG and file search.
"""

import argparse
import os
import sys
from pathlib import Path

from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig
from gaia.logger import get_logger

logger = get_logger(__name__)


def parse_args():
    """Parse command line arguments."""
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
        help="Use ChatGPT/OpenAI API instead of local LLM",
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
        help="Model ID for local LLM (default: Qwen3-Coder-30B-A3B-Instruct-GGUF)",
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

    return parser.parse_args()


def interactive_mode(agent: ChatAgent):
    """Run agent in interactive chat mode."""
    print("=" * 60)
    print("Chat Agent with RAG - Interactive Mode")
    print("=" * 60)

    # Display model information
    model_name = getattr(agent, "model_display_name", "Unknown")
    print(f"\n🤖 Model: {model_name}")

    print("\nCapabilities:")
    print("  • Document Q&A using RAG")
    print("    - Documents: PDF, TXT, MD, CSV, JSON")
    print("    - Backend: Python, Java, C/C++, Go, Rust, Ruby, PHP, Swift, etc.")
    print("    - Web: JS/TS, HTML, CSS/SCSS/SASS, Vue, Svelte, React (JSX/TSX)")
    print("    - Config: YAML, XML, TOML, INI, ENV")
    print("  • Document summarization with multiple styles")
    print("  • Code retrieval and search (no code generation)")
    print("  • File search and operations")
    print("  • Shell command execution")
    print("  • Auto-indexing when files change")
    print("  • Session persistence with auto-save")
    print("\nSession Commands:")
    print("  /resume [id]   - Resume a previous session (or list if no id)")
    print("  /save          - Save current session")
    print("  /sessions      - List all available sessions")
    print("  /reset         - Clear conversation and start fresh")
    print("\nDocument Commands:")
    print("  /index <path>  - Index a document or directory")
    print("  /watch <dir>   - Watch a directory for changes")
    print("  /list          - List indexed documents")
    print("  /status        - Show system status")
    print("\nDebug Commands:")
    print("  /chunks <file> - Show all chunks for a document")
    print("  /chunk <n>     - Show specific chunk content")
    print("  /test <query>  - Test query retrieval with scores")
    print(
        "  /dump <file|#> - Export document + chunks to markdown (use number from /list)"
    )
    print("  /clear-cache   - Clear RAG cache (force re-indexing)")
    print("  /search-debug  - Toggle RAG debug mode")
    print("\nOther Commands:")
    print("  /help or /?    - Show this help")
    print("  /quit          - Exit")
    print("\nOr just type your question to chat with indexed documents.")
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou: ").strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                command = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else None

                if command == "/quit":
                    # Auto-save before quitting
                    if agent.current_session:
                        print("\n💾 Saving current session...")
                        agent.save_current_session()
                    print("Goodbye!")
                    break

                elif command in ["/help", "/?"]:
                    print("\n" + "=" * 60)
                    print("Chat Agent - Available Commands & Capabilities")
                    print("=" * 60)
                    print("\n🎯 CAPABILITIES:")
                    print("  • Document Q&A using RAG")
                    print("    - Documents: PDF, TXT, MD, CSV, JSON")
                    print(
                        "    - Backend: Python, Java, C/C++, Go, Rust, Ruby, PHP, Swift, Kotlin, Scala"
                    )
                    print(
                        "    - Web: JS/TS, HTML, CSS/SCSS/SASS/LESS, Vue, Svelte, Astro, React"
                    )
                    print("    - Config: YAML, XML, TOML, INI, ENV, Properties")
                    print("    - Build: Gradle, CMake, Makefiles")
                    print("    - Database: SQL")
                    print(
                        "  • Document summarization (brief, detailed, bullets, executive, etc.)"
                    )
                    print("  • Code retrieval and search (no code generation)")
                    print("  • File operations and search")
                    print("  • Shell command execution (ls, grep, find, etc.)")
                    print("  • Auto-indexing when files change")
                    print("  • Session persistence with auto-save")
                    print("\n📋 SESSION MANAGEMENT:")
                    print("  /resume [id]   - Resume session (or list if no id)")
                    print("  /save          - Save current session")
                    print("  /sessions      - List all sessions")
                    print("  /reset         - Clear and start fresh")
                    print("\n📚 DOCUMENT MANAGEMENT:")
                    print("  /index <path>  - Index file or directory")
                    print("  /watch <dir>   - Watch directory for changes")
                    print("  /list          - List indexed documents")
                    print("  /status        - Show system status")
                    print("\n🔍 DEBUG & OBSERVABILITY:")
                    print("  /chunks <file> - Show all chunks for a document")
                    print("  /chunk <n>     - Show specific chunk content")
                    print("  /test <query>  - Test query retrieval with scores")
                    print(
                        "  /dump <file|#> - Export document + chunks to markdown (use number from /list)"
                    )
                    print("  /clear-cache   - Clear RAG cache (force re-indexing)")
                    print("  /search-debug  - Toggle RAG debug mode")
                    print("\n💬 EXAMPLE QUERIES:")
                    print("  Documents:")
                    print("    'What does the document say about X?'")
                    print("    'Summarize report.pdf in bullet points'")
                    print("  Backend Code:")
                    print("    'Where is the UserAuth class defined?'")
                    print("    'Find all functions that use database connections'")
                    print("    'What does the authenticate() function do?'")
                    print("  Web Development:")
                    print("    'Find all CSS classes with hover effects'")
                    print("    'Where are the API endpoints defined?'")
                    print("    'Show me all Vue components that use props'")
                    print("  General:")
                    print("    'List all Python files in src/'")
                    print("    'Search for TODO comments in my code'")
                    print("    'Find all files that import React'")
                    print("\n⚙️  OTHER COMMANDS:")
                    print("  /help or /?    - Show this help")
                    print("  /quit          - Exit (auto-saves)")
                    print("=" * 60)

                elif command == "/resume":
                    if not arg:
                        # List available sessions
                        sessions = agent.session_manager.list_sessions()
                        if not sessions:
                            print("\n📂 No saved sessions found.")
                        else:
                            print("\n" + "=" * 60)
                            print("Available Sessions")
                            print("=" * 60)
                            for sess in sessions:
                                print(f"\n  ID: {sess['session_id']}")
                                print(f"  Created: {sess['created_at']}")
                                print(f"  Updated: {sess['updated_at']}")
                                print(f"  Documents: {sess['num_documents']}")
                                print(f"  Messages: {sess['num_messages']}")
                            print("\n" + "=" * 60)
                            print("\nUse: /resume <session_id> to load a session")
                    else:
                        # Resume specific session
                        print(f"\n📂 Resuming session: {arg}")
                        if agent.load_session(arg):
                            session = agent.current_session
                            print("✅ Loaded session with:")
                            print(f"   - {len(session.indexed_documents)} documents")
                            print(
                                f"   - {len(session.watched_directories)} watched directories"
                            )
                            print(f"   - {len(session.chat_history)} chat messages")
                        else:
                            print(f"❌ Failed to load session: {arg}")

                elif command == "/save":
                    print("\n💾 Saving current session...")
                    if agent.save_current_session():
                        session_id = (
                            agent.current_session.session_id
                            if agent.current_session
                            else "unknown"
                        )
                        print(f"✅ Session saved: {session_id}")
                    else:
                        print("❌ Failed to save session")

                elif command == "/sessions":
                    sessions = agent.session_manager.list_sessions()
                    if not sessions:
                        print("\n📂 No saved sessions found.")
                    else:
                        print("\n" + "=" * 60)
                        print(f"Found {len(sessions)} Session(s)")
                        print("=" * 60)
                        for i, sess in enumerate(sessions, 1):
                            print(f"\n{i}. {sess['session_id']}")
                            print(f"   Created: {sess['created_at']}")
                            print(
                                f"   Documents: {sess['num_documents']}, Messages: {sess['num_messages']}"
                            )
                        print("\n" + "=" * 60)

                elif command == "/reset":
                    print("\n🔄 Resetting conversation...")
                    # Save current session first
                    if agent.current_session:
                        agent.save_current_session()
                    # Create new session
                    agent.current_session = agent.session_manager.create_session()
                    # Clear chat history (if agent tracks it)
                    if hasattr(agent, "chat_history"):
                        agent.chat_history = []
                    print("✅ Conversation reset. Previous session saved.")
                    print(f"   New session: {agent.current_session.session_id}")

                elif command == "/index":
                    if not arg:
                        print("Usage: /index <file_or_directory_path>")
                        print("Example: /index /path/to/document.pdf")
                        print("         /index /path/to/documents/")
                        continue

                    # Check if it's a directory or file
                    path = Path(arg)

                    if path.is_dir():
                        print(f"\n📁 Indexing all documents in directory: {arg}")
                        # Find all supported document types
                        doc_patterns = ["*.pdf", "*.txt", "*.md", "*.csv", "*.json"]
                        doc_files = []
                        for pattern in doc_patterns:
                            doc_files.extend(path.glob(pattern))

                        if not doc_files:
                            print("❌ No supported documents found in directory")
                            print("   Supported types: PDF, TXT, MD, CSV, JSON")
                            continue

                        print(f"Found {len(doc_files)} document(s)\n")
                        success_count = 0
                        for doc_file in doc_files:
                            print(f"  📄 Indexing: {doc_file.name}...")
                            # Directly call the RAG index method
                            try:
                                result = agent.rag.index_document(
                                    str(doc_file.absolute())
                                )
                                if result.get("success"):
                                    print(
                                        f"    ✅ Success: {result.get('num_chunks', 0)} chunks created"
                                    )
                                    success_count += 1
                                else:
                                    error = result.get("error", "Unknown error")
                                    print(f"    ❌ Failed: {error}")
                            except Exception as e:
                                print(f"    ❌ Error: {e}")

                        print(
                            f"\n📊 Summary: {success_count}/{len(doc_files)} documents indexed successfully"
                        )

                    else:
                        # Single file
                        if not os.path.exists(arg):
                            print(f"\n❌ File not found: {arg}")
                            print("   Please check the file path and try again")
                            continue

                        print(f"\n📄 Indexing: {path.name}")
                        print("=" * 60)

                        try:
                            # Directly call the RAG index method
                            result = agent.rag.index_document(str(path.absolute()))

                            if result.get("success"):
                                # Display success with detailed stats
                                print("✅ INDEXING SUCCESSFUL")
                                print("=" * 60)
                                print(f"📁 File: {result.get('file_name', path.name)}")
                                print(f"📄 Type: {result.get('file_type', 'Unknown')}")
                                print(
                                    f"💾 Size: {result.get('file_size_mb', 0):.2f} MB"
                                )

                                # Show num_pages for PDFs
                                if result.get("num_pages"):
                                    print(f"📖 Pages: {result['num_pages']}")

                                print(
                                    f"📦 Chunks Created: {result.get('num_chunks', 0)}"
                                )

                                # Show cache/reindex status
                                if result.get("from_cache"):
                                    print("⚡ Loaded from cache (fast)")
                                elif result.get("already_indexed"):
                                    print("ℹ️  Already indexed (skipped)")
                                elif result.get("reindexed"):
                                    print("🔄 Reindexed (updated)")

                                print("\n📊 GLOBAL STATISTICS")
                                print("=" * 60)
                                print(
                                    f"Total Documents Indexed: {result.get('total_indexed_files', 0)}"
                                )
                                print(f"Total Chunks: {result.get('total_chunks', 0)}")
                                print("=" * 60)
                            else:
                                # Display error
                                print("❌ INDEXING FAILED")
                                print("=" * 60)
                                error = result.get("error", "Unknown error")
                                print(f"Error: {error}")
                                if result.get("file_name"):
                                    print(f"File: {result['file_name']}")
                                print("=" * 60)

                        except Exception as e:
                            print("❌ INDEXING FAILED")
                            print("=" * 60)
                            print(f"Error: {e}")
                            print("=" * 60)

                elif command == "/watch":
                    if not arg:
                        print("Usage: /watch <directory_path>")
                        print("Example: /watch /path/to/documents")
                        continue
                    result = agent.process_query(f"Watch the directory: {arg}")
                    print(f"\n{result['result']}")

                elif command == "/list":
                    # Directly access indexed files from RAG system
                    print("\n" + "=" * 60)
                    print("📚 INDEXED DOCUMENTS")
                    print("=" * 60)

                    if not agent.rag.indexed_files:
                        print("No documents indexed yet.")
                        print("\nUse /index <path> to index a document")
                    else:
                        print(f"Total: {len(agent.rag.indexed_files)} document(s)\n")
                        for i, file_path in enumerate(
                            sorted(agent.rag.indexed_files), 1
                        ):
                            file_name = Path(file_path).name
                            file_type = Path(file_path).suffix

                            # Get chunk count for this file if available
                            num_chunks = 0
                            if file_path in agent.rag.file_to_chunk_indices:
                                num_chunks = len(
                                    agent.rag.file_to_chunk_indices[file_path]
                                )

                            print(f"{i}. {file_name}")
                            print(f"   Type: {file_type}")
                            print(f"   Chunks: {num_chunks}")
                            print(f"   Path: {file_path}")
                            print()

                    print("=" * 60)

                elif command == "/status":
                    # Directly access RAG status
                    print("\n" + "=" * 60)
                    print("📊 RAG SYSTEM STATUS")
                    print("=" * 60)

                    status = agent.rag.get_status()

                    print("\n📚 Documents:")
                    print(f"   Indexed Files: {status['indexed_files']}")
                    print(f"   Total Chunks: {status['total_chunks']}")

                    print("\n⚙️  Configuration:")
                    print(f"   Chunk Size: {status['config']['chunk_size']} tokens")
                    print(
                        f"   Chunk Overlap: {status['config']['chunk_overlap']} tokens"
                    )
                    print(f"   Max Chunks per Query: {status['config']['max_chunks']}")

                    print("\n💾 Storage:")
                    print(f"   Cache Directory: {status['cache_dir']}")
                    print(f"   Embedding Model: {status['embedding_model']}")

                    # Show watched directories
                    if agent.watch_directories:
                        print("\n👀 Watched Directories:")
                        for i, dir_path in enumerate(agent.watch_directories, 1):
                            print(f"   {i}. {dir_path}")

                    # Show session info
                    if agent.current_session:
                        print("\n📋 Current Session:")
                        print(f"   ID: {agent.current_session.session_id}")
                        print(f"   Created: {agent.current_session.created_at}")
                        print(f"   Updated: {agent.current_session.updated_at}")
                        print(
                            f"   Documents: {len(agent.current_session.indexed_documents)}"
                        )
                        print(f"   Messages: {len(agent.current_session.chat_history)}")

                    print("\n" + "=" * 60)

                elif command == "/chunks":
                    if not arg:
                        print("Usage: /chunks <filename>")
                        print("Example: /chunks document.pdf")
                        continue

                    # Find file in indexed files
                    matching_files = [
                        f
                        for f in agent.rag.indexed_files
                        if Path(f).name == arg or f == arg
                    ]

                    if not matching_files:
                        print(f"\n❌ File not indexed: {arg}")
                        print("\nIndexed files:")
                        for f in agent.rag.indexed_files:
                            print(f"  - {Path(f).name}")
                        continue

                    file_path = matching_files[0]

                    # Get chunks for this file
                    if file_path not in agent.rag.file_to_chunk_indices:
                        print(f"\n❌ No chunks found for: {arg}")
                        continue

                    chunk_indices = agent.rag.file_to_chunk_indices[file_path]

                    print("\n" + "=" * 60)
                    print(f"DOCUMENT CHUNKS: {Path(file_path).name}")
                    print("=" * 60)
                    print(f"Total Chunks: {len(chunk_indices)}")
                    print(f"Chunk Size: {agent.rag.config.chunk_size} tokens")
                    print(f"Overlap: {agent.rag.config.chunk_overlap} tokens\n")

                    for i, chunk_idx in enumerate(chunk_indices, 1):
                        if chunk_idx < len(agent.rag.chunks):
                            chunk = agent.rag.chunks[chunk_idx]
                            token_count = len(chunk) // 4  # Rough estimate

                            print(f"CHUNK {i} ({token_count} tokens):")
                            print("┌" + "─" * 58 + "┐")

                            # Show first 200 chars of chunk
                            preview = chunk[:200].replace("\n", " ")
                            if len(chunk) > 200:
                                preview += "..."

                            # Word wrap the preview
                            import textwrap

                            wrapped = textwrap.wrap(preview, width=56)
                            for line in wrapped:
                                print(f"│ {line:<56} │")

                            print("└" + "─" * 58 + "┘")
                            print()

                    print("=" * 60)
                    print("\nUse '/chunk <n>' to see full content of a specific chunk")

                elif command == "/chunk":
                    if not arg or not arg.isdigit():
                        print("Usage: /chunk <number>")
                        print("Example: /chunk 5")
                        continue

                    chunk_num = int(arg) - 1  # Convert to 0-indexed

                    if chunk_num < 0 or chunk_num >= len(agent.rag.chunks):
                        print(
                            f"\n❌ Invalid chunk number. Valid range: 1-{len(agent.rag.chunks)}"
                        )
                        continue

                    chunk = agent.rag.chunks[chunk_num]

                    # Find which file this chunk belongs to
                    source_file = agent.rag.chunk_to_file.get(chunk_num, "Unknown")

                    print("\n" + "=" * 60)
                    print(f"CHUNK {arg}")
                    print("=" * 60)
                    print(
                        f"Source: {Path(source_file).name if source_file != 'Unknown' else 'Unknown'}"
                    )
                    print(f"Token count: {len(chunk) // 4} (estimated)")
                    print(f"Character count: {len(chunk):,}")
                    print("\nCONTENT:")
                    print("┌" + "─" * 58 + "┐")

                    # Word wrap content
                    import textwrap

                    wrapped = textwrap.wrap(chunk, width=56)
                    for line in wrapped:
                        print(f"│ {line:<56} │")

                    print("└" + "─" * 58 + "┘")
                    print("=" * 60)

                elif command == "/test":
                    if not arg:
                        print("Usage: /test <query>")
                        print("Example: /test what is the vision?")
                        continue

                    if not agent.rag.indexed_files:
                        print("\n❌ No documents indexed. Use /index <path> first.")
                        continue

                    print("\n" + "=" * 60)
                    print(f'QUERY TEST: "{arg}"')
                    print("=" * 60)

                    # Test retrieval
                    try:
                        # pylint: disable=protected-access
                        chunks, scores = agent.rag._retrieve_chunks(arg)

                        print(f"\nTop {len(chunks)} matching chunks:\n")

                        for i, (chunk, score) in enumerate(zip(chunks, scores), 1):
                            # Find chunk index
                            chunk_idx = (
                                agent.rag.chunks.index(chunk)
                                if chunk in agent.rag.chunks
                                else -1
                            )
                            source_file = agent.rag.chunk_to_file.get(
                                chunk_idx, "Unknown"
                            )

                            # Color code by score
                            if score > 0.75:
                                emoji = "⭐"
                            elif score > 0.60:
                                emoji = "✅"
                            else:
                                emoji = "⚠️"

                            print(
                                f"{i}. {emoji} CHUNK {chunk_idx + 1} (score: {score:.3f})"
                            )
                            print(
                                f"   Source: {Path(source_file).name if source_file != 'Unknown' else 'Unknown'}"
                            )

                            # Preview
                            preview = chunk[:150].replace("\n", " ")
                            if len(chunk) > 150:
                                preview += "..."
                            print(f"   Preview: {preview}")
                            print()

                        print("=" * 60)
                        print("\nScore Legend:")
                        print("  ⭐ Excellent (>0.75) - Highly relevant")
                        print("  ✅ Good (0.60-0.75) - Probably relevant")
                        print("  ⚠️  Poor (<0.60) - May not be relevant")

                    except Exception as e:
                        print(f"\n❌ Query test failed: {e}")

                elif command == "/dump":
                    if not arg:
                        print("Usage: /dump <filename_or_number>")
                        print("Example: /dump document.pdf")
                        print("Example: /dump 1")
                        continue

                    # Find file in indexed files
                    from datetime import datetime

                    file_path = None

                    # Check if arg is a number (index)
                    if arg.isdigit():
                        index = int(arg) - 1  # Convert to 0-based index
                        indexed_list = list(agent.rag.indexed_files)
                        if 0 <= index < len(indexed_list):
                            file_path = indexed_list[index]
                        else:
                            print(f"\n❌ Invalid index: {arg}")
                            print(f"Valid range: 1-{len(indexed_list)}")
                            continue
                    else:
                        # Try to match by filename
                        matching_files = [
                            f
                            for f in agent.rag.indexed_files
                            if Path(f).name == arg or f == arg
                        ]

                        if not matching_files:
                            print(f"\n❌ File not indexed: {arg}")
                            print("\nIndexed files:")
                            for i, f in enumerate(agent.rag.indexed_files, 1):
                                print(f"  {i}. {Path(f).name}")
                            continue

                        file_path = matching_files[0]

                    # Get chunks for this file
                    if file_path not in agent.rag.file_to_chunk_indices:
                        print(f"\n❌ No chunks found for: {arg}")
                        continue

                    chunk_indices = agent.rag.file_to_chunk_indices[file_path]

                    print(f"\n📝 Generating markdown dump for: {Path(file_path).name}")

                    # Try to get cached metadata first (faster!)
                    cached_metadata = agent.rag.file_metadata.get(file_path, {})
                    extracted_text = cached_metadata.get("full_text")
                    num_pages = cached_metadata.get("num_pages")
                    vlm_pages = cached_metadata.get("vlm_pages", 0)
                    total_images = cached_metadata.get("total_images", 0)

                    # Fall back to re-extraction only if cache is missing
                    if not extracted_text:
                        print("  ⚠️  No cached text found, re-extracting from file...")
                        try:
                            # pylint: disable=protected-access
                            extracted_text, metadata = (
                                agent.rag._extract_text_from_file(file_path)
                            )
                            num_pages = metadata.get("num_pages")
                            vlm_pages = metadata.get("vlm_pages", 0)
                            total_images = metadata.get("total_images", 0)
                        except Exception as e:
                            print(f"❌ Failed to extract text: {e}")
                            continue
                    else:
                        print(
                            "  ✅ Using cached text and metadata (no re-extraction needed)"
                        )

                    # Create markdown content
                    markdown_lines = []
                    markdown_lines.append(f"# RAG Debug Dump: {Path(file_path).name}")
                    markdown_lines.append(
                        f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )

                    # Metadata
                    markdown_lines.append("## Document Metadata\n")
                    markdown_lines.append(f"- **File:** {Path(file_path).name}")
                    markdown_lines.append(f"- **Path:** {file_path}")
                    file_size = os.path.getsize(file_path) / (1024 * 1024)
                    markdown_lines.append(f"- **Size:** {file_size:.2f} MB")
                    if num_pages:
                        markdown_lines.append(f"- **Pages:** {num_pages}")
                    if vlm_pages and vlm_pages > 0:
                        markdown_lines.append(
                            f"- **VLM Enhanced Pages:** {vlm_pages} (images processed)"
                        )
                    if total_images and total_images > 0:
                        markdown_lines.append(
                            f"- **Total Images Extracted:** {total_images}"
                        )
                    markdown_lines.append(f"- **Characters:** {len(extracted_text):,}")
                    markdown_lines.append(f"- **Chunks:** {len(chunk_indices)}")
                    markdown_lines.append(
                        f"- **Chunk Size:** {agent.rag.config.chunk_size} tokens"
                    )
                    markdown_lines.append(
                        f"- **Chunk Overlap:** {agent.rag.config.chunk_overlap} tokens"
                    )
                    markdown_lines.append("")

                    # Full document text
                    markdown_lines.append("---\n")
                    markdown_lines.append("## Full Document Text (As Extracted)")
                    markdown_lines.append("\n```")
                    markdown_lines.append(extracted_text)
                    markdown_lines.append("```\n")

                    # Chunked version with boundaries
                    markdown_lines.append("---\n")
                    markdown_lines.append("## Document with Chunk Boundaries\n")

                    for i, chunk_idx in enumerate(chunk_indices, 1):
                        if chunk_idx < len(agent.rag.chunks):
                            chunk = agent.rag.chunks[chunk_idx]

                            # Extract page number from chunk (with lookback)
                            from gaia.agents.chat.tools.rag_tools import (
                                extract_page_from_chunk,
                            )

                            page_num = extract_page_from_chunk(
                                chunk, chunk_idx, agent.rag.chunks
                            )
                            page_info = (
                                f"Page {page_num}" if page_num else "Page Unknown"
                            )

                            markdown_lines.append(
                                f"### 🔷 CHUNK {i} ({page_info}, Index: {chunk_idx})\n"
                            )
                            markdown_lines.append(
                                f"- **Token Count:** {len(chunk) // 4} (estimated)"
                            )
                            markdown_lines.append(
                                f"- **Character Count:** {len(chunk):,}"
                            )
                            markdown_lines.append("")
                            markdown_lines.append("```")
                            markdown_lines.append(chunk)
                            markdown_lines.append("```\n")

                    markdown_lines.append("---\n")
                    markdown_lines.append("## Chunk Summary\n")
                    markdown_lines.append("| Chunk | Tokens | Characters | Preview |")
                    markdown_lines.append("|-------|--------|------------|---------|")

                    for i, chunk_idx in enumerate(chunk_indices, 1):
                        if chunk_idx < len(agent.rag.chunks):
                            chunk = agent.rag.chunks[chunk_idx]
                            tokens = len(chunk) // 4
                            chars = len(chunk)
                            preview = chunk[:50].replace("\n", " ").replace("|", "\\|")
                            markdown_lines.append(
                                f"| {i} | {tokens} | {chars:,} | {preview}... |"
                            )

                    # Write to file
                    output_filename = f"{Path(file_path).stem}_rag_dump.md"
                    output_path = Path.cwd() / output_filename

                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(markdown_lines))

                    # Calculate output size
                    output_content = "\n".join(markdown_lines)
                    output_size = len(output_content)

                    print(f"✅ Markdown dump created: {output_filename}")
                    print(f"   Path: {output_path}")
                    print("\n📊 Statistics:")
                    print(f"   Document: {len(extracted_text):,} characters")
                    print(f"   Chunks: {len(chunk_indices)}")
                    print(f"   Output: {output_size:,} characters")
                    print("\nYou can now:")
                    print("  1. Review chunking quality")
                    print("  2. Verify PDF extraction accuracy")
                    print("  3. Search for specific content (Ctrl+F)")
                    print("  4. Share with others for troubleshooting")

                elif command == "/clear-cache":
                    print("\n⚠️  This will clear the RAG cache for all documents.")
                    print(
                        "   Cached chunks will be deleted and documents will need to be re-indexed."
                    )
                    response = input("\nAre you sure? (yes/no): ").strip().lower()

                    if response == "yes":
                        import shutil

                        cache_dir = agent.rag.config.cache_dir
                        if os.path.exists(cache_dir):
                            shutil.rmtree(cache_dir)
                            os.makedirs(cache_dir, exist_ok=True)
                            print(f"\n✅ Cache cleared: {cache_dir}")

                            # Clear in-memory state as well
                            agent.rag.indexed_files.clear()
                            agent.rag.chunks.clear()
                            agent.rag.chunk_to_file.clear()
                            agent.rag.file_to_chunk_indices.clear()
                            agent.rag.file_metadata.clear()
                            agent.rag.index = None
                            agent.indexed_files.clear()

                            print(
                                "\nAll documents will be re-indexed from scratch on next access."
                            )
                        else:
                            print(f"\nℹ️  Cache directory doesn't exist: {cache_dir}")
                    else:
                        print("\n❌ Cache clear cancelled")

                elif command == "/search-debug":
                    # Toggle RAG debug mode
                    current_debug = getattr(agent.rag.config, "show_stats", False)
                    agent.rag.config.show_stats = not current_debug

                    if agent.rag.config.show_stats:
                        print("\n🔧 RAG Debug Mode: ENABLED")
                        print("\nAll queries will now show:")
                        print("  - Embedding generation details")
                        print("  - Search execution info")
                        print("  - Similarity scores")
                        print("  - Chunk selection reasoning")
                    else:
                        print("\n🔧 RAG Debug Mode: DISABLED")
                        print("\nQueries will run in normal mode")

                else:
                    print(f"Unknown command: {command}. Type /help for help.")

                continue

            # Process regular query
            result = agent.process_query(user_input)
            # The answer is already streamed by the agent, no need to print it again

            # Update conversation history for session persistence
            if hasattr(agent, "conversation_history"):
                agent.conversation_history.append(
                    {"role": "user", "content": user_input}
                )
                if result.get("result"):
                    agent.conversation_history.append(
                        {"role": "assistant", "content": result["result"]}
                    )

            if result.get("error_count", 0) > 0:
                print(f"\n⚠️  {result['error_count']} error(s) occurred")

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except EOFError:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            logger.error(f"Error: {e}")
            print(f"\n❌ Error: {e}")


def main():
    """Main entry point."""
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
            use_chatgpt=args.use_chatgpt,
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
        # Cleanup
        try:
            agent.stop_watching()
        except Exception:  # pylint: disable=broad-except
            pass


if __name__ == "__main__":
    sys.exit(main())
