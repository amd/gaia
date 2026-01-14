#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA RAG Application - Simple PDF document Q&A
"""

import argparse
import os
import sys
from pathlib import Path

from gaia.rag.sdk import RAGSDK, RAGConfig


def index_command(args):
    """Index PDF documents."""
    config = RAGConfig(
        model=args.model,
        show_stats=args.verbose,
        chunk_size=args.chunk_size,
        max_chunks=args.max_chunks,
    )

    rag = RAGSDK(config)

    success_count = 0
    for pdf_path in args.files:
        if not os.path.exists(pdf_path):
            print(f"❌ File not found: {pdf_path}")
            continue

        print(f"📄 Indexing: {pdf_path}")
        if rag.index_document(pdf_path):
            print(f"✅ Successfully indexed: {pdf_path}")
            success_count += 1
        else:
            print(f"❌ Failed to index: {pdf_path}")

    print(f"\n📊 Indexed {success_count}/{len(args.files)} documents")

    # Show status
    status = rag.get_status()
    print(f"📚 Total chunks: {status['total_chunks']}")


def query_command(args):
    """Query indexed documents."""
    if not args.question:
        print("❌ Question is required for query command")
        return

    # If PDF files provided, index them first
    config = RAGConfig(
        model=args.model,
        show_stats=args.verbose,
        chunk_size=args.chunk_size,
        max_chunks=args.max_chunks,
    )

    rag = RAGSDK(config)

    # Index documents if provided
    if args.files:
        print("📄 Indexing documents...")
        for pdf_path in args.files:
            if os.path.exists(pdf_path):
                print(f"  • {pdf_path}")
                rag.index_document(pdf_path)

    # Check if we have any indexed documents
    status = rag.get_status()
    if status["total_chunks"] == 0:
        print("❌ No documents indexed. Please index documents first.")
        print("   Use: gaia rag index document.pdf")
        return

    print(f"\n❓ Question: {args.question}")
    print("🤔 Searching...")

    try:
        response = rag.query(args.question)

        print("\n💬 Answer:")
        print(response.text)

        if args.verbose and response.chunks:
            print(f"\n📖 Retrieved {len(response.chunks)} relevant chunks:")
            for i, (chunk, score) in enumerate(
                zip(response.chunks, response.chunk_scores)
            ):
                print(f"\n  Chunk {i+1} (relevance: {score:.3f}):")
                print(f"  {chunk[:200]}...")

        if response.stats and args.verbose:
            print(f"\n📊 Stats: {response.stats}")

    except Exception as e:
        print(f"❌ Query failed: {e}")


def status_command(args):  # pylint: disable=unused-argument
    """Show RAG system status."""
    config = RAGConfig()
    rag = RAGSDK(config)

    status = rag.get_status()

    print("📊 GAIA RAG Status")
    print("=" * 30)
    print(f"Indexed files: {status['indexed_files']}")
    print(f"Total chunks: {status['total_chunks']}")
    print(f"Cache directory: {status['cache_dir']}")
    print(f"Embedding model: {status['embedding_model']}")
    print("\nConfiguration:")
    print(f"  Chunk size: {status['config']['chunk_size']} tokens")
    print(f"  Chunk overlap: {status['config']['chunk_overlap']} tokens")
    print(f"  Max chunks per query: {status['config']['max_chunks']}")


def clear_cache_command(args):
    """Clear RAG cache."""
    config = RAGConfig()
    rag = RAGSDK(config)

    if args.force or input("Clear RAG cache? (y/N): ").lower() == "y":
        rag.clear_cache()
        print("✅ Cache cleared")
    else:
        print("Cache not cleared")


def quick_command(args):
    """Quick RAG query - index document and query in one step."""
    if not args.question:
        print("❌ Question is required for quick command")
        return

    if not args.file:
        print("❌ PDF file is required for quick command")
        return

    if not os.path.exists(args.file):
        print(f"❌ File not found: {args.file}")
        return

    # Configure RAG
    config = RAGConfig(
        model=args.model,
        show_stats=args.verbose,
        chunk_size=args.chunk_size,
        max_chunks=args.max_chunks,
    )

    rag = RAGSDK(config)

    # Index document
    if args.verbose:
        print(f"📄 Indexing: {args.file}")
    else:
        print(f"📄 Processing {Path(args.file).name}...")

    if not rag.index_document(args.file):
        print(f"❌ Failed to index: {args.file}")
        return

    if args.verbose:
        print("✅ Indexed successfully")

    # Query
    print(f"\n❓ Question: {args.question}")
    print("🤔 Generating answer...")

    try:
        response = rag.query(args.question)

        print("\n💬 Answer:")
        print(response.text)

        if args.verbose and response.chunks:
            print(f"\n📖 Retrieved {len(response.chunks)} relevant chunks:")
            for i, (chunk, score) in enumerate(
                zip(response.chunks, response.chunk_scores)
            ):
                print(f"\n  Chunk {i+1} (relevance: {score:.3f}):")
                print(f"  {chunk[:200]}...")

        if response.stats and args.verbose:
            print(f"\n📊 Stats: {response.stats}")

    except Exception as e:
        print(f"❌ Query failed: {e}")


def main():
    """Main entry point for RAG CLI."""
    parser = argparse.ArgumentParser(
        description="GAIA RAG - Simple PDF document Q&A",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Index a PDF document
  gaia rag index document.pdf
  
  # Query indexed documents
  gaia rag query "What are the key features?"
  
  # Query with on-the-fly indexing (index + query in one step)
  gaia rag query "What is this document about?" document.pdf
  
  # Show system status
  gaia rag status
  
  # Clear cache
  gaia rag clear-cache
        """,
    )

    # Add subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Index command
    index_parser = subparsers.add_parser("index", help="Index PDF documents")
    index_parser.add_argument("files", nargs="+", help="PDF files to index")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query indexed documents")
    query_parser.add_argument("question", help="Question to ask")
    query_parser.add_argument("files", nargs="*", help="Additional PDF files to index")

    # Status command
    subparsers.add_parser("status", help="Show RAG system status")

    # Clear cache command
    clear_parser = subparsers.add_parser("clear-cache", help="Clear RAG cache")
    clear_parser.add_argument(
        "--force", action="store_true", help="Force clear without confirmation"
    )

    # Quick command (index + query in one step)
    quick_parser = subparsers.add_parser(
        "quick", help="Quick RAG query (index + query in one step)"
    )
    quick_parser.add_argument("file", help="PDF file to index and query")
    quick_parser.add_argument("question", help="Question to ask")

    # Common arguments for all commands
    for subparser in [index_parser, query_parser, quick_parser]:
        subparser.add_argument(
            "--model", default="Llama-3.2-3B-Instruct-Hybrid", help="Model to use"
        )
        subparser.add_argument(
            "--chunk-size", type=int, default=500, help="Chunk size in tokens"
        )
        subparser.add_argument(
            "--max-chunks", type=int, default=3, help="Maximum chunks to retrieve"
        )
        subparser.add_argument(
            "--verbose", "-v", action="store_true", help="Show detailed output"
        )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == "index":
            index_command(args)
        elif args.command == "query":
            query_command(args)
        elif args.command == "quick":
            quick_command(args)
        elif args.command == "status":
            status_command(args)
        elif args.command == "clear-cache":
            clear_cache_command(args)

    except KeyboardInterrupt:
        print("\n\n⚠️  Operation interrupted")
    except Exception as e:
        print(f"❌ Error: {e}")
        if hasattr(args, "verbose") and args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
