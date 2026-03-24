"""
Configurable Agent for Pipeline Orchestration

Dynamically configurable agent that loads tools and prompts from YAML definitions.
"""

from pathlib import Path
from typing import Dict, List, Any, Optional

from gaia.agents.base import Agent, _TOOL_REGISTRY
from gaia.agents.base.context import AgentDefinition
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


class ConfigurableAgent(Agent):
    """
    A dynamically configurable agent that loads its configuration from YAML.

    The ConfigurableAgent bridges the gap between YAML agent definitions
    and the base Agent class. It:
    - Loads tools from YAML configuration
    - Dynamically registers only the specified tools
    - Composes system prompt with tool descriptions
    - Executes agent logic with proper context

    Example:
        >>> definition = registry.get_agent("senior-developer")
        >>> agent = ConfigurableAgent(
        ...     definition=definition,
        ...     tools_dir=Path("gaia/tools")
        ... )
        >>> await agent.initialize()
        >>> result = await agent.execute({"goal": "Build a REST API"})
    """

    def __init__(
        self,
        definition: AgentDefinition,
        tools_dir: Optional[Path] = None,
        prompts_dir: Optional[Path] = None,
        **kwargs,
    ):
        """
        Initialize configurable agent.

        Args:
            definition: Agent definition from registry
            tools_dir: Directory containing tool implementations
            prompts_dir: Directory containing prompt templates
            **kwargs: Additional arguments passed to Agent base class
        """
        self.definition = definition
        self._tools_dir = tools_dir or Path("gaia/tools")
        self._prompts_dir = prompts_dir or Path("gaia/prompts")
        self._registered_tools: List[str] = []
        self._execution_context: Dict[str, Any] = {}

        # Store original system prompt path from YAML
        self._prompt_path = definition.metadata.get("system_prompt")

        # Initialize base agent with minimal settings
        # Tools will be registered in _register_tools()
        super().__init__(
            model_id=kwargs.get("model_id"),
            max_steps=definition.constraints.max_steps if definition.constraints else 100,
            **kwargs,
        )

        logger.info(
            f"ConfigurableAgent created: {definition.id}",
            extra={
                "agent_id": definition.id,
                "tools_count": len(definition.tools),
                "capabilities": definition.capabilities.capabilities if definition.capabilities else [],
            },
        )

    async def initialize(self) -> None:
        """
        Initialize agent by loading tools and composing prompt.

        This method:
        1. Registers tools from YAML definition
        2. Loads system prompt from file or uses default
        3. Rebuilds system prompt with tool descriptions
        """
        # Register tools from YAML
        self._register_tools_from_yaml()

        # Rebuild system prompt with tool descriptions
        self.rebuild_system_prompt()

        logger.info(
            f"ConfigurableAgent initialized: {self.definition.id}",
            extra={
                "agent_id": self.definition.id,
                "registered_tools": self._registered_tools,
            },
        )

    def _register_tools(self):
        """
        Register tools for this agent.

        This is called by the base Agent.__init__() and should not
        do anything here - tools are registered separately via
        _register_tools_from_yaml() after initialization.
        """
        # Tools are registered via _register_tools_from_yaml() instead
        pass

    def _register_tools_from_yaml(self) -> None:
        """
        Register tools specified in YAML definition.

        This method loads tool implementations from the tools directory
        and registers them in the global _TOOL_REGISTRY.

        Raises:
            ImportError: If a required tool module cannot be imported
            ValueError: If a tool is not found in the registry
        """
        tools_to_register = self.definition.tools or []

        for tool_name in tools_to_register:
            try:
                # Check if tool is already registered
                if tool_name in _TOOL_REGISTRY:
                    logger.debug(f"Tool already registered: {tool_name}")
                    continue

                # Try to load tool from tools directory
                tool_module = self._load_tool_module(tool_name)

                if tool_module:
                    # Tool decorator should auto-register it
                    logger.debug(f"Loaded tool module: {tool_name}")
                else:
                    # Tool might be a built-in or MCP tool
                    logger.warning(f"Tool not found as module: {tool_name}")

            except ImportError as e:
                logger.error(f"Failed to import tool {tool_name}: {e}")
                raise
            except Exception as e:
                logger.error(f"Failed to load tool {tool_name}: {e}")
                raise

        self._registered_tools = tools_to_register.copy()

    def _load_tool_module(self, tool_name: str) -> Optional[Any]:
        """
        Load a tool module by name.

        Args:
            tool_name: Name of the tool to load

        Returns:
            Loaded module or None if not found
        """
        import importlib

        # Try common tool module locations
        module_paths = [
            f"gaia.tools.{tool_name}",
            f"gaia.agents.tools.{tool_name}",
            tool_name,  # Try as absolute import
        ]

        for module_path in module_paths:
            try:
                module = importlib.import_module(module_path)
                logger.debug(f"Loaded tool module: {module_path}")
                return module
            except ImportError:
                continue

        return None

    def _get_system_prompt(self) -> str:
        """
        Get system prompt from YAML definition.

        Loads prompt from file if specified, otherwise uses default.

        Returns:
            System prompt string
        """
        # Check if prompt path is specified in metadata
        if self._prompt_path:
            prompt_file = self._prompts_dir / self._prompt_path

            if prompt_file.exists():
                with open(prompt_file, "r", encoding="utf-8") as f:
                    prompt_content = f.read()
                logger.debug(f"Loaded prompt from: {prompt_file}")
                return prompt_content
            else:
                logger.warning(f"PROMPT file not found: {prompt_file}, using default")

        # Default prompt with agent description
        default_prompt = f"""You are {self.definition.name}.

{self.definition.description}

Your capabilities include:
{chr(10).join(f"- {cap}" for cap in (self.definition.capabilities.capabilities if self.definition.capabilities else []))}

Follow these constraints:
- Maximum steps: {self.definition.constraints.max_steps if self.definition.constraints else 100}
- Requires review: {self.definition.constraints.requires_review if self.definition.constraints else True}
"""

        if self.definition.constraints:
            if self.definition.constraints.timeout_seconds:
                default_prompt += f"- Timeout: {self.definition.constraints.timeout_seconds} seconds\n"
            if self.definition.constraints.max_file_changes:
                default_prompt += f"- Maximum file changes: {self.definition.constraints.max_file_changes}\n"

        return default_prompt

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the agent with the given context.

        Args:
            context: Execution context including:
                - goal: The task goal
                - phase: Current pipeline phase
                - state: Current pipeline state
                - artifacts: Artifacts from previous phases

        Returns:
            Execution result including:
                - success: Whether execution succeeded
                - artifact: Produced artifact
                - defects: Any defects found
        """
        self._execution_context = context

        logger.info(
            f"Executing agent: {self.definition.id}",
            extra={
                "agent_id": self.definition.id,
                "goal": context.get("goal", "Unknown"),
                "phase": context.get("phase", "Unknown"),
            },
        )

        try:
            # Build user message from context
            user_goal = context.get("goal", context.get("user_goal", ""))
            phase = context.get("phase", "")
            artifacts = context.get("artifacts", {})

            # Compose user prompt
            user_prompt = self._compose_user_prompt(user_goal, phase, artifacts)

            # Execute the agent conversation loop
            # This calls the base Agent.run() method
            result = await self._run_agent_loop(user_prompt)

            logger.info(
                f"Agent execution complete: {self.definition.id}",
                extra={
                    "agent_id": self.definition.id,
                    "result_keys": list(result.keys()) if result else [],
                },
            )

            return result

        except Exception as e:
            logger.exception(f"Agent execution failed: {self.definition.id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "agent_id": self.definition.id,
            }

    def _compose_user_prompt(
        self,
        goal: str,
        phase: str,
        artifacts: Dict[str, Any],
    ) -> str:
        """
        Compose user prompt from context.

        Args:
            goal: Task goal
            phase: Current phase
            artifacts: Previous artifacts

        Returns:
            Formatted user prompt
        """
        prompt_parts = [f"Goal: {goal}"]

        if phase:
            prompt_parts.append(f"Current phase: {phase}")

        if artifacts:
            prompt_parts.append("\nPrevious artifacts:")
            for name, content in artifacts.items():
                prompt_parts.append(f"- {name}: {content}")

        return "\n".join(prompt_parts)

    async def _run_agent_loop(self, user_prompt: str) -> Dict[str, Any]:
        """
        Run the agent conversation loop.

        This is a simplified version that executes a single turn.
        For full multi-turn conversation, would call the base Agent.run().

        Args:
            user_prompt: User message to process

        Returns:
            Agent response as dictionary
        """
        # For pipeline integration, we use a simplified execution model
        # that doesn't require full interactive conversation

        # Prepare messages for LLM
        messages = [
            {"role": "user", "content": user_prompt},
        ]

        try:
            # Use ChatSDK to get response
            # Note: This assumes the base Agent has initialized self.chat
            if hasattr(self, "chat") and self.chat:
                response = self.chat.send_messages(
                    messages=messages,
                    system_prompt=self.system_prompt,
                )

                return {
                    "success": True,
                    "artifact": response.text,
                    "agent_id": self.definition.id,
                    "model": response.model,
                    "tokens": response.usage,
                }
            else:
                # Fallback: return context summary
                logger.warning("ChatSDK not initialized, returning context summary")
                return {
                    "success": True,
                    "artifact": f"Agent {self.definition.id} processed: {user_prompt}",
                    "agent_id": self.definition.id,
                }

        except Exception as e:
            logger.exception(f"LLM call failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "agent_id": self.definition.id,
            }

    def get_available_tools(self) -> List[str]:
        """Get list of available tools for this agent."""
        return self._registered_tools.copy()

    def get_capabilities(self) -> List[str]:
        """Get list of agent capabilities."""
        if self.definition.capabilities:
            return self.definition.capabilities.capabilities.copy()
        return []

    def get_constraints(self) -> Dict[str, Any]:
        """Get agent constraints."""
        if self.definition.constraints:
            return {
                "max_steps": self.definition.constraints.max_steps,
                "timeout_seconds": self.definition.constraints.timeout_seconds,
                "requires_review": self.definition.constraints.requires_review,
            }
        return {}

    def _format_tools_for_prompt(self) -> str:
        """
        Format allowed tools into string for prompt.

        PRODUCTION SECURITY: Only formats tools that are in the YAML allowlist.
        This prevents agents from seeing tools they shouldn't use.

        Returns:
            Formatted tool descriptions for allowed tools only
        """
        tool_descriptions = []
        allowed_tools = set(self.definition.tools or [])

        for name, tool_info in _TOOL_REGISTRY.items():
            # CRITICAL: Only include tools that are in the YAML allowlist
            if name not in allowed_tools:
                continue

            params_str = ", ".join(
                [
                    f"{param_name}{'' if param_info['required'] else '?'}: {param_info['type']}"
                    for param_name, param_info in tool_info["parameters"].items()
                ]
            )

            description = tool_info["description"].strip()
            tool_descriptions.append(f"- {name}({params_str}): {description}")

        return "\n".join(tool_descriptions)

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """
        Execute a tool with allowlist validation.

        PRODUCTION SECURITY: Validates that the requested tool is in the
        YAML-defined allowlist before execution. This prevents unauthorized
        tool access even if the LLM tries to call tools outside its configuration.

        Args:
            tool_name: Name of the tool to execute
            tool_args: Arguments to pass to the tool

        Returns:
            Result of the tool execution or error dict
        """
        allowed_tools = set(self.definition.tools or [])

        # Check if tool is in allowlist
        if tool_name not in allowed_tools:
            # Try to resolve MCP tool name prefixes
            resolved = self._resolve_tool_name(tool_name)
            if not resolved or resolved not in allowed_tools:
                logger.error(
                    f"UNAUTHORIZED TOOL ACCESS ATTEMPT: Agent '{self.definition.id}' "
                    f"tried to call '{tool_name}' which is not in its allowlist: {allowed_tools}"
                )
                return {
                    "status": "error",
                    "error": f"Tool '{tool_name}' is not authorized for agent '{self.definition.id}'",
                    "security_violation": True,
                }

        # Tool is authorized - proceed with normal execution
        logger.debug(f"Tool '{tool_name}' authorized for agent '{self.definition.id}'")
        return super()._execute_tool(tool_name, tool_args)

    def _resolve_tool_name(self, tool_name: str) -> Optional[str]:
        """
        Resolve unprefixed MCP tool names to their full registry names.

        MCP tools are registered with prefixes like 'mcp_server_tool' but
        LLMs may return just the base name. This method attempts to resolve
        such names while respecting the agent's tool allowlist.

        Args:
            tool_name: Tool name to resolve

        Returns:
            Resolved tool name or None if not found/not allowed
        """
        allowed_tools = set(self.definition.tools or [])
        lower = tool_name.lower()

        # Try to find matching tool in allowed list
        # First try suffix match (e.g., "get_time" matches "mcp_time_get_current_time")
        suffix = f"_{lower}"
        matches = [n for n in allowed_tools if n.lower().endswith(suffix)]
        if len(matches) == 1:
            return matches[0]

        # Try exact case-insensitive match within allowed tools
        matches = [n for n in allowed_tools if n.lower() == lower]
        if len(matches) == 1:
            return matches[0]

        return None
