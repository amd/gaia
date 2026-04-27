# Phase 1 Quick-Start Guide

**Phase:** 1 -- Core Orchestration Kernel
**Date:** 2026-04-26
**Status:** Complete (89 tests passing)

---

This guide shows you how to use the Phase 1 orchestration kernel programmatically, write objectives YAML, run the tests, and hook into the dispatch loop.

---

## 1. Prerequisites

No additional dependencies. The orchestration kernel uses only:
- `pyyaml` (already a GAIA dependency)
- Python 3.10+ (async/await, dataclasses)

Import from the orchestration package:

```python
from gaia.orchestration import (
    ProjectOrchestrator,
    OrchestratorPipelineAdapter,
    ProjectObjectives,
    Objective,
    ObjectiveStatus,
    DependencyGraph,
    Artifact,
    ObjectiveUpdateHook,
    TaskSpawnHook,
)
```

---

## 2. Using the Orchestrator Programmatically

### 2.1 Basic Usage

```python
import asyncio
from gaia.orchestration import (
    ProjectOrchestrator,
    OrchestratorConfig,
    ProjectObjectives,
    Objective,
    ObjectiveStatus,
)

async def main():
    # Create orchestrator with default config
    config = OrchestratorConfig(
        objectives_path=".gaia/objectives.yaml",
        auto_commit=False,        # Safe default: no git operations
        dry_run=False,            # Set True to preview without executing
        enable_evaluation=False,  # Rule-based evaluation (LLM opt-in)
        max_cycle_iterations=100, # Safety valve
        enable_nexus=True,        # Event chronicle
    )

    orchestrator = ProjectOrchestrator(config=config)

    # Load objectives from YAML
    orchestrator.load_objectives()

    # Run the dispatch loop
    state = await orchestrator.run()

    print(f"Cycles: {state.cycle_count}")
    print(f"Processed: {state.objectives_processed}")
    print(f"Failed: {state.objectives_failed}")

asyncio.run(main())
```

### 2.2 Building Objectives in Memory (No YAML File)

```python
from gaia.orchestration import ProjectObjectives, Objective, ObjectiveStatus

# Build objectives programmatically
project = ProjectObjectives(
    project_id="my-project",
    name="My Project",
    objectives=[
        Objective(
            objective_id="obj-001",
            title="Design database schema",
            description="Create PostgreSQL schema with migrations",
            status=ObjectiveStatus.QUEUED,
            dependencies=[],
            priority=1,
            phase="PLANNING",
        ),
        Objective(
            objective_id="obj-002",
            title="Build API endpoints",
            description="Implement REST API for user management",
            status=ObjectiveStatus.QUEUED,
            dependencies=["obj-001"],  # Depends on database schema
            priority=2,
            phase="DEVELOPMENT",
        ),
        Objective(
            objective_id="obj-003",
            title="Write integration tests",
            description="Integration tests for all API endpoints",
            status=ObjectiveStatus.QUEUED,
            dependencies=["obj-002"],  # Depends on API endpoints
            priority=3,
            phase="QUALITY",
        ),
    ],
)

# Save to YAML (atomic write)
project.save_atomic(".gaia/objectives.yaml")

# Get ready objectives (deps met, QUEUED status)
ready = project.get_ready_objectives()
# Returns: [obj-001] (no dependencies, QUEUED)

obj_001 = ready[0]
obj_001.transition_to(ObjectiveStatus.IN_PROGRESS)
# ... execute obj-001 ...
obj_001.transition_to(ObjectiveStatus.COMPLETED)

# Now obj-002 is ready
ready = project.get_ready_objectives()
# Returns: [obj-002] (dep on obj-001 met, QUEUED)
```

---

## 3. Example Objectives YAML

The orchestrator reads from `.gaia/objectives.yaml`:

