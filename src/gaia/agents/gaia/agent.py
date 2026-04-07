# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GaiaAgent — lightweight built-in agent with fast TTFT.

Uses a lean ~1,500-token system prompt instead of ChatAgent's ~7,400-token prompt,
reducing boot warmup and first-message latency on AMD iGPU hardware.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.chat.tools.rag_tools import RAGToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.logger import get_logger
from gaia.mcp.mixin import MCPClientMixin
from gaia.security import PathValidator

logger = get_logger(__name__)


@dataclass
class GaiaAgentConfig:
    """Configuration for GaiaAgent."""

    # LLM settings
    base_url: Optional[str] = None
    model_id: Optional[str] = None  # None = use default Qwen3.5-35B-A3B-GGUF

    # Execution settings
    max_steps: int = 10
    streaming: bool = False

    # Debug/output settings
    debug: bool = False
    show_stats: bool = False
    silent_mode: bool = False
    output_dir: Optional[str] = None

    # RAG settings
    rag_documents: List[str] = field(default_factory=list)
    library_documents: List[str] = field(default_factory=list)

    # Security
    allowed_paths: Optional[List[str]] = None


class GaiaAgent(Agent, RAGToolsMixin, FileSearchToolsMixin, MCPClientMixin):
    """Lightweight built-in agent with document Q&A and file tools.

    Uses a lean system prompt (~1,500 tokens) for fast TTFT on AMD iGPU hardware.
    Provides RAG, file search, and MCP tool support.
    """

    AGENT_ID = "gaia"
    AGENT_NAME = "GAIA"
    AGENT_DESCRIPTION = (
        "Fast, lightweight AI assistant with document Q&A and file tools"
    )
    CONVERSATION_STARTERS = [
        "What can you help me with?",
        "Search my documents",
        "Find files on my computer",
    ]

    def __init__(self, config: Optional[GaiaAgentConfig] = None):
        """Initialize GaiaAgent.

        Args:
            config: GaiaAgentConfig. Uses defaults if None.
        """
        config = config or GaiaAgentConfig()
        self.config = config

        # Security: configure allowed paths for file operations
        if config.allowed_paths is None:
            self.allowed_paths = [Path.cwd()]
        else:
            self.allowed_paths = [Path(p).resolve() for p in config.allowed_paths]
        self.path_validator = PathValidator(config.allowed_paths)

        # Effective model and base URL
        effective_model_id = config.model_id or "Qwen3.5-35B-A3B-GGUF"
        effective_base_url = (
            config.base_url
            if config.base_url is not None
            else os.getenv("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")
        )

        # Initialize RAG SDK (optional — graceful degradation if not installed)
        try:
            from gaia.rag.sdk import RAGSDK, RAGConfig

            rag_config = RAGConfig(
                model=effective_model_id,
                show_stats=config.show_stats,
                base_url=effective_base_url,
                allowed_paths=config.allowed_paths,
            )
            self.rag = RAGSDK(rag_config)
        except Exception as e:
            logger.warning(
                "RAG not available (install with: uv pip install -e '.[rag]'): %s", e
            )
            self.rag = None

        self.indexed_files: set = set()
        self.library_documents: List[str] = config.library_documents

        # MCP client manager — must be set before super().__init__() because
        # Agent.__init__() calls _register_tools(), which uses self._mcp_manager.
        try:
            from gaia.mcp.client.config import MCPConfig
            from gaia.mcp.client.mcp_client_manager import MCPClientManager

            self._mcp_manager = MCPClientManager(config=MCPConfig(), debug=config.debug)
        except Exception as e:
            logger.debug("MCP not available: %s", e)
            self._mcp_manager = None

        # Call parent constructor (triggers _register_tools internally)
        super().__init__(
            base_url=effective_base_url,
            model_id=effective_model_id,
            max_steps=config.max_steps,
            streaming=config.streaming,
            show_stats=config.show_stats,
            silent_mode=config.silent_mode,
            debug=config.debug,
            output_dir=config.output_dir,
        )

        # Index initial documents
        if config.rag_documents and self.rag:
            for doc_path in config.rag_documents:
                try:
                    result = self.rag.index_document(doc_path)
                    if result.get("success"):
                        self.indexed_files.add(doc_path)
                except Exception as e:
                    logger.warning("Failed to index document %s: %s", doc_path, e)

    def _create_console(self) -> AgentConsole:
        return AgentConsole()

    def _get_system_prompt(self) -> str:
        """Return the lean GAIA system prompt."""
        from gaia.agents.gaia.system_prompt import GAIA_SYSTEM_PROMPT

        return GAIA_SYSTEM_PROMPT

    def _register_tools(self) -> None:
        """Register tools: RAG, file search, and MCP."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        _TOOL_REGISTRY.clear()
        self.register_rag_tools()
        self.register_file_search_tools()
        if self._mcp_manager is not None:
            self.load_mcp_servers_from_config()
