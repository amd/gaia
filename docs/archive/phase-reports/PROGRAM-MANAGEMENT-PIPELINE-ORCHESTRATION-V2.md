# PROGRAM MANAGEMENT PLAN: Pipeline Orchestration v2 (Project-Level)

**Branch:** `feature/pipeline-orchestration-v1`
**Author:** Program Management (Recursive Iterative Pipeline)
**Date:** 2026-04-26
**Status:** Phase 1 COMPLETE | Phase 2 READY
**Phase Focus:** Phase 1 implemented (89 tests, 8.5/10 quality); Phase 2 planned
**Last Updated:** 2026-04-26 (Phase 1 implementation complete)

---

## 1. EXECUTIVE SUMMARY

The strategist identified a critical gap: `PipelineEngine` runs a single pipeline, but nothing orchestrates multiple pipelines toward a project goal. The missing layer is a three-part orchestration kernel:

1. **ProjectOrchestrator** -- Long-running loop above PipelineEngine: reads objectives, dispatches pipelines, evaluates outcomes, updates objectives, git commit/PR, repeat.
2. **ProjectSupervisor hierarchy** -- Strategic agent overseeing specialized supervisors (Quality, Git, Code).
3. **Objectives Document** -- YAML-format roadmap with status tracking, dependencies, and artifact links.

This plan breaks Phase 1 and Phase 2 into specific, trackable work items with acceptance criteria, file-level implementation targets, risk mitigation, and testing strategy.

**Key constraint:** The existing `PipelineOrchestrator` in `src/gaia/pipeline/orchestrator.py` is a DIFFERENT concept (domain analysis, workflow modeling, agent spawning within a single pipeline). Our new `ProjectOrchestrator` operates at a higher level -- managing multiple PipelineEngine executions across multiple objectives.

---

## 1.5 PHASE 1 COMPLETION STATUS (UPDATED 2026-04-26)

### Phase 1: Core Orchestration Kernel -- COMPLETE

All Phase 1 work items have been implemented and tested. Below is the mapping of planned work items to actual deliverables.

| Work Item | Planned File | Actual File | Status |
|-----------|-------------|-------------|--------|
| 1.1 Objectives Model | `orchestration/objectives.py` | `orchestration/models.py` (604 lines) | COMPLETE |
| 1.2 Orchestrator Core | `orchestration/engine.py` | `orchestration/engine.py` (584 lines) | COMPLETE |
| 1.3 Package Init | `orchestration/__init__.py` | `orchestration/__init__.py` (44 lines) | COMPLETE |
| 1.4 Hook Events | `orchestration/hooks.py` | `orchestration/hooks.py` (193 lines) | COMPLETE |
| 1.5 Tests | `tests/unit/orchestration/` | 2 test files (89 tests) | COMPLETE |
| 1.5b Adapter Layer | (not planned) | `orchestration/adapters.py` (323 lines) | ADDED |

**Actual deliverables vs. planned:**

| Metric | Planned | Actual | Delta |
|--------|---------|--------|-------|
| Production files | 4 | 6 | +2 (added adapters.py) |
| Production lines | ~1,390 | ~1,748 | +358 |
| Test files | 3 (unit) + 1 (integration) | 2 (unit) | Deferred integration |
| Test count | ~220 | 89 | Fewer, but higher quality |
| Quality score | N/A | 8.5/10 | Measured |

**Key deviations from plan:**

1. **File renamed:** `objectives.py` became `models.py` to better reflect the broader scope (Objective, DependencyGraph, ProjectObjectives).
2. **Adapter layer added:** `adapters.py` was not in the original plan but was created to establish a clean architectural boundary between the orchestrator and PipelineEngine.
3. **Status enum values:** Changed from `TODO/IN_PROGRESS/DONE` to `QUEUED/IN_PROGRESS/COMPLETED/BLOCKED/CANCELLED` for clarity and consistency with PipelineEngine.
4. **Atomic writes:** Added `save_atomic()` with temp file + `os.replace()` to prevent YAML corruption.
5. **Dependency graph:** Added `DependencyGraph` class with forward/reverse index, cycle detection, and cascade computation.
6. **CircuitBreaker:** Integrated into the adapter layer with correct invocation pattern.
7. **auto_commit defaults to False:** Plan suggested True; implementation defaults to False for safety.
8. **Bugs fixed during implementation:** Double-shutdown in adapters.py, HookResult constructor (tests used `reason` kwarg; actual API uses `metadata` dict), CircuitBreaker pattern verified correct.

**All 6 critical QA gaps resolved:**

| Gap ID | Test | Description |
|--------|------|-------------|
| G1 | `test_run_halts_on_halted_pipeline` | Hook halt_pipeline=True breaks dispatch loop |
| G2 | `test_run_max_iterations_exceeded` | Safety valve at max_cycle_iterations |
| G3 | `test_run_project_stuck_all_blocked` | All-blocked project exits loop |
| G5 | `test_git_commit_auto_commit_true` | Git called when auto_commit=True |
| G6 | `test_nexus_service_event_commit` | NexusService receives lifecycle events |
| G7 | `test_circuit_breaker_open_state` | Breaker trips after 5 failures, execute fails fast |

**Test performance:** 89 tests, 0.60 seconds execution time. All tests use mocks; no real LLM calls, no real git, no real filesystem side effects.

**Full implementation report:** See `docs/archive/phase-reports/PHASE1-IMPLEMENTATION-REPORT.md`

---

## 2. CURRENT CODEBASE INVENTORY

### Existing Components (NO changes required, reuse as-is)

| Component | File | Purpose | Reuse Strategy |
|-----------|------|---------|----------------|
| `PipelineEngine` | `src/gaia/pipeline/engine.py` | 4-phase cycle | Black-box client; orchestrator calls `initialize()` + `start()` |
| `HookSystem` | `src/gaia/hooks/base.py`, `hooks/registry.py` | BaseHook, HookContext, HookResult, HookRegistry, HookExecutor | Extend with new orchestrator events |
| `PipelineState/Context/Snapshot` | `src/gaia/pipeline/state.py` | State machine, context, snapshot | Reuse `PipelineContext` for objective-to-pipeline mapping |
| `DecisionEngine` | `src/gaia/pipeline/decision_engine.py` | CONTINUE/LOOP_BACK/PAUSE/FAIL | Reuse within pipeline; orchestrator uses its own evaluation |
| `SupervisorAgent` | `src/gaia/quality/supervisor.py` | LLM-backed quality decisions | Rename/refactor to QualitySupervisor in Phase 2 |
| `CircuitBreaker` | `src/gaia/resilience/circuit_breaker.py` | Failure isolation | Wrap git operations in Phase 2 |
| `NexusService` | `src/gaia/state/nexus.py` | Event persistence, Chronicle | Extend with orchestrator event types |
| `SSE Hooks` | `src/gaia/pipeline/sse_hooks.py` | 5 frontend streaming hooks | Extend with orchestrator SSE events |

### Components to CREATE

| Component | Target File | Phase |
|-----------|-------------|-------|
| `Objective` + `ProjectObjectives` | `src/gaia/orchestration/objectives.py` | 1 |
| `ProjectOrchestrator` | `src/gaia/orchestration/engine.py` | 1 |
| `__init__.py` | `src/gaia/orchestration/__init__.py` | 1 |
| `OrchestratorHookEvents` | `src/gaia/orchestration/hooks.py` | 1 |
| `ProjectSupervisor` | `src/gaia/orchestration/supervisors/project.py` | 2 |
| `GitSupervisor` | `src/gaia/orchestration/supervisors/git.py` | 2 |
| `SupervisorRegistry` | `src/gaia/orchestration/supervisors/registry.py` | 2 |
| `supervisors/__init__.py` | `src/gaia/orchestration/supervisors/__init__.py` | 2 |

### Exceptions to ADD

Add to `src/gaia/exceptions.py`:

```python
# =============================================================================
# Orchestration Exceptions (Phase 1-2)
# =============================================================================

class OrchestrationError(GAIAException):
    """Base exception for orchestration-related errors."""
    pass

class ObjectivesLoadError(OrchestrationError):
    """Raised when objectives YAML cannot be loaded or parsed."""
    def __init__(self, path: str, error: str):
        super().__init__(f"Failed to load objectives from {path}: {error}", {"path": path})
        self.path = path

class ObjectivesSaveError(OrchestrationError):
    """Raised when objectives YAML cannot be saved."""
    def __init__(self, path: str, error: str):
        super().__init__(f"Failed to save objectives to {path}: {error}", {"path": path})
        self.path = path

class OrchestratorNotReadyError(OrchestrationError):
    """Raised when orchestrator operations are attempted before initialization."""
    def __init__(self, message: str = "Orchestrator is not ready"):
        super().__init__(message)

class GitOperationError(OrchestrationError):
    """Raised when a git operation fails."""
    def __init__(self, operation: str, error: str):
        super().__init__(f"Git operation '{operation}' failed: {error}", {"operation": operation})
        self.operation = operation
```

---

## 3. PHASE 1: CORE ORCHESTRATION KERNEL

### Work Item 1.1: Objectives Model

**File:** `src/gaia/orchestration/objectives.py`
**Dependencies:** None (pure data layer)
**Estimate:** 1-2 developer hours

**Acceptance Criteria:**
- [ ] YAML round-trip: load, modify, save produces valid YAML
- [ ] Status transitions: TODO->IN_PROGRESS->DONE (valid), TODO->DONE (invalid, rejected)
- [ ] Dependency validation: objective with unsatisfied deps cannot transition to IN_PROGRESS
- [ ] Artifact tracking: artifacts can be added to objectives and persisted
- [ ] Uses dataclasses (matches existing codebase patterns, no Pydantic)

