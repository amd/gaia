# SPDX-License-Identifier: MIT

"""Regression tests for RAG startup/indexing status propagation."""

from unittest.mock import Mock, patch

from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.talk.sdk import TalkSDK


def _uninitialized_chat() -> AgentSDK:
    """Build an AgentSDK without starting an LLM provider."""
    chat = AgentSDK.__new__(AgentSDK)
    chat.config = AgentConfig()
    chat.log = Mock()
    chat.rag = None
    chat.rag_enabled = False
    return chat


def test_chat_enable_rag_requires_a_successful_index_result():
    """A non-empty failure dict must not produce a success log or result."""
    chat = _uninitialized_chat()

    with patch("gaia.rag.sdk.RAGSDK") as rag_class:
        rag_class.return_value.index_document.return_value = {
            "success": False,
            "error": "embedding model unavailable",
        }

        result = chat.enable_rag(documents=["manual.pdf"])

    assert result is False
    assert chat.rag_enabled is True
    assert any(
        "Failed to index document" in call.args[0]
        for call in chat.log.warning.call_args_list
    )
    assert not any(
        "Successfully indexed" in call.args[0] for call in chat.log.info.call_args_list
    )
    assert any(
        "indexed 0 of 1 documents" in call.args[0]
        for call in chat.log.warning.call_args_list
    )


def test_talk_enable_rag_propagates_chat_index_failure():
    """Talk must not report RAG success when Chat reports a failed index."""
    talk = TalkSDK.__new__(TalkSDK)
    talk.chat_sdk = Mock()
    talk.chat_sdk.enable_rag.return_value = False
    talk.log = Mock()

    result = talk.enable_rag(documents=["manual.pdf"])

    assert result is False
    talk.chat_sdk.enable_rag.assert_called_once_with(documents=["manual.pdf"])
    assert not any(
        "RAG enabled with" in call.args[0] for call in talk.log.info.call_args_list
    )
    talk.log.warning.assert_called_once_with(
        "RAG enabled but one or more documents failed to index"
    )


def test_talk_enable_rag_preserves_success_status():
    """Talk keeps its success log when Chat indexes every document."""
    talk = TalkSDK.__new__(TalkSDK)
    talk.chat_sdk = Mock()
    talk.chat_sdk.enable_rag.return_value = True
    talk.log = Mock()

    result = talk.enable_rag(documents=["manual.pdf"])

    assert result is True
    assert any(
        "RAG enabled with 1 documents" in call.args[0]
        for call in talk.log.info.call_args_list
    )
    talk.log.warning.assert_not_called()
