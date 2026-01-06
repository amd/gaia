#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA RAG Demo - Simple demonstration of PDF document Q&A capabilities
"""

import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent))


def demo_basic_rag():
    """Demo basic RAG functionality."""
    print("=" * 60)
    print("GAIA RAG Demo - Basic PDF Q&A")
    print("=" * 60)

    try:
        from gaia.rag.sdk import RAGSDK, RAGConfig, quick_rag

        # Example 1: Quick RAG query
        print("\n📄 Example 1: Quick RAG Query")
        print("-" * 40)

        # Using the existing test PDF file
        pdf_path = "data/pdf/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf"

        # Check if the PDF file exists
        if os.path.exists(pdf_path):
            print(f"📄 Using test PDF: {pdf_path}")
            print("🤔 Asking: 'What is this document about?'")
            print("⏳ Processing... (this may take a moment)")

            try:
                # For the first demo, we'll use the SDK directly to show chunks
                config = RAGConfig(show_stats=True, chunk_size=500, max_chunks=3)
                rag = RAGSDK(config)

                # Index the document
                print("📚 Indexing document...")
                rag.index_document(pdf_path)

                # Query with detailed response
                print("🔍 Querying document...")
                response = rag.query("What is this document about?")
                print(f"\n💬 Answer: {response.text}")

                # Show retrieved chunks
                if response.chunks:
                    print(f"\n📖 Retrieved {len(response.chunks)} relevant chunks:")
                    print("=" * 50)
                    for i, (chunk, score) in enumerate(
                        zip(response.chunks, response.chunk_scores), 1
                    ):
                        print(f"\n📄 Chunk {i} (relevance score: {score:.3f}):")
                        print("-" * 30)
                        # Show first 200 characters of each chunk
                        chunk_preview = (
                            chunk[:200] + "..." if len(chunk) > 200 else chunk
                        )
                        print(chunk_preview)
                    print("=" * 50)

                # Try another question using quick_rag
                print("\n" + "-" * 40)
                print("🤔 Asking: 'What are the main safety requirements?'")
                answer2 = quick_rag(
                    pdf_path, "What are the main safety requirements?", show_stats=False
                )
                print(f"\n💬 Answer: {answer2}")

            except Exception as e:
                print(f"❌ Error during RAG query: {e}")
                print(
                    "This might be due to missing dependencies or LLM service not running"
                )
        else:
            print(f"⚠️  Test PDF not found at: {pdf_path}")
            print("To try this demo, you need:")
            print(
                "1. Activate environment: source .venv/bin/activate (Linux/macOS) or .\\.venv\\Scripts\\Activate.ps1 (Windows)"
            )
            print('2. Install RAG dependencies: uv pip install -e ".[rag]"')
            print("3. Either use the test PDF or get your own PDF file")
            print("4. Run: answer = quick_rag('document.pdf', 'What is this about?')")

        # Example 2: RAG SDK with multiple operations
        print("\n📚 Example 2: RAG SDK with Multiple Operations")
        print("-" * 40)

        config = RAGConfig(show_stats=True, chunk_size=500, max_chunks=3)
        rag = RAGSDK(config)

        print("RAG SDK initialized successfully!")
        print(
            f"Configuration: chunk_size={config.chunk_size}, max_chunks={config.max_chunks}"
        )

        # Show status
        status = rag.get_status()
        print("\nRAG Status:")
        print(f"  Indexed files: {status['indexed_files']}")
        print(f"  Total chunks: {status['total_chunks']}")
        print(f"  Cache directory: {status['cache_dir']}")

        print("\nTo index a PDF and query it:")
        print("  rag.index_document('document.pdf')")
        print("  response = rag.query('What are the key points?')")
        print("  print(response.text)")

    except ImportError as e:
        print(f"❌ Missing dependencies: {e}")
        print("\nInstall RAG dependencies with:")
        print("  # IMPORTANT: Activate virtual environment first")
        print("  # Linux/macOS:")
        print("  source .venv/bin/activate")
        print("  # Windows PowerShell:")
        print("  .\\.venv\\Scripts\\Activate.ps1")
        print("  ")
        print("  # Install RAG extras")
        print('  uv pip install -e ".[rag]"')
        print("  ")
        print("  # Or install dependencies individually:")
        print("  uv pip install pypdf sentence-transformers faiss-cpu")

    print("\n" + "=" * 60)


def wait_for_user():
    """Wait for user to press Enter to continue."""
    try:
        input("\n🔄 Press Enter to continue to the next demo...")
        print()
    except KeyboardInterrupt:
        print("\n\n👋 Demo interrupted by user. Goodbye!")
        exit(0)


def demo_chat_with_rag():
    """Demo RAG integration with Chat SDK."""
    print("\n📱 Example 3: Chat with RAG Integration")
    print("-" * 40)

    try:
        from gaia.chat.sdk import ChatConfig, ChatSDK

        # Create chat with RAG support
        config = ChatConfig(show_stats=True)
        _chat = ChatSDK(config)

        print("Chat SDK initialized successfully!")

        # Enable RAG (you would pass actual PDF paths here)
        # chat.enable_rag(documents=["document1.pdf", "document2.pdf"])
        print("\nTo enable RAG in chat:")
        print("  chat.enable_rag(documents=['document.pdf'])")
        print("  response = chat.send('What does the document say about X?')")
        print("  # The chat will automatically use document context")

        print("\nRAG-enhanced chat features:")
        print("  • Automatic document context injection")
        print("  • Maintains conversation history")
        print("  • Seamless switching between RAG and normal chat")

    except ImportError as e:
        print(f"❌ Error: {e}")

    print("-" * 40)


def demo_error_handling():
    """Demo proper error handling."""
    print("\n⚠️  Example 4: Error Handling")
    print("-" * 40)

    try:
        from gaia.rag.sdk import RAGSDK, RAGConfig, quick_rag

        # Example 1: File not found error
        print("# Testing file validation...")
        try:
            _result = quick_rag("nonexistent.pdf", "What is this?")
        except ValueError as e:
            print(f"✅ Caught expected error: {e}")

        # Example 2: Empty inputs
        print("\n# Testing input validation...")
        try:
            _result = quick_rag("", "What is this?")
        except ValueError as e:
            print(f"✅ Caught expected error: {e}")

        try:
            _result = quick_rag("document.pdf", "")
        except ValueError as e:
            print(f"✅ Caught expected error: {e}")

        # Example 3: SDK with file validation
        print("\n# Testing SDK file validation...")
        config = RAGConfig(show_stats=False)
        rag = RAGSDK(config)

        try:
            success = rag.index_document("nonexistent.pdf")
            print(f"Index result: {success} (should be False)")
        except ValueError as e:
            print(f"✅ Caught expected error: {e}")

        print("\n✅ Error handling working correctly!")

    except ImportError as e:
        print(f"❌ Missing dependencies: {e}")
        print('Install with: uv pip install -e ".[rag]"')

    print("-" * 40)


def demo_cli_commands():
    """Demo CLI command examples."""
    print("\n🖥️  Example 5: CLI Commands")
    print("-" * 40)

    print("# IMPORTANT: Always activate virtual environment first")
    print("# Linux/macOS:")
    print("source .venv/bin/activate")
    print("# Windows PowerShell:")
    print(".\\.venv\\Scripts\\Activate.ps1")
    print("")

    print("# Index PDF documents")
    print("gaia rag index document1.pdf document2.pdf")
    print("")

    print("# Query indexed documents")
    print('gaia rag query "What are the key features mentioned?"')
    print("")

    print("# Quick query (index + query in one step)")
    print('gaia rag quick document.pdf "What is this document about?"')
    print("")

    print("# Query with on-the-fly indexing (alternative to quick)")
    print('gaia rag query "What is this about?" document.pdf')
    print("")

    print("# Show system status")
    print("gaia rag status")
    print("")

    print("# Clear cache (with confirmation)")
    print("gaia rag clear-cache")
    print("")

    print("# Clear cache (force, no confirmation)")
    print("gaia rag clear-cache --force")
    print("")

    print("# Get help for any command")
    print("gaia rag --help")
    print("gaia rag index --help")

    print("-" * 40)


def main():
    """Run all demos."""
    print("🤖 GAIA RAG System Demo")
    print("This demo will walk you through GAIA's RAG capabilities step by step.")
    print("Press Ctrl+C at any time to exit.")
    wait_for_user()

    demo_basic_rag()
    wait_for_user()

    demo_chat_with_rag()
    wait_for_user()

    demo_error_handling()
    wait_for_user()

    demo_cli_commands()

    print("\n✨ Demo completed!")
    print("\nNext steps:")
    print("1. Activate virtual environment:")
    print("   Linux/macOS: source .venv/bin/activate")
    print("   Windows: .\\.venv\\Scripts\\Activate.ps1")
    print('2. Install RAG dependencies: uv pip install -e ".[rag]"')
    print("3. Get a PDF document to test with")
    print("4. Try the CLI commands: gaia rag --help")
    print("5. Use RAG in Python: from gaia.rag.sdk import RAGSDK, quick_rag")
    print("\nTroubleshooting:")
    print("• File not found errors: Use absolute paths")
    print("• Memory issues: Reduce chunk_size (e.g., 200-300)")
    print("• Import errors: Ensure all dependencies installed")


if __name__ == "__main__":
    main()