```python
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
"""
Objectives Model for GAIA Project Orchestration.

Provides data classes for project-level objectives tracking:
- Objective: A single unit of work
- ProjectObjectives: Collection of objectives (the YAML roadmap)
- ObjectiveArtifact: Output produced by a pipeline execution

The objectives YAML file serves as the project roadmap. The ProjectOrchestrator
reads and updates this file after each pipeline execution, creating an automatic
dependency cascade similar to Excel formulas recalculating when cells change.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


class ObjectiveStatus(Enum):
    """Valid lifecycle states for an objective."""
    TODO = auto()
    IN_PROGRESS = auto()
    DONE = auto()
    BLOCKED = auto()
    SKIPPED = auto()

    def is_terminal(self) -> bool:
        return self in {ObjectiveStatus.DONE, ObjectiveStatus.SKIPPED}

    def can_transition_to(self, new_status: "ObjectiveStatus") -> bool:
        transitions = {
            ObjectiveStatus.TODO: {ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.SKIPPED},
            ObjectiveStatus.IN_PROGRESS: {ObjectiveStatus.DONE, ObjectiveStatus.BLOCKED},
            ObjectiveStatus.BLOCKED: {ObjectiveStatus.TODO, ObjectiveStatus.SKIPPED},
            ObjectiveStatus.DONE: set(),
            ObjectiveStatus.SKIPPED: set(),
        }
        return new_status in transitions.get(self, set())


@dataclass
class ObjectiveArtifact:
    """An artifact produced by a pipeline execution for this objective."""
    name: str
    pipeline_id: str
    path: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Objective:
    """A single unit of work in the project."""
    id: str
    title: str
    description: str = ""
    status: ObjectiveStatus = ObjectiveStatus.TODO
    priority: int = 0
    dependencies: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    artifacts: List[ObjectiveArtifact] = field(default_factory=list)
    pipeline_id: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(self, new_status: ObjectiveStatus, error_message: Optional[str] = None) -> bool:
        if not self.status.can_transition_to(new_status):
            raise ObjectiveTransitionError(
                f"Cannot transition {self.id} from {self.status.name} to {new_status.name}"
            )
        self.status = new_status
        self.error_message = error_message
        self.updated_at = datetime.now(timezone.utc)
        return True

    def add_artifact(self, artifact: ObjectiveArtifact) -> None:
        self.artifacts.append(artifact)
        self.updated_at = datetime.now(timezone.utc)

    def is_blocked_by_dependencies(self, all_objectives: Dict[str, "Objective"]) -> List[str]:
        blocked = []
        for dep_id in self.dependencies:
            dep = all_objectives.get(dep_id)
            if dep is None or not dep.status.is_terminal():
                blocked.append(dep_id)
        return blocked

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "status": self.status.name, "priority": self.priority,
            "dependencies": self.dependencies,
            "acceptance_criteria": self.acceptance_criteria,
            "artifacts": [{"name": a.name, "pipeline_id": a.pipeline_id, "path": a.path,
                           "created_at": a.created_at.isoformat(), "metadata": a.metadata}
                          for a in self.artifacts],
            "pipeline_id": self.pipeline_id, "error_message": self.error_message,
            "retry_count": self.retry_count, "max_retries": self.max_retries,
            "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Objective":
        artifacts = [
            ObjectiveArtifact(name=a["name"], pipeline_id=a["pipeline_id"], path=a["path"],
                created_at=datetime.fromisoformat(a["created_at"]) if "created_at" in a else datetime.now(timezone.utc),
                metadata=a.get("metadata", {}))
            for a in data.get("artifacts", [])
        ]
        return cls(
            id=data["id"], title=data["title"], description=data.get("description", ""),
            status=ObjectiveStatus[data.get("status", "TODO")], priority=data.get("priority", 0),
            dependencies=data.get("dependencies", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            artifacts=artifacts, pipeline_id=data.get("pipeline_id"),
            error_message=data.get("error_message"),
            retry_count=data.get("retry_count", 0), max_retries=data.get("max_retries", 3),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.now(timezone.utc),
        )


@dataclass
class ProjectObjectives:
    """Top-level model: the objectives YAML roadmap."""
    project_name: str
    version: str = "1.0"
    objectives: Dict[str, Objective] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def objective_list(self) -> List[Objective]:
        return sorted(self.objectives.values(), key=lambda o: (o.priority, o.created_at))

    @property
    def next_ready_objective(self) -> Optional[Objective]:
        """Highest-priority objective that is TODO with all deps satisfied."""
        candidates = [obj for obj in self.objective_list
                      if obj.status == ObjectiveStatus.TODO
                      and len(obj.is_blocked_by_dependencies(self.objectives)) == 0]
        return candidates[0] if candidates else None

    @property
    def is_complete(self) -> bool:
        return all(o.status.is_terminal() for o in self.objectives.values())

    @property
    def is_blocked(self) -> bool:
        non_terminal = [o for o in self.objectives.values() if not o.status.is_terminal()]
        if not non_terminal:
            return False
        return all(len(o.is_blocked_by_dependencies(self.objectives)) > 0 for o in non_terminal)

    def add_objective(self, objective: Objective) -> None:
        self.objectives[objective.id] = objective
        self.updated_at = datetime.now(timezone.utc)

    def get_summary(self) -> Dict[str, Any]:
        counts = {s.name: 0 for s in ObjectiveStatus}
        for obj in self.objectives.values():
            counts[obj.status.name] += 1
        total = len(self.objectives)
        return {"project_name": self.project_name, "total": total, **counts,
                "complete_pct": round(counts["DONE"] / total * 100, 1) if total > 0 else 0}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": {"name": self.project_name, "version": self.version,
                        "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat()},
            "objectives": [obj.to_dict() for obj in self.objective_list],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    @classmethod
    def load(cls, path: Path) -> "ProjectObjectives":
        with open(path) as f:
            data = yaml.safe_load(f)
        project_data = data.get("project", {})
        objectives = {}
        for obj_data in data.get("objectives", []):
            obj = Objective.from_dict(obj_data)
            objectives[obj.id] = obj
        return cls(
            project_name=project_data.get("name", "Unknown"),
            version=project_data.get("version", "1.0"),
            objectives=objectives,
            created_at=datetime.fromisoformat(project_data["created_at"]) if "created_at" in project_data else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(project_data["updated_at"]) if "updated_at" in project_data else datetime.now(timezone.utc),
        )


class ObjectiveTransitionError(Exception):
    """Raised when an invalid objective status transition is attempted."""
    pass


class ObjectiveDependencyError(Exception):
    """Raised when an objective dependency cannot be resolved."""
    pass
```

#### Example YAML Output

```yaml
project:
  name: "My Project"
  version: "1.0"
  created_at: "2026-04-26T12:00:00+00:00"
  updated_at: "2026-04-26T14:30:00+00:00"
objectives:
  - id: "OBJ-001"
    title: "Implement REST API"
    description: "Build OpenAPI-compliant REST endpoints for user management"
    status: "DONE"
    priority: 1
    dependencies: []
    acceptance_criteria:
      - "All CRUD endpoints tested"
      - "OpenAPI spec generated"
    artifacts:
      - name: "api_code"
        pipeline_id: "pipe-abc123"
        path: "src/api/users.py"
        created_at: "2026-04-26T14:00:00+00:00"
    pipeline_id: "pipe-abc123"
    created_at: "2026-04-26T12:00:00+00:00"
    updated_at: "2026-04-26T14:30:00+00:00"
```

---

### Work Item 1.2: ProjectOrchestrator Core

**File:** `src/gaia/orchestration/engine.py`
**Dependencies:** 1.1 (Objectives Model)
**Estimate:** 4-6 developer hours

**Acceptance Criteria:**
- [ ] Orchestrator loads objectives YAML and identifies next ready objective
- [ ] Orchestrator dispatches a PipelineEngine for each objective
- [ ] Orchestrator waits for pipeline completion and reads result
- [ ] Orchestrator evaluates pipeline outcome against acceptance criteria
- [ ] Orchestrator updates objective status and saves YAML
- [ ] Orchestrator runs until all objectives complete or unrecoverable failure
- [ ] Orchestrator commits updated objectives to git
- [ ] Orchestrator emits chronicle events via NexusService
- [ ] Orchestrator can be paused/resumed

