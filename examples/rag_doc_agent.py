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

from gaia import Agent
from gaia.agents.chat.tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig


class DocAgent(Agent, RAGToolsMixin):
    """Agent that answers questions using indexed documents."""

    def __init__(self, index_dir: str = "./docs", **kwargs):
        """Initialize the Document Q&A Agent.

        Args:
            index_dir: Directory containing documents to index
            **kwargs: Additional arguments passed to Agent
        """
        # Initialize Agent with lightweight model for faster inference
        Agent.__init__(self, model_id="Qwen3-4B-GGUF", **kwargs)

        # Store the index directory
        self.index_dir = Path(index_dir)

        # Create directory if it doesn't exist
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Initialize RAG SDK
        rag_config = RAGConfig(chunk_size=500, chunk_overlap=100, max_chunks=5)
        self.rag = RAGSDK(rag_config)

        # Index documents in the directory
        if self.index_dir.exists() and any(self.index_dir.iterdir()):
            print(f"Indexing documents from: {self.index_dir}")
            for doc_path in self.index_dir.rglob("*"):
                if doc_path.is_file() and doc_path.suffix in [".txt", ".md", ".pdf"]:
                    self.rag.index_document(str(doc_path))
            print(f"  ✅ Indexed documents from {self.index_dir}")
        else:
            print(f"⚠️  No documents found in {self.index_dir}")
            print("  Add some documents (PDF, TXT, MD) to this directory first.")

        # Register RAG tools from mixin
        self.register_rag_tools()

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
        """Register agent tools.

        RAG tools are registered via register_rag_tools() in __init__.
        """
        # RAG tools already registered in __init__
        pass


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
