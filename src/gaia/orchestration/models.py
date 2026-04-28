"""
Core data models for the GAIA Project Orchestration Kernel.

Provides dataclasses for objective management, dependency tracking,
and YAML serialization with atomic writes to prevent corruption.

Key classes:
    - Objective: Represents a single task within a project phase
    - ObjectiveStatus: Valid lifecycle states for objectives
    - Artifact: Tracks outputs produced by objectives
    - ProjectObjectives: Collection-level YAML persistence
    - DependencyGraph: Reverse dependency index and circular dependency detection
"""

import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from gaia.utils.logging import get_logger

logger = get_logger(__name__)

# Valid status transitions — "Excel equations" metaphor:
# queued -> in_progress -> completed
# queued -> blocked (deps not met)
# in_progress -> blocked (external failure)
# Any -> cancelled (user abort)
_VALID_TRANSITIONS: Dict["ObjectiveStatus", Set["ObjectiveStatus"]] = {}


class ObjectiveStatus(Enum):
    """Lifecycle states for an Objective."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    def can_transition_to(self, target: "ObjectiveStatus") -> bool:
        """Check if a status transition is valid."""
        allowed = _VALID_TRANSITIONS.get(self, set())
        return target in allowed


# Populate valid transitions after enum is defined
_VALID_TRANSITIONS[ObjectiveStatus.QUEUED] = {
    ObjectiveStatus.IN_PROGRESS,
    ObjectiveStatus.BLOCKED,
    ObjectiveStatus.CANCELLED,
}
_VALID_TRANSITIONS[ObjectiveStatus.IN_PROGRESS] = {
    ObjectiveStatus.COMPLETED,
    ObjectiveStatus.BLOCKED,
    ObjectiveStatus.CANCELLED,
}
_VALID_TRANSITIONS[ObjectiveStatus.BLOCKED] = {
    ObjectiveStatus.QUEUED,
    ObjectiveStatus.CANCELLED,
}
_VALID_TRANSITIONS[ObjectiveStatus.COMPLETED] = set()  # terminal
_VALID_TRANSITIONS[ObjectiveStatus.CANCELLED] = set()  # terminal


@dataclass
class Artifact:
    """
    Tracks an output produced by an objective.

    Artifacts capture concrete results: commit SHAs, PR URLs,
    generated documents, etc.

    Attributes:
        artifact_id: Unique identifier for this artifact
        name: Human-readable name
        artifact_type: Category (commit, pr, document, report, etc.)
        url_or_path: Reference to the artifact
        metadata: Additional context (author, timestamp, etc.)
    """

    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    artifact_type: str = "generic"
    url_or_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type,
            "url_or_path": self.url_or_path,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        """Deserialize from dictionary."""
        return cls(
            artifact_id=data.get("artifact_id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            artifact_type=data.get("artifact_type", "generic"),
            url_or_path=data.get("url_or_path", ""),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class Objective:
    """
    Represents a single task within a project phase.

    Each objective is a discrete unit of work that maps to a PipelineEngine
    execution. Objectives have dependencies, status tracking, and artifact
    collection.

    Attributes:
        objective_id: Unique identifier
        title: Human-readable title
        description: Detailed description of the work
        status: Current lifecycle state
        dependencies: List of objective_ids this depends on
        artifacts: Outputs produced when completed
        priority: Scheduling priority (lower = higher priority)
        phase: Pipeline phase (PLANNING, DEVELOPMENT, QUALITY, DECISION)
        pipeline_config: Config dict passed to PipelineEngine
        created_at: ISO timestamp of creation
        updated_at: ISO timestamp of last update
        error_message: Reason for blocked/failed state
    """

    objective_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    status: ObjectiveStatus = ObjectiveStatus.QUEUED
    dependencies: List[str] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    priority: int = 5
    phase: str = "DEVELOPMENT"
    pipeline_config: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    error_message: Optional[str] = None

    def transition_to(self, new_status: ObjectiveStatus) -> None:
        """
        Transition to a new status.

        Raises:
            ValueError: If the transition is not valid
        """
        if not self.status.can_transition_to(new_status):
            raise ValueError(
                f"Invalid transition from {self.status.value} to {new_status.value} "
                f"for objective '{self.title}' ({self.objective_id})"
            )
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_artifact(self, artifact: Artifact) -> None:
        """Add an artifact to this objective."""
        self.artifacts.append(artifact)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "objective_id": self.objective_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "dependencies": self.dependencies,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "priority": self.priority,
            "phase": self.phase,
            "pipeline_config": self.pipeline_config,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Objective":
        """Deserialize from dictionary."""
        obj = cls(
            objective_id=data.get("objective_id", str(uuid.uuid4())[:8]),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=ObjectiveStatus(data.get("status", "queued")),
            dependencies=data.get("dependencies", []),
            artifacts=[
                Artifact.from_dict(a) for a in data.get("artifacts", [])
            ],
            priority=data.get("priority", 5),
            phase=data.get("phase", "DEVELOPMENT"),
            pipeline_config=data.get("pipeline_config", {}),
            created_at=data.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
            updated_at=data.get(
                "updated_at", datetime.now(timezone.utc).isoformat()
            ),
            error_message=data.get("error_message"),
        )
        return obj


@dataclass
class ProjectObjectives:
    """
    Collection of objectives for a project.

    Manages YAML serialization/deserialization with atomic writes
    to prevent corruption. Objectives are stored in .gaia/objectives.yaml.

    Atomic write strategy:
        1. Write to a .tmp file via NamedTemporaryFile
        2. Call os.replace(tmp_path, target_path) for atomic swap

    Attributes:
        project_id: Unique project identifier
        name: Human-readable project name
        objectives: List of objectives
        metadata: Additional project metadata
    """

    project_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    objectives: List[Objective] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_objective(self, objective: Objective) -> None:
        """Add an objective to the project."""
        self.objectives.append(objective)

    def get_objective(self, objective_id: str) -> Optional[Objective]:
        """Look up an objective by ID."""
        for obj in self.objectives:
            if obj.objective_id == objective_id:
                return obj
        return None

    def get_ready_objectives(self) -> List[Objective]:
        """
        Return objectives that are ready to execute.

        An objective is ready if:
        - Status is QUEUED
        - All dependencies are in COMPLETED state
        """
        ready = []
        completed_ids = {
            o.objective_id
            for o in self.objectives
            if o.status == ObjectiveStatus.COMPLETED
        }
        for obj in self.objectives:
            if obj.status != ObjectiveStatus.QUEUED:
                continue
            deps_met = all(dep_id in completed_ids for dep_id in obj.dependencies)
            if deps_met:
                ready.append(obj)
        # Sort by priority (lower = higher priority)
        ready.sort(key=lambda o: o.priority)
        return ready

    def get_all_objective_ids(self) -> Set[str]:
        """Return all objective IDs in the project."""
        return {o.objective_id for o in self.objectives}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "objectives": [o.to_dict() for o in self.objectives],
            "metadata": self.metadata,
        }

    def to_yaml(self) -> str:
        """Serialize to YAML string."""
        return yaml.dump(
            self.to_dict(),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectObjectives":
        """Deserialize from dictionary."""
        return cls(
            project_id=data.get("project_id", str(uuid.uuid4())[:8]),
            name=data.get("name", ""),
            objectives=[
                Objective.from_dict(o) for o in data.get("objectives", [])
            ],
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_yaml_string(cls, yaml_str: str) -> "ProjectObjectives":
        """Deserialize from YAML string."""
        data = yaml.safe_load(yaml_str)
        if data is None:
            data = {}
        return cls.from_dict(data)

    def save_atomic(self, path: str) -> None:
        """
        Save objectives to YAML file with atomic write.

        Writes to a .tmp file first, then uses os.replace() for an
        atomic swap to prevent partial writes from corrupting the file.

        Args:
            path: Target file path (e.g., .gaia/objectives.yaml)
        """
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        yaml_content = self.to_yaml()

        # Write to temp file in the same directory (same filesystem) so
        # os.replace is atomic across platforms
        tmp_path = str(target_path) + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(yaml_content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(target_path))
            logger.info(
                f"Atomic save complete: {target_path}",
                extra={"project_id": self.project_id},
            )
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    @classmethod
    def load(cls, path: str) -> "ProjectObjectives":
        """
        Load objectives from a YAML file.

        Args:
            path: File path to load from

        Returns:
            ProjectObjectives instance, or empty project if file does not exist
        """
        p = Path(path)
        if not p.exists():
            logger.info(f"No objectives file found at {path}, returning empty project")
            return cls()
        content = p.read_text(encoding="utf-8")
        return cls.from_yaml_string(content)


class DependencyGraph:
    """
    Dependency graph for objectives — the "Excel equations" metaphor.

    Provides:
    - Forward dependency tracking (what does X depend on?)
    - Reverse dependency index (what depends on X?)
    - Circular dependency detection via topological sort
    - Cascade computation (if X fails, what must be re-evaluated?)

    Usage:
        >>> graph = DependencyGraph(objectives)
        >>> graph.detect_cycles()  # returns empty list if no cycles
        >>> graph.get_reverse_deps("obj-001")  # objects that depend on obj-001
        >>> graph.max_cascade_depth("obj-001")  # max cascade depth
    """

    def __init__(self, objectives: Optional[List[Objective]] = None) -> None:
        """
        Initialize dependency graph.

        Args:
            objectives: List of objectives to build graph from
        """
        # Forward edges: objective_id -> set of dependency IDs
        self._forward: Dict[str, Set[str]] = {}
        # Reverse edges: objective_id -> set of dependents
        self._reverse: Dict[str, Set[str]] = {}
        self._all_ids: Set[str] = set()

        if objectives:
            self._build(objectives)

    def _build(self, objectives: List[Objective]) -> None:
        """Build forward and reverse indices from objectives."""
        self._forward.clear()
        self._reverse.clear()
        self._all_ids.clear()

        for obj in objectives:
            self._all_ids.add(obj.objective_id)
            if obj.objective_id not in self._forward:
                self._forward[obj.objective_id] = set()
            if obj.objective_id not in self._reverse:
                self._reverse[obj.objective_id] = set()

            for dep_id in obj.dependencies:
                self._forward[obj.objective_id].add(dep_id)
                if dep_id not in self._reverse:
                    self._reverse[dep_id] = set()
                self._reverse[dep_id].add(obj.objective_id)

    def add_objective(self, objective: Objective) -> None:
        """Add a single objective to the graph."""
        self._all_ids.add(objective.objective_id)
        if objective.objective_id not in self._forward:
            self._forward[objective.objective_id] = set()
        if objective.objective_id not in self._reverse:
            self._reverse[objective.objective_id] = set()

        for dep_id in objective.dependencies:
            self._forward[objective.objective_id].add(dep_id)
            if dep_id not in self._reverse:
                self._reverse[dep_id] = set()
            self._reverse[dep_id].add(objective.objective_id)

    def remove_objective(self, objective_id: str) -> None:
        """Remove an objective and its edges from the graph."""
        self._all_ids.discard(objective_id)
        # Remove forward edges
        deps = self._forward.pop(objective_id, set())
        for dep_id in deps:
            self._reverse.get(dep_id, set()).discard(objective_id)
        # Remove reverse edges
        dependents = self._reverse.pop(objective_id, set())
        for dep_id in dependents:
            self._forward.get(dep_id, set()).discard(objective_id)

    def get_dependencies(self, objective_id: str) -> Set[str]:
        """Get the set of IDs that objective_id depends on."""
        return set(self._forward.get(objective_id, set()))

    def get_reverse_deps(self, objective_id: str) -> Set[str]:
        """
        Get the reverse dependency index — what depends on objective_id.

        These are the objectives that will be affected if objective_id
        changes or fails.
        """
        return set(self._reverse.get(objective_id, set()))

    def detect_cycles(self) -> List[List[str]]:
        """
        Detect circular dependencies using DFS-based topological sort.

        Returns:
            List of cycles, where each cycle is a list of objective IDs
            forming the loop. Empty list means no cycles.
        """
        cycles: List[List[str]] = []
        # States: 0 = unvisited, 1 = in progress, 2 = done
        state: Dict[str, int] = {oid: 0 for oid in self._all_ids}
        path: List[str] = []

        def dfs(node: str) -> None:
            state[node] = 1  # Mark as in-progress
            path.append(node)

            for dep_id in self._forward.get(node, set()):
                if dep_id not in state:
                    continue  # External dependency, skip
                if state[dep_id] == 1:
                    # Found a cycle — extract it from path
                    cycle_start = path.index(dep_id)
                    cycle = path[cycle_start:] + [dep_id]
                    cycles.append(cycle)
                elif state[dep_id] == 0:
                    dfs(dep_id)

            path.pop()
            state[node] = 2  # Mark as done

        for node in list(self._all_ids):
            if state.get(node, 0) == 0:
                dfs(node)

        return cycles

    def compute_cascade(self, objective_id: str) -> Set[str]:
        """
        Compute all objectives affected by a change to objective_id.

        This is the "cascade" — everything that transitively depends
        on objective_id.

        Args:
            objective_id: The objective that changed

        Returns:
            Set of all objective IDs in the cascade
        """
        affected: Set[str] = set()
        queue = [objective_id]

        while queue:
            current = queue.pop(0)
            for dependent in self._reverse.get(current, set()):
                if dependent not in affected:
                    affected.add(dependent)
                    queue.append(dependent)

        return affected

    def max_cascade_depth(self, objective_id: str) -> int:
        """
        Compute the maximum cascade depth from objective_id.

        Returns:
            Maximum number of hops in the cascade chain.
            0 if objective_id has no reverse dependencies.
        """
        if not self._reverse.get(objective_id):
            return 0

        visited: Set[str] = set()
        max_depth = 0

        def _dfs(node: str, depth: int) -> None:
            nonlocal max_depth
            visited.add(node)
            max_depth = max(max_depth, depth)
            for dependent in self._reverse.get(node, set()):
                if dependent not in visited:
                    _dfs(dependent, depth + 1)

        _dfs(objective_id, 0)
        return max_depth

    def topological_order(self) -> List[str]:
        """
        Return objectives in topological order (dependencies first).

        Returns:
            List of objective IDs in execution order.

        Raises:
            ValueError: If circular dependencies are detected
        """
        cycles = self.detect_cycles()
        if cycles:
            cycle_desc = " -> ".join(cycles[0])
            raise ValueError(f"Circular dependencies detected: {cycle_desc}")

        # Kahn's algorithm
        in_degree: Dict[str, int] = {oid: 0 for oid in self._all_ids}
        for node in self._all_ids:
            for dep_id in self._forward.get(node, set()):
                if dep_id in in_degree:
                    pass  # dep_id -> node means node has in_degree +1

        # Recalculate: in_degree[node] = number of deps node has that are in graph
        in_degree = {}
        for oid in self._all_ids:
            in_degree[oid] = len(
                self._forward.get(oid, set()) & self._all_ids
            )

        queue: List[str] = [oid for oid in self._all_ids if in_degree[oid] == 0]
        result: List[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for dependent in self._reverse.get(node, set()):
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        return result

    @property
    def nodes(self) -> Set[str]:
        """Return all node IDs in the graph."""
        return set(self._all_ids)

    def partition_into_levels(self) -> List[List[str]]:
        """
        Return objectives grouped by dependency levels.

        Level 0 = objectives with no dependencies (can run in parallel)
        Level 1 = objectives whose deps are all in Level 0
        etc.

        Returns:
            List of lists, where each inner list contains objective_ids
            that can execute concurrently.

        Raises:
            ValueError: If circular dependencies are detected
        """
        cycles = self.detect_cycles()
        if cycles:
            cycle_desc = " -> ".join(cycles[0])
            raise ValueError(f"Circular dependencies detected: {cycle_desc}")

        # Kahn's algorithm variant: batch zero-in-degree nodes as levels
        in_degree: Dict[str, int] = {}
        for oid in self._all_ids:
            in_degree[oid] = len(
                self._forward.get(oid, set()) & self._all_ids
            )

        remaining: Set[str] = set(self._all_ids)
        levels: List[List[str]] = []
        processed: int = 0

        while remaining:
            # Collect all remaining nodes with zero in-degree
            level = [oid for oid in remaining if in_degree.get(oid, 0) == 0]
            if not level:
                break

            levels.append(level)
            processed += len(level)

            # Remove these nodes from remaining and update in-degrees
            for node in level:
                remaining.discard(node)
                for dependent in self._reverse.get(node, set()):
                    if dependent in in_degree:
                        in_degree[dependent] -= 1

        if processed != len(self._all_ids):
            raise ValueError("Unable to partition all objectives into levels")

        return levels


@dataclass
class ConflictReport:
    """Records a file-level conflict between parallel objectives."""

    conflicting_objective_ids: list[str]
    affected_files: set[str]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            from datetime import datetime, timezone
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class LevelResult:
    """Outcome of executing one dependency level in parallel mode."""

    level_number: int
    objective_ids: list[str]
    outcomes: dict  # objective_id -> ObjectiveOutcome
    conflicts: list  # List[ConflictReport]
    success_count: int = 0
    failure_count: int = 0
    verdict: str = "CONTINUE"  # Verdict enum value as string
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            from datetime import datetime, timezone
            self.timestamp = datetime.now(timezone.utc).isoformat()