```python
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
"""
Project Orchestrator - Long-running loop above PipelineEngine.

The ProjectOrchestrator manages a multi-objective project by:
1. Loading an objectives YAML roadmap
2. Iterating through objectives in priority order
3. Dispatching a PipelineEngine for each objective
4. Evaluating pipeline outcomes against acceptance criteria
5. Updating objective status and persisting to YAML
6. Committing changes to git
7. Repeating until all objectives are done or unrecoverably blocked

This treats PipelineEngine as a BLACK BOX -- it only calls initialize() and start().
It does NOT modify PipelineEngine internals.
"""

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING
from uuid import uuid4

from gaia.orchestration.objectives import (
    Objective, ObjectiveArtifact, ObjectiveStatus, ProjectObjectives,
)
from gaia.hooks.registry import HookExecutor, HookRegistry
from gaia.utils.logging import get_logger

if TYPE_CHECKING:
    from gaia.pipeline.engine import PipelineEngine
    from gaia.pipeline.state import PipelineSnapshot
    from gaia.state.nexus import NexusService

logger = get_logger(__name__)


class OrchestratorState:
    """Orchestrator lifecycle states."""
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class OrchestratorConfig:
    """Configuration for ProjectOrchestrator."""
    objectives_path: Path
    max_objective_retries: int = 3
    auto_commit: bool = True
    enable_hooks: bool = True
    git_user_name: str = "GAIA Orchestrator"
    git_user_email: str = "orchestrator@gai"
    pipeline_factory: Optional[Callable] = None


class OrchestratorError(Exception):
    """Raised when the orchestrator encounters an unrecoverable error."""
    pass


class ProjectOrchestrator:
    """
    Long-running orchestration loop above PipelineEngine.

    Lifecycle:
    1. Load objectives YAML
    2. While objectives remain:
       a. Find next ready objective (TODO + deps satisfied)
       b. Mark it IN_PROGRESS
       c. Dispatch PipelineEngine with objective as user_goal
       d. Wait for pipeline completion
       e. Evaluate pipeline outcome against acceptance criteria
       f. Update objective status (DONE/BLOCKED/retry)
       g. Save updated objectives YAML
       h. Git commit
    3. Report final status

    Example:
        >>> config = OrchestratorConfig(objectives_path=Path("objectives.yaml"))
        >>> orchestrator = ProjectOrchestrator(config)
        >>> await orchestrator.initialize()
        >>> result = await orchestrator.run()
        >>> print(f"Completed: {result['summary']}")
    """

    def __init__(self, config: OrchestratorConfig):
        self._config = config
        self._state = OrchestratorState.INITIALIZING
        self._objectives: Optional[ProjectObjectives] = None
        self._hook_registry: Optional[HookRegistry] = None
        self._hook_executor: Optional[HookExecutor] = None
        self._nexus: Optional["NexusService"] = None
        self._current_objective: Optional[Objective] = None
        self._pause_event: Optional[asyncio.Event] = None
        self._running = False
        self._metrics: Dict[str, Any] = {
            "objectives_dispatched": 0, "objectives_completed": 0,
            "objectives_failed": 0, "pipelines_executed": 0,
            "git_commits": 0, "start_time": None, "end_time": None,
        }
        logger.info("ProjectOrchestrator created", extra={"objectives_path": str(config.objectives_path)})

    async def initialize(self) -> None:
        """Load objectives, set up hooks and Nexus."""
        logger.info("Initializing ProjectOrchestrator")
        self._state = OrchestratorState.INITIALIZING

        if not self._config.objectives_path.exists():
            raise FileNotFoundError(f"Objectives file not found: {self._config.objectives_path}")

        self._objectives = ProjectObjectives.load(self._config.objectives_path)
        logger.info(f"Loaded objectives: {self._objectives.get_summary()}")

        if self._config.enable_hooks:
            self._hook_registry = HookRegistry()
            self._hook_executor = HookExecutor(self._hook_registry)

        from gaia.state.nexus import NexusService
        self._nexus = NexusService.get_instance()

        self._pause_event = asyncio.Event()
        self._pause_event.set()

        self._commit_event("orchestrator_init", {
            "objectives_path": str(self._config.objectives_path),
            "objective_count": len(self._objectives.objectives),
        })

        self._state = OrchestratorState.READY
        logger.info("ProjectOrchestrator initialized and ready")

    async def run(self) -> Dict[str, Any]:
        """Execute the orchestration loop until completion or failure."""
        if self._state != OrchestratorState.READY:
            raise OrchestratorError(f"Cannot run: state={self._state}. Call initialize() first.")

        self._running = True
        self._metrics["start_time"] = datetime.now(timezone.utc).isoformat()
        self._state = OrchestratorState.RUNNING

        logger.info("Starting orchestration loop")
        self._commit_event("orchestrator_start", self._objectives.get_summary())

        try:
            await self._orchestration_loop()
        except asyncio.CancelledError:
            logger.info("Orchestration loop cancelled")
            self._state = OrchestratorState.PAUSED
        except Exception as e:
            logger.exception(f"Orchestration loop failed: {e}")
            self._state = OrchestratorState.FAILED
            self._commit_event("orchestrator_failed", {"error": str(e)})
        finally:
            self._running = False
            self._metrics["end_time"] = datetime.now(timezone.utc).isoformat()

        return self._get_final_result()

    async def pause(self) -> None:
        if self._pause_event:
            self._pause_event.clear()

    async def resume(self) -> None:
        if self._pause_event:
            self._pause_event.set()

    async def shutdown(self) -> None:
        self._running = False
        if self._pause_event:
            self._pause_event.set()
        logger.info("ProjectOrchestrator shutdown")

    # --- Internal Methods ---

    async def _orchestration_loop(self) -> None:
        loop_count = 0
        max_loops = len(self._objectives.objectives) * 10

        while loop_count < max_loops:
            if self._objectives.is_complete:
                logger.info("All objectives completed successfully")
                self._state = OrchestratorState.COMPLETE
                self._commit_event("orchestrator_complete", self._objectives.get_summary())
                return

            if self._objectives.is_blocked:
                logger.warning("All remaining objectives are blocked")
                self._state = OrchestratorState.FAILED
                self._commit_event("orchestrator_blocked", self._objectives.get_summary())
                return

            if self._pause_event:
                await self._pause_event.wait()

            next_obj = self._objectives.next_ready_objective
            if next_obj is None:
                logger.warning("No ready objective found but project not complete")
                self._state = OrchestratorState.FAILED
                return

            await self._dispatch_objective(next_obj)
            self._objectives.save(self._config.objectives_path)

            if self._config.auto_commit:
                await self._git_commit(f"orchestrator: update objectives for {next_obj.id}")

            loop_count += 1

        raise OrchestratorError(f"Orchestration loop exceeded maximum iterations ({max_loops})")

    async def _dispatch_objective(self, objective: Objective) -> None:
        """Dispatch a PipelineEngine for the given objective."""
        self._current_objective = objective
        self._metrics["objectives_dispatched"] += 1

        try:
            objective.transition_to(ObjectiveStatus.IN_PROGRESS)
        except Exception:
            logger.warning(f"Objective {objective.id} cannot transition to IN_PROGRESS (status={objective.status.name})")
            return

        await self._emit_event("OBJECTIVE_START", {"objective_id": objective.id, "title": objective.title})
        self._commit_event("orchestrator_objective_dispatched", {"objective_id": objective.id, "title": objective.title})

        logger.info(f"Dispatching pipeline for objective {objective.id}: {objective.title}")

        pipeline = self._create_pipeline(objective)
        try:
            snapshot = await self._execute_pipeline(pipeline, objective)
            self._metrics["pipelines_executed"] += 1
            outcome = self._evaluate_outcome(objective, snapshot)
            await self._update_objective(objective, outcome, snapshot)
        except Exception as e:
            logger.exception(f"Pipeline execution failed for {objective.id}: {e}")
            await self._handle_objective_failure(objective, str(e))

        self._current_objective = None

    def _create_pipeline(self, objective: Objective) -> "PipelineEngine":
        if self._config.pipeline_factory:
            return self._config.pipeline_factory(objective)
        from gaia.pipeline.engine import PipelineEngine
        return PipelineEngine(enable_logging=False, skip_lemonade=True)

    async def _execute_pipeline(self, pipeline: "PipelineEngine", objective: Objective) -> "PipelineSnapshot":
        from gaia.pipeline.state import PipelineContext

        pipeline_id = f"orch-{objective.id.lower()}-{uuid4().hex[:8]}"

        context = PipelineContext(
            pipeline_id=pipeline_id,
            user_goal=objective.description or objective.title,
            metadata={
                "objective_id": objective.id,
                "objective_title": objective.title,
                "acceptance_criteria": objective.acceptance_criteria,
            },
            max_iterations=10,
            quality_threshold=0.85,
        )

        config = {"template": "generic", "enable_hooks": True}
        await pipeline.initialize(context, config)
        return await pipeline.start()

    def _evaluate_outcome(self, objective: Objective, snapshot: "PipelineSnapshot") -> Dict[str, Any]:
        from gaia.pipeline.state import PipelineState

        quality_score = snapshot.quality_score or 0.0
        artifacts = snapshot.artifacts or {}

        met_criteria = []
        unmet_criteria = []
        for criterion in objective.acceptance_criteria:
            if self._check_criterion(criterion, artifacts, quality_score):
                met_criteria.append(criterion)
            else:
                unmet_criteria.append(criterion)

        success = (
            snapshot.state == PipelineState.COMPLETED
            and len(unmet_criteria) == 0
            and quality_score >= 0.85
        )

        reason = ""
        if snapshot.state != PipelineState.COMPLETED:
            reason = f"Pipeline state: {snapshot.state.name}"
        elif unmet_criteria:
            reason = f"Unmet criteria: {unmet_criteria}"
        elif quality_score < 0.85:
            reason = f"Quality score {quality_score:.2f} below minimum 0.85"

        return {
            "success": success, "met_criteria": met_criteria, "unmet_criteria": unmet_criteria,
            "artifacts": artifacts, "quality_score": quality_score, "reason": reason,
            "pipeline_state": snapshot.state.name,
        }

    def _check_criterion(self, criterion: str, artifacts: Dict[str, Any], quality_score: float) -> bool:
        """Keyword matching against artifacts. Simple but effective."""
        criterion_lower = criterion.lower()
        for name, value in artifacts.items():
            if any(word in name.lower() for word in criterion_lower.split() if len(word) > 3):
                return True
            text = value.lower() if isinstance(value, str) else str(value).lower()
            if any(word in text for word in criterion_lower.split() if len(word) > 3):
                return True
        if ("test" in criterion_lower or "error" in criterion_lower) and quality_score >= 0.85:
            return True
        return False

    async def _update_objective(self, objective: Objective, outcome: Dict[str, Any], snapshot: "PipelineSnapshot") -> None:
        if outcome["success"]:
            objective.transition_to(ObjectiveStatus.DONE)
            self._metrics["objectives_completed"] += 1

            for artifact_name in (snapshot.artifacts or {}).keys():
                objective.add_artifact(ObjectiveArtifact(
                    name=artifact_name,
                    pipeline_id=snapshot.artifacts.get("pipeline_id", "unknown"),
                    path=f"artifacts/{objective.id}/{artifact_name}",
                    metadata={"quality_score": outcome["quality_score"]},
                ))

            objective.pipeline_id = snapshot.artifacts.get("pipeline_id")

            await self._emit_event("OBJECTIVE_COMPLETE", {
                "objective_id": objective.id,
                "met_criteria": outcome["met_criteria"],
                "quality_score": outcome["quality_score"],
            })
            self._commit_event("orchestrator_objective_completed", {
                "objective_id": objective.id, "quality_score": outcome["quality_score"],
            })
            logger.info(f"Objective {objective.id} completed: {objective.title}")
        else:
            objective.retry_count += 1
            if objective.retry_count < objective.max_retries:
                objective.transition_to(ObjectiveStatus.TODO)
                logger.warning(f"Objective {objective.id} failed, retry {objective.retry_count}/{objective.max_retries}: {outcome['reason']}")
                self._commit_event("orchestrator_objective_retry", {
                    "objective_id": objective.id, "retry_count": objective.retry_count, "reason": outcome["reason"],
                })
            else:
                objective.transition_to(ObjectiveStatus.BLOCKED,
                    error_message=f"Max retries ({objective.max_retries}) exceeded. Last: {outcome['reason']}")
                self._metrics["objectives_failed"] += 1
                await self._emit_event("OBJECTIVE_FAILED", {"objective_id": objective.id, "reason": outcome["reason"]})
                self._commit_event("orchestrator_objective_blocked", {"objective_id": objective.id, "reason": outcome["reason"]})
                logger.error(f"Objective {objective.id} blocked: {outcome['reason']}")

    async def _handle_objective_failure(self, objective: Objective, error: str) -> None:
        objective.retry_count += 1
        if objective.retry_count < objective.max_retries:
            try:
                objective.transition_to(ObjectiveStatus.TODO)
            except Exception:
                pass
            logger.warning(f"Objective {objective.id} pipeline error, retrying: {error}")
        else:
            try:
                objective.transition_to(ObjectiveStatus.BLOCKED, error_message=f"Pipeline error after {objective.retry_count} retries: {error}")
            except Exception:
                pass
            self._metrics["objectives_failed"] += 1
            logger.error(f"Objective {objective.id} permanently failed: {error}")

    async def _git_commit(self, message: str) -> bool:
        try:
            subprocess.run(["git", "add", str(self._config.objectives_path)], check=True, capture_output=True, timeout=30)
            subprocess.run(["git", "commit", "-m", message, f"--author={self._config.git_user_name} <{self._config.git_user_email}>"],
                check=True, capture_output=True, timeout=30)
            self._metrics["git_commits"] += 1
            logger.info(f"Git commit: {message}")
            return True
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git commit failed: {e.stderr.decode().strip()}")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("Git commit timed out")
            return False

    async def _emit_event(self, event_name: str, data: Dict[str, Any]) -> None:
        if self._hook_executor:
            from gaia.hooks.base import HookContext
            context = HookContext(event=event_name, pipeline_id="orchestrator", data=data)
            await self._hook_executor.execute_hooks(event_name, context)

    def _commit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._nexus:
            self._nexus.commit(agent_id="ProjectOrchestrator", event_type=event_type, payload=payload, phase=None, loop_id=None)

    def _get_final_result(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "summary": self._objectives.get_summary() if self._objectives else {},
            "metrics": self._metrics,
        }
```

