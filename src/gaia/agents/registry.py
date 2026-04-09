# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent registry for discovering, loading, and creating agents."""

import dataclasses
import importlib
import importlib.util
import inspect
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from pydantic import BaseModel, field_validator

from gaia.logger import get_logger

logger = get_logger(__name__)

# KNOWN_TOOLS maps tool name -> (module_path, class_name) for lazy import
KNOWN_TOOLS: Dict[str, tuple] = {
    "rag": ("gaia.agents.chat.tools.rag_tools", "RAGToolsMixin"),
    "file_search": ("gaia.agents.tools.file_tools", "FileSearchToolsMixin"),
    "file_io": ("gaia.agents.code.tools.file_io", "FileIOToolsMixin"),
    "shell": ("gaia.agents.chat.tools.shell_tools", "ShellToolsMixin"),
    "screenshot": ("gaia.agents.tools.screenshot_tools", "ScreenshotToolsMixin"),
    "sd": ("gaia.sd.mixin", "SDToolsMixin"),
    "vlm": ("gaia.vlm.mixin", "VLMToolsMixin"),
}


class AgentManifest(BaseModel):
    """Pydantic v2 model for validating YAML agent manifests."""

    manifest_version: int = 1
    id: str
    name: str
    description: str = ""
    instructions: str = ""  # System prompt for YAML-only agents
    tools: List[str] = ["rag", "file_search"]
    mcp_servers: Dict[str, Dict[str, Any]] = {}
    models: List[str] = []  # Ordered preference list
    conversation_starters: List[str] = []

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, v):
        unknown = [t for t in v if t not in KNOWN_TOOLS]
        if unknown:
            raise ValueError(f"Unknown tools: {unknown}. Valid: {list(KNOWN_TOOLS)}")
        return v

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        if not v or not v.strip():
            raise ValueError("Agent ID cannot be empty")
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,50}[a-z0-9]$", v):
            raise ValueError(
                f"Agent ID '{v}' is invalid. "
                "Use lowercase letters, digits, and hyphens (e.g. 'my-agent')."
            )
        return v


@dataclass
class AgentRegistration:
    """Metadata and factory for a registered agent."""

    id: str
    name: str
    description: str
    source: str  # "builtin" | "custom_python" | "custom_manifest"
    conversation_starters: List[str]
    factory: Callable[..., Any]  # returns Agent instance
    agent_dir: Optional[Path]
    models: List[str]  # ordered preference list
    hidden: bool = False  # hidden agents are excluded from the UI agent selector


