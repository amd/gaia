from dataclasses import dataclass, field
from typing import Optional

from gaia.agents.base.agent import Agent, default_max_steps
from gaia.agents.tools import ScreenshotToolsMixin
from gaia.logger import get_logger
from gaia.mcp.mixin import MCPClientMixin
from gaia.sd.mixin import SDToolsMixin
from gaia.vlm.mixin import VLMToolsMixin

logger = get_logger(__name__)


@dataclass
class ChatAgentLiteConfig:
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = field(default_factory=default_max_steps)


class ChatAgentLite(
    Agent, VLMToolsMixin, SDToolsMixin, ScreenshotToolsMixin, MCPClientMixin
):
    """Lightweight ChatAgent: conversational only, minimal tools.

    This agent is intended to be a slim conversational assistant without
    the heavy RAG and file I/O mixins.
    """

    def __init__(self, config: Optional[ChatAgentLiteConfig] = None):
        if config is None:
            config = ChatAgentLiteConfig()
        self.config = config

        # Agent has no MCP servers; the UI auto-calls get_mcp_status_report()
        # on every chat send and MCPClientMixin.__init__ never runs because
        # Agent.__init__ doesn't chain super().
        self._mcp_manager = None

        # Avoid initializing local Lemonade during unit tests
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
        # VLM/SD mixins register their own tools via init methods; do not init by default.
        # Register only lightweight tools shared across chat: screenshots (if available)
        try:
            self.register_screenshot_tools()
        except Exception as e:
            # optional in test environments — non-fatal, but visible
            logger.debug("Screenshot tools not registered: %s", e)

    def _get_system_prompt(self) -> str:
        return "You are AMD GAIA Chat Assistant. Be concise, helpful, and safe."
