# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent registry for discovering, loading, and creating agents."""

import dataclasses
import hashlib
import importlib
import importlib.metadata
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

AGENT_ENTRY_POINT_GROUP = "gaia.agents"

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
    "filesystem": ("gaia.agents.tools.filesystem_tools", "FileSystemToolsMixin"),
    "scratchpad": ("gaia.agents.tools.scratchpad_tools", "ScratchpadToolsMixin"),
    "browser": ("gaia.agents.tools.browser_tools", "BrowserToolsMixin"),
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
    {
        "chat",
        "doc",
        "file",
        "data",
        "web",
        "chat-lite",
        "doc-lite",
        "file-lite",
        "data-lite",
        "web-lite",
        "gaia-lite",
        "builder",
        "email",
        "connectors-demo",
    }
)


# Session-level kwargs that constrain the agent's effective sandbox. If
# python_factory drops one of these for a class that doesn't declare it, the
# session-intended constraint silently relaxes to the agent's default — log
# at WARNING so the author can see what to declare. Other dropped kwargs stay
# at debug level (mostly noise).
_SECURITY_RELEVANT_KWARGS: frozenset[str] = frozenset({"allowed_paths"})


def _accepted_init_params(klass: type) -> Optional[set[str]]:
    """Return the union of keyword-passable __init__ parameters across
    klass's MRO. Returns None if every inspected level along the chain
    accepts ``**kwargs`` (callers should then forward all kwargs as-is).

    Used by ``python_factory`` to filter session-level kwargs (injected by
    the UI host, see ``_session_agent_kwargs`` in ``gaia.ui._chat_helpers``)
    against what the user-supplied agent class can actually accept.

    NOTE: ``python_factory`` uses ``__init__`` introspection because
    user-supplied agents have no config dataclass; ``chat_factory`` uses
    ``dataclasses.fields(ChatAgentConfig)`` because that IS the contract for
    built-ins. Two different primitives, same goal — drop kwargs the target
    won't accept by keyword.

    Edge cases handled:
    - ``POSITIONAL_ONLY`` (PEP 570) and ``VAR_POSITIONAL`` (``*args``) are
      excluded; they can't be passed by keyword.
    - C-extension ``__init__`` raises on ``inspect.signature``: be permissive
      and return ``None`` (don't claim to know what's accepted).
    - Class whose entire MRO inherits ``object.__init__``: return ``set()`` so
      the caller filters everything out (``object.__init__`` rejects all kwargs).
    """
    accepted: set[str] = set()
    inspected_levels = 0
    all_inspected_levels_have_var_keyword = True

    for cls in klass.__mro__:
        if cls is object:
            break
        init = cls.__dict__.get("__init__")
        if init is None:
            continue
        try:
            sig = inspect.signature(init)
        except (ValueError, TypeError):
            return None
        inspected_levels += 1
        level_has_var_keyword = False
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            if param.kind is inspect.Parameter.VAR_KEYWORD:
                level_has_var_keyword = True
            elif param.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                accepted.add(name)
        if not level_has_var_keyword:
            all_inspected_levels_have_var_keyword = False

    if inspected_levels == 0:
        return set()
    return None if all_inspected_levels_have_var_keyword else accepted