---

### Work Item 1.3: Orchestrator Package Init

**File:** `src/gaia/orchestration/__init__.py`
**Estimate:** 10 minutes

```python
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
"""
GAIA Project Orchestration - Multi-objective pipeline coordination.

The orchestration layer manages a multi-objective project by:
1. Loading an objectives YAML roadmap
2. Dispatching PipelineEngine for each objective
3. Evaluating outcomes and updating the roadmap
4. Committing changes to git
"""

from gaia.orchestration.objectives import (
    Objective, ObjectiveArtifact, ObjectiveStatus, ProjectObjectives,
    ObjectiveTransitionError, ObjectiveDependencyError,
)
from gaia.orchestration.engine import (
    ProjectOrchestrator, OrchestratorConfig, OrchestratorState, OrchestratorError,
)

__all__ = [
    "Objective", "ObjectiveArtifact", "ObjectiveStatus", "ProjectObjectives",
    "ObjectiveTransitionError", "ObjectiveDependencyError",
    "ProjectOrchestrator", "OrchestratorConfig", "OrchestratorState", "OrchestratorError",
]
```

---

### Work Item 1.4: Orchestrator Hook Events

**File:** `src/gaia/orchestration/hooks.py`
**Dependencies:** 1.2 (ProjectOrchestrator)
**Estimate:** 2-3 developer hours

**Acceptance Criteria:**
- [ ] New hook events: OBJECTIVE_START, OBJECTIVE_COMPLETE, OBJECTIVE_FAILED, OBJECTIVE_BLOCKED, PIPELINE_DISPATCHED, ORCHESTRATOR_START, ORCHESTRATOR_COMPLETE
- [ ] Default hook group creation function (matches SSE hook pattern)

```python
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
"""
Orchestrator Hook Events and Default Hooks.

Extends the hook system with orchestrator-specific events.
Follows the same pattern as SSE hooks: extend BaseHook, register via HookRegistry.
"""

import logging
from typing import Any, Dict, List, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult

logger = logging.getLogger(__name__)

# Orchestrator event names (strings, matching existing hook pattern)
ORCHESTRATOR_START = "ORCHESTRATOR_START"
ORCHESTRATOR_COMPLETE = "ORCHESTRATOR_COMPLETE"
ORCHESTRATOR_FAILED = "ORCHESTRATOR_FAILED"
OBJECTIVE_START = "OBJECTIVE_START"
OBJECTIVE_COMPLETE = "OBJECTIVE_COMPLETE"
OBJECTIVE_FAILED = "OBJECTIVE_FAILED"
OBJECTIVE_BLOCKED = "OBJECTIVE_BLOCKED"
PIPELINE_DISPATCHED = "PIPELINE_DISPATCHED"


class OrchestratorLogHook(BaseHook):
    """Log all orchestrator events for debugging/auditing."""
    name = "orchestrator_log"
    event = "*"
    priority = HookPriority.LOW
    blocking = False
    description = "Log all orchestrator events"

    async def execute(self, context: HookContext) -> HookResult:
        if context.event in (ORCHESTRATOR_START, ORCHESTRATOR_COMPLETE, ORCHESTRATOR_FAILED,
                             OBJECTIVE_START, OBJECTIVE_COMPLETE, OBJECTIVE_FAILED, OBJECTIVE_BLOCKED):
            logger.info(f"Orchestrator event: {context.event}", extra={"event": context.event, "data": context.data})
        return HookResult(success=True)


def create_orchestrator_hook_group(enable_logging: bool = True) -> List[BaseHook]:
    """Create a list of orchestrator hooks."""
    hooks = []
    if enable_logging:
        hooks.append(OrchestratorLogHook())
    return hooks
```

---

### Work Item 1.5: Phase 1 Tests

**Files:**
- `tests/unit/orchestration/__init__.py` (empty)
- `tests/unit/orchestration/test_objectives.py`
- `tests/unit/orchestration/test_orchestrator.py`
- `tests/integration/orchestration/__init__.py` (empty)
- `tests/integration/orchestration/test_orchestrator_pipeline.py`

**Estimate:** 3-4 developer hours

#### 1.5a: `tests/unit/orchestration/test_objectives.py`

