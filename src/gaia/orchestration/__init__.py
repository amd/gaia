"""
GAIA Project Orchestration Kernel

Provides project-level orchestration on top of PipelineEngine:
- Objective-driven task management via .gaia/objectives.yaml
- Dependency-aware scheduling (circular dependency detection, cascade execution)
- Hook integration on a dedicated orchestrator HookRegistry
- Git-aware commit/PR workflows with auto_commit=False default
- NexusService integration for unified state tracking

Example:
    >>> from gaia.orchestration import ProjectOrchestrator
    >>> orchestrator = ProjectOrchestrator()
    >>> await orchestrator.run()
"""

from gaia.orchestration.models import (
    Artifact,
    DependencyGraph,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)
from gaia.orchestration.adapters import OrchestratorPipelineAdapter
from gaia.orchestration.engine import ProjectOrchestrator
from gaia.orchestration.hooks import (
    ObjectiveUpdateHook,
    TaskSpawnHook,
)
from gaia.orchestration.supervisor import (
    HealthScore,
    ObjectiveOutcome,
    ProjectSupervisor,
    SupervisorConfig,
    Verdict,
)
from gaia.orchestration.supervisors import (
    GitOperation,
    GitSupervisor,
    SupervisorRegistry,
)

__all__ = [
    "Artifact",
    "DependencyGraph",
    "Objective",
    "ObjectiveStatus",
    "ProjectObjectives",
    "OrchestratorPipelineAdapter",
    "ProjectOrchestrator",
    "ObjectiveUpdateHook",
    "TaskSpawnHook",
    "HealthScore",
    "ObjectiveOutcome",
    "ProjectSupervisor",
    "SupervisorConfig",
    "Verdict",
    "GitOperation",
    "GitSupervisor",
    "SupervisorRegistry",
]

__version__ = "1.0.0"