```yaml
project_id: a1b2c3d4
name: My Project
objectives:
  - objective_id: obj-001
    title: Design database schema
    description: Create PostgreSQL schema with migrations
    status: completed
    dependencies: []
    artifacts:
      - artifact_id: art-001
        name: schema.sql
        artifact_type: document
        url_or_path: src/db/schema.sql
        metadata:
          author: developer
        created_at: "2026-04-26T12:00:00+00:00"
    priority: 1
    phase: PLANNING
    pipeline_config:
      template: generic
      quality_threshold: 0.90
      max_iterations: 10
    created_at: "2026-04-26T10:00:00+00:00"
    updated_at: "2026-04-26T12:00:00+00:00"
    error_message: null
  - objective_id: obj-002
    title: Build API endpoints
    description: Implement REST API for user management
    status: queued
    dependencies:
      - obj-001
    artifacts: []
    priority: 2
    phase: DEVELOPMENT
    pipeline_config:
      template: generic
      quality_threshold: 0.90
      max_iterations: 10
    created_at: "2026-04-26T10:00:00+00:00"
    updated_at: "2026-04-26T10:00:00+00:00"
    error_message: null
  - objective_id: obj-003
    title: Write integration tests
    description: Integration tests for all API endpoints
    status: queued
    dependencies:
      - obj-002
    artifacts: []
    priority: 3
    phase: QUALITY
    pipeline_config:
      template: generic
      quality_threshold: 0.90
      max_iterations: 10
    created_at: "2026-04-26T10:00:00+00:00"
    updated_at: "2026-04-26T10:00:00+00:00"
    error_message: null
metadata: {}
```

### 3.1 Status Values

Valid status values in YAML (lowercase string):

| Value | Enum | Description |
|-------|------|-------------|
| `queued` | `QUEUED` | Waiting for dependencies to complete |
| `in_progress` | `IN_PROGRESS` | Currently being executed |
| `completed` | `COMPLETED` | Successfully finished (terminal) |
| `blocked` | `BLOCKED` | Dependencies unmet or execution failed |
| `cancelled` | `CANCELLED` | Manually cancelled (terminal) |

### 3.2 Valid Status Transitions

```
queued     -> in_progress, blocked, cancelled
in_progress -> completed, blocked, cancelled
blocked    -> queued, cancelled
completed  -> (terminal, no transitions)
cancelled  -> (terminal, no transitions)
```

Invalid transitions raise `ValueError`.

---

## 4. Running the Tests

### 4.1 All Orchestration Tests

```bash
# 89 tests, ~0.60 seconds
python -m pytest tests/unit/orchestration/ -v
```

### 4.2 Model Tests Only (45 tests)

```bash
python -m pytest tests/unit/orchestration/test_objectives.py -v
```

Test classes:
- `TestArtifact` (4 tests) -- serialization, round-trips
- `TestObjectiveStatusTransitions` (7 tests) -- all valid/invalid transitions
- `TestObjectiveTransitions` (4 tests) -- status changes, timestamps, artifacts
- `TestObjectiveSerialization` (3 tests) -- `to_dict`/`from_dict`/round-trip
- `TestProjectObjectives` (9 tests) -- lookups, ready objectives, YAML, atomic writes
- `TestDependencyGraph` (18 tests) -- build, deps, cycles, cascade

### 4.3 Orchestrator Tests Only (44 tests)

```bash
python -m pytest tests/unit/orchestration/test_orchestrator.py -v
```

Test classes:
- `TestOrchestratorPipelineAdapter` (6 tests) -- execute, CircuitBreaker, status transitions
- `TestProjectOrchestrator` (12 tests) -- init, config, hooks, git, evaluation, pause/resume
- `TestDispatchLoop` (3 tests) -- full cycle, failure, dependency ordering
- `TestHookExecution` (3 tests) -- START/COMPLETE/FAILED hook firing
- `TestGitIntegration` (2 tests) -- auto_commit=False, dry_run
- `TestOrchestratorHooks` (2 tests) -- ObjectiveUpdateHook, TaskSpawnHook
- `TestCriticalDispatchLoopPaths` (6 tests) -- G1-G7 critical gaps from QA review
- `TestAdapterResilience` (4 tests) -- no-breaker path, shutdown on failure
- `TestHookFailurePaths` (3 tests) -- missing config, no execution result, dry_run
- `TestEdgeCases` (3 tests) -- invalid status, artifact addition, half-open state

### 4.4 Specific Test Class

```bash
# Just the critical gap tests
python -m pytest tests/unit/orchestration/test_orchestrator.py::TestCriticalDispatchLoopPaths -v

# Just the model tests
python -m pytest tests/unit/orchestration/test_objectives.py::TestDependencyGraph -v
```

### 4.5 Full Pipeline Suite Baseline

```bash
# 801 tests baseline (orchestration tests are a subset)
python -m pytest tests/ -x --timeout=120
```

---

## 5. Hooking Into the Dispatch Loop