```python
"""Unit tests for objectives model."""
import tempfile
from pathlib import Path
import pytest
import yaml

from gaia.orchestration.objectives import (
    Objective, ObjectiveArtifact, ObjectiveStatus, ProjectObjectives,
    ObjectiveTransitionError,
)


class TestObjectiveStatus:
    def test_valid_transitions(self):
        assert ObjectiveStatus.TODO.can_transition_to(ObjectiveStatus.IN_PROGRESS)
        assert ObjectiveStatus.IN_PROGRESS.can_transition_to(ObjectiveStatus.DONE)
        assert ObjectiveStatus.IN_PROGRESS.can_transition_to(ObjectiveStatus.BLOCKED)
        assert ObjectiveStatus.BLOCKED.can_transition_to(ObjectiveStatus.TODO)

    def test_invalid_transitions(self):
        assert not ObjectiveStatus.TODO.can_transition_to(ObjectiveStatus.DONE)
        assert not ObjectiveStatus.DONE.can_transition_to(ObjectiveStatus.TODO)
        assert not ObjectiveStatus.IN_PROGRESS.can_transition_to(ObjectiveStatus.TODO)

    def test_is_terminal(self):
        assert ObjectiveStatus.DONE.is_terminal()
        assert ObjectiveStatus.SKIPPED.is_terminal()
        assert not ObjectiveStatus.TODO.is_terminal()
        assert not ObjectiveStatus.IN_PROGRESS.is_terminal()


class TestObjective:
    def test_create_objective(self):
        obj = Objective(id="OBJ-001", title="Test", description="A test objective")
        assert obj.status == ObjectiveStatus.TODO
        assert obj.priority == 0
        assert obj.retry_count == 0

    def test_transition_to_in_progress(self):
        obj = Objective(id="OBJ-001", title="Test")
        obj.transition_to(ObjectiveStatus.IN_PROGRESS)
        assert obj.status == ObjectiveStatus.IN_PROGRESS

    def test_transition_to_done(self):
        obj = Objective(id="OBJ-001", title="Test")
        obj.transition_to(ObjectiveStatus.IN_PROGRESS)
        obj.transition_to(ObjectiveStatus.DONE)
        assert obj.status == ObjectiveStatus.DONE

    def test_invalid_transition_rejected(self):
        obj = Objective(id="OBJ-001", title="Test")
        with pytest.raises(ObjectiveTransitionError):
            obj.transition_to(ObjectiveStatus.DONE)

    def test_blocked_by_dependencies(self):
        objs = {
            "OBJ-001": Objective(id="OBJ-001", title="First"),
            "OBJ-002": Objective(id="OBJ-002", title="Second", dependencies=["OBJ-001"]),
        }
        blocked = objs["OBJ-002"].is_blocked_by_dependencies(objs)
        assert "OBJ-001" in blocked

    def test_deps_satisfied(self):
        objs = {
            "OBJ-001": Objective(id="OBJ-001", title="First"),
            "OBJ-002": Objective(id="OBJ-002", title="Second", dependencies=["OBJ-001"]),
        }
        objs["OBJ-001"].transition_to(ObjectiveStatus.IN_PROGRESS)
        objs["OBJ-001"].transition_to(ObjectiveStatus.DONE)
        blocked = objs["OBJ-002"].is_blocked_by_dependencies(objs)
        assert len(blocked) == 0

    def test_add_artifact(self):
        obj = Objective(id="OBJ-001", title="Test")
        artifact = ObjectiveArtifact(name="code", pipeline_id="pipe-1", path="src/main.py")
        obj.add_artifact(artifact)
        assert len(obj.artifacts) == 1
        assert obj.artifacts[0].name == "code"

    def test_to_dict_round_trip(self):
        obj = Objective(id="OBJ-001", title="Test", description="Desc", priority=1, acceptance_criteria=["c1", "c2"])
        data = obj.to_dict()
        restored = Objective.from_dict(data)
        assert restored.id == obj.id
        assert restored.title == obj.title
        assert restored.acceptance_criteria == obj.acceptance_criteria


class TestProjectObjectives:
    def test_create_empty(self):
        proj = ProjectObjectives(project_name="Test")
        assert len(proj.objectives) == 0
        assert proj.next_ready_objective is None

    def test_add_objective(self):
        proj = ProjectObjectives(project_name="Test")
        proj.add_objective(Objective(id="OBJ-001", title="First"))
        assert len(proj.objectives) == 1

    def test_next_ready_returns_highest_priority(self):
        proj = ProjectObjectives(project_name="Test")
        proj.add_objective(Objective(id="OBJ-002", title="Second", priority=2))
        proj.add_objective(Objective(id="OBJ-001", title="First", priority=1))
        ready = proj.next_ready_objective
        assert ready.id == "OBJ-001"

    def test_next_ready_skips_blocked(self):
        proj = ProjectObjectives(project_name="Test")
        proj.add_objective(Objective(id="OBJ-001", title="First"))
        proj.add_objective(Objective(id="OBJ-002", title="Second", dependencies=["OBJ-001"]))
        ready = proj.next_ready_objective
        assert ready.id == "OBJ-001"  # OBJ-002 blocked by OBJ-001

    def test_next_ready_after_completion(self):
        proj = ProjectObjectives(project_name="Test")
        proj.add_objective(Objective(id="OBJ-001", title="First"))
        proj.add_objective(Objective(id="OBJ-002", title="Second", dependencies=["OBJ-001"]))
        proj.objectives["OBJ-001"].transition_to(ObjectiveStatus.IN_PROGRESS)
        proj.objectives["OBJ-001"].transition_to(ObjectiveStatus.DONE)
        ready = proj.next_ready_objective
        assert ready.id == "OBJ-002"  # OBJ-002 is now ready

    def test_is_complete(self):
        proj = ProjectObjectives(project_name="Test")
        proj.add_objective(Objective(id="OBJ-001", title="First"))
        assert not proj.is_complete
        proj.objectives["OBJ-001"].transition_to(ObjectiveStatus.IN_PROGRESS)
        proj.objectives["OBJ-001"].transition_to(ObjectiveStatus.DONE)
        assert proj.is_complete

    def test_yaml_round_trip(self):
        proj = ProjectObjectives(project_name="Test")
        proj.add_objective(Objective(id="OBJ-001", title="First", description="Do first thing", acceptance_criteria=["c1", "c2"]))
        proj.add_objective(Objective(id="OBJ-002", title="Second", dependencies=["OBJ-001"]))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            path = Path(f.name)

        try:
            proj.save(path)
            assert path.exists()
            with open(path) as fh:
                data = yaml.safe_load(fh)
            assert "project" in data
            assert "objectives" in data
            assert len(data["objectives"]) == 2

            loaded = ProjectObjectives.load(path)
            assert loaded.project_name == proj.project_name
            assert len(loaded.objectives) == len(proj.objectives)
            assert loaded.objectives["OBJ-001"].acceptance_criteria == ["c1", "c2"]
        finally:
            path.unlink(missing_ok=True)

    def test_get_summary(self):
        proj = ProjectObjectives(project_name="Test")
        proj.add_objective(Objective(id="OBJ-001", title="First"))
        proj.add_objective(Objective(id="OBJ-002", title="Second"))
        proj.objectives["OBJ-001"].transition_to(ObjectiveStatus.IN_PROGRESS)
        proj.objectives["OBJ-001"].transition_to(ObjectiveStatus.DONE)
        summary = proj.get_summary()
        assert summary["total"] == 2
        assert summary["DONE"] == 1
        assert summary["TODO"] == 1
        assert summary["complete_pct"] == 50.0
```

#### 1.5b: `tests/unit/orchestration/test_orchestrator.py`

```python
"""Unit tests for ProjectOrchestrator."""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gaia.orchestration.engine import ProjectOrchestrator, OrchestratorConfig, OrchestratorState
from gaia.orchestration.objectives import Objective, ObjectiveStatus, ProjectObjectives
from gaia.pipeline.state import PipelineState, PipelineSnapshot


@pytest.fixture
def sample_objectives_file():
    proj = ProjectObjectives(project_name="Test Project")
    proj.add_objective(Objective(id="OBJ-001", title="Build API", description="Implement REST API endpoints",
                                  acceptance_criteria=["Endpoints respond", "Error handling"], priority=1))
    proj.add_objective(Objective(id="OBJ-002", title="Add Auth", description="Implement authentication",
                                  dependencies=["OBJ-001"], acceptance_criteria=["JWT works"], priority=2))
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    proj.save(Path(tmp.name))
    tmp.close()
    yield Path(tmp.name)
    tmp.unlink(missing_ok=True)


@pytest.fixture
def orchestrator_config(sample_objectives_file):
    return OrchestratorConfig(objectives_path=sample_objectives_file, max_objective_retries=2,
                              auto_commit=False, enable_hooks=False)


def _make_snapshot(state=PipelineState.COMPLETED, quality=0.92):
    snap = PipelineSnapshot(state=state)
    snap.quality_score = quality
    snap.artifacts = {"api_code": "# API code here", "test_results": "All tests passed", "pipeline_id": "test-pipe-123"}
    return snap


def _make_mock_pipeline(snapshot=None):
    pipeline = MagicMock()
    pipeline.initialize = AsyncMock()
    pipeline.start = AsyncMock(return_value=snapshot or _make_snapshot())
    return pipeline


class TestProjectOrchestratorInit:
    @pytest.mark.asyncio
    async def test_initialize_loads_objectives(self, orchestrator_config):
        with patch("gaia.orchestration.engine.NexusService"):
            orch = ProjectOrchestrator(orchestrator_config)
            await orch.initialize()
            assert orch._state == OrchestratorState.READY
            assert orch._objectives is not None
            assert len(orch._objectives.objectives) == 2

    @pytest.mark.asyncio
    async def test_initialize_fails_on_missing_file(self):
        config = OrchestratorConfig(objectives_path=Path("/nonexistent/file.yaml"))
        orch = ProjectOrchestrator(config)
        with pytest.raises(FileNotFoundError):
            await orch.initialize()


class TestProjectOrchestratorDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_updates_objective_on_success(self, orchestrator_config):
        mock_pipeline = _make_mock_pipeline()
        with patch("gaia.orchestration.engine.NexusService"):
            orch = ProjectOrchestrator(orchestrator_config)
            await orch.initialize()
            orch._config.pipeline_factory = lambda obj: mock_pipeline
            obj = orch._objectives.next_ready_objective
            assert obj.status == ObjectiveStatus.TODO
            await orch._dispatch_objective(obj)
            assert obj.status == ObjectiveStatus.DONE

    @pytest.mark.asyncio
    async def test_dispatch_retries_on_failure(self, orchestrator_config):
        fail_snapshot = _make_snapshot(quality=0.50)
        mock_pipeline = _make_mock_pipeline(fail_snapshot)
        with patch("gaia.orchestration.engine.NexusService"):
            orch = ProjectOrchestrator(orchestrator_config)
            await orch.initialize()
            orch._config.pipeline_factory = lambda obj: mock_pipeline
            obj = orch._objectives.next_ready_objective
            await orch._dispatch_objective(obj)
            assert obj.status == ObjectiveStatus.TODO  # Back for retry
            assert obj.retry_count == 1


class TestProjectOrchestratorLoop:
    @pytest.mark.asyncio
    async def test_run_completes_single_objective(self, orchestrator_config):
        with patch("gaia.orchestration.engine.NexusService"):
            orch = ProjectOrchestrator(orchestrator_config)
            await orch.initialize()
            orch._config.pipeline_factory = lambda obj: _make_mock_pipeline()
            result = await orch.run()
            assert result["state"] == OrchestratorState.COMPLETE
            assert orch._objectives.objectives["OBJ-001"].status == ObjectiveStatus.DONE

    @pytest.mark.asyncio
    async def test_run_processes_dependencies(self, orchestrator_config):
        with patch("gaia.orchestration.engine.NexusService"):
            orch = ProjectOrchestrator(orchestrator_config)
            await orch.initialize()
            orch._config.pipeline_factory = lambda obj: _make_mock_pipeline()
            result = await orch.run()
            assert result["state"] == OrchestratorState.COMPLETE
            assert orch._objectives.objectives["OBJ-001"].status == ObjectiveStatus.DONE
            assert orch._objectives.objectives["OBJ-002"].status == ObjectiveStatus.DONE
```

---

### Phase 1 Dependency Graph

```
1.1 Objectives Model (objectives.py)
  |
  +--> 1.2 Orchestrator Core (engine.py)
  |      |
  |      +--> 1.3 __init__.py
  |      |
  |      +--> 1.4 Hook Events (hooks.py)
  |
  +--> 1.5 Unit Tests (tests/unit/orchestration/)
```

---

## 4. PHASE 2: SUPERVISOR HIERARCHY (Detailed)

### Work Item 2.1: ProjectSupervisor

**File:** `src/gaia/orchestration/supervisors/project.py`
**Dependencies:** 1.1 (Objectives Model), 1.2 (Orchestrator)
**Estimate:** 4-6 developer hours

