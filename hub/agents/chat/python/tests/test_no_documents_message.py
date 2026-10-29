# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An Agent UI chat with nothing attached says so, not that the index is empty.

The RAG tools' side of this lives in tests/unit/test_rag_no_documents_message.py.
"""

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig


def _chat_agent(**config) -> ChatAgent:
    agent = ChatAgent.__new__(ChatAgent)
    agent.config = ChatAgentConfig(**config)
    return agent


def test_an_agent_ui_chat_says_nothing_is_attached_to_it():
    message = _chat_agent(ui_session_id="s1")._no_documents_message()

    assert message.startswith("No documents are attached to this chat.")
    assert "Document library" in message
    assert "empty" not in message


def test_outside_the_agent_ui_the_index_is_simply_empty():
    assert (
        _chat_agent()._no_documents_message() == "No documents are currently indexed."
    )