The orchestrator has its own `HookRegistry` (separate from `PipelineEngine`'s). You can register custom hooks for orchestrator events.

### 5.1 Available Hook Events

| Event | Fired When |
|-------|------------|
| `OBJECTIVE_START` | Before dispatching an objective to PipelineEngine |
| `OBJECTIVE_COMPLETE` | After a successful objective execution |
| `OBJECTIVE_FAILED` | After a failed objective execution |
| `PHASE_COMPLETE` | After all objectives in a phase finish |
| `CYCLE_COMPLETE` | After each dispatch-evaluate-update cycle |

### 5.2 Registering a Custom Hook

```python
from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult
from gaia.orchestration.engine import OBJECTIVE_START, OBJECTIVE_COMPLETE

class MyLoggingHook(BaseHook):
    name = "my_logging_hook"
    event = OBJECTIVE_START  # or use "*" for all events
    priority = HookPriority.NORMAL
    blocking = False

    async def execute(self, context: HookContext) -> HookResult:
        print(f"Objective starting: {context.data.get('objective_title', 'unknown')}")
        return HookResult.success_result()

# Register with orchestrator
orchestrator = ProjectOrchestrator(config=config)
orchestrator.hook_registry.register(MyLoggingHook())
```

### 5.3 Halting the Pipeline from a Hook

A hook can halt the dispatch loop by returning `halt_pipeline=True`:

```python
class HaltingHook(BaseHook):
    name = "halt_on_condition"
    event = OBJECTIVE_START
    priority = HookPriority.HIGH

    async def execute(self, context: HookContext) -> HookResult:
        if context.data.get("objective_title") == "Dangerous Task":
            return HookResult(
                success=True,
                halt_pipeline=True,
                metadata={"reason": "Blocked by safety policy"},
            )
        return HookResult.success_result()
```

### 5.4 Built-in Hooks

**ObjectiveUpdateHook** -- saves objectives YAML after completion:

```python
from gaia.orchestration.hooks import ObjectiveUpdateHook

hook = ObjectiveUpdateHook(config={
    "project": my_project,
    "path": ".gaia/objectives.yaml",
})
orchestrator.hook_registry.register(hook)
```

**TaskSpawnHook** -- creates remediation objectives from failures:

```python
from gaia.orchestration.hooks import TaskSpawnHook

hook = TaskSpawnHook(config={
    "project": my_project,
    "priority": 3,
    "max_spawned": 5,
})
orchestrator.hook_registry.register(hook)
```

### 5.5 Hook Priority Levels

```python
from gaia.hooks.base import HookPriority

HookPriority.CRITICAL  # Fires first
HookPriority.HIGH      # Fires early
HookPriority.NORMAL    # Default
HookPriority.LOW       # Fires last
```

---

## 6. Dependency Graph API

The `DependencyGraph` class provides the "Excel equations" metaphor for cascade computation.

```python
from gaia.orchestration.models import Objective, DependencyGraph

objectives = [
    Objective(objective_id="a", title="A"),
    Objective(objective_id="b", title="B", dependencies=["a"]),
    Objective(objective_id="c", title="C", dependencies=["a", "b"]),
]

graph = DependencyGraph(objectives)

# Forward dependencies: what does "c" depend on?
graph.get_dependencies("c")  # {"a", "b"}

# Reverse dependencies: what depends on "a"?
graph.get_reverse_deps("a")  # {"b", "c"}

# Detect circular dependencies
graph.detect_cycles()  # [] (no cycles)

# Cascade: if "a" changes, what is affected?
graph.compute_cascade("a")  # {"b", "c"}

# Max cascade depth from "a"
graph.max_cascade_depth("a")  # 2 (a -> b -> c)

# Topological order (dependencies first)
graph.topological_order()  # ["a", "b", "c"]
```

---

## 7. Configuration Reference

### 7.1 OrchestratorConfig

```python
@dataclass
class OrchestratorConfig:
    objectives_path: str = ".gaia/objectives.yaml"
    auto_commit: bool = False          # Git commits disabled by default
    dry_run: bool = False              # Preview mode (no saves, no git)
    enable_evaluation: bool = False    # LLM evaluation (opt-in)
    max_cycle_iterations: int = 100   # Safety valve
    enable_nexus: bool = True          # NexusService event chronicle
```

### 7.2 CircuitBreaker Defaults (in Adapter)

```python
CircuitBreakerConfig(
    failure_threshold=5,    # Trips after 5 consecutive failures
    recovery_timeout=30.0,  # Seconds before attempting recovery
    success_threshold=2,    # Consecutive successes to close
)
```

### 7.3 Evaluation Thresholds (Rule-Based)

| Condition | Verdict |
|-----------|---------|
| `success=True AND quality_score >= 0.90` | PASS |
| `success=True but quality_score < 0.90` | REVIEW |
| `success=False` | FAIL |
| `success=True but no quality_score` | PASS |

---

## 8. Atomic Write Strategy

The orchestrator uses atomic writes to prevent YAML corruption:

1. Write to `<target>.tmp` in the same directory (ensures same filesystem)
2. `os.replace(tmp_path, target_path)` -- atomic swap on all platforms
3. Clean up temp file on failure

This guarantees the objectives file is never partially written, even on crash or power loss.

---

## 9. Git Integration

### 9.1 Default Behavior (auto_commit=False)

No git operations are performed by default. The orchestrator updates objectives in memory and saves to YAML atomically.

### 9.2 Enabling Auto-Commit

```python
config = OrchestratorConfig(
    objectives_path=".gaia/objectives.yaml",
    auto_commit=True,
)
```

When enabled, the orchestrator runs:
```bash
git add .gaia/objectives.yaml
git commit -m "chore(orchestrator): complete objective '<title>'" --author="Name <email>"
```

### 9.3 Git Config Fallback

If `git config user.name` or `git config user.email` are not set, the orchestrator falls back to:
- Name: `"GAIA Orchestrator"`
- Email: `"gaia-orchestrator@local"`

---

## 10. Common Patterns

### 10.1 Running a Single Objective

```python
from gaia.orchestration import OrchestratorPipelineAdapter, Objective, ObjectiveStatus

adapter = OrchestratorPipelineAdapter()
objective = Objective(
    title="Build feature",
    description="Implement user authentication",
    status=ObjectiveStatus.QUEUED,
)

result = await adapter.execute_with_result_update(objective)

if result.success:
    print(f"Quality: {result.quality_score}")
    for artifact in result.artifacts:
        print(f"  Artifact: {artifact.name} ({artifact.artifact_type})")
else:
    print(f"Error: {result.error_message}")
```

### 10.2 Inspecting CircuitBreaker Stats

```python
stats = adapter.get_circuit_breaker_stats()
print(f"State: {stats['state']}")       # closed, open, half-open
print(f"Failures: {stats.get('failure_count', 0)}")
print(f"Successes: {stats.get('success_count', 0)}")
```

### 10.3 Pause and Resume

```python
orchestrator = ProjectOrchestrator(config=config)
orchestrator.load_objectives()

# Start in background
task = asyncio.create_task(orchestrator.run())

# Pause mid-execution
orchestrator.pause("Manual intervention required")

# Resume when ready
orchestrator.resume()

# Wait for completion
state = await task
```

---

## 11. File Locations Reference

| File | Path |
|------|------|
| Package init | `src/gaia/orchestration/__init__.py` |
| Models | `src/gaia/orchestration/models.py` |
| Adapter | `src/gaia/orchestration/adapters.py` |
| Engine | `src/gaia/orchestration/engine.py` |
| Hooks | `src/gaia/orchestration/hooks.py` |
| Model tests | `tests/unit/orchestration/test_objectives.py` |
| Orchestrator tests | `tests/unit/orchestration/test_orchestrator.py` |
| Phase 1 report | `docs/archive/phase-reports/PHASE1-IMPLEMENTATION-REPORT.md` |
| Program plan v2 | `docs/archive/phase-reports/PROGRAM-MANAGEMENT-PIPELINE-ORCHESTRATION-V2.md` |

---

## 12. Troubleshooting

### 12.1 "Invalid transition from completed to in_progress"

The objective is in a terminal state (`completed` or `cancelled`). Check your YAML for incorrect status values.

### 12.2 "Circuit breaker is open"

The adapter's CircuitBreaker tripped due to consecutive PipelineEngine failures. Wait `recovery_timeout` (default: 30s) or reset manually:

```python
adapter._circuit_breaker.reset()
```

### 12.3 "No project in ObjectiveUpdateHook config"

The hook requires a `project` key in its config dict:

```python
hook = ObjectiveUpdateHook(config={
    "project": my_project,  # Required
    "path": ".gaia/objectives.yaml",
})
```

### 12.4 Git commit fails

The orchestrator falls back gracefully if git is unavailable. Check:
- Is this a git repository?
- Is the objectives file tracked or at least in the working directory?
- Are there unstaged changes blocking the commit?

---

*This guide covers Phase 1 (Core Orchestration Kernel). Phase 2 (Supervisor Hierarchy) will add multi-level oversight, dynamic reprioritization, and parallel execution.*
