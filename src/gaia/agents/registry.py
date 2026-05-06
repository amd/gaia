# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent registry for discovering, loading, and creating agents."""

import dataclasses
import hashlib
import importlib
import importlib.util
import inspect
import os
import platform
import re
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import yaml

from gaia.connectors.providers.base import ConnectorRequirement
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


# Reserved agent IDs that custom agents (under ~/.gaia/agents/) must not
# claim. Loaded lazily by ``_RESERVED_BUILTIN_IDS`` so the list stays in sync
# with what ``_register_builtin_agents`` actually registers.
_RESERVED_BUILTIN_IDS: frozenset[str] = frozenset(
    {"chat", "builder", "gaia-lite", "email", "connectors-demo"}
)


def _wrap_factory_with_namespaced_id(
    factory: Callable[..., Any], namespaced_id: str
) -> Callable[..., Any]:
    """
    Wrap a registration factory so the resulting Agent instance carries its
    namespaced ID for ``Agent.process_query`` to read at runtime.

    The base ``Agent.process_query`` reads ``_gaia_namespaced_agent_id`` (and
    falls back to ``AGENT_ID``) when wrapping the call in the agent context
    contextvar. Setting this attribute on the instance is what lets a
    custom-installed agent get its proper ``custom:<sha256>:<id>`` namespace
    instead of the bare ``AGENT_ID``.
    """

    def _factory(**kwargs):
        instance = factory(**kwargs)
        # Attribute access — use setattr because subclasses may override
        # __setattr__ to validate fields. We set on the instance, not the
        # class, so two different registrations of the same class don't
        # collide.
        try:
            instance._gaia_namespaced_agent_id = namespaced_id
        except (AttributeError, TypeError):
            # If the agent uses __slots__ without an entry for this field,
            # we still proceed — process_query will fall back to AGENT_ID.
            pass
        return instance

    return _factory


def _compute_custom_origin_hash(py_file: Path) -> str:
    """
    Compute the custom-agent origin hash used in ``namespaced_agent_id``.

    Hashes the raw bytes of ``agent.py``. A different file (different code)
    therefore produces a different namespaced id, so a custom agent that
    later changes its scope claims will get a fresh grant-ledger key — the
    user re-grants explicitly rather than inheriting the prior grant.
    """
    return hashlib.sha256(py_file.read_bytes()).hexdigest()[:16]


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
    # Minimum free system memory (GB) recommended before loading this agent's
    # preferred model. `None` = no requirement declared. The UI shows a warning
    # in Settings when memory_available_gb < min_memory_gb so the user isn't
    # surprised by a load failure or heavy swapping mid-session.
    min_memory_gb: Optional[float] = None
    # T-X2 (issue #915):
    # ``required_connections`` is the agent class's ``REQUIRED_CONNECTORS``
    # ClassVar surfaced into the registry so the AgentUI consent dialog and
    # the CLI ``gaia connectors grants`` command can render the prompt
    # without re-importing the agent module.
    required_connections: List[ConnectorRequirement] = field(default_factory=list)
    # T-X2 (issue #915, plan amendment A9):
    # ``namespaced_agent_id`` is the grant-ledger key for this agent. Built-in
    # agents use ``builtin:<id>``; custom agents under ``~/.gaia/agents/``
    # use ``custom:<sha256-of-agent.py>:<id>``. This namespacing prevents a
    # malicious custom agent from claiming a built-in's AGENT_ID to inherit
    # a previously-granted scope. Always non-empty.
    namespaced_agent_id: str = ""


