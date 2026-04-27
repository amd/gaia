# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent registry for discovering, loading, and creating agents."""

import dataclasses
import importlib
import importlib.util
import inspect
import os
import re
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import yaml

from gaia.logger import get_logger

logger = get_logger(__name__)

# KNOWN_TOOLS maps tool name -> (module_path, class_name) for lazy import.
# Consumed by BuilderAgent's template (src/gaia/agents/builder/template.py) to
# scaffold tool-mixin imports and base classes when generating agent.py files.
KNOWN_TOOLS: Dict[str, tuple] = {
    "rag": ("gaia.agents.chat.tools.rag_tools", "RAGToolsMixin"),
    "code_index": ("gaia.agents.code_index.tools.mixin", "CodeIndexToolsMixin"),
    "file_search": ("gaia.agents.tools.file_tools", "FileSearchToolsMixin"),
    "file_io": ("gaia.agents.code.tools.file_io", "FileIOToolsMixin"),
    "shell": ("gaia.agents.chat.tools.shell_tools", "ShellToolsMixin"),
    "screenshot": ("gaia.agents.tools.screenshot_tools", "ScreenshotToolsMixin"),
    "sd": ("gaia.sd.mixin", "SDToolsMixin"),
    "vlm": ("gaia.vlm.mixin", "VLMToolsMixin"),
}

# Manifest-fingerprint keys used to detect a legacy YAML manifest masquerading
# as a companion sidecar.  The companion sidecar may carry only `models:`.
_MANIFEST_FINGERPRINT_KEYS = frozenset(
    {"manifest_version", "tools", "instructions", "mcp_servers", "id"}
)


@dataclass
class AgentRegistration:
    """Metadata and factory for a registered agent."""

    id: str
    name: str
    description: str
    source: Literal["builtin", "custom_python"]
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
        self._lemonade_models_last_fail: Optional[float] = None  # monotonic timestamp
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
        """Load agent from a directory. Only ``agent.py`` is supported.

        A directory containing only ``agent.yaml`` (no ``agent.py``) is the
        legacy YAML-manifest format, removed in v0.17.5.  Such directories
        emit a ``DeprecationWarning`` and are skipped.
        """
        py_file = agent_dir / "agent.py"
        yaml_file = agent_dir / "agent.yaml"

        if py_file.exists():
            self._load_python_agent(
                agent_dir, py_file, yaml_file if yaml_file.exists() else None
            )
            return

        if yaml_file.exists():
            warnings.warn(
                f"YAML manifest agents are no longer supported. "
                f"Convert {agent_dir}/agent.yaml to agent.py "
                f"(see https://amd-gaia.ai/guides/custom-agent). Skipping.",
                DeprecationWarning,
                stacklevel=2,
            )
            logger.warning(
                "registry: skipping YAML-only agent at %s (deprecated)", agent_dir
            )
            return

        logger.warning("registry: No agent.py in %s, skipping", agent_dir)

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

        # Read optional companion YAML for `models:` metadata.  Anything outside
        # `models:` is a manifest leftover and should be migrated into agent.py.
        models: List[str] = []
        if yaml_file:
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    yaml_data = yaml.safe_load(f)
                if isinstance(yaml_data, dict):
                    leftover = _MANIFEST_FINGERPRINT_KEYS & yaml_data.keys()
                    if leftover:
                        warnings.warn(
                            f"{yaml_file}: manifest-style keys "
                            f"{sorted(leftover)} are ignored; only `models:` is "
                            "read from the companion YAML. Move these into "
                            "agent.py "
                            "(see https://amd-gaia.ai/guides/custom-agent).",
                            DeprecationWarning,
                            stacklevel=2,
                        )
                    raw_models = yaml_data.get("models")
                    if isinstance(raw_models, list):
                        bad = [m for m in raw_models if not isinstance(m, str)]
                        if bad:
                            logger.warning(
                                "registry: companion YAML %s: 'models' contains "
                                "non-string entries %r — ignoring those",
                                yaml_file,
                                bad,
                            )
                        models = [m for m in raw_models if isinstance(m, str)]
                    elif raw_models is not None:
                        logger.warning(
                            "registry: companion YAML %s: 'models' must be a "
                            "list of strings, got %s — ignoring",
                            yaml_file,
                            type(raw_models).__name__,
                        )
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
    # Runtime registration helper
    # ------------------------------------------------------------------

    def register_from_dir(self, agent_dir: Path) -> None:
        """Load a single agent directory and register it at runtime.

        Used by BuilderAgent's ``create_agent`` tool so a newly written
        ``agent.py`` is immediately available without a server restart.

        Args:
            agent_dir: Path to the agent directory (must contain ``agent.py``).
                Must be located under ``~/.gaia/agents/`` to prevent loading
                code from arbitrary filesystem locations.
        """
        agent_dir = Path(agent_dir).resolve()
        agents_root = (Path.home() / ".gaia" / "agents").resolve()
        try:
            agent_dir.relative_to(agents_root)
        except ValueError:
            raise ValueError(
                f"register_from_dir: agent_dir '{agent_dir}' is outside the "
                f"allowed agents root '{agents_root}'"
            )
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

    _LEMONADE_RETRY_INTERVAL = 10.0  # seconds between retries when offline

    def _get_available_models(self) -> List[str]:
        """Query Lemonade server for available models (cached on success).

        Retries are rate-limited to every 10 seconds so that an offline
        Lemonade server does not block each chat request for 2 s.
        """
        if self._lemonade_models is not None:
            return self._lemonade_models

        if (
            self._lemonade_models_last_fail is not None
            and time.monotonic() - self._lemonade_models_last_fail
            < self._LEMONADE_RETRY_INTERVAL
        ):
            return []

        try:
            import requests

            base_url = os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")
            resp = requests.get(f"{base_url}/models", timeout=2)
            if resp.ok:
                data = resp.json()
                self._lemonade_models = [m["id"] for m in data.get("data", [])]
                return self._lemonade_models
        except Exception:
            pass

        # Record failure timestamp; do NOT cache models so we retry after the interval.
        self._lemonade_models_last_fail = time.monotonic()
        return []