def _wrap_factory_with_namespaced_id(
    factory: Callable[..., Any], namespaced_id: str
) -> Callable[..., Any]:
    """
    Wrap a registration factory so the resulting Agent instance carries its
    namespaced ID.

    Two things have to happen for the per-agent connectors activation filter
    (#1005) to work correctly:

    1. The agent class must see the namespaced id BEFORE its ``__init__``
       calls ``_register_tools`` — that's where MCP tools get registered and
       where ``_active_mcp_servers`` reads the id to decide which servers'
       tools to surface. We pass it as a ``namespaced_agent_id`` kwarg so
       config classes that declare the field (e.g. ``ChatAgentConfig``) can
       stamp ``self._gaia_namespaced_agent_id`` at the top of ``__init__``.
       Factories that filter kwargs by their config fields will pick this up
       automatically; factories whose config does NOT declare the field
       drop it harmlessly.
    2. The instance attribute is also stamped after the factory returns as
       belt-and-braces — covers agents whose config doesn't (yet) declare
       the field, and ensures ``Agent.process_query`` sees the id at
       runtime even if step 1 didn't apply.
    """

    def _factory(**kwargs):
        # Inject for kwarg-aware factories (step 1).
        kwargs.setdefault("namespaced_agent_id", namespaced_id)
        instance = factory(**kwargs)
        # Belt-and-braces post-init stamp (step 2). Use setattr so subclasses
        # with custom ``__setattr__`` validation see a well-formed write,
        # and tolerate __slots__-defined agents that can't accept the
        # attribute (process_query will fall back to AGENT_ID).
        try:
            instance._gaia_namespaced_agent_id = namespaced_id
        except (AttributeError, TypeError):
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
class DeviceConfig:
    """A verified (device, model, recipe, backend) configuration for an agent.

    Each agent declares which device targets it supports.  The Agent UI
    renders a device dropdown filtered by detected hardware; the CLI
    exposes ``--device {cpu,gpu,npu}``.

    Attributes:
        device: Target device — ``"cpu"``, ``"gpu"``, or ``"npu"``.
        model: Lemonade model ID for this device (e.g. ``"Gemma-4-E4B-it-GGUF"``
            for llamacpp, ``"gemma-4-E4B-it"`` for FLM).
        recipe: Lemonade recipe name (``"llamacpp"`` or ``"flm"``).
        backend: Lemonade backend spec (``"llamacpp:vulkan"``, ``"llamacpp:cpu"``,
            ``"flm:npu"``).
        verified: Whether this combination has been tested end-to-end via
            agent eval.  Unverified configs show a warning badge in the UI.
        ctx_size: Default context window size for this configuration.
    """

    device: Literal["cpu", "gpu", "npu"]
    model: str
    recipe: str
    backend: str
    verified: bool = False
    ctx_size: int = 32768


# Default device configurations for built-in agents using Gemma 4 E4B.
# GPU is the default device — most broadly available on AMD hardware.
DEFAULT_DEVICE_CONFIGS: List[DeviceConfig] = [
    DeviceConfig(
        device="gpu",
        model="Gemma-4-E4B-it-GGUF",
        recipe="llamacpp",
        backend="llamacpp:vulkan",
        verified=True,
        ctx_size=32768,
    ),
    DeviceConfig(
        device="cpu",
        model="Gemma-4-E4B-it-GGUF",
        recipe="llamacpp",
        backend="llamacpp:cpu",
        verified=False,
        ctx_size=32768,
    ),
    DeviceConfig(
        device="npu",
        model="gemma4-it-e2b-FLM",
        recipe="flm",
        backend="flm:npu",
        verified=True,
        ctx_size=4096,
    ),
]


@dataclass
class AgentRegistration:
    """Metadata and factory for a registered agent."""

    id: str
    name: str
    description: str
    source: Literal["builtin", "custom_python", "native", "installed"]
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
    # use ``custom:<sha256-of-agent.py>:<id>``; installed wheel agents use
    # ``installed:<id>``. This namespacing prevents a malicious custom or
    # installed agent from claiming a built-in's AGENT_ID to inherit a
    # previously-granted scope. Always non-empty.
    namespaced_agent_id: str = ""
    # Agent Hub metadata — used by the Agent UI to render rich discovery cards.
    # Hardcoded for builtins (lazy-import factories must not instantiate agents);
    # custom agents declare via class attributes (AGENT_CATEGORY, etc.).
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    icon: str = ""  # lucide icon name (e.g. "message-circle", "zap")
    tools_count: int = 0
    language: str = "python"  # "python" | "cpp"
    # Multi-device support (issue #1220): declared (device, model, recipe,
    # backend) tuples.  GPU is the default.  The Agent UI renders a device
    # dropdown; the CLI exposes ``--device``.  Built-in agents inherit
    # ``DEFAULT_DEVICE_CONFIGS`` automatically; custom agents start empty
    # (GPU-only via the existing ``models`` field).
    device_configs: List[DeviceConfig] = field(
        default_factory=lambda: [
            dataclasses.replace(dc) for dc in DEFAULT_DEVICE_CONFIGS
        ]
    )