class AgentRegistry:
    """Central registry for discovering, loading, and creating agents.

    Call :meth:`discover` once at server startup to scan built-in agents
    and the ``~/.gaia/agents/`` directory for custom agents.
    """

    # Legacy agent IDs that were renamed. Existing UI sessions store the old
    # ID in ``sessions.agent_type`` in the ChatDatabase; silently resolving
    # the alias keeps those sessions working without a DB migration. All
    # lookups (``get``, ``create_agent``, ``resolve_model``) honour the map.
    _LEGACY_ID_ALIASES: Dict[str, str] = {
        "chat-lite": "gaia-lite",
    }

    def __init__(self):
        self._agents: Dict[str, AgentRegistration] = {}
        self._lemonade_models: Optional[List[str]] = None  # cache
        self._lemonade_models_last_fail: Optional[float] = None  # monotonic timestamp
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Legacy ID resolution
    # ------------------------------------------------------------------

    def canonical_id(self, agent_id: str) -> str:
        """Return the current canonical ID for *agent_id*, resolving aliases.

        Returns the input unchanged when no alias exists so callers can use
        the result as a stable cache key — two requests for ``chat-lite`` and
        ``gaia-lite`` both produce ``gaia-lite``, so the per-session agent
        cache doesn't thrash when a client mixes the old and new names.
        """
        if agent_id in self._agents:
            return agent_id
        return self._LEGACY_ID_ALIASES.get(agent_id, agent_id)

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
                factory=_wrap_factory_with_namespaced_id(chat_factory, "builtin:chat"),
                agent_dir=None,
                models=[],
                required_connections=[],
                namespaced_agent_id="builtin:chat",
            )
        )
        logger.info("registry: Registered built-in agent: chat (ChatAgent)")

        # --- Gaia Lite (smaller ~4B model, Mac/low-memory friendly) ---
        # Reuses the full ChatAgent feature set but presets model_id to a
        # compact ~4B checkpoint so it runs on hardware that can't host the
        # 35B default. It's an agent (tools + RAG + MCP), not a bare chat
        # bot — the "Lite" refers only to the model size. The preset is
        # applied via setdefault so callers can still override (e.g. for
        # tests or explicit user selection).
        #
        # Preferred-model list is platform-conditional:
        #
        # macOS primary is **Qwen3.5-4B-GGUF** (2.91 GB, "tool-calling"
        # label in the Lemonade catalog). Empirically Gemma-3-4b-it-GGUF
        # emits tool calls as Gemini-style ```tool_code``` text blocks
        # rather than OpenAI-compatible function calls — the GAIA agent
        # runtime never executes them, so the model can't actually use
        # RAG, file search, or any other tool. The "personality" eval
        # category dropped to 1/3 PASS with Gemma 3 4B, with both
        # failures rooted in this format mismatch (see the eval run
        # at eval/results/eval-20260426-061705).
        # Qwen 3.5 is explicitly trained for the OpenAI tool-call format.
        #
        # Linux/Windows still leads with Gemma-4-E4B-it-GGUF — those
        # platforms use a different llama.cpp bundle that ships the
        # gemma4 architecture handler (added in llama.cpp b8637 /
        # ggml-org/llama.cpp#21309), and Gemma 4 also carries the
        # "tool-calling" label. macOS Lemonade v10.2.0 ships an older
        # bundle (b8460) that can't load Gemma 4, and Lemonade actively
        # reverts local binary swaps on restart — tracked upstream at
        # lemonade-sdk/lemonade#1741.
        #
        # Each list keeps the alternative platform's primary as a
        # fallback so ``resolve_model`` can fall through if the primary
        # is unavailable on a given install.
        #
        # Single source of truth — the factory's setdefault below MUST
        # agree with this list's first entry, or the runtime preset and
        # the UI's advertised primary will diverge.
        if platform.system() == "Darwin":
            _GAIA_LITE_MODELS = ["Qwen3.5-4B-GGUF", "Gemma-4-E4B-it-GGUF"]
        else:
            _GAIA_LITE_MODELS = ["Gemma-4-E4B-it-GGUF", "Qwen3.5-4B-GGUF"]

        # Memory floor for the gaia-lite agent. Gemma 4 E4B / Qwen3.5-4B Q4_K_M
        # weights are ~2.5–2.7 GB on disk; add KV-cache for a 32K context window
        # plus runtime overhead → 5 GB is the comfortable load floor. Below
        # this the UI surfaces a Memory Warning so the user isn't surprised
        # by a load failure or heavy swapping mid-session. Update the constant
        # (not just the registration) if the preferred model list grows a
        # bigger checkpoint, so the rationale stays adjacent to the number.
        _GAIA_LITE_MIN_MEMORY_GB = 5.0

        def gaia_lite_factory(**kwargs):
            from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

            valid_fields = {f.name for f in dataclasses.fields(ChatAgentConfig)}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            # setdefault pulls from the registration's preferred-models list
            # so the runtime preset stays in lockstep with the UI's advertised
            # primary — change the list, the factory follows automatically.
            filtered.setdefault("model_id", _GAIA_LITE_MODELS[0])
            config = ChatAgentConfig(**filtered)
            return ChatAgent(config=config)

        self._register(
            AgentRegistration(
                id="gaia-lite",
                name="Gaia Lite",
                description=(
                    "Lightweight GAIA agent — same features as the default Chat "
                    "Agent (RAG, file tools, MCP) but runs on a ~4B model, "
                    "suitable for Mac and other hardware that cannot host the "
                    "35B default."
                ),
                source="builtin",
                conversation_starters=[
                    "What can you help me with?",
                    "Summarize this document",
                    "Search my files for...",
                ],
                factory=_wrap_factory_with_namespaced_id(
                    gaia_lite_factory, "builtin:gaia-lite"
                ),
                agent_dir=None,
                models=_GAIA_LITE_MODELS,
                min_memory_gb=_GAIA_LITE_MIN_MEMORY_GB,
                required_connections=[],
                namespaced_agent_id="builtin:gaia-lite",
            )
        )
        logger.info(
            "registry: Registered built-in agent: gaia-lite (ChatAgent, primary %s)",
            _GAIA_LITE_MODELS[0],
        )

        # --- ConnectorsDemoAgent ---
        # Demo agent that uses Google + GitHub connectors end-to-end so
        # the per-agent grant flow has a real consumer to validate it.
        # Visible in the AgentUI dropdown — users can select it to test
        # their connector setup.
        try:
            from gaia.agents.connectors_demo.agent import (
                ConnectorsDemoAgent,
                ConnectorsDemoAgentConfig,
            )

            def connectors_demo_factory(**kwargs):
                valid_fields = {
                    f.name for f in dataclasses.fields(ConnectorsDemoAgentConfig)
                }
                config = ConnectorsDemoAgentConfig(
                    **{k: v for k, v in kwargs.items() if k in valid_fields}
                )
                return ConnectorsDemoAgent(config=config)

            self._register(
                AgentRegistration(
                    id="connectors-demo",
                    name="Connectors Demo",
                    description=(
                        "Demonstrates the connectors framework — pulls real "
                        "data from your connected Google account and GitHub PAT."
                    ),
                    source="builtin",
                    conversation_starters=[
                        "What's in my inbox?",
                        "What's on my calendar today?",
                        "List my recent Drive files",
                        "List my GitHub repositories",
                    ],
                    factory=_wrap_factory_with_namespaced_id(
                        connectors_demo_factory, "builtin:connectors-demo"
                    ),
                    agent_dir=None,
                    models=[],
                    # #962 fix — pre-existing bug: this previously listed
                    # bare provider strings (``["google", "mcp-github"]``)
                    # but ``AgentRegistration.required_connections`` is
                    # typed as ``List[ConnectorRequirement]`` and the UI
                    # router calls ``.provider``/``.scopes``/``.reason``
                    # on the items. Bare strings silently broke
                    # ``_reg_to_info`` in agents.py. Convert to the
                    # canonical objects so the registry stays consistent.
                    required_connections=list(ConnectorsDemoAgent.REQUIRED_CONNECTORS),
                    namespaced_agent_id="builtin:connectors-demo",
                )
            )
            logger.info(
                "registry: Registered built-in agent: connectors-demo "
                "(ConnectorsDemoAgent)"
            )
        except ImportError as e:
            logger.debug("registry: ConnectorsDemoAgent not available, skipping: %s", e)

        # --- EmailTriageAgent (#962) ---
        # First concrete email provider for the Email Triage Agent
        # parent issue (#645). Reads/organizes/replies through Gmail
        # via the connectors framework; processes all email content
        # locally on Lemonade.
        try:
            from gaia.agents.email.agent import EmailTriageAgent
            from gaia.agents.email.config import EmailAgentConfig

            def email_factory(**kwargs):
                valid_fields = {f.name for f in dataclasses.fields(EmailAgentConfig)}
                config = EmailAgentConfig(
                    **{k: v for k, v in kwargs.items() if k in valid_fields}
                )
                return EmailTriageAgent(config=config)

            self._register(
                AgentRegistration(
                    id="email",
                    name=EmailTriageAgent.AGENT_NAME,
                    description=EmailTriageAgent.AGENT_DESCRIPTION,
                    source="builtin",
                    conversation_starters=list(EmailTriageAgent.CONVERSATION_STARTERS),
                    factory=_wrap_factory_with_namespaced_id(
                        email_factory, "builtin:email"
                    ),
                    agent_dir=None,
                    models=[],
                    required_connections=list(EmailTriageAgent.REQUIRED_CONNECTORS),
                    namespaced_agent_id="builtin:email",
                )
            )
            logger.info("registry: Registered built-in agent: email (EmailTriageAgent)")
        except ImportError as e:
            logger.debug("registry: EmailTriageAgent not available, skipping: %s", e)

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
                    factory=_wrap_factory_with_namespaced_id(
                        builder_factory, "builtin:builder"
                    ),
                    agent_dir=None,
                    models=[],
                    hidden=True,
                    required_connections=[],
                    namespaced_agent_id="builtin:builder",
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

        # T-X2 (issue #915, plan amendment A9): block custom agents from
        # claiming a built-in's reserved AGENT_ID. Without this, a custom
        # agent with `AGENT_ID = "chat"` could inherit a grant the user
        # previously gave to the built-in chat agent.
        if agent_id in _RESERVED_BUILTIN_IDS:
            raise ValueError(
                f"AGENT_ID {agent_id!r} is reserved for the built-in agent. "
                f"Choose a different id in {py_file}."
            )

        # T-X2: collect declarative scope claims and namespaced grant key.
        required_connections = list(
            getattr(agent_class, "REQUIRED_CONNECTORS", []) or []
        )
        origin_hash = _compute_custom_origin_hash(py_file)
        namespaced_id = f"custom:{origin_hash}:{agent_id}"

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
                            preview = bad[:5]
                            suffix = (
                                f" (and {len(bad) - 5} more)" if len(bad) > 5 else ""
                            )
                            logger.warning(
                                "registry: companion YAML %s: 'models' contains "
                                "%d non-string entries — ignoring (sample: %r%s)",
                                yaml_file,
                                len(bad),
                                preview,
                                suffix,
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
                factory=_wrap_factory_with_namespaced_id(python_factory, namespaced_id),
                agent_dir=agent_dir,
                models=models,
                required_connections=required_connections,
                namespaced_agent_id=namespaced_id,
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
        """Return the registration for *agent_id*, or ``None``.

        Legacy aliases (e.g. ``chat-lite`` → ``gaia-lite``) are resolved
        transparently so existing persisted sessions keep working after a
        rename.
        """
        return self._agents.get(self.canonical_id(agent_id))

    def list(self) -> List[AgentRegistration]:
        """Return all registered agents."""
        return list(self._agents.values())

    def create_agent(self, agent_id: str, **kwargs) -> Any:
        """Create an agent instance by ID.

        Raises:
            ValueError: If *agent_id* is not registered.
        """
        # Route through get() so legacy aliases (e.g. chat-lite → gaia-lite)
        # resolve consistently with lookups.
        reg = self.get(agent_id)
        if reg is None:
            raise ValueError(
                f"Unknown agent ID: '{agent_id}'. "
                f"Available: {list(self._agents.keys())}"
            )
        logger.info(
            "registry: Creating agent '%s' (resolved id='%s')", agent_id, reg.id
        )
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
            agent_id: Registered agent identifier. Legacy aliases (see
                :attr:`_LEGACY_ID_ALIASES`) are resolved transparently so
                stored session IDs that pre-date a rename still pick up
                the canonical agent's preferred model list.
            available_models: Pre-fetched list of model IDs.  When
                ``None``, queries the Lemonade server automatically.
        """
        # Use get() not _agents.get() so alias → canonical mapping applies.
        # Otherwise a session stored with agent_type="chat-lite" would fall
        # through to the default 35B model instead of the 4B preset, silently
        # regressing the whole reason gaia-lite exists.
        reg = self.get(agent_id)
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
