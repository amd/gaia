#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
RAG Document Q&A Agent Example

A document Q&A agent that uses RAG (Retrieval Augmented Generation) to answer
questions based on indexed documents. Perfect for private, HIPAA-compliant
document search with 100% local execution.

Requirements:
- Python 3.12+
- Lemonade server running for LLM reasoning
- Documents to index (PDF, TXT, MD, etc.)
- GAIA installed with RAG extras: `uv pip install -e ".[rag]"`

Run:
    uv run examples/rag_doc_agent.py [document_directory]

Examples:
    # Index current directory
    uv run examples/rag_doc_agent.py

    # Index specific directory
    uv run examples/rag_doc_agent.py ~/company_docs
"""

import sys
from pathlib import Path

from gaia import Agent, tool
from gaia.rag.sdk import RAGSDK, RAGConfig


class DocAgent(Agent):
    """Agent that answers questions using indexed documents.

    This example uses RAGSDK directly with a single custom `query_documents`
    tool rather than inheriting from the ChatAgent-specific RAGToolsMixin,
    keeping the example small and self-contained.
    """

    # File types we ship as RAG-ready out of the box.
    SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}

    def __init__(self, index_dir: str = "./docs", **kwargs):
        """Initialize the Document Q&A Agent.

        Args:
            index_dir: Directory containing documents to index
            **kwargs: Additional arguments passed to Agent
        """
        # Store the index directory before super().__init__() so that any
        # early hook (like _get_system_prompt) can reference it safely.
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Use the compact 4B model for faster local inference.  ``setdefault``
        # lets callers override the model via kwargs (used by the integration
        # tests to pin to whatever model is loaded by the CI Lemonade server).
        kwargs.setdefault("model_id", "Qwen3-4B-Instruct-2507-GGUF")

        # Initialize the shared RAG SDK with the same model so that both the
        # agent's reasoning and the RAG SDK's internal answer-synthesis call
        # use the same loaded Lemonade model.  Without this the RAG path
        # silently tries to load the framework default (Qwen3.5-35B-A3B-GGUF).
        self.rag = RAGSDK(
            RAGConfig(
                model=kwargs["model_id"],
                chunk_size=500,
                chunk_overlap=100,
                max_chunks=5,
                allowed_paths=[str(self.index_dir.resolve())],
            )
        )

        super().__init__(**kwargs)

        # Index any documents already in the directory.
        self._index_directory()

    def _index_directory(self) -> None:
        """Index every supported file in `self.index_dir`."""
        if not self.index_dir.exists() or not any(self.index_dir.iterdir()):
            print(f"⚠️  No documents found in {self.index_dir}")
            print("  Add some documents (PDF, TXT, MD) to this directory first.")
            return

        print(f"Indexing documents from: {self.index_dir}")
        indexed = 0
        for doc_path in self.index_dir.rglob("*"):
            if (
                doc_path.is_file()
                and doc_path.suffix.lower() in self.SUPPORTED_SUFFIXES
            ):
                result = self.rag.index_document(str(doc_path))
                if result.get("success"):
                    indexed += 1
        print(f"  ✅ Indexed {indexed} document(s) from {self.index_dir}")

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return f"""You are a document Q&A assistant.

Answer questions using the indexed documents from: {self.index_dir}

When answering:
1. Use the query_documents tool to search for relevant information
2. Cite specific documents when possible
3. If the information isn't in the documents, say so clearly
4. Be concise and accurate

All data stays local - perfect for sensitive/private documents."""

    def _register_tools(self) -> None:
        """Register a minimal RAG query tool bound to this agent."""
        agent = self

        @tool
        def query_documents(question: str) -> dict:
            """Query indexed documents for information relevant to `question`.

            Args:
                question: The natural-language question to answer with RAG.

            Returns:
                dict with the retrieved answer text, the chunks that were
                retrieved, and the list of source files.
            """
            response = agent.rag.query(question, include_metadata=True)
            return {
                "answer": response.text,
                "chunks": response.chunks or [],
                "source_files": response.source_files or [],
            }


def main():
    """Run the RAG Document Q&A Agent."""
    # Get directory from command line or use default
    index_dir = sys.argv[1] if len(sys.argv) > 1 else "./docs"

    print("=" * 60)
    print("RAG Document Q&A Agent")
    print("=" * 60)
    print(f"\nIndexing directory: {index_dir}")
    print("\nThis agent uses RAG to answer questions from your documents.")
    print("All data stays 100% local - HIPAA-compliant!")
    print("\nExamples:")
    print("  - 'What's our Q4 revenue policy?'")
    print("  - 'Summarize the main points from the technical spec'")
    print("  - 'What does the document say about security?'")
    print("\nType 'quit' or 'exit' to stop.\n")

    # Create agent (uses local Lemonade server by default)
    try:
        agent = DocAgent(index_dir=index_dir)
        print("Document Q&A Agent ready!\n")
    except Exception as e:
        print(f"Error initializing agent: {e}")
        print("\nMake sure Lemonade server is running:")
        print("  lemonade-server serve")
        return

    # Interactive loop
    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            # Process the query
            result = agent.process_query(user_input)
            if result.get("result"):
                print(f"\nAgent: {result['result']}\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