**Acceptance Criteria:**
- [ ] ProjectSupervisor reads objectives YAML and selects next objective
- [ ] Evaluates pipeline outcomes against acceptance criteria using LLM (optional)
- [ ] Can override orchestrator's simple keyword-based evaluation
- [ ] Produces structured rationale for accept/reject decisions
- [ ] Commits decisions to Chronicle

```python
# src/gaia/orchestration/supervisors/project.py
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
"""
Project Supervisor - Strategic agent overseeing multi-objective project.

Unlike QualitySupervisor (which operates WITHIN a single pipeline),
ProjectSupervisor operates ABOVE pipelines -- it decides WHAT to build next.

Primarily RULE-BASED with optional LLM enhancement for acceptance criteria evaluation.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.state.nexus import NexusService
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class OutcomeVerdict(Enum):
    ACCEPT = auto()
    REJECT_RETRY = auto()
    REJECT_BLOCK = auto()
    REQUEST_CHANGE = auto()


@dataclass
class SupervisionDecision:
    verdict: OutcomeVerdict
    objective_id: str
    reason: str
    met_criteria: List[str] = field(default_factory=list)
    unmet_criteria: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    rationale: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.name, "objective_id": self.objective_id,
            "reason": self.reason, "met_criteria": self.met_criteria,
            "unmet_criteria": self.unmet_criteria, "suggestions": self.suggestions,
            "rationale": self.rationale, "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class ProjectSupervisor(Agent):
    """
    Strategic supervisor for multi-objective project orchestration.

    Operates ABOVE pipelines -- decides what to build next and whether output is acceptable.
    Unlike QualitySupervisor (within a pipeline), this is primarily rule-based with optional LLM.

    Example:
        >>> supervisor = ProjectSupervisor()
        >>> decision = await supervisor.evaluate_outcome(
        ...     objective_id="OBJ-001", pipeline_snapshot=snapshot,
        ...     acceptance_criteria=["Endpoints respond"], quality_score=0.92,
        ...     artifacts={"api_code": "# code"})
        >>> print(decision.verdict.name)
        ACCEPT
    """

    def __init__(self, model_id: str = "Qwen3.5-35B-A3B-GGUF", debug: bool = False,
                 silent_mode: bool = False, skip_lemonade: bool = True,
                 use_llm_evaluation: bool = False):
        super().__init__(model_id=model_id, debug=debug, silent_mode=silent_mode, skip_lemonade=skip_lemonade)
        self._use_llm = use_llm_evaluation
        self._lock = threading.RLock()
        self._decision_history: List[SupervisionDecision] = []
        self._nexus: Optional[NexusService] = None

    async def evaluate_outcome(
        self, objective_id: str, pipeline_snapshot: Any, acceptance_criteria: List[str],
        quality_score: float, artifacts: Dict[str, Any],
    ) -> SupervisionDecision:
        if self._use_llm:
            met_criteria, unmet_criteria = await self._llm_evaluate(acceptance_criteria, artifacts, quality_score)
        else:
            met_criteria, unmet_criteria = self._rule_based_evaluate(acceptance_criteria, artifacts, quality_score)

        if len(unmet_criteria) == 0 and quality_score >= 0.85:
            verdict = OutcomeVerdict.ACCEPT
            reason = f"All {len(met_criteria)} criteria met, quality {quality_score:.2f}"
        elif quality_score >= 0.70:
            verdict = OutcomeVerdict.REJECT_RETRY
            reason = f"Quality acceptable ({quality_score:.2f}) but {len(unmet_criteria)} criteria unmet"
        else:
            verdict = OutcomeVerdict.REJECT_RETRY
            reason = f"Quality too low ({quality_score:.2f}), {len(unmet_criteria)} criteria unmet"

        decision = SupervisionDecision(
            verdict=verdict, objective_id=objective_id, reason=reason,
            met_criteria=met_criteria, unmet_criteria=unmet_criteria,
            suggestions=self._generate_suggestions(unmet_criteria),
            rationale=self._build_rationale(verdict, met_criteria, unmet_criteria, quality_score),
        )
        self._record_decision(decision)
        self._commit_to_chronicle(decision)
        return decision

    def _rule_based_evaluate(self, criteria, artifacts, quality_score) -> tuple:
        met, unmet = [], []
        for c in criteria:
            if self._check_criterion(c, artifacts, quality_score):
                met.append(c)
            else:
                unmet.append(c)
        return met, unmet

    def _check_criterion(self, criterion, artifacts, quality_score) -> bool:
        cl = criterion.lower()
        for name, value in artifacts.items():
            if any(w in name.lower() for w in cl.split() if len(w) > 3):
                return True
            text = value.lower() if isinstance(value, str) else str(value).lower()
            if any(w in text for w in cl.split() if len(w) > 3):
                return True
        if ("test" in cl or "error" in cl) and quality_score >= 0.85:
            return True
        return False

    async def _llm_evaluate(self, criteria, artifacts, quality_score) -> tuple:
        prompt = f"Evaluate criteria against artifacts. Score: {quality_score}. Criteria: {criteria}. Artifacts: {list(artifacts.keys())}. Return JSON: {{'met': [...], 'unmet': [...]}}"
        try:
            import json
            response = self._invoke_llm(prompt)
            result = json.loads(response)
            return result.get("met", []), result.get("unmet", [])
        except Exception as e:
            logger.warning(f"LLM evaluation failed: {e}")
            return self._rule_based_evaluate(criteria, artifacts, quality_score)

    def _generate_suggestions(self, unmet_criteria) -> List[str]:
        suggestions = []
        for c in unmet_criteria:
            if "test" in c.lower():
                suggestions.append("Ensure test artifacts captured")
            if "error" in c.lower():
                suggestions.append("Add comprehensive error handling")
            if "doc" in c.lower():
                suggestions.append("Generate documentation artifacts")
        return suggestions

    def _build_rationale(self, verdict, met, unmet, quality_score) -> str:
        return f"Verdict: {verdict.name} | Quality: {quality_score:.2f} | Met: {len(met)}/{len(met)+len(unmet)} | Unmet: {unmet}"

    def _record_decision(self, decision: SupervisionDecision) -> None:
        with self._lock:
            self._decision_history.append(decision)

    def _commit_to_chronicle(self, decision: SupervisionDecision) -> None:
        try:
            if self._nexus is None:
                self._nexus = NexusService.get_instance()
            self._nexus.commit(agent_id="ProjectSupervisor", event_type="supervisor_decision",
                               payload=decision.to_dict(), phase=None, loop_id=None)
        except Exception as e:
            logger.warning(f"Failed to commit to chronicle: {e}")

    def get_decision_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [d.to_dict() for d in reversed(self._decision_history[-limit:])]
```

---

### Work Item 2.2: GitSupervisor

**File:** `src/gaia/orchestration/supervisors/git.py`
**Dependencies:** None (uses CircuitBreaker from `resilience/`)
**Estimate:** 3-4 developer hours

**Acceptance Criteria:**
- [ ] Git operations wrapped in CircuitBreaker
- [ ] Branch creation, commit, PR creation, rollback
- [ ] Conflict detection before merge
- [ ] All operations have timeout (30s)

```python
# src/gaia/orchestration/supervisors/git.py
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
"""
Git Supervisor - Manages git operations for the orchestration layer.

All git operations are wrapped in CircuitBreaker to prevent cascading failures.
"""

import logging
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.resilience.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from gaia.utils.logging import get_logger

logger = get_logger(__name__)
GIT_TIMEOUT = 30


@dataclass
class GitOperation:
    operation: str
    branch: str
    message: str
    success: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None


class GitSupervisor:
    """
    Manages git operations for project orchestration.

    Example:
        >>> supervisor = GitSupervisor(repo_path=Path("."))
        >>> supervisor.create_branch("obj-001-build-api")
        >>> supervisor.commit("feat: implement user API")
    """

    def __init__(self, repo_path: Path = Path("."), git_user_name: str = "GAIA Orchestrator",
                 git_user_email: str = "orchestrator@gai",
                 circuit_breaker_config: Optional[CircuitBreakerConfig] = None):
        self._repo_path = repo_path
        self._user_name = git_user_name
        self._user_email = git_user_email
        self._lock = threading.RLock()
        self._operation_log: List[GitOperation] = []
        cb_config = circuit_breaker_config or CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60.0, success_threshold=2)
        self._circuit_breaker = CircuitBreaker(cb_config)

    def create_branch(self, branch_name: str, base_branch: Optional[str] = None) -> bool:
        def _create():
            current = self._get_current_branch()
            base = base_branch or current
            self._run_git(["checkout", "-B", branch_name, base])
        return self._protected("create_branch", _create, branch_name, f"Create branch {branch_name}")

    def commit(self, message: str, files: Optional[List[str]] = None) -> bool:
        def _commit():
            if files:
                self._run_git(["add"] + files)
            else:
                self._run_git(["add", "-A"])
            self._run_git(["commit", "-m", message, f"--author={self._user_name} <{self._user_email}>"])
        return self._protected("commit", _commit, self._get_current_branch(), message)

    def push(self, remote: str = "origin", branch: Optional[str] = None) -> bool:
        def _push():
            target = branch or self._get_current_branch()
            self._run_git(["push", "-u", remote, target])
        return self._protected("push", _push, branch or self._get_current_branch(), f"Push to {remote}")

    def create_pr(self, title: str, body: str, target_branch: str = "main", source_branch: Optional[str] = None) -> Optional[str]:
        source = source_branch or self._get_current_branch()
        try:
            result = self._run_git(["pr", "create", "--title", title, "--body", body, "--base", target_branch, "--head", source])
            return result.strip()
        except Exception as e:
            logger.warning(f"Failed to create PR: {e}")
            return None

    def rollback(self, branch_name: str, to_commit: str = "HEAD~1") -> bool:
        def _rollback():
            current = self._get_current_branch()
            self._run_git(["checkout", branch_name])
            self._run_git(["reset", "--hard", to_commit])
            self._run_git(["checkout", current])
        return self._protected("rollback", _rollback, branch_name, f"Rollback to {to_commit}")

    def detect_conflicts(self, source_branch: str, target_branch: str = "main") -> List[str]:
        try:
            result = self._run_git(["diff", "--name-only", f"{target_branch}...{source_branch}"])
            return [f for f in result.strip().split("\n") if f]
        except Exception as e:
            logger.warning(f"Conflict detection failed: {e}")
            return []

    def _protected(self, operation: str, func, branch: str, message: str) -> bool:
        try:
            self._circuit_breaker(func)
            with self._lock:
                self._operation_log.append(GitOperation(operation=operation, branch=branch, message=message, success=True))
            logger.info(f"Git {operation} succeeded: {message}")
            return True
        except Exception as e:
            with self._lock:
                self._operation_log.append(GitOperation(operation=operation, branch=branch, message=message, success=False, error=str(e)))
            logger.error(f"Git {operation} failed: {e}")
            return False

    def _run_git(self, args: List[str]) -> str:
        result = subprocess.run(["git"] + args, cwd=str(self._repo_path), capture_output=True, text=True, timeout=GIT_TIMEOUT)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
        return result.stdout

    def _get_current_branch(self) -> str:
        return self._run_git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()

    def get_operation_log(self) -> List[Dict[str, Any]]:
        return [op.__dict__ for op in self._operation_log]

    def get_statistics(self) -> Dict[str, Any]:
        total = len(self._operation_log)
        successful = sum(1 for op in self._operation_log if op.success)
        return {"total_operations": total, "successful": successful, "failed": total - successful,
                "circuit_breaker_state": self._circuit_breaker.state}
```

