# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
from dataclasses import dataclass, field
from typing import List, Optional

from gaia.agents.base.agent import Agent, default_max_steps
from gaia.agents.tools import (
    FileIOToolsMixin,
    FileSearchToolsMixin,
    FileToolsMixin,
    RAGToolsMixin,
)
from gaia.mcp.mixin import MCPClientMixin


@dataclass
class DocumentQAAgentConfig:
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = field(default_factory=default_max_steps)
    rag_documents: Optional[List[str]] = None


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
        except ImportError:
            # Optional dependency not installed in test environments
            self.rag = None

        # Agent has no MCP servers; the UI auto-calls get_mcp_status_report()
        # on every chat send and MCPClientMixin.__init__ never runs because
        # Agent.__init__ doesn't chain super().
        self._mcp_manager = None

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
        except (ImportError, AttributeError) as e:
            # Optional mixin dependencies may be missing in test envs; log debug
            from gaia.logger import get_logger

            get_logger(__name__).debug("DocumentQAAgent: optional tools skipped: %s", e)

    def _get_system_prompt(self) -> str:
        return "You are DocumentQAAgent. Use indexed documents to answer user queries accurately and cite sources."
