# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the Doc Search reference agent.

These tests mock the LLM and the RAG SDK, so they run with no live Lemonade
server and no embedding model. They verify the composition pattern: the agent
inherits the framework ``RAGToolsMixin`` and activates its tools.
"""

from unittest.mock import MagicMock, patch

import gaia_agent_doc_search
from gaia_agent_doc_search.agent import DocSearchAgent

from gaia.agents.tools import RAGToolsMixin


def test_build_registration_metadata():
    reg = gaia_agent_doc_search.build_registration()
    assert reg.id == "doc-search"
    assert reg.category == "examples"
    assert reg.tools_count == 10
    assert reg.namespaced_agent_id == "installed:doc-search"
    assert reg.models == ["Gemma-4-E4B-it-GGUF"]


def test_agent_composes_rag_mixin():
    """The example's whole point: it inherits the framework RAG mixin."""
    assert issubclass(DocSearchAgent, RAGToolsMixin)


def test_rag_tools_register():
    """Constructing the agent (LLM + RAG mocked) registers RAG tools."""
    with (
        patch("gaia.rag.sdk.RAGSDK", return_value=MagicMock()),
        patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()),
    ):
        agent = DocSearchAgent(skip_lemonade=True)

    # The mixin contributes a recognizable retrieval tool.
    assert "query_documents" in agent._tools_registry
    assert "index_document" in agent._tools_registry
    assert "query_documents" in agent.system_prompt