---

### Work Item 2.3: Supervisor Registry

**File:** `src/gaia/orchestration/supervisors/registry.py`
**Dependencies:** 2.1, 2.2
**Estimate:** 1-2 developer hours

```python
# src/gaia/orchestration/supervisors/registry.py
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
"""
Supervisor Registry - Manages named supervisor instances.

Similar to HookRegistry pattern -- allows ProjectOrchestrator to query
supervisors by role.
"""

import threading
from typing import Any, Dict, List, Optional

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class SupervisorRegistry:
    """
    Registry for supervisor instances.

    Example:
        >>> registry = SupervisorRegistry()
        >>> registry.register("project", ProjectSupervisor())
        >>> registry.register("git", GitSupervisor())
        >>> supervisor = registry.get("project")
    """

    def __init__(self):
        self._supervisors: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def register(self, role: str, supervisor: Any) -> None:
        with self._lock:
            self._supervisors[role] = supervisor
            logger.info(f"Registered supervisor: {role}")

    def unregister(self, role: str) -> bool:
        with self._lock:
            if role in self._supervisors:
                del self._supervisors[role]
                return True
            return False

    def get(self, role: str) -> Optional[Any]:
        with self._lock:
            return self._supervisors.get(role)

    def has(self, role: str) -> bool:
        with self._lock:
            return role in self._supervisors

    def get_all_roles(self) -> List[str]:
        with self._lock:
            return list(self._supervisors.keys())

    def get_statistics(self) -> Dict[str, Any]:
        with self._lock:
            return {"total": len(self._supervisors), "roles": list(self._supervisors.keys())}
```

---

### Work Item 2.4: Supervisor Package Init

**File:** `src/gaia/orchestration/supervisors/__init__.py`
**Estimate:** 10 minutes

```python
# Copyright 2025-2026 AMD. SPDX-License-Identifier: MIT
from gaia.orchestration.supervisors.project import ProjectSupervisor, SupervisionDecision, OutcomeVerdict
from gaia.orchestration.supervisors.git import GitSupervisor, GitOperation
from gaia.orchestration.supervisors.registry import SupervisorRegistry

__all__ = ["ProjectSupervisor", "SupervisionDecision", "OutcomeVerdict", "GitSupervisor", "GitOperation", "SupervisorRegistry"]
```

---

### Work Item 2.5: Wire Supervisors into Orchestrator

**Changes:** Modify `src/gaia/orchestration/engine.py`
**Estimate:** 2-3 developer hours

Add to `OrchestratorConfig`:
```python
use_supervisor: bool = False
```

Add to `ProjectOrchestrator.__init__`:
```python
self._project_supervisor = None
self._git_supervisor = None
self._supervisor_registry = SupervisorRegistry()
```

Add to `ProjectOrchestrator.initialize`:
```python
if self._config.use_supervisor:
    self._project_supervisor = ProjectSupervisor()
    self._git_supervisor = GitSupervisor(repo_path=self._config.objectives_path.parent)
    self._supervisor_registry.register("project", self._project_supervisor)
    self._supervisor_registry.register("git", self._git_supervisor)
```

Modify `_evaluate_outcome` to optionally delegate to ProjectSupervisor:
```python
def _evaluate_outcome(self, objective, snapshot):
    if self._project_supervisor:
        # Delegate to LLM-enhanced supervisor
        decision = asyncio.run(self._project_supervisor.evaluate_outcome(
            objective_id=objective.id,
            pipeline_snapshot=snapshot,
            acceptance_criteria=objective.acceptance_criteria,
            quality_score=snapshot.quality_score or 0.0,
            artifacts=snapshot.artifacts or {},
        ))
        # Map SupervisionDecision.verdict to orchestrator logic
        if decision.verdict.name == "ACCEPT":
            return {"success": True, "met_criteria": decision.met_criteria,
                    "unmet_criteria": decision.unmet_criteria, ...}
        elif decision.verdict.name == "REJECT_RETRY":
            return {"success": False, "met_criteria": decision.met_criteria,
                    "unmet_criteria": decision.unmet_criteria, "reason": decision.reason, ...}
        else:
            return {"success": False, ...}
```

---

### Phase 2 Dependency Graph

```
2.1 ProjectSupervisor (supervisors/project.py)
2.2 GitSupervisor (supervisors/git.py)           [parallel, no deps on each other]
  |
  +--> 2.3 SupervisorRegistry (supervisors/registry.py)
  |
  +--> 2.4 Wire into Orchestrator (engine.py modification)
  |
  +--> 2.5 supervisors/__init__.py
  |
  +--> Phase 2 Tests (tests/unit/orchestration/supervisors/)
```

---

## 5. PHASES 3-5: HIGH-LEVEL OVERVIEW

### Phase 3: Automation Hooks ("Hooks Recalculate")

**Concept:** When objective state changes, dependent hooks automatically propagate effects.

**Hooks to Create:**

| Hook | Event | Action | File |
|------|-------|--------|------|
| `ObjectiveUpdateHook` | OBJECTIVE_COMPLETE | Check dependent objectives, unblock if all deps DONE | `src/gaia/orchestration/hooks/objective_update.py` |
| `GitBranchHook` | OBJECTIVE_START | Auto-create feature branch `obj/{id}-{slug}` | `src/gaia/orchestration/hooks/git_branch.py` |
| `GitCommitHook` | OBJECTIVE_COMPLETE | Commit objectives YAML + artifacts | `src/gaia/orchestration/hooks/git_commit.py` |
| `GitPRHook` | ORCHESTRATOR_COMPLETE | Auto-create PR if project complete | `src/gaia/orchestration/hooks/git_pr.py` |
| `GitRollbackHook` | OBJECTIVE_FAILED | Revert branch to previous state | `src/gaia/orchestration/hooks/git_rollback.py` |
| `TaskSpawnHook` | OBJECTIVE_BLOCKED | Create sub-objective for the blocker | `src/gaia/orchestration/hooks/task_spawn.py` |

**New HookEvent enums to add to `src/gaia/hooks/base.py` HookEvent:**
```python
# Orchestration events
OBJECTIVE_START = auto()
OBJECTIVE_COMPLETE = auto()
OBJECTIVE_FAILED = auto()
OBJECTIVE_BLOCKED = auto()
PIPELINE_DISPATCHED = auto()
ORCHESTRATOR_START = auto()
ORCHESTRATOR_COMPLETE = auto()
```

**Estimate:** 6-8 developer hours
**Risk:** LOW -- all follow existing BaseHook pattern

---

### Phase 4: Advanced Features

| Feature | Description | Dependencies | Files |
|---------|-------------|-------------|-------|
| Parallel dispatch | Run independent objectives concurrently | Phase 3 hooks | `engine.py` -- add `asyncio.gather()` for independent objectives |
| Git worktrees | Each objective runs in isolated worktree | GitSupervisor | `src/gaia/orchestration/worktree.py` |
| CircuitBreaker on git ops | Already exists in `resilience/` | Phase 2 GitSupervisor | Already wired in 2.2 |
| Conflict detection | Detect before merge | GitSupervisor | Already wired in 2.2 |

**Estimate:** 8-12 developer hours
**Risk:** MEDIUM -- parallel dispatch introduces race conditions, worktrees add complexity

**Fallback:** If parallel dispatch is too complex, keep sequential-only. Git worktrees can be deferred to Phase 5.

---

### Phase 5: Hardening & Production

| Item | Description | Files |
|------|-------------|-------|
| Performance testing | Run orchestrator against 50+ objective project | `tests/perf/test_orchestrator_scale.py` |
| Security audit | Review git operations for injection vulns | Manual review |
| Documentation | User guide for objectives YAML format | `docs/guides/orchestration.mdx` |
| UI dashboard | Visualize objectives pipeline in Agent UI | `src/gaia/ui/routers/orchestration.py` |
| SSE events for orchestrator | Extend SSE hooks for orchestrator events | `src/gaia/pipeline/sse_hooks.py` extension |

**Estimate:** 8-12 developer hours
**Risk:** LOW

---

## 6. RISK MITIGATION MATRIX

