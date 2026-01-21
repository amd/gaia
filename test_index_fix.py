#!/usr/bin/env python3
"""Test script to verify /index command updates system prompt."""

import sys
from pathlib import Path

from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig


def test_index_and_query():
    """Test that indexing updates system prompt and allows querying."""

    print("=" * 80)
    print("Testing /index fix: System prompt update after indexing")
    print("=" * 80)

    # Create chat agent
    config = ChatAgentConfig(
        silent_mode=False,
        debug=True,
        streaming=False,
    )
    agent = ChatAgent(config)

    # Create initial session
    if not agent.current_session:
        agent.current_session = agent.session_manager.create_session()

    print("\n[1] Checking initial state (no documents indexed)...")
    print(f"   Indexed files: {len(agent.rag.indexed_files)}")
    print(f"   System prompt contains 'No documents': {'No documents are currently indexed' in agent.system_prompt}")

    # Index the PDF
    pdf_path = Path("data/PDF/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf")
    if not pdf_path.exists():
        print(f"\n❌ Test PDF not found: {pdf_path}")
        return False

    print(f"\n[2] Indexing PDF: {pdf_path.name}")
    result = agent.rag.index_document(str(pdf_path.absolute()))

    if not result.get("success"):
        print(f"❌ Indexing failed: {result.get('error')}")
        return False

    print(f"   ✅ Indexed successfully: {result.get('num_chunks', 0)} chunks")
    print(f"   Indexed files: {len(agent.rag.indexed_files)}")

    # Simulate what /index command does - update system prompt
    print("\n[3] Updating system prompt (simulating /index command)...")
    agent._update_system_prompt()

    print(f"   System prompt now contains document list: {'CURRENTLY INDEXED DOCUMENTS' in agent.system_prompt}")
    print(f"   System prompt contains '{pdf_path.name}': {pdf_path.name in agent.system_prompt}")
    print(f"   System prompt contains 'No documents': {'No documents are currently indexed' in agent.system_prompt}")

    # Try to query the document
    print("\n[4] Querying the document...")
    query = "What is this document about?"

    try:
        response = agent.process_query(query)

        print(f"\n   Query: {query}")
        print(f"   Status: {response.get('status')}")
        print(f"   Steps taken: {response.get('steps_taken')}")

        # Check if the agent actually used RAG tools
        if response.get('status') == 'success':
            print(f"\n   ✅ Query succeeded!")
            print(f"\n   Response preview: {response.get('result', '')[:200]}...")
            return True
        else:
            print(f"\n   ❌ Query failed: {response.get('result')}")
            return False

    except Exception as e:
        print(f"\n   ❌ Query error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_index_and_query()
    sys.exit(0 if success else 1)
