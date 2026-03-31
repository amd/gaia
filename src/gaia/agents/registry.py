"""
GAIA Agent Registry

Dynamic agent registry with hot-reload support and capability-based routing.
"""

import asyncio
import concurrent.futures
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _run_async(coro):
    """Run an async coroutine from sync context, safe when a loop is already running."""
    try:
        asyncio.get_running_loop()
        # Already inside an async context — delegate to a new thread to avoid deadlock
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from gaia.agents.base import (
    AgentCapabilities,
    AgentConstraints,
    AgentDefinition,
    AgentTriggers,
)
from gaia.exceptions import AgentLoadError, AgentNotFoundError, AgentSelectionError
from gaia.pipeline.defect_types import DEFECT_SPECIALISTS, DefectType
from gaia.utils.id_generator import generate_id
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class AgentRegistry:
    """
    Dynamic agent registry with hot-reload support.

    The AgentRegistry provides:
    - Auto-discovery of agent definitions from YAML files
    - Hot-reload when agent files change
    - Capability-based agent routing
    - State-based agent activation
    - Thread-safe operations

    Example:
        >>> registry = AgentRegistry(agents_dir="gaia/config/agents")
        >>> await registry.initialize()
        >>> agent_id = registry.select_agent(
        ...     task_description="Build a REST API",
        ...     current_phase="DEVELOPMENT",
        ...     state={"complexity": 0.7}
        ... )
        >>> print(f"Selected agent: {agent_id}")
    """

    # Predefined agent categories and their typical agents
    AGENT_CATEGORIES: Dict[str, List[str]] = {
        "planning": [
            "planning-analysis-strategist",
            "solutions-architect",
            "api-designer",
            "database-architect",
        ],
        "development": [
            "senior-developer",
            "frontend-specialist",
            "backend-specialist",
            "devops-engineer",
            "data-engineer",
        ],
        "review": [
            "quality-reviewer",
            "security-auditor",
            "performance-analyst",
            "accessibility-reviewer",
            "test-coverage-analyzer",
        ],
        "management": [
            "software-program-manager",
            "technical-writer",
            "release-manager",
        ],
    }

    def __init__(
        self,
        agents_dir: Optional[str] = None,
        auto_reload: bool = True,
        max_concurrent_loads: int = 5,
    ):
        """
        Initialize agent registry.

        Args:
            agents_dir: Directory containing agent YAML definitions
            auto_reload: Whether to watch for file changes
            max_concurrent_loads: Maximum concurrent file loads
        """
        self._agents_dir = Path(agents_dir) if agents_dir else None
        self._auto_reload = auto_reload
        self._max_concurrent_loads = max_concurrent_loads

        # Agent storage
        self._agents: Dict[str, AgentDefinition] = {}

        # Indexes for fast lookup
        self._capability_index: Dict[str, List[str]] = {}  # capability -> agent IDs
        self._trigger_index: Dict[str, List[str]] = {}  # keyword -> agent IDs
        self._category_index: Dict[str, List[str]] = {}  # category -> agent IDs

        # LRU cache for capability lookups (QW-002)
        self._get_agents_by_capability_cached = self._lru_cache_wrapper(
            self._get_agents_by_capability_impl
        )

        # Thread safety
        self._lock = asyncio.Lock()

        # File watcher (optional)
        self._observer: Any = None
        self._watch_task: Optional[asyncio.Task] = None

        logger.info(
            f"AgentRegistry initialized",
            extra={
                "agents_dir": str(self._agents_dir),
                "auto_reload": self._auto_reload,
            },
        )

    async def initialize(self) -> None:
        """
        Initialize registry and load agents.

        Creates agents directory if needed and loads all agent definitions.
        Sets up hot-reload if enabled.
        """
        # Ensure directory exists
        if self._agents_dir:
            self._agents_dir.mkdir(parents=True, exist_ok=True)
            await self._load_all_agents()
            self._build_indexes()

        # Set up hot-reload if enabled
        if self._auto_reload and self._agents_dir:
            await self._setup_hot_reload()

        logger.info(
            f"AgentRegistry initialized with {len(self._agents)} agents",
            extra={"agent_count": len(self._agents)},
        )

    async def _load_all_agents(self) -> None:
        """Load all agent definitions from YAML files."""
        if not self._agents_dir:
            return

        yaml_files = list(self._agents_dir.glob("*.yaml"))
        yaml_files.extend(self._agents_dir.glob("*.yml"))

        for yaml_file in yaml_files:
            try:
                agent = await self._load_agent(yaml_file)
                async with self._lock:
                    self._agents[agent.id] = agent
                logger.debug(f"Loaded agent: {agent.id}")
            except Exception as e:
                logger.error(
                    f"Failed to load agent from {yaml_file}: {e}",
                    extra={"file": str(yaml_file)},
                )

    async def _load_agent(self, yaml_file: Path) -> AgentDefinition:
        """
        Load single agent from YAML file.

        Args:
            yaml_file: Path to YAML file

        Returns:
            AgentDefinition instance

        Raises:
            AgentLoadError: If loading fails
        """
        try:
            if yaml is None:
                raise ImportError("PyYAML is required for agent loading")

            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                raise ValueError("Empty YAML file")

            # Handle both direct and nested 'agent' key formats
            agent_data = data.get("agent", data)

            # Parse nested structures
            triggers_data = agent_data.get("triggers", {})
            capabilities_data = agent_data.get("capabilities", [])
            constraints_data = agent_data.get("constraints", {})
            execution_targets = agent_data.get("execution_targets", {})

            return AgentDefinition(
                id=agent_data.get("id", ""),
                name=agent_data.get("name", ""),
                version=agent_data.get("version", "1.0.0"),
                category=agent_data.get("category", ""),
                description=agent_data.get("description", ""),
                triggers=AgentTriggers(
                    keywords=triggers_data.get("keywords", []),
                    phases=triggers_data.get("phases", []),
                    complexity_range=(
                        tuple(
                            triggers_data.get(
                                "complexity_range", {"min": 0, "max": 1}
                            ).values()
                        )
                        if isinstance(triggers_data.get("complexity_range"), dict)
                        else (0.0, 1.0)
                    ),
                ),
                capabilities=AgentCapabilities(
                    capabilities=(
                        capabilities_data if isinstance(capabilities_data, list) else []
                    ),
                    tools=agent_data.get("tools", []),
                    execution_targets=(
                        execution_targets if isinstance(execution_targets, dict) else {}
                    ),
                ),
                system_prompt=agent_data.get("system_prompt", ""),
                tools=agent_data.get("tools", []),
                execution_targets=execution_targets,
                constraints=AgentConstraints(
                    max_file_changes=constraints_data.get("max_file_changes", 20),
                    max_lines_per_file=constraints_data.get("max_lines_per_file", 500),
                    requires_review=constraints_data.get("requires_review", True),
                    timeout_seconds=constraints_data.get("timeout_seconds", 300),
                ),
                metadata=agent_data.get("metadata", {}),
                enabled=agent_data.get("enabled", True),
            )

        except yaml.YAMLError as e:
            raise AgentLoadError(str(yaml_file), f"YAML parsing error: {e}")
        except Exception as e:
            raise AgentLoadError(str(yaml_file), str(e))

    def _build_indexes(self) -> None:
        """Build capability, trigger, and category indexes for fast routing."""
        self._capability_index.clear()
        self._trigger_index.clear()
        self._category_index.clear()

        for agent_id, agent in self._agents.items():
            if not agent.enabled:
                continue

            # Index by category
            if agent.category not in self._category_index:
                self._category_index[agent.category] = []
            self._category_index[agent.category].append(agent_id)

            # Index by capabilities
            if agent.capabilities:
                for capability in agent.capabilities.capabilities:
                    if capability not in self._capability_index:
                        self._capability_index[capability] = []
                    self._capability_index[capability].append(agent_id)

            # Index by triggers (keywords)
            if agent.triggers.keywords:
                for keyword in agent.triggers.keywords:
                    kw_lower = keyword.lower()
                    if kw_lower not in self._trigger_index:
                        self._trigger_index[kw_lower] = []
                    self._trigger_index[kw_lower].append(agent_id)

        self.invalidate_capability_cache()

    async def _setup_hot_reload(self) -> None:
        """Set up file watcher for hot-reload."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            class AgentFileHandler(FileSystemEventHandler):
                def __init__(self, registry: "AgentRegistry"):
                    self.registry = registry

                def on_modified(self, event):
                    if event.src_path.endswith((".yaml", ".yml")):
                        asyncio.create_task(
                            self.registry._reload_agent(Path(event.src_path))
                        )

                def on_created(self, event):
                    if event.src_path.endswith((".yaml", ".yml")):
                        asyncio.create_task(
                            self.registry._load_agent_and_index(Path(event.src_path))
                        )

                def on_deleted(self, event):
                    if event.src_path.endswith((".yaml", ".yml")):
                        asyncio.create_task(
                            self.registry._unload_agent(Path(event.src_path))
                        )

            self._observer = Observer()
            self._observer.schedule(
                AgentFileHandler(self),
                str(self._agents_dir),
                recursive=False,
            )
            self._observer.start()
            logger.info("Hot-reload watcher started")

        except ImportError:
            logger.warning("watchdog not installed - hot-reload disabled")
            self._auto_reload = False

    async def _reload_agent(self, yaml_file: Path) -> None:
        """Reload single agent on file change."""
        try:
            agent = await self._load_agent(yaml_file)
            async with self._lock:
                self._agents[agent.id] = agent
                self._build_indexes()
            logger.info(f"Hot-reloaded agent: {agent.id}")
        except Exception as e:
            logger.error(f"Failed to reload agent {yaml_file}: {e}")

    async def _load_agent_and_index(self, yaml_file: Path) -> None:
        """Load new agent and add to indexes."""
        try:
            agent = await self._load_agent(yaml_file)
            async with self._lock:
                self._agents[agent.id] = agent
                self._build_indexes()
            logger.info(f"Loaded new agent: {agent.id}")
        except Exception as e:
            logger.error(f"Failed to load agent {yaml_file}: {e}")

    async def _unload_agent(self, yaml_file: Path) -> None:
        """Unload agent when file is deleted."""
        try:
            # Extract agent ID from filename
            agent_id = yaml_file.stem
            async with self._lock:
                if agent_id in self._agents:
                    del self._agents[agent_id]
                    self._build_indexes()
            logger.info(f"Unloaded agent: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to unload agent {yaml_file}: {e}")

    def select_agent(
        self,
        task_description: str,
        current_phase: str,
        state: Dict[str, Any],
        required_capabilities: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Select best agent for the task.

        Routing Logic:
        1. Filter by required capabilities
        2. Filter by phase
        3. Filter by complexity
        4. Score by keyword matching
        5. Return highest scored

        Args:
            task_description: Natural language task description
            current_phase: Current pipeline phase
            state: Current pipeline state (complexity, etc.)
            required_capabilities: Optional list of required capabilities

        Returns:
            Agent ID or None if no match

        Example:
            >>> agent_id = registry.select_agent(
            ...     task_description="Implement user authentication",
            ...     current_phase="DEVELOPMENT",
            ...     state={"complexity": 0.8},
            ...     required_capabilities=["api-design", "security"]
            ... )
        """

        async def _select() -> Optional[str]:
            async with self._lock:
                if not self._agents:
                    return None

                candidates = set(self._agents.keys())

                # Filter by enabled
                candidates = {aid for aid in candidates if self._agents[aid].enabled}

                # Filter by required capabilities
                if required_capabilities:
                    capable_agents = set()
                    for cap in required_capabilities:
                        capable_agents.update(self._capability_index.get(cap, []))
                    if capable_agents:
                        candidates &= capable_agents
                    else:
                        # No agents with required capabilities
                        return None

                # Filter by phase
                for agent_id in list(candidates):
                    agent = self._agents[agent_id]
                    phase_triggers = agent.triggers.phases
                    if phase_triggers and current_phase not in phase_triggers:
                        candidates.discard(agent_id)

                # Filter by complexity
                complexity = state.get("complexity", 0.5)
                for agent_id in list(candidates):
                    agent = self._agents[agent_id]
                    min_complex, max_complex = agent.triggers.complexity_range
                    if not (min_complex <= complexity <= max_complex):
                        candidates.discard(agent_id)

                # Score by keyword matching
                task_lower = task_description.lower()
                scored_candidates = []

                for agent_id in candidates:
                    agent = self._agents[agent_id]
                    score = 0

                    # Keyword matching
                    for keyword in agent.triggers.keywords:
                        if keyword.lower() in task_lower:
                            score += 2

                    # Capability matching bonus
                    for cap in agent.capabilities.capabilities:
                        if cap.lower() in task_lower:
                            score += 1

                    # Phase match bonus
                    if current_phase in agent.triggers.phases:
                        score += 3

                    scored_candidates.append((agent_id, score))

                if not scored_candidates:
                    return None

                # Return highest scored
                scored_candidates.sort(key=lambda x: (-x[1], x[0]))
                return scored_candidates[0][0]

        return _run_async(_select())

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        """
        Get agent by ID.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentDefinition or None
        """
        return self._agents.get(agent_id)

    # Aliases allow callers to use alternative category names that map to canonical ones.
    CATEGORY_ALIASES: Dict[str, str] = {
        "quality": "review",
    }

    def get_agents_by_category(self, category: str) -> List[AgentDefinition]:
        """
        Get all agents in a category.

        Supports category aliases so that e.g. ``"quality"`` resolves to
        the canonical ``"review"`` category used in agent YAML definitions.

        Args:
            category: Category name (planning, development, review, management)
                or an alias (e.g. "quality")

        Returns:
            List of AgentDefinition instances
        """
        resolved = self.CATEGORY_ALIASES.get(category, category)
        agent_ids = self._category_index.get(resolved, [])
        return [self._agents[aid] for aid in agent_ids if aid in self._agents]

    def get_agents_by_capability(self, capability: str) -> List[AgentDefinition]:
        """
        Get all agents with a capability.

        Uses LRU-cached capability index lookup (QW-002).

        Args:
            capability: Capability name

        Returns:
            List of AgentDefinition instances
        """
        agent_ids = self._get_agents_by_capability_cached(capability)
        return [self._agents[aid] for aid in agent_ids if aid in self._agents]

    def get_all_agents(self) -> Dict[str, AgentDefinition]:
        """Get all registered agents."""
        return dict(self._agents)

    def get_enabled_agents(self) -> Dict[str, AgentDefinition]:
        """Get all enabled agents."""
        return {aid: agent for aid, agent in self._agents.items() if agent.enabled}

    def register_agent(self, definition: AgentDefinition) -> None:
        """
        Register an agent definition.

        Args:
            definition: AgentDefinition to register
        """

        async def _register():
            async with self._lock:
                self._agents[definition.id] = definition
                self._build_indexes()
            logger.info(f"Registered agent: {definition.id}")

        _run_async(_register())

    def unregister_agent(self, agent_id: str) -> bool:
        """
        Unregister an agent by ID.

        Args:
            agent_id: Agent ID to remove

        Returns:
            True if agent was removed, False if not found
        """

        async def _unregister():
            async with self._lock:
                if agent_id in self._agents:
                    del self._agents[agent_id]
                    self._build_indexes()
                    logger.info(f"Unregistered agent: {agent_id}")
                    return True
                return False

        return _run_async(_unregister())

    def get_statistics(self) -> Dict[str, Any]:
        """Get registry statistics."""
        return {
            "total_agents": len(self._agents),
            "enabled_agents": sum(1 for a in self._agents.values() if a.enabled),
            "categories": {
                cat: len(agents) for cat, agents in self._category_index.items()
            },
            "capabilities": len(self._capability_index),
            "trigger_keywords": len(self._trigger_index),
        }

    def _get_agents_by_capability_impl(self, capability: str) -> List[str]:
        """Internal capability lookup for LRU caching (QW-002)."""
        return self._capability_index.get(capability, [])

    def _lru_cache_wrapper(self, func):
        """
        Create an LRU-cached version of a method.

        Args:
            func: Function to wrap

        Returns:
            LRU-cached function
        """
        return lru_cache(maxsize=128)(func)

    def invalidate_capability_cache(self) -> None:
        """
        Invalidate the LRU cache for capability lookups.

        Should be called when agents are added or removed.
        """
        if hasattr(self, "_get_agents_by_capability_cached") and hasattr(
            self._get_agents_by_capability_cached, "cache_clear"
        ):
            self._get_agents_by_capability_cached.cache_clear()

    def get_specialist_agent(
        self,
        defect_type: str,
        fallback: str = "senior-developer",
    ) -> Optional[str]:
        """
        Get specialist agent for a defect type.

        Uses the centralized DEFECT_SPECIALISTS mapping from defect_types module
        for consistent specialist routing across the GAIA pipeline.

        Args:
            defect_type: Defect type name (e.g., "SECURITY", "PERFORMANCE")
            fallback: Fallback agent ID if no specialist found

        Returns:
            Agent ID of specialist, or fallback if not found
        """
        defect_type_upper = defect_type.upper() if isinstance(defect_type, str) else ""
        try:
            defect_enum = DefectType[defect_type_upper]
        except KeyError:
            defect_enum = DefectType.UNKNOWN

        candidates = DEFECT_SPECIALISTS.get(defect_enum, [])

        for candidate_id in candidates:
            agent = self.get_agent(candidate_id)
            if agent and agent.enabled:
                return candidate_id

        if fallback and fallback not in candidates:
            agent = self.get_agent(fallback)
            if agent and agent.enabled:
                return fallback

        enabled_agents = self.get_enabled_agents()
        if enabled_agents:
            return next(iter(enabled_agents.keys()))

        return None

    def get_specialist_agents(
        self,
        defect_types: List[str],
    ) -> Dict[str, Optional[str]]:
        """
        Get specialist agents for multiple defect types.

        Args:
            defect_types: List of defect type names

        Returns:
            Dictionary mapping defect types to agent IDs
        """
        return {dt: self.get_specialist_agent(dt) for dt in defect_types}

    def shutdown(self) -> None:
        """Shutdown registry and stop file watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
        logger.info("AgentRegistry shutdown complete")