| Phase | Risk | Probability | Impact | Mitigation | Fallback |
|-------|------|------------|--------|------------|----------|
| Phase 1 | Objectives YAML format ambiguity | LOW | LOW | Start simple, extend later | Keep format minimal, add fields in Phase 2 |
| Phase 1 | PipelineEngine not a true black box | MEDIUM | MEDIUM | Test with mock PipelineEngine first | If PipelineEngine needs changes, keep them isolated in a thin adapter layer |
| Phase 1 | Objective evaluation too simplistic | LOW | LOW | Accept keyword matching as V1 | Phase 2 ProjectSupervisor adds LLM evaluation |
| Phase 2 | Git operations fail in CI environments | MEDIUM | MEDIUM | CircuitBreaker + auto_commit=False in CI | Disable git features, use local YAML only |
| Phase 2 | ProjectSupervisor LLM latency | LOW | LOW | Default to rule-based, LLM optional | Stay rule-based, document LLM as opt-in |
| Phase 3 | Hook cascade loops (A triggers B triggers A) | MEDIUM | HIGH | Guard each hook with re-entrancy check | Disable problematic hooks, add max-depth limit |
| Phase 4 | Parallel dispatch race conditions | MEDIUM | HIGH | Careful locking on objectives file | Stay sequential, document parallel as opt-in |
| Phase 4 | Git worktree complexity | LOW | MEDIUM | Thorough testing | Defer worktrees, use shared directory |
| Phase 5 | Performance degradation at scale | LOW | MEDIUM | Load testing early | Optimize YAML I/O, add caching |

---

## 7. INTEGRATION POINTS MAP

```
                    ┌─────────────────────────────────────┐
                    │       ProjectOrchestrator           │
                    │    (src/gaia/orchestration/engine)   │
                    └──────────────┬──────────────────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            │                      │                      │
            ▼                      ▼                      ▼
    ┌───────────────┐    ┌──────────────────┐   ┌────────────────────┐
    │ Objectives     │    │ PipelineEngine   │   │ Supervisors        │
    │ Model (YAML)  │    │ (black box)      │   │ (Phase 2)          │
    │ objectives.py │    │ engine.py        │   │ supervisors/*.py   │
    └───────┬───────┘    └────────┬─────────┘   └──────────┬─────────┘
            │                     │                        │
            ▼                     ▼                        ▼
    ┌───────────────┐    ┌──────────────────┐   ┌────────────────────┐
    │ YAML File I/O │    │ PipelineContext  │   │ CircuitBreaker     │
    │ (pyyaml)      │    │ PipelineSnapshot │   │ (resilience/)      │
    └───────┬───────┘    └────────┬─────────┘   └──────────┬─────────┘
            │                     │                        │
            ▼                     ▼                        ▼
    ┌───────────────┐    ┌──────────────────┐   ┌────────────────────┐
    │ NexusService  │    │ HookExecutor     │   │ Git (subprocess)   │
    │ Chronicle     │    │ HookRegistry     │   │                    │
    │ (state/nexus) │    │ (hooks/*)        │   │                    │
    └───────────────┘    └──────────────────┘   └────────────────────┘
```

**Key integration rules:**
1. **PipelineEngine is never modified.** The orchestrator is a consumer, not a modifier.
2. **NexusService is extended, not modified.** New event types are added to the existing commit interface.
3. **HookRegistry/HookExecutor are extended.** New event names are added; existing hooks are untouched.
4. **Exceptions follow the GAIAException pattern.** New exceptions added to `src/gaia/exceptions.py`.
5. **CircuitBreaker is reused.** No new resilience primitives needed.

---

## 8. TESTING STRATEGY

### Unit Tests (Per Phase)

| Phase | Test File | What | Mock What |
|-------|-----------|------|-----------|
| Phase 1 | `tests/unit/orchestration/test_objectives.py` | Model serialization, transitions, deps | Nothing (pure data) |
| Phase 1 | `tests/unit/orchestration/test_orchestrator.py` | Dispatch loop, evaluation, retry | PipelineEngine, NexusService, subprocess |
| Phase 1 | `tests/unit/orchestration/test_hooks.py` | Hook firing, event propagation | Nothing (unit) |
| Phase 2 | `tests/unit/orchestration/supervisors/test_project.py` | Evaluation logic, verdict mapping | LLM (for _llm_evaluate path) |
| Phase 2 | `tests/unit/orchestration/supervisors/test_git.py` | Git commands, CircuitBreaker wiring | subprocess |
| Phase 2 | `tests/unit/orchestration/supervisors/test_registry.py` | Register/get/unregister | Nothing |
| Phase 3 | `tests/unit/orchestration/hooks/test_*.py` | Each hook individually | GitSupervisor, ProjectObjectives |

### Integration Tests

| Phase | Test File | What |
|-------|-----------|------|
| Phase 1 | `tests/integration/orchestration/test_orchestrator_pipeline.py` | Real orchestrator + real PipelineEngine, 2-3 objectives |
| Phase 2 | `tests/integration/orchestration/test_supervisor_hierarchy.py` | All 3 supervisors working together |
| Phase 3 | `tests/integration/orchestration/test_hook_chain.py` | Objective complete -> dependency unblock -> task spawn |
| Phase 4 | `tests/integration/orchestration/test_parallel_dispatch.py` | Concurrent independent objectives |
| Phase 5 | `tests/perf/test_orchestrator_scale.py` | 50+ objective project |

### Test Configuration

All tests use `skip_lemonade=True` (no LLM server needed). Unit tests mock `NexusService.get_instance()`. Integration tests require a real git repo (use `tmp_path` with initialized git repo).

---

## 9. FILE SUMMARY

### Files to CREATE (Phase 1 + 2)

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `src/gaia/orchestration/__init__.py` | 30 | Package init |
| `src/gaia/orchestration/objectives.py` | 200 | Objective + ProjectObjectives models |
| `src/gaia/orchestration/engine.py` | 350 | ProjectOrchestrator core |
| `src/gaia/orchestration/hooks.py` | 80 | Orchestrator hook events |
| `src/gaia/orchestration/supervisors/__init__.py` | 10 | Supervisor package init |
| `src/gaia/orchestration/supervisors/project.py` | 180 | ProjectSupervisor |
| `src/gaia/orchestration/supervisors/git.py` | 150 | GitSupervisor |
| `src/gaia/orchestration/supervisors/registry.py` | 60 | SupervisorRegistry |
| `tests/unit/orchestration/__init__.py` | 0 | Test package |
| `tests/unit/orchestration/test_objectives.py` | 120 | Objectives model tests |
| `tests/unit/orchestration/test_orchestrator.py` | 100 | Orchestrator tests |
| `tests/integration/orchestration/__init__.py` | 0 | Test package |
| `tests/integration/orchestration/test_orchestrator_pipeline.py` | 80 | Integration tests |

### Files to MODIFY

| File | Change | Lines |
|------|--------|-------|
| `src/gaia/exceptions.py` | Add 5 orchestration exception classes | +30 |

### Total New Code

~1,390 lines of production code + ~300 lines of tests = ~1,690 lines

---

## 10. SEQUENTIAL EXECUTION ORDER

```
Sprint 1 (Week 1):
  Day 1-2:  1.1 Objectives Model + tests
  Day 3-5:  1.2 ProjectOrchestrator + 1.3 __init__.py + tests

Sprint 2 (Week 2):
  Day 1-2:  1.4 Hook Events + tests
  Day 3-5:  1.5 Integration tests

Sprint 3 (Week 3):
  Day 1-3:  2.1 ProjectSupervisor + 2.2 GitSupervisor + tests
  Day 4-5:  2.3 SupervisorRegistry + 2.4 Wire into Orchestrator

Sprint 4 (Week 4):
  Day 1-2:  Phase 2 integration tests
  Day 3-5:  Phase 3 Hooks (first 3 of 6 hooks)
```

---

## 11. DEFINITION OF DONE

Phase 1 is complete when:
- [x] All acceptance criteria met for work items 1.1-1.5 (with adapter layer addition)
- [x] All unit tests pass (89/89 passing, 0.60s)
- [ ] All integration tests pass (deferred to Phase 2)
- [x] No regressions in existing test suite
- [x] Objectives YAML round-trip verified (4 atomic write tests)
- [x] Orchestrator run completes end-to-end with mocked adapter
- [x] All 6 critical QA gaps resolved
- [x] Quality score: 8.5/10

Phase 2 is complete when:
- [ ] All acceptance criteria met for work items 2.1-2.5
- [ ] All unit tests pass
- [ ] ProjectSupervisor can override orchestrator's keyword-based evaluation
- [ ] GitSupervisor operations are CircuitBreaker-protected
- [ ] Supervisor hierarchy wired into orchestrator run loop

---

## 12. OPEN QUESTIONS FOR REVIEW

1. **Objectives YAML location:** Should it be project-root level (`./objectives.yaml`) or in a dedicated directory (`./.gaia/objectives.yaml`)? Recommendation: `.gaia/objectives.yaml` to avoid cluttering project root.

2. **Git commit author:** The orchestrator commits as "GAIA Orchestrator <orchestrator@gai>". Should this be configurable to match the user's git identity?

3. **PipelineEngine adapter:** If PipelineEngine needs minor changes to support orchestrator use (e.g., better artifact extraction), should we create a thin `OrchestratorPipelineAdapter` class, or should we accept that some changes to PipelineEngine are okay? Recommendation: Keep PipelineEngine untouched; create adapter if needed.

4. **LLM evaluation default:** Should ProjectSupervisor use LLM-based evaluation by default or rule-based? Recommendation: Rule-based by default, LLM opt-in via `use_llm_evaluation=True`.

5. **Auto-commit safety:** Should auto-commit be disabled by default to prevent accidental git operations? Recommendation: Disabled by default, enabled via `auto_commit=True` config.

6. **Error handling for YAML corruption:** If objectives YAML becomes corrupted mid-execution, should orchestrator attempt recovery from git history? Phase 1: No (fail hard). Phase 4: Yes (git-based recovery).

---

*End of Program Management Plan. Ready for Quality Reviewer assessment.*
