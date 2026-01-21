#!/usr/bin/env python3
"""Test that system prompt gets updated after indexing documents."""

import sys
from pathlib import Path

# Mock the check to avoid needing Lemonade server
import unittest.mock as mock

with mock.patch('gaia.llm.lemonade_manager.LemonadeManager.ensure_ready'):
    from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig


def test_system_prompt_update():
    """Test that _update_system_prompt() correctly updates the prompt with indexed documents."""

    print("=" * 80)
    print("Testing: System prompt update after document indexing")
    print("=" * 80)

    # Create chat agent with mock LLM
    config = ChatAgentConfig(
        silent_mode=True,
        debug=False,
        streaming=False,
    )

    try:
        agent = ChatAgent(config)
    except Exception as e:
        print(f"\n❌ Failed to create agent: {e}")
        return False

    # Check RAG is available
    if agent.rag is None:
        print("\n⚠️  RAG is not available (missing dependencies)")
        print("This is expected if RAG dependencies aren't installed")
        return True

    print("\n[1] Initial state (no documents indexed)")
    print(f"   Indexed files: {len(agent.rag.indexed_files)}")

    # Check initial system prompt
    initial_prompt = agent.system_prompt
    has_no_docs_message = "No documents are currently indexed" in initial_prompt
    print(f"   System prompt contains 'No documents' message: {has_no_docs_message}")

    if not has_no_docs_message:
        print("   ⚠️  Initial prompt should indicate no documents")

    # Simulate indexing by manually adding a document path to indexed_files
    # (We're testing the prompt update logic, not actual PDF processing)
    print("\n[2] Simulating document indexing...")
    fake_doc_path = "/fake/path/test-document.pdf"
    agent.rag.indexed_files.add(fake_doc_path)
    print(f"   Added fake document: {fake_doc_path}")
    print(f"   Indexed files count: {len(agent.rag.indexed_files)}")

    # Before update - system prompt should still say no documents
    print("\n[3] Before _update_system_prompt() call...")
    has_no_docs_before = "No documents are currently indexed" in agent.system_prompt
    has_doc_list_before = "CURRENTLY INDEXED DOCUMENTS" in agent.system_prompt
    print(f"   Prompt still says 'No documents': {has_no_docs_before}")
    print(f"   Prompt has document list section: {has_doc_list_before}")

    # Call the update method (this is what the fix adds to /index command)
    print("\n[4] Calling _update_system_prompt()...")
    agent._update_system_prompt()

    # After update - system prompt should reflect the indexed document
    print("\n[5] After _update_system_prompt() call...")
    updated_prompt = agent.system_prompt
    has_no_docs_after = "No documents are currently indexed" in updated_prompt
    has_doc_list_after = "CURRENTLY INDEXED DOCUMENTS" in updated_prompt
    has_indexed_count = "You have 1 document(s) already indexed" in updated_prompt

    print(f"   Prompt says 'No documents': {has_no_docs_after}")
    print(f"   Prompt has document list section: {has_doc_list_after}")
    print(f"   Prompt mentions '1 document(s)': {has_indexed_count}")

    # Verify the fix worked
    success = (
        has_no_docs_before  # Before: said "no documents"
        and not has_no_docs_after  # After: doesn't say "no documents"
        and has_doc_list_after  # After: has document list
        and has_indexed_count  # After: shows count
    )

    if success:
        print("\n✅ SUCCESS: System prompt correctly updated after indexing!")
        print("   The fix ensures the agent knows about indexed documents.")
        return True
    else:
        print("\n❌ FAILURE: System prompt not properly updated")
        if has_no_docs_after:
            print("   Issue: Prompt still says 'No documents are currently indexed'")
        if not has_doc_list_after:
            print("   Issue: Prompt missing document list section")
        if not has_indexed_count:
            print("   Issue: Prompt doesn't show document count")
        return False


if __name__ == "__main__":
    try:
        success = test_system_prompt_update()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