class AgentRegistry:
    """Central registry for discovering, loading, and creating agents.

    Call :meth:`discover` once at server startup to scan built-in agents
    and the ``~/.gaia/agents/`` directory for custom agents.
    """

    def __init__(self):
        self._agents: Dict[str, AgentRegistration] = {}
        self._lemonade_models: Optional[List[str]] = None  # cache
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> None:
        """Discover and register all agents. Call once at server startup."""
        logger.info("registry: Starting agent discovery")

        # 1. Register built-in agents
        self._register_builtin_agents()

        # 2. Scan ~/.gaia/agents/
        agents_dir = Path.home() / ".gaia" / "agents"
        if agents_dir.exists():
            subdirs = sorted(d for d in agents_dir.iterdir() if d.is_dir())
            logger.info(
                "registry: Found %d agent directories: %s",
                len(subdirs),
                [d.name for d in subdirs],
            )
            for agent_dir in subdirs:
                try:
                    self._load_from_dir(agent_dir)
                except Exception as e:
                    logger.warning(
                        "registry: Failed to load agent from %s: %s", agent_dir, e
                    )
        else:
            logger.info("registry: No custom agent directory found at %s", agents_dir)

        agent_ids = list(self._agents.keys())
        logger.info(
            "registry: Agent discovery complete. %d agents registered: %s",
            len(agent_ids),
            agent_ids,
        )

    # ------------------------------------------------------------------
    # Built-in agents
    # ------------------------------------------------------------------

    def _register_builtin_agents(self) -> None:
        """Register built-in agents (ChatAgent, BuilderAgent, etc.)."""

        # --- ChatAgent ---
        def chat_factory(**kwargs):
            from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

            valid_fields = {f.name for f in dataclasses.fields(ChatAgentConfig)}
            config = ChatAgentConfig(
                **{k: v for k, v in kwargs.items() if k in valid_fields}
            )
            return ChatAgent(config=config)

        self._register(
            AgentRegistration(
                id="chat",
                name="Chat Agent",
                description="Full-featured document Q&A assistant with RAG, file tools, and MCP support",
                source="builtin",
                conversation_starters=[
                    "What can you help me with?",
                    "Search my documents for information about...",
                    "Find files related to...",
                ],
                factory=chat_factory,
                agent_dir=None,
                models=[],
            )
        )
        logger.info("registry: Registered built-in agent: chat (ChatAgent)")

        # --- BuilderAgent ---
        try:
            from gaia.agents.builder.agent import BuilderAgent, BuilderAgentConfig

            def builder_factory(**kwargs):
                valid_fields = {f.name for f in dataclasses.fields(BuilderAgentConfig)}
                config = BuilderAgentConfig(
                    **{k: v for k, v in kwargs.items() if k in valid_fields}
                )
                return BuilderAgent(config=config)

            self._register(
                AgentRegistration(
                    id="builder",
                    name="Gaia Builder",
                    description="Create a new custom GAIA agent through conversation",
                    source="builtin",
                    conversation_starters=[
                        "Help me create a custom agent",
                        "I want to build a new agent",
                    ],
                    factory=builder_factory,
                    agent_dir=None,
                    models=[],
                    hidden=True,
                )
            )
            logger.info("registry: Registered built-in agent: builder (BuilderAgent)")
        except ImportError:
            logger.debug(
                "registry: BuilderAgent not available, skipping built-in registration"
            )

    # ------------------------------------------------------------------
    # Directory loading
    # ------------------------------------------------------------------

    def _load_from_dir(self, agent_dir: Path) -> None:
        """Load agent from a directory. Python takes precedence over YAML."""
        py_file = agent_dir / "agent.py"
        yaml_file = agent_dir / "agent.yaml"

        if py_file.exists():
            self._load_python_agent(
                agent_dir, py_file, yaml_file if yaml_file.exists() else None
            )
        elif yaml_file.exists():
            self._load_manifest_agent(agent_dir, yaml_file)
        else:
            logger.warning(
                "registry: No agent.py or agent.yaml in %s, skipping", agent_dir
            )

    # ------------------------------------------------------------------
    # Python agent loading
    # ------------------------------------------------------------------

    def _load_python_agent(
        self,
        agent_dir: Path,
        py_file: Path,
        yaml_file: Optional[Path],
    ) -> None:
        """Load a Python agent module from ``agent_dir/agent.py``."""
        logger.info("registry: Loading Python agent from %s", py_file)

        safe_dir_name = re.sub(r"[^a-zA-Z0-9_]", "_", agent_dir.name)
        spec = importlib.util.spec_from_file_location(
            f"gaia_custom_agent_{safe_dir_name}", py_file
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not create import spec for {py_file}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Find Agent subclass with required class attributes
        from gaia.agents.base.agent import Agent as BaseAgent

        agent_class = None
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseAgent)
                and obj is not BaseAgent
                and hasattr(obj, "AGENT_ID")
                and hasattr(obj, "AGENT_NAME")
            ):
                agent_class = obj
                break

        if agent_class is None:
            raise ValueError(
                f"No Agent subclass with AGENT_ID and AGENT_NAME found in {py_file}"
            )

        agent_id = agent_class.AGENT_ID
        agent_name = agent_class.AGENT_NAME
        agent_desc = getattr(agent_class, "AGENT_DESCRIPTION", "")
        starters = getattr(agent_class, "CONVERSATION_STARTERS", [])

        # Read optional companion YAML for models/mcp_servers metadata
        models: List[str] = []
        if yaml_file:
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    yaml_data = yaml.safe_load(f)
                if yaml_data:
                    models = yaml_data.get("models", [])
            except Exception as e:
                logger.warning(
                    "registry: Could not read companion YAML %s: %s", yaml_file, e
                )

        klass = agent_class

        def python_factory(klass=klass, **kwargs):
            return klass(**kwargs)

        self._register(
            AgentRegistration(
                id=agent_id,
                name=agent_name,
                description=agent_desc,
                source="custom_python",
                conversation_starters=list(starters),
                factory=python_factory,
                agent_dir=agent_dir,
                models=models,
            )
        )
        logger.info(
            "registry: Registered Python agent: %s (%s)",
            agent_id,
            agent_class.__name__,
        )

    # ------------------------------------------------------------------
    # YAML manifest agent loading
    # ------------------------------------------------------------------

    def _load_manifest_agent(self, agent_dir: Path, yaml_file: Path) -> None:
        """Load a YAML manifest agent and create a dynamic class via ``type()``."""
        logger.info("registry: Loading YAML manifest from %s", yaml_file)

        max_size = 1_048_576  # 1 MB
        if yaml_file.stat().st_size > max_size:
            raise ValueError(
                f"Agent manifest too large ({yaml_file.stat().st_size} bytes > {max_size}): {yaml_file}"
            )

        with open(yaml_file, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        manifest = AgentManifest(**raw)

        agent_class = self._create_manifest_agent_class(manifest, agent_dir)

        klass = agent_class

        def manifest_factory(klass=klass, **kwargs):
            return klass(**kwargs)  # pylint: disable=abstract-class-instantiated

        self._register(
            AgentRegistration(
                id=manifest.id,
                name=manifest.name,
                description=manifest.description,
                source="custom_manifest",
                conversation_starters=manifest.conversation_starters,
                factory=manifest_factory,
                agent_dir=agent_dir,
                models=manifest.models,
            )
        )
        logger.info(
            "registry: Registered manifest agent: %s (%s) with tools: %s",
            manifest.id,
            manifest.name,
            manifest.tools,
        )

    def _create_manifest_agent_class(self, manifest: AgentManifest, agent_dir: Path):
        """Build a dynamic Agent subclass from a YAML manifest."""
        from gaia.agents.base.agent import Agent as BaseAgent
        from gaia.mcp.mixin import MCPClientMixin

        # Build MRO: Agent + tool mixins + MCPClientMixin
        bases: list = [BaseAgent]
        for tool_name in manifest.tools:
            if tool_name not in KNOWN_TOOLS:
                continue
            module_path, class_name = KNOWN_TOOLS[tool_name]
            try:
                mod = importlib.import_module(module_path)
                mixin_class = getattr(mod, class_name)
                bases.append(mixin_class)
            except Exception as e:
                logger.warning(
                    "registry: Could not load tool mixin %s: %s", tool_name, e
                )
        bases.append(MCPClientMixin)

        # Write merged MCP config to agent_dir for this agent's use
        merged_config_path = self._write_merged_mcp_config(manifest, agent_dir)

        # Capture manifest data for closures
        instructions = manifest.instructions
        merged_config_path_str = str(merged_config_path) if merged_config_path else None
        tool_names = list(manifest.tools)

        dyn_class_name = (
            "".join(w.capitalize() for w in manifest.name.split()) + "Agent"
        )

        def _get_system_prompt(_):
            return instructions

        def _register_tools(self_inner):
            from gaia.agents.base.tools import _TOOL_REGISTRY

            _TOOL_REGISTRY.clear()
            for t_name in tool_names:
                register_method = f"register_{t_name}_tools"
                if hasattr(self_inner, register_method):
                    getattr(self_inner, register_method)()
            # Load MCP tools after Python tools so they aren't wiped by the clear above.
            # Load MCP tools last so they survive the TOOL_REGISTRY.clear() above.
            if getattr(self_inner, "_mcp_manager", None) is not None:
                self_inner.load_mcp_servers_from_config()

        def _create_console(_):
            from gaia.agents.base.console import AgentConsole

            return AgentConsole()

        # Build the class without __init__ first so agent_class is defined before
        # the __init__ closure captures it (avoids forward-reference fragility).
        agent_class = type(
            dyn_class_name,
            tuple(bases),
            {
                "_get_system_prompt": _get_system_prompt,
                "_register_tools": _register_tools,
                "_create_console": _create_console,
                "AGENT_ID": manifest.id,
                "AGENT_NAME": manifest.name,
                "AGENT_DESCRIPTION": manifest.description,
                "CONVERSATION_STARTERS": manifest.conversation_starters,
            },
        )

        # Define __init__ after agent_class exists so super(agent_class, ...) is safe.
        # Manually initialize _mcp_manager BEFORE super().__init__() because
        # Agent.__init__ calls _register_tools() which may reference
        # self._mcp_manager.  MCPClientMixin.__init__ is never called implicitly
        # here (Agent.__init__ doesn't propagate super() through the full MRO),
        # so we set up the manager directly.
        def __init__(self_inner, _cls=agent_class, **kwargs):
            if merged_config_path_str:
                try:
                    from gaia.mcp.client.config import MCPConfig
                    from gaia.mcp.client.mcp_client_manager import MCPClientManager

                    mcp_config = MCPConfig(config_file=merged_config_path_str)
                    self_inner._mcp_manager = MCPClientManager(config=mcp_config)
                    # Do NOT call load_mcp_servers_from_config here — super().__init__()
                    # triggers _register_tools() which clears _TOOL_REGISTRY and then
                    # re-loads MCP tools at the end.  Loading here would be wiped.
                except Exception as _mcp_err:
                    logger.warning(
                        "registry: MCP init failed for %s: %s", _cls.__name__, _mcp_err
                    )
                    self_inner._mcp_manager = None
            super(_cls, self_inner).__init__(**kwargs)

        agent_class.__init__ = __init__
        return agent_class

    # ------------------------------------------------------------------
    # MCP config merging
    # ------------------------------------------------------------------

    def _write_merged_mcp_config(
        self, manifest: AgentManifest, agent_dir: Path
    ) -> Optional[Path]:
        """Merge manifest mcp_servers with global config, write to agent_dir."""
        if not manifest.mcp_servers:
            return None

        try:
            from gaia.mcp.client.config import MCPConfig

            global_config = MCPConfig()
            global_servers = global_config.get_servers()
            # Manifest wins on conflicts (additive merge)
            merged = {**global_servers, **manifest.mcp_servers}
            config_path = agent_dir / "mcp_servers.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": merged}, f, indent=2)
            return config_path
        except Exception as e:
            logger.warning("registry: Could not write merged MCP config: %s", e)
            return None

    def register_from_dir(self, agent_dir: Path) -> None:
        """Load a single agent directory and register it at runtime.

        Used by BuilderAgent's ``create_agent`` tool so a newly written
        manifest is immediately available without a server restart.

        Args:
            agent_dir: Path to the agent directory (must contain agent.py or
                agent.yaml).
        """
        agent_dir = Path(agent_dir)
        try:
            self._load_from_dir(agent_dir)
            logger.info("registry: Hot-loaded agent from %s", agent_dir)
        except Exception as exc:
            logger.warning(
                "registry: Failed to hot-load agent from %s: %s", agent_dir, exc
            )
            raise

    # ------------------------------------------------------------------
    # Registration & lookup
    # ------------------------------------------------------------------

    def _register(self, registration: AgentRegistration) -> None:
        with self._lock:
            if registration.id in self._agents:
                logger.warning(
                    "registry: Agent ID '%s' already registered, overwriting",
                    registration.id,
                )
            self._agents[registration.id] = registration

    def get(self, agent_id: str) -> Optional[AgentRegistration]:
        """Return the registration for *agent_id*, or ``None``."""
        return self._agents.get(agent_id)

    def list(self) -> List[AgentRegistration]:
        """Return all registered agents."""
        return list(self._agents.values())

    def create_agent(self, agent_id: str, **kwargs) -> Any:
        """Create an agent instance by ID.

        Raises:
            ValueError: If *agent_id* is not registered.
        """
        reg = self._agents.get(agent_id)
        if reg is None:
            raise ValueError(
                f"Unknown agent ID: '{agent_id}'. "
                f"Available: {list(self._agents.keys())}"
            )
        logger.info("registry: Creating agent '%s'", agent_id)
        return reg.factory(**kwargs)

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    def resolve_model(
        self,
        agent_id: str,
        available_models: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Return first preferred model that is available, or ``None``.

        Args:
            agent_id: Registered agent identifier.
            available_models: Pre-fetched list of model IDs.  When
                ``None``, queries the Lemonade server automatically.
        """
        reg = self._agents.get(agent_id)
        if not reg or not reg.models:
            return None

        if available_models is None:
            available_models = self._get_available_models()

        for model in reg.models:
            if model in available_models:
                logger.info(
                    "registry: Agent %s: preferred model %s available",
                    agent_id,
                    model,
                )
                return model
            logger.info(
                "registry: Agent %s: preferred model %s not available, trying next",
                agent_id,
                model,
            )

        logger.warning(
            "registry: Agent %s: no preferred models available, using server default",
            agent_id,
        )
        return None

    def _get_available_models(self) -> List[str]:
        """Query Lemonade server for available models (cached)."""
        if self._lemonade_models is not None:
            return self._lemonade_models

        try:
            import requests

            base_url = os.getenv("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")
            resp = requests.get(f"{base_url}/models", timeout=2)
            if resp.ok:
                data = resp.json()
                self._lemonade_models = [m["id"] for m in data.get("data", [])]
                return self._lemonade_models
        except Exception:
            pass

        # Do NOT cache on failure — allow retrying when Lemonade comes online.
        return []
