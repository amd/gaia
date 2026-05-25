# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Browser-focused GAIA agent."""

from dataclasses import dataclass
from typing import List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import BrowserToolsMixin
from gaia.mcp.mixin import MCPClientMixin
from gaia.security import PathValidator
from gaia.web.client import WebClient


@dataclass
class BrowserAgentConfig:
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = 10
    streaming: bool = False
    debug: bool = False
    debug_prompts: bool = False
    show_prompts: bool = False
    show_stats: bool = False
    silent_mode: bool = False
    output_dir: Optional[str] = None
    allowed_paths: Optional[List[str]] = None
    browser_timeout: int = 30
    browser_max_download_size: int = 100 * 1024 * 1024
    browser_rate_limit: float = 1.0


class BrowserAgent(Agent, BrowserToolsMixin, MCPClientMixin):
    """Agent focused on web search, page fetching, and downloads."""

    def __init__(self, config: Optional[BrowserAgentConfig] = None):
        if config is None:
            config = BrowserAgentConfig()
        self.config = config
        self.path_validator = PathValidator(
            config.allowed_paths,
            on_prompt_start=lambda: self.console.stop_progress(),
            on_prompt_end=lambda: self.console.resume_progress(),
        )
        self._path_validator = self.path_validator
        self._web_client = WebClient(
            timeout=config.browser_timeout,
            max_download_size=config.browser_max_download_size,
            rate_limit=config.browser_rate_limit,
        )

        super().__init__(
            use_claude=config.use_claude,
            use_chatgpt=config.use_chatgpt,
            claude_model=config.claude_model,
            base_url=config.base_url,
            model_id=config.model_id,
            max_steps=config.max_steps,
            debug_prompts=config.debug_prompts,
            show_prompts=config.show_prompts,
            output_dir=config.output_dir,
            streaming=config.streaming,
            show_stats=config.show_stats,
            silent_mode=config.silent_mode,
            debug=config.debug,
            skip_lemonade=True,
        )

    def _register_tools(self) -> None:
        _TOOL_REGISTRY.clear()
        self.register_browser_tools()
        self._snapshot_tools()

    def _get_system_prompt(self) -> str:
        return (
            "You are BrowserAgent, a web research specialist. Use search_web to "
            "find sources, fetch_page to read them, and download_file only when "
            "the user needs a local copy. Cite URLs you used and say when a page "
            "cannot be fetched."
        )

    def close(self) -> None:
        if self._web_client:
            self._web_client.close()
