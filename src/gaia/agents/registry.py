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
import sys
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import yaml

from gaia.connectors.providers.base import ConnectorRequirement
from gaia.llm.lemonade_client import GPU_CTX_SIZE, NPU_CTX_SIZE
from gaia.logger import get_logger

logger = get_logger(__name__)

# Entry-point groups scanned for installed agent wheels. The hub packaging
# format (docs/spec/agent-hub-restructure.mdx) declares agents under
# ``gaia.agent`` (singular); the original group was ``gaia.agents`` (plural).
# Both are scanned so packages using either form are discovered.
AGENT_ENTRY_POINT_GROUP = "gaia.agents"
AGENT_ENTRY_POINT_GROUPS = ("gaia.agents", "gaia.agent")

# KNOWN_TOOLS maps tool name -> (module_path, class_name) for lazy import.
# Consumed by BuilderAgent's template (src/gaia/agents/builder/template.py) to
# scaffold tool-mixin imports and base classes when generating agent.py files.
KNOWN_TOOLS: Dict[str, tuple] = {
    "rag": ("gaia.agents.tools.rag_tools", "RAGToolsMixin"),
    "code_index": ("gaia.agents.tools.code_index_tools", "CodeIndexToolsMixin"),
    "file_search": ("gaia.agents.tools.file_tools", "FileSearchToolsMixin"),
    "file_io": ("gaia.agents.tools.file_io_tools", "FileIOToolsMixin"),
    "shell": ("gaia.agents.tools.shell_tools", "ShellToolsMixin"),
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


# Reserved agent IDs that custom agents (under ~/.gaia/agents/) must not claim.
# Covers the built-ins ``_register_builtin_agents`` registers plus the legacy
# "-lite" / ``gaia-lite`` aliases — those no longer register their own card
# (#1162) but remain reserved so a custom agent can't claim the old ID and
# shadow the alias resolution in ``_LEGACY_ID_ALIASES``.
# Only ids that resolve to a framework *builtin* belong here. The chat/doc/file
# profiles, data, web, email (and all their -lite / gaia-lite aliases) migrated
# to standalone hub wheels (#1102), so they register via the gaia.agent entry
# point and are no longer reserved builtins. ``builder`` is the only remaining
# framework agent.
_RESERVED_BUILTIN_IDS: frozenset[str] = frozenset(
    {
        "builder",
    }
)


# BuilderAgent's model preference, best-to-worst. The first two entries match
# what `gaia init` profiles actually install (`Gemma-4-E4B-it-GGUF` is the
# default-profile model; `gemma4-it-e2b-FLM` is the npu-profile one — see
# `INIT_PROFILES` in `gaia.installer.init_command`); no profile installs the
# 35B, so it must not be the only entry (#2243).
#
# Gemma leads so the builder resolves to the same model every other agent uses.
# Preferring the 35B would reintroduce the evict-and-reload this consolidation
# removed, on the machines that happen to still have it installed. The 35B stays
# last so an existing install keeps working.
BUILDER_PREFERRED_MODELS: List[str] = [
    "Gemma-4-E4B-it-GGUF",
    "gemma4-it-e2b-FLM",
    "Qwen3.5-35B-A3B-GGUF",
]


def resolve_preferred_model(
    preferred_models: List[str], available_models: List[str]
) -> Optional[str]:
    """Return the first of *preferred_models* present in *available_models*.

    Pure ordering primitive shared by ``AgentRegistry.resolve_model`` and any
    caller (e.g. ``BuilderAgent``) that needs to pick a model from a live
    Lemonade catalog without going through a full registry instance.
    """
    for model in preferred_models:
        if model in available_models:
            return model
    return None


def get_lemonade_models(base_url: str, timeout: float = 2.0) -> Optional[List[str]]:
    """Query Lemonade's ``/models`` endpoint directly (no cache, no backoff).

    Returns the list of installed model ids on a 2xx response — an empty list
    means Lemonade is reachable but nothing is loaded/installed. Returns
    ``None`` when the request could not be completed at all (connection
    error, timeout, non-2xx, malformed response). Callers must not conflate
    the two: "unreachable" and "reachable but nothing installed" call for
    different messages and different remediation.
    """
    try:
        import requests

        resp = requests.get(f"{base_url}/models", timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]
    except Exception:
        pass
    return None


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


def class_factory(agent_class: type) -> Callable[..., Any]:
    """Return a kwarg-filtering factory for a plain ``Agent`` subclass.

    Standalone agent packages (``hub/agents/<id>/python/``) call this from
    their ``build_registration()`` to wire a registry factory without
    re-implementing the kwarg-filtering dance. Session-level kwargs injected
    by the registry/UI host (``namespaced_agent_id``, ``allowed_paths``,
    ``model_id`` …) are filtered down to the parameters the constructor
    actually accepts — unless the constructor declares ``**kwargs``, in which
    case every kwarg is forwarded as-is.

    For agents constructed from a config dataclass (``Agent(config=Cfg(...))``)
    write a small explicit factory instead; this helper is for the common
    ``Agent(**kwargs)`` shape.
    """
    accepted = _accepted_init_params(agent_class)

    def factory(**kwargs):
        if accepted is None:
            return agent_class(**kwargs)
        return agent_class(**{k: v for k, v in kwargs.items() if k in accepted})

    return factory


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


# Default embedder (GPU/CPU). The NPU device overrides this with the FLM-native
# embedder so chat + embeddings stay co-resident on the NPU backend (#1744).
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text-v2-moe-GGUF"


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
        embedding_model: Embedder model id for RAG/memory on this device. NPU
            uses the FLM-native embedder so the chat model and embedder stay
            co-resident on the NPU backend; a GGUF embedder runs on Vulkan and
            evicts the FLM chat model every turn on a shared-memory APU (#1744).
    """

    device: Literal["cpu", "gpu", "npu"]
    model: str
    recipe: str
    backend: str
    verified: bool = False
    ctx_size: int = GPU_CTX_SIZE
    embedding_model: str = DEFAULT_EMBEDDING_MODEL


# Default device configurations for built-in agents using Gemma 4 E4B.
# GPU is the default device — most broadly available on AMD hardware.
DEFAULT_DEVICE_CONFIGS: List[DeviceConfig] = [
    DeviceConfig(
        device="gpu",
        model="Gemma-4-E4B-it-GGUF",
        recipe="llamacpp",
        backend="llamacpp:vulkan",
        verified=True,
        ctx_size=GPU_CTX_SIZE,
    ),
    DeviceConfig(
        device="cpu",
        model="Gemma-4-E4B-it-GGUF",
        recipe="llamacpp",
        backend="llamacpp:cpu",
        verified=False,
        ctx_size=GPU_CTX_SIZE,
    ),
    DeviceConfig(
        device="npu",
        model="gemma4-it-e2b-FLM",
        recipe="flm",
        backend="flm:npu",
        verified=True,
        ctx_size=NPU_CTX_SIZE,
        # FLM-native embedder so chat + embeddings stay co-resident on the NPU
        # backend and don't thrash NPU<->Vulkan every turn (#1744).
        embedding_model="embed-gemma-300m-FLM",
    ),
]


def get_embedding_model_for_device(device: Optional[str]) -> str:
    """Return the embedder model id for a device target.

    Single source of truth: reads ``DEFAULT_DEVICE_CONFIGS`` so the embedder
    choice lives next to the chat model/recipe/backend for each device. The NPU
    profile uses the FLM-native embedder (see ``DeviceConfig.embedding_model``);
    GPU/CPU and an unspecified device default to the GGUF nomic embedder, which
    matches the GPU-default policy elsewhere in the CLI.
    """
    for dc in DEFAULT_DEVICE_CONFIGS:
        if dc.device == device:
            return dc.embedding_model
    return DEFAULT_EMBEDDING_MODEL


@dataclass
class ModelTier:
    """A selectable model size for an agent (#1162).

    Each agent exposes one entry per size — ``full`` (the agent's default
    model) and ``lite`` (a ~4B model for faster responses on lower-end
    hardware). The Agent UI renders these as a model-size selector on the
    agent card instead of shipping a separate "… Lite" card per agent.

    Attributes:
        name: Stable tier key — ``"full"`` or ``"lite"``.
        label: Human-readable label for the selector (e.g. ``"Lite (~4B)"``).
        models: Ordered model-preference list for this tier. Empty means
            "use the agent's own default model" (the registry leaves
            ``model_id`` unset so the agent's ``__init__`` governs).
        min_memory_gb: Minimum recommended free RAM (GB) for this tier, or
            ``None`` when the tier declares no requirement.
        default: Whether this tier is the default selection.
    """

    name: str
    label: str
    models: List[str] = field(default_factory=list)
    min_memory_gb: Optional[float] = None
    default: bool = False


def _select_tier_model(tiers: List[ModelTier], tier_name: str) -> Optional[str]:
    """Return the preferred model for *tier_name*, or ``None``.

    ``None`` when the tier is unknown or declares no models (the agent's
    own default model should govern in that case).
    """
    for tier in tiers:
        if tier.name == tier_name and tier.models:
            return tier.models[0]
    return None


# Platform-conditional ~4B preset for the shared "lite" tier (#1162). macOS
# prefers Qwen (OpenAI tool-call format); Linux/Windows prefer Gemma.
def lite_models() -> List[str]:
    """Return the ordered ~4B model preference list for the ``lite`` tier."""
    if platform.system() == "Darwin":
        return ["Qwen3.5-4B-GGUF", "Gemma-4-E4B-it-GGUF"]
    return ["Gemma-4-E4B-it-GGUF", "Qwen3.5-4B-GGUF"]


LITE_MIN_MEMORY_GB = 5.0


def build_model_tiers(full_label: str) -> List[ModelTier]:
    """Build the full+lite tier pair shared by the consolidated agents (#1162).

    The ``full`` tier carries no model list — the agent's own ``__init__``
    default governs — so full-size behaviour is unchanged. The ``lite`` tier
    pins the ~4B preset. Promoted to module level so standalone hub agent
    packages (``hub/agents/<id>/python/``) can declare the same tiers in their
    ``build_registration()`` without re-deriving the preset list.
    """
    return [
        ModelTier(name="full", label=full_label, models=[], default=True),
        ModelTier(
            name="lite",
            label="Lite (~4B)",
            models=lite_models(),
            min_memory_gb=LITE_MIN_MEMORY_GB,
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
    # ``consumes_mcp_servers`` is the agent class's ``CONSUMES_MCP_SERVERS``
    # ClassVar surfaced into the registry. True for agents that load MCP
    # servers dynamically at runtime (e.g. ChatAgent); lets the Settings
    # "Active for" panel list them as activatable for MCP-server connectors
    # even though they declare no static ``REQUIRED_CONNECTORS`` for those.
    consumes_mcp_servers: bool = False
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
    # Model-size tiers (issue #1162). Agents that support a "full" vs "lite"
    # (~4B) model selection declare both here; the Agent UI renders a single
    # card with a size selector instead of duplicate "… Lite" cards. Empty for
    # agents that expose only one model size.
    model_tiers: List[ModelTier] = field(default_factory=list)
    # The concrete Agent subclass this registration resolves to, when it is
    # already imported at registration time. Populated for file-based custom
    # agents (`source == "custom_python"`), whose module is exec'd during
    # discovery; left None for builtins/installed wheels that use lazy-import
    # factories. Lets the hub health check inspect the class (e.g. unimplemented
    # abstract methods) without re-importing or constructing the agent (#2268).
    agent_class: Optional[type] = None


class AgentRegistry:
    """Central registry for discovering, loading, and creating agents.

    Call :meth:`discover` once at server startup to scan built-in agents
    and the ``~/.gaia/agents/`` directory for custom agents.
    """

    # Legacy agent IDs that were consolidated (issue #1162). Each agent used to
    # ship a separate "-lite" registration; they are now a model *tier* of the
    # single base agent. Existing UI sessions store the old ID in
    # ``sessions.agent_type`` in the ChatDatabase; resolving the alias keeps
    # those sessions working without a DB migration. All lookups (``get``,
    # ``create_agent``, ``resolve_model``) honour the map.
    #
    # ``gaia-lite`` historically aliased ``doc-lite`` (doc profile, ~4B model),
    # so it resolves to ``doc`` with the lite tier.
    _LEGACY_ID_ALIASES: Dict[str, str] = {
        "chat-lite": "chat",
        "doc-lite": "doc",
        "file-lite": "file",
        "data-lite": "data",
        "web-lite": "web",
        "gaia-lite": "doc",
    }

    # Model tier implied by each legacy "-lite" alias. ``create_agent`` and
    # ``resolve_model`` use this to select the ~4B preset for a session stored
    # under the old ID, so consolidating the registrations doesn't silently
    # promote those sessions back to the full-size model.
    _LEGACY_ID_TIERS: Dict[str, str] = {
        "chat-lite": "lite",
        "doc-lite": "lite",
        "file-lite": "lite",
        "data-lite": "lite",
        "web-lite": "lite",
        "gaia-lite": "lite",
    }

    def __init__(self):
        self._agents: Dict[str, AgentRegistration] = {}
        self._lemonade_models: Optional[List[str]] = None  # cache
        self._lemonade_models_last_fail: Optional[float] = None  # monotonic timestamp
        self._lock = threading.Lock()
        # Records agent IDs whose load failed during discover() / register_from_dir().
        # Populated by _record_load_error(); read by get_load_error() and Stage D.
        self._load_errors: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Load-error tracking
    # ------------------------------------------------------------------

    def _record_load_error(self, agent_id: str, reason: str) -> None:
        """Record a concise load-failure reason for *agent_id*.

        Kept in ``_load_errors`` so Stage D (chat helpers) can surface a
        helpful message when the user requests a broken agent.  The existing
        discovery try/except is unchanged — this is additive only.
        """
        with self._lock:
            self._load_errors[agent_id] = reason

    def get_load_error(self, agent_id: str) -> Optional[str]:
        """Return the recorded load-error reason for *agent_id*, or None.

        Errors are keyed by the agent's directory name (e.g. 'my-bot'),
        which matches the resolved agent id.  A caller that passes a type
        string that was normalised differently will get None gracefully.
        None also means the agent loaded fine or was never attempted.
        """
        return self._load_errors.get(agent_id)

    # ------------------------------------------------------------------
    # Legacy ID resolution
    # ------------------------------------------------------------------

    def canonical_id(self, agent_id: str) -> str:
        """Return the current canonical ID for *agent_id*, resolving aliases.

        Returns the input unchanged when no alias exists so callers can use
        the result as a stable cache key — both ``chat`` and the legacy
        ``chat-lite`` resolve to ``chat``, so the per-session agent cache
        doesn't thrash when a client mixes the old and new names.
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
                    self._record_load_error(agent_dir.name, f"{type(e).__name__}: {e}")
        else:
            logger.info("registry: No custom agent directory found at %s", agents_dir)

        # 2.5. Prime sys.path with every hub-installed wheel agent's
        # site-packages BEFORE the entry-point scan below. installer.install()
        # only mutates sys.path in the process that ran the install
        # (_hot_register); a fresh process (e.g. the next `gaia chat`
        # invocation) never sees a wheel agent installed by an earlier `gaia
        # init` unless something re-adds it here (#2358).
        self._prime_installed_wheel_agents_path()

        # 3. Discover installed Python agents exposed by standalone wheels
        self._discover_installed_agents()

        # 4. Discover native (C++/binary) agents from agent-manifest.json
        self._discover_native_agents()

        agent_ids = list(self._agents.keys())
        logger.info(
            "registry: Agent discovery complete. %d agents registered: %s",
            len(agent_ids),
            agent_ids,
        )

    def _prime_installed_wheel_agents_path(self) -> None:
        """Append every hub-installed WHEEL agent's ``site-packages`` to
        ``sys.path`` so the entry-point scan in ``_discover_installed_agents``
        can find it, even in a process that never ran the install (#2358).

        Import of ``gaia.hub.installer`` is deliberately function-local:
        ``installer.py`` does an unconditional module-level ``from
        gaia.agents.registry import _RESERVED_BUILTIN_IDS``, so a module-level
        import here would create a circular partial-init error on whichever
        module happens to import ``gaia.agents.registry`` first.

        Uses ``sys.path.append`` (never ``insert(0)``): entry-point discovery
        via ``importlib.metadata.entry_points()`` unions dist-info across
        every ``sys.path`` entry regardless of order, so prepending buys
        nothing for discovery here — but it WOULD give an isolated per-agent
        ``site-packages`` process-wide precedence over the active
        environment's own pinned dependencies (``amd-gaia`` and its NPU/
        torch/numpy pins), since ``pip install --target`` installs a fresh
        copy of any dependency the active env doesn't already satisfy.
        """
        from gaia.hub import installer as hub_installer

        try:
            installed = hub_installer.list_installed()
        except Exception as exc:  # noqa: BLE001 - best-effort discovery step
            logger.warning(
                "registry: could not list hub-installed agents for sys.path "
                "priming: %s",
                exc,
            )
            return

        for agent_id, info in installed.items():
            if info.artifact_kind != hub_installer.ARTIFACT_KIND_WHEEL:
                continue
            if info.path is None:
                continue
            site_packages = info.path / hub_installer.SITE_PACKAGES_DIRNAME
            if not site_packages.is_dir():
                continue
            sp = str(site_packages)
            if sp not in sys.path:
                sys.path.append(sp)
                logger.debug(
                    "registry: primed sys.path with %s's site-packages (%s)",
                    agent_id,
                    sp,
                )

        # Invalidate import-machinery caches (mirrors _hot_register) so the
        # entry-point scan right after this sees the newly appended paths.
        importlib.invalidate_caches()

    # ------------------------------------------------------------------
    # Built-in agents
    # ------------------------------------------------------------------

    def _register_builtin_agents(self) -> None:
        """Register built-in agents (ChatAgent, BuilderAgent, etc.)."""

        # ChatAgent ships as the standalone ``gaia-agent-chat`` wheel (#1102)
        # exposing three prompt-profile ids — ``chat``/``doc``/``file`` — each
        # via its own ``gaia.agent`` entry point, discovered in
        # ``_discover_installed_agents``. The full+lite model tiers live in the
        # package's ``build_chat``/``build_doc``/``build_file`` using the
        # module-level ``build_model_tiers`` helper. No built-in registration
        # here.

        # AnalystAgent (id="data") and BrowserAgent (id="web") ship as the
        # standalone ``gaia-agent-analyst`` / ``gaia-agent-browser`` wheels
        # (#1102), discovered via the ``gaia.agent`` entry point in
        # ``_discover_installed_agents`` — no built-in registration here.

        # The former ``chat-lite``/``doc-lite``/``file-lite``/``data-lite``/
        # ``web-lite``/``gaia-lite`` registrations are consolidated into the
        # ``lite`` model tier of the agents above (#1162). The old IDs resolve
        # through ``_LEGACY_ID_ALIASES`` so existing sessions keep working.

        # ConnectorsDemoAgent ships as the standalone ``gaia-agent-connectors-demo``
        # wheel (#1102) and is discovered via the ``gaia.agent`` entry point in
        # ``_discover_installed_agents`` — no built-in registration here.

        # EmailTriageAgent (id="email") ships as the standalone
        # ``gaia-agent-email`` wheel (#1102), discovered via the ``gaia.agent``
        # entry point in ``_discover_installed_agents`` — no built-in
        # registration here. Its REST + MCP surfaces ship in that wheel too
        # (``gaia_agent_email.api_routes`` / ``gaia_agent_email.mcp_server``).

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
                    models=BUILDER_PREFERRED_MODELS,
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

    def discover_installed_agents(self) -> None:
        """Re-scan entry points for installed agent wheels (public hot-reload hook).

        Called by the Agent Hub installer after ``uv pip install --target`` so a
        freshly installed agent appears in the live registry without a server
        restart. Idempotent — already-registered ids are skipped.
        """
        self._discover_installed_agents()

    def _discover_installed_agents(self) -> None:
        """Register installed Python agents exposed by standalone wheels.

        Scans the ``gaia.agent`` / ``gaia.agents`` entry-point groups. Each
        entry point loads either an :class:`AgentRegistration`, a zero-argument
        callable returning one (the ``build_registration()`` convention used by
        ``hub/agents/<id>/python/``), or an ``Agent`` subclass. This is how the
        framework-only core wheel finds the production agents once they ship as
        separate ``gaia-agent-<id>`` packages (issue #1102).
        """
        seen_entry_points: set = set()
        agent_entry_points = []
        for group in AGENT_ENTRY_POINT_GROUPS:
            for ep in importlib.metadata.entry_points(group=group):
                key = (ep.name, ep.value)
                if key in seen_entry_points:
                    continue
                seen_entry_points.add(key)
                agent_entry_points.append(ep)

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

        # Spec form (docs/spec/agent-hub-restructure.mdx): the entry point may
        # target the Agent subclass directly, e.g.
        # ``chat = "gaia_agent_chat.agent:ChatAgent"``. Build a registration
        # from its class attributes in that case.
        from gaia.agents.base.agent import Agent as BaseAgent

        if isinstance(loaded, type) and issubclass(loaded, BaseAgent):
            registration = self._registration_from_class(loaded, entry_point.name)
        else:
            registration = loaded() if callable(loaded) else loaded

        if not isinstance(registration, AgentRegistration):
            raise TypeError(
                f"agent entry point {entry_point.name!r} must load an "
                "AgentRegistration, a zero-argument callable returning one, or "
                "an Agent subclass"
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

    def _registration_from_class(
        self, agent_class: type, entry_point_name: str
    ) -> AgentRegistration:
        """Build an :class:`AgentRegistration` from an Agent subclass.

        Reads identity + hub-display metadata from class attributes
        (``AGENT_ID``, ``AGENT_NAME``, ``AGENT_DESCRIPTION``,
        ``CONVERSATION_STARTERS``, ``AGENT_CATEGORY`` …) — the same attributes
        custom agents under ``~/.gaia/agents/`` declare. Falls back to the
        entry-point name for the id when ``AGENT_ID`` is absent.
        """
        agent_id = getattr(agent_class, "AGENT_ID", "") or entry_point_name
        agent_name = getattr(agent_class, "AGENT_NAME", "") or agent_id
        return AgentRegistration(
            id=agent_id,
            name=agent_name,
            description=getattr(agent_class, "AGENT_DESCRIPTION", "") or "",
            source="installed",
            conversation_starters=list(
                getattr(agent_class, "CONVERSATION_STARTERS", []) or []
            ),
            factory=class_factory(agent_class),
            agent_dir=None,
            models=list(getattr(agent_class, "AGENT_MODELS", []) or []),
            required_connections=list(
                getattr(agent_class, "REQUIRED_CONNECTORS", []) or []
            ),
            consumes_mcp_servers=bool(
                getattr(agent_class, "CONSUMES_MCP_SERVERS", False)
            ),
            namespaced_agent_id=f"installed:{agent_id}",
            category=getattr(agent_class, "AGENT_CATEGORY", "general") or "general",
            tags=list(getattr(agent_class, "AGENT_TAGS", []) or []),
            icon=getattr(agent_class, "AGENT_ICON", "") or "",
            tools_count=getattr(agent_class, "AGENT_TOOLS_COUNT", 0) or 0,
        )

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
        consumes_mcp_servers = bool(getattr(agent_class, "CONSUMES_MCP_SERVERS", False))
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
                agent_class=agent_class,
                models=models,
                required_connections=required_connections,
                consumes_mcp_servers=consumes_mcp_servers,
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
            self._record_load_error(agent_dir.name, f"{type(exc).__name__}: {exc}")
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

        A legacy ``-lite`` alias (e.g. ``chat-lite``) resolves to its base
        agent and selects the ``lite`` model tier — unless the caller already
        pinned a ``model_tier`` or ``model_id`` (#1162).

        Raises:
            ValueError: If *agent_id* is not registered.
        """
        # Route through get() so legacy aliases (e.g. chat-lite → chat)
        # resolve consistently with lookups.
        reg = self.get(agent_id)
        if reg is None:
            raise ValueError(
                f"Unknown agent ID: '{agent_id}'. "
                f"Available: {list(self._agents.keys())}"
            )
        tier = self._LEGACY_ID_TIERS.get(agent_id)
        if tier and "model_tier" not in kwargs and "model_id" not in kwargs:
            kwargs["model_tier"] = tier
        logger.info(
            "registry: Creating agent '%s' (resolved id='%s'%s)",
            agent_id,
            reg.id,
            f", tier='{tier}'" if tier else "",
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
        # through to the default model instead of the 4B preset, silently
        # regressing the whole reason the lite tier exists.
        reg = self.get(agent_id)
        if not reg:
            return None

        # A legacy "-lite" alias resolves to the base agent, which carries no
        # top-level ``models`` preference — pull the preset from the matching
        # model tier instead so the ~4B model still wins (#1162).
        preferred_models = reg.models
        tier_name = self._LEGACY_ID_TIERS.get(agent_id)
        if tier_name:
            tier = next((t for t in reg.model_tiers if t.name == tier_name), None)
            if tier and tier.models:
                preferred_models = tier.models

        if not preferred_models:
            return None

        if available_models is None:
            available_models = self._get_available_models()

        resolved = resolve_preferred_model(preferred_models, available_models)
        if resolved is not None:
            logger.info(
                "registry: Agent %s: preferred model %s available",
                agent_id,
                resolved,
            )
            return resolved

        logger.warning(
            "registry: Agent %s: no preferred models available (%s), using server default",
            agent_id,
            preferred_models,
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

        base_url = os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")
        models = get_lemonade_models(base_url)
        if models is not None:
            self._lemonade_models = models
            return self._lemonade_models

        # Record failure timestamp; do NOT cache models so we retry after the interval.
        self._lemonade_models_last_fail = time.monotonic()
        return []
