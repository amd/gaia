"""
TaskSpawnHook — Generates remediation objectives from defects/gaps.

Listens for OBJECTIVE_FAILED events and inspects the execution
result for defects. If defects are found, creates new remediation
objectives and adds them to the project.
"""

import logging
from typing import Any, Dict, List, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult

from gaia.orchestration.models import Objective, ObjectiveStatus, ProjectObjectives

logger = logging.getLogger(__name__)


class TaskSpawnHook(BaseHook):
    """
    Hook that generates new objectives from defects or gaps.

    Listens for OBJECTIVE_FAILED events and inspects the execution
    result for defects. If defects are found, creates new remediation
    objectives and adds them to the project.

    Configuration:
        config["project"]: ProjectObjectives instance to modify
        config["priority"]: Priority for spawned tasks (default: 3)
        config["max_spawned"]: Max objectives to spawn per failure (default: 5)

    Example:
        >>> hook = TaskSpawnHook(config={
        ...     "project": my_project,
        ...     "priority": 3,
        ... })
        >>> orchestrator.hook_registry.register(hook)
    """

    name = "task_spawn"
    event = "OBJECTIVE_FAILED"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Generate remediation objectives from defects"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the hook.

        Args:
            config: Must contain 'project' (ProjectObjectives)
        """
        super().__init__(config)

    async def execute(self, context: HookContext) -> HookResult:
        """
        Spawn new objectives based on failure data.

        Args:
            context: Hook context with failure details

        Returns:
            HookResult with spawned objective count
        """
        project: Optional[ProjectObjectives] = self.config.get("project")
        max_spawned = self.config.get("max_spawned", 5)
        priority = self.config.get("priority", 3)

        if project is None:
            return HookResult.failure_result(
                error_message="No project in TaskSpawnHook config",
            )

        # Get execution result from context
        exec_result = context.data.get("execution_result")
        if exec_result is None:
            return HookResult.success_result(
                metadata={"spawned": 0, "reason": "No execution result"},
            )

        # Extract error as a potential defect
        error_msg = getattr(exec_result, "error_message", None) or context.data.get(
            "error", ""
        )

        spawned: List[str] = []

        # Spawn a remediation objective for the error
        if error_msg and len(spawned) < max_spawned:
            remediation = Objective(
                title=f"Fix: {context.data.get('objective_title', 'Unknown')} failure",
                description=(
                    f"Remediation for failed objective: {error_msg}"
                ),
                status=ObjectiveStatus.QUEUED,
                dependencies=[context.phase or ""],
                priority=priority,
                phase="QUALITY",
            )
            project.add_objective(remediation)
            spawned.append(remediation.objective_id)
            logger.info(
                f"TaskSpawnHook spawned remediation: {remediation.title}",
                extra={"objective_id": remediation.objective_id},
            )

        return HookResult.success_result(
            metadata={
                "spawned": len(spawned),
                "spawned_ids": spawned,
            },
        )
