# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
from dataclasses import dataclass
from typing import Optional

from gaia.agents.base.agent import Agent
from gaia.agents.chat.tools import ShellToolsMixin
from gaia.agents.code.tools.file_io import FileIOToolsMixin
from gaia.agents.tools import FileSearchToolsMixin, ScreenshotToolsMixin
from gaia.mcp.mixin import MCPClientMixin


@dataclass
class FileIOAgentConfig:
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = 10


class FileIOAgent(
    Agent,
    FileIOToolsMixin,
    FileSearchToolsMixin,
    ShellToolsMixin,
    ScreenshotToolsMixin,
    MCPClientMixin,
):
    """Agent focused on file system and safe shell operations."""

    def __init__(self, config: Optional[FileIOAgentConfig] = None):
        if config is None:
            config = FileIOAgentConfig()
        self.config = config

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
        try:
            self.register_file_io_tools()
            self.register_file_search_tools()
            self.register_shell_tools()
            self.register_screenshot_tools()
        except (ImportError, AttributeError) as e:
            from gaia.logger import get_logger

            get_logger(__name__).debug("FileIOAgent: optional tools skipped: %s", e)

    def _get_system_prompt(self) -> str:
        return "You are FileIOAgent. Perform file operations safely and ask for confirmation before destructive actions."