class AgentRegistry:
    """Central registry for discovering, loading, and creating agents.

    Call :meth:`discover` once at server startup to scan built-in agents
    and the ``~/.gaia/agents/`` directory for custom agents.
    """

    # Legacy agent IDs that were renamed. Existing UI sessions store the old
    # ID in ``sessions.agent_type`` in the ChatDatabase; silently resolving
    # the alias keeps those sessions working without a DB migration. All
    # lookups (``get``, ``create_agent``, ``resolve_model``) honour the map.
    _LEGACY_ID_ALIASES: Dict[str, str] = {}

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

        # 3. Discover installed Python agents exposed by standalone wheels
        self._discover_entry_point_agents()

        # 4. Discover native (C++/binary) agents from agent-manifest.json
        self._discover_native_agents()

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

        # --- Chat Agent (conversation-only, lean prompt) ---
        def chat_factory(**kwargs):
            from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

            valid_fields = {f.name for f in dataclasses.fields(ChatAgentConfig)}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            filtered.setdefault("prompt_profile", "chat")
            config = ChatAgentConfig(**filtered)
            return ChatAgent(config=config)

        self._register(
            AgentRegistration(
                id="chat",
                name="Chat",
                description="General conversation — fast, personality-first, no document tools",
                source="builtin",
                conversation_starters=[
                    "What can you help me with?",
                    "Tell me about yourself",
                    "What's new today?",
                ],
                factory=_wrap_factory_with_namespaced_id(chat_factory, "builtin:chat"),
                agent_dir=None,
                models=[],
                required_connections=[],
                namespaced_agent_id="builtin:chat",
                category="conversation",
                tags=["chat", "general", "personality"],
                icon="message-circle",
                tools_count=0,
            )
        )
        logger.info(
            "registry: Registered built-in agent: chat (ChatAgent, profile=chat)"
        )

        # --- Doc Agent (document Q&A with RAG) ---
        def doc_factory(**kwargs):
            from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

            valid_fields = {f.name for f in dataclasses.fields(ChatAgentConfig)}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            filtered.setdefault("prompt_profile", "doc")
            config = ChatAgentConfig(**filtered)
            return ChatAgent(config=config)

        self._register(
            AgentRegistration(
                id="doc",
                name="Doc Agent",
                description="Document Q&A with RAG — ask questions about PDFs, reports, and manuals",
                source="builtin",
                conversation_starters=[
                    "Search my documents for...",
                    "Summarize this document",
                    "What does the report say about...",
                ],
                factory=_wrap_factory_with_namespaced_id(doc_factory, "builtin:doc"),
                agent_dir=None,
                models=[],
                required_connections=[],
                namespaced_agent_id="builtin:doc",
                category="documents",
                tags=["rag", "files", "search", "mcp"],
                icon="file-text",
                tools_count=15,
            )
        )
        logger.info("registry: Registered built-in agent: doc (ChatAgent, profile=doc)")

        # --- File Agent (file system operations) ---
        def file_factory(**kwargs):
            from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

            valid_fields = {f.name for f in dataclasses.fields(ChatAgentConfig)}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            filtered.setdefault("prompt_profile", "file")
            filtered.setdefault("enable_filesystem", True)
            config = ChatAgentConfig(**filtered)
            return ChatAgent(config=config)

        self._register(
            AgentRegistration(
                id="file",
                name="File Agent",
                description="File system navigation, search, and analysis",
                source="builtin",
                conversation_starters=[
                    "Find files related to...",
                    "What's in my Documents folder?",
                    "Show me the project structure",
                ],
                factory=_wrap_factory_with_namespaced_id(file_factory, "builtin:file"),
                agent_dir=None,
                models=[],
                required_connections=[],
                namespaced_agent_id="builtin:file",
                category="productivity",
                tags=["files", "search", "filesystem", "shell"],
                icon="folder-search",
                tools_count=10,
            )
        )
        logger.info(
            "registry: Registered built-in agent: file (ChatAgent, profile=file)"
        )

        # --- Data Agent (data analysis with scratchpad) ---
        def data_factory(**kwargs):
            from gaia.agents.analyst.agent import AnalystAgent, AnalystAgentConfig

            valid_fields = {f.name for f in dataclasses.fields(AnalystAgentConfig)}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            config = AnalystAgentConfig(**filtered)
            return AnalystAgent(config=config)

        self._register(
            AgentRegistration(
                id="data",
                name="Analyst Agent",
                description="Data analysis — CSV, Excel, structured queries and tables",
                source="builtin",
                conversation_starters=[
                    "Analyze my spending data",
                    "What are the trends in this CSV?",
                    "Who is the top performer?",
                ],
                factory=_wrap_factory_with_namespaced_id(data_factory, "builtin:data"),
                agent_dir=None,
                models=[],
                required_connections=[],
                namespaced_agent_id="builtin:data",
                category="productivity",
                tags=["data", "csv", "excel", "analysis"],
                icon="table",
                tools_count=10,
            )
        )
        logger.info("registry: Registered built-in agent: data (AnalystAgent)")

        # --- Web Agent (web research) ---
        def web_factory(**kwargs):
            from gaia.agents.browser.agent import BrowserAgent, BrowserAgentConfig

            valid_fields = {f.name for f in dataclasses.fields(BrowserAgentConfig)}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            config = BrowserAgentConfig(**filtered)
            return BrowserAgent(config=config)

        self._register(
            AgentRegistration(
                id="web",
                name="Browser Agent",
                description="Web research — search, fetch pages, and download files",
                source="builtin",
                conversation_starters=[
                    "Search the web for...",
                    "What's the latest on...",
                    "Fetch this URL for me",
                ],
                factory=_wrap_factory_with_namespaced_id(web_factory, "builtin:web"),
                agent_dir=None,
                models=[],
                required_connections=[],
                namespaced_agent_id="builtin:web",
                category="research",
                tags=["web", "search", "browser", "download"],
                icon="globe",
                tools_count=10,
            )
        )
        logger.info("registry: Registered built-in agent: web (BrowserAgent)")

        # --- Lite variants of all 5 agents ---
        # Each agent (chat, doc, file, data, web) has a "-lite" variant that
        # uses a smaller ~4B model for faster responses on lower-end hardware.
        # Platform-conditional model list:
        #   macOS: Qwen3.5-4B-GGUF (tool-calling label, OpenAI format)
        #   Linux/Windows: Gemma-4-E4B-it-GGUF (tool-calling label)
        if platform.system() == "Darwin":
            _LITE_MODELS = ["Qwen3.5-4B-GGUF", "Gemma-4-E4B-it-GGUF"]
        else:
            _LITE_MODELS = ["Gemma-4-E4B-it-GGUF", "Qwen3.5-4B-GGUF"]
        _LITE_MIN_MEMORY_GB = 5.0

        _LITE_AGENTS = [
            {
                "id": "chat-lite",
                "name": "Chat Lite",
                "description": "Fast general conversation on a lightweight ~4B model",
                "profile": "chat",
                "starters": [
                    "What can you help me with?",
                    "Tell me about yourself",
                ],
                "extra_config": {},
            },
            {
                "id": "doc-lite",
                "name": "Doc Agent Lite",
                "description": "Document Q&A with RAG on a lightweight ~4B model",
                "profile": "doc",
                "starters": [
                    "Search my documents for...",
                    "Summarize this document",
                ],
                "extra_config": {},
            },
            {
                "id": "file-lite",
                "name": "File Agent Lite",
                "description": "File system navigation and search on a lightweight ~4B model",
                "profile": "file",
                "starters": [
                    "Find files related to...",
                    "What's in my Documents folder?",
                ],
                "extra_config": {"enable_filesystem": True},
            },
            {
                "id": "data-lite",
                "name": "Data Agent Lite",
                "description": "Data analysis and CSV processing on a lightweight ~4B model",
                "profile": "data",
                "starters": [
                    "Analyze my spending data",
                    "What are the trends in this CSV?",
                ],
                "extra_config": {"enable_scratchpad": True},
            },
            {
                "id": "web-lite",
                "name": "Web Agent Lite",
                "description": "Web research and page fetching on a lightweight ~4B model",
                "profile": "web",
                "starters": [
                    "Search the web for...",
                    "Fetch this URL for me",
                ],
                "extra_config": {"enable_browser": True},
            },
        ]

        # Hub metadata for lite variants — mirrors their full-size counterparts.
        _LITE_HUB_META = {
            "chat-lite": {
                "category": "conversation",
                "tags": ["chat", "general", "lightweight"],
                "icon": "message-circle",
                "tools_count": 0,
            },
            "doc-lite": {
                "category": "documents",
                "tags": ["rag", "files", "lightweight"],
                "icon": "file-text",
                "tools_count": 15,
            },
            "file-lite": {
                "category": "productivity",
                "tags": ["files", "search", "lightweight"],
                "icon": "folder-search",
                "tools_count": 10,
            },
            "data-lite": {
                "category": "productivity",
                "tags": ["data", "csv", "lightweight"],
                "icon": "table",
                "tools_count": 10,
            },
            "web-lite": {
                "category": "research",
                "tags": ["web", "search", "lightweight"],
                "icon": "globe",
                "tools_count": 10,
            },
        }

        for agent_def in _LITE_AGENTS:
            aid = agent_def["id"]
            profile = agent_def["profile"]
            extra = agent_def["extra_config"]

            def _make_lite_factory(_profile, _extra):
                def factory(**kwargs):
                    if _profile == "data":
                        from gaia.agents.analyst.agent import (
                            AnalystAgent,
                            AnalystAgentConfig,
                        )

                        valid_fields = {
                            f.name for f in dataclasses.fields(AnalystAgentConfig)
                        }
                        filtered = {
                            k: v for k, v in kwargs.items() if k in valid_fields
                        }
                        filtered.setdefault("model_id", _LITE_MODELS[0])
                        config = AnalystAgentConfig(**filtered)
                        return AnalystAgent(config=config)

                    if _profile == "web":
                        from gaia.agents.browser.agent import (
                            BrowserAgent,
                            BrowserAgentConfig,
                        )

                        valid_fields = {
                            f.name for f in dataclasses.fields(BrowserAgentConfig)
                        }
                        filtered = {
                            k: v for k, v in kwargs.items() if k in valid_fields
                        }
                        filtered.setdefault("model_id", _LITE_MODELS[0])
                        config = BrowserAgentConfig(**filtered)
                        return BrowserAgent(config=config)

                    from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

                    valid_fields = {f.name for f in dataclasses.fields(ChatAgentConfig)}
                    filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
                    filtered.setdefault("model_id", _LITE_MODELS[0])
                    filtered.setdefault("prompt_profile", _profile)
                    for k, v in _extra.items():
                        filtered.setdefault(k, v)
                    config = ChatAgentConfig(**filtered)
                    return ChatAgent(config=config)

                return factory

            hub = _LITE_HUB_META.get(aid, {})
            self._register(
                AgentRegistration(
                    id=aid,
                    name=agent_def["name"],
                    description=agent_def["description"],
                    source="builtin",
                    conversation_starters=agent_def["starters"],
                    factory=_wrap_factory_with_namespaced_id(
                        _make_lite_factory(profile, extra), f"builtin:{aid}"
                    ),
                    agent_dir=None,
                    models=_LITE_MODELS,
                    min_memory_gb=_LITE_MIN_MEMORY_GB,
                    required_connections=[],
                    namespaced_agent_id=f"builtin:{aid}",
                    category=hub.get("category", "general"),
                    tags=hub.get("tags", []),
                    icon=hub.get("icon", ""),
                    tools_count=hub.get("tools_count", 0),
                )
            )
            logger.info(
                "registry: Registered built-in agent: %s (ChatAgent, profile=%s, lite)",
                aid,
                profile,
            )

        # Keep gaia-lite as a legacy alias for backward compatibility
        self._register(
            AgentRegistration(
                id="gaia-lite",
                name="Gaia Lite",
                description=(
                    "Lightweight GAIA agent — same features as the default Chat "
                    "Agent but runs on a ~4B model. Equivalent to doc-lite."
                ),
                source="builtin",
                conversation_starters=[
                    "What can you help me with?",
                    "Summarize this document",
                ],
                factory=_wrap_factory_with_namespaced_id(
                    _make_lite_factory("doc", {}), "builtin:gaia-lite"
                ),
                agent_dir=None,
                models=_LITE_MODELS,
                min_memory_gb=_LITE_MIN_MEMORY_GB,
                required_connections=[],
                namespaced_agent_id="builtin:gaia-lite",
                category="documents",
                tags=["lightweight", "fast", "rag"],
                icon="zap",
                tools_count=15,
            )
        )
        logger.info(
            "registry: Registered built-in agent: gaia-lite (legacy, primary %s)",
            _LITE_MODELS[0],
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
                    category="productivity",
                    tags=["google", "gmail", "github", "calendar"],
                    icon="plug",
                    tools_count=4,
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
                    category="productivity",
                    tags=["email", "gmail", "calendar", "triage"],
                    icon="mail",
                    tools_count=6,
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
                    category="infrastructure",
                    tags=["scaffold", "create"],
                    icon="wrench",
                    tools_count=1,
                )
            )
            logger.info("registry: Registered built-in agent: builder (BuilderAgent)")
        except ImportError:
            logger.debug(
                "registry: BuilderAgent not available, skipping built-in registration"
            )

    # ------------------------------------------------------------------
    # Installed Python agent discovery
    # ------------------------------------------------------------------

    def _discover_entry_point_agents(self) -> None:
        """Register installed Python agents from the ``gaia.agents`` group."""
        agent_entry_points = importlib.metadata.entry_points(
            group=AGENT_ENTRY_POINT_GROUP
        )

        registered = 0
        for entry_point in agent_entry_points:
            try:
                registration = self._load_entry_point_registration(entry_point)
            except Exception as exc:
                logger.warning(
                    "registry: Failed to load agent entry point %s: %s",
                    entry_point.name,
                    exc,
                    exc_info=True,
                )
                continue

            if registration.id in self._agents:
                logger.warning(
                    "registry: entry point agent %s skipped — ID already registered",
                    registration.id,
                )
                continue

            self._register(registration)
            registered += 1

        if registered:
            logger.info("registry: Registered %d entry point agent(s)", registered)

    def _load_entry_point_registration(
        self, entry_point: importlib.metadata.EntryPoint
    ) -> AgentRegistration:
        loaded = entry_point.load()
        registration = loaded() if callable(loaded) else loaded
        if not isinstance(registration, AgentRegistration):
            raise TypeError(
                f"{AGENT_ENTRY_POINT_GROUP} entry point {entry_point.name!r} "
                "must load an AgentRegistration or a zero-argument callable "
                "returning one"
            )
        namespaced_id = f"installed:{registration.id}"
        registration = dataclasses.replace(
            registration,
            namespaced_agent_id=namespaced_id,
            source="installed",
            factory=_wrap_factory_with_namespaced_id(
                registration.factory, namespaced_id
            ),
        )
        return registration

    # ------------------------------------------------------------------
    # Native (C++/binary) agent discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _noop_factory(**_kwargs):
        """Placeholder factory for native agents that cannot be created in-process."""
        raise RuntimeError(
            "Native agents require the Electron Agent Process Manager "
            "(JSON-RPC over stdio). They cannot be started from the web backend."
        )

    def _discover_native_agents(self) -> None:
        """Register native agents from ``agent-manifest.json``.

        The Electron desktop app seeds ``~/.gaia/agent-manifest.json`` with
        metadata for C++/.NET/native binary agents managed by the Agent
        Process Manager.  We read this manifest so ``GET /api/agents``
        returns a unified list of all agents — Python and native alike.
        Native agents cannot be instantiated in-process; their factory
        raises at call time.
        """
        manifest_locations = [
            Path.home() / ".gaia" / "agent-manifest.json",
            Path.home() / ".gaia" / "agents" / "agent-manifest.json",
        ]
        manifest_path = None
        for candidate in manifest_locations:
            if candidate.exists():
                manifest_path = candidate
                break

        if manifest_path is None:
            logger.debug(
                "registry: No agent-manifest.json found, skipping native agents"
            )
            return

        import json

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.warning(
                "registry: Failed to read agent-manifest.json at %s: %s",
                manifest_path,
                e,
            )
            return

        if not isinstance(manifest, dict):
            logger.warning("registry: agent-manifest.json is not an object, skipping")
            return

        agents_list = manifest.get("agents", [])
        if not isinstance(agents_list, list):
            logger.warning(
                "registry: agent-manifest.json 'agents' is not a list, skipping"
            )
            return

        registered = 0
        for entry in agents_list:
            if not isinstance(entry, dict) or "id" not in entry or "name" not in entry:
                continue
            agent_id = entry["id"]
            # Skip if a Python agent already claimed this ID.
            if agent_id in self._agents:
                logger.debug(
                    "registry: native agent %s skipped — ID already registered",
                    agent_id,
                )
                continue
            categories = entry.get("categories", [])
            self._register(
                AgentRegistration(
                    id=agent_id,
                    name=entry["name"],
                    description=entry.get("description", ""),
                    source="native",
                    conversation_starters=[],
                    factory=self._noop_factory,
                    agent_dir=Path.home() / ".gaia" / "agents" / agent_id,
                    models=[],
                    hidden=False,
                    namespaced_agent_id=f"native:{agent_id}",
                    category=categories[0] if categories else "general",
                    tags=list(categories),
                    icon=entry.get("icon", ""),
                    tools_count=entry.get("toolsCount", 0),
                    language=entry.get("language", "cpp"),
                )
            )
            registered += 1

        if registered:
            logger.info(
                "registry: Registered %d native agent(s) from %s",
                registered,
                manifest_path,
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
                f"(see https://amd-gaia.ai/docs/guides/custom-agent). Skipping.",
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

        # Agent Hub metadata — optional class attributes for rich card display.
        agent_category = getattr(agent_class, "AGENT_CATEGORY", "custom")
        agent_icon = getattr(agent_class, "AGENT_ICON", "")
        agent_tags = list(getattr(agent_class, "AGENT_TAGS", []) or [])
        agent_tools_count = getattr(agent_class, "AGENT_TOOLS_COUNT", 0)

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
                            "(see https://amd-gaia.ai/docs/guides/custom-agent).",
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
        # Compute the accepted-kwargs set once at registration time so any
        # introspection failure (e.g. C-extension __init__) surfaces during
        # agent load rather than on the user's first message, and so we
        # don't repeat the MRO walk on every create_agent call.
        accepted_init_params = _accepted_init_params(klass)

        def python_factory(klass=klass, accepted=accepted_init_params, **kwargs):
            if accepted is None:
                return klass(**kwargs)
            filtered = {k: v for k, v in kwargs.items() if k in accepted}
            dropped = set(kwargs) - set(filtered)
            if dropped:
                sec_dropped = dropped & _SECURITY_RELEVANT_KWARGS
                if sec_dropped:
                    logger.warning(
                        "registry: python_factory dropped security-relevant "
                        "kwargs %s for %s — agent will use default constraints. "
                        "Declare these in __init__ if the agent needs them.",
                        sorted(sec_dropped),
                        klass.__name__,
                    )
                non_sec_dropped = dropped - sec_dropped
                if non_sec_dropped:
                    logger.debug(
                        "registry: python_factory dropped %d kwargs not "
                        "accepted by %s.__init__: %s",
                        len(non_sec_dropped),
                        klass.__name__,
                        sorted(non_sec_dropped),
                    )
            return klass(**filtered)

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
                category=agent_category,
                tags=agent_tags,
                icon=agent_icon,
                tools_count=agent_tools_count,
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
