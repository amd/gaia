# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
DocumentQAAgent — RAG-focused agent scaffold.
"""

from dataclasses import dataclass
from typing import Optional

from gaia.agents.base.agent import Agent
from gaia.agents.chat.tools import FileToolsMixin, RAGToolsMixin
from gaia.agents.code.tools.file_io import FileIOToolsMixin
from gaia.agents.tools import FileSearchToolsMixin
from gaia.mcp.mixin import MCPClientMixin


@dataclass
class DocumentQAAgentConfig:
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = 10
    rag_documents: list[str] = None


class DocumentQAAgent(
    Agent,
    RAGToolsMixin,
    FileToolsMixin,
    FileIOToolsMixin,
    FileSearchToolsMixin,
    MCPClientMixin,
):
    """RAG-focused agent for document Q&A and indexing."""

    def __init__(self, config: Optional[DocumentQAAgentConfig] = None):
        if config is None:
            config = DocumentQAAgentConfig()
        self.config = config

        # Minimal RAG initialization is attempted, but tests may run without RAG deps
        try:
            from gaia.rag.sdk import RAGSDK, RAGConfig

            rag_config = RAGConfig(model=config.model_id or "Qwen3.5-35B-A3B-GGUF")
            self.rag = RAGSDK(rag_config)
        except Exception:
            self.rag = None

        super().__init__(
            use_claude=config.use_claude,
            use_chatgpt=config.use_chatgpt,
            claude_model=config.claude_model,
            base_url=config.base_url,
            model_id=config.model_id,
            max_steps=config.max_steps,
            skip_lemonade=True,
        )

    def _register_tools(self) -> None:
        # Register RAG + file-related tools
        try:
            self.register_rag_tools()
            self.register_file_tools()
            self.register_file_search_tools()
            self.register_file_io_tools()
        except Exception:
            # Allow import-time/test-time environments to skip optional deps
            pass

    def _get_system_prompt(self) -> str:
        return "You are DocumentQAAgent. Use indexed documents to answer user queries accurately and cite sources."
