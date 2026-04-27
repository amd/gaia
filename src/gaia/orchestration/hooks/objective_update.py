"""
ObjectiveUpdateHook — Saves objectives YAML after objective completion.

Listens for OBJECTIVE_COMPLETE events and performs an atomic save
of the ProjectObjectives to disk.
"""

import logging
from typing import Any, Dict, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult

from gaia.orchestration.models import ProjectObjectives

logger = logging.getLogger(__name__)


class ObjectiveUpdateHook(BaseHook):
    """
    Hook that saves the objectives YAML after an objective completes.

    Listens for OBJECTIVE_COMPLETE events (orchestrator-specific event)
    and performs an atomic save of the ProjectObjectives to disk.

    This ensures that objectives.yaml is always up to date with the
    latest status transitions.

    Configuration:
        config["project"]: ProjectObjectives instance to save
        config["path"]: File path to save to

    Example:
        >>> hook = ObjectiveUpdateHook(config={
        ...     "project": my_project,
        ...     "path": ".gaia/objectives.yaml",
        ... })
        >>> orchestrator.hook_registry.register(hook)
    """

    name = "objective_update"
    event = "OBJECTIVE_COMPLETE"
    priority = HookPriority.HIGH
    blocking = False
    description = "Save objectives YAML after objective completion"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the hook.

        Args:
            config: Must contain 'project' (ProjectObjectives) and 'path' (str)
        """
        super().__init__(config)

    async def execute(self, context: HookContext) -> HookResult:
        """
        Save objectives to disk.

        Args:
            context: Hook context with objective completion data

        Returns:
            HookResult indicating save success/failure
        """
        project: Optional[ProjectObjectives] = self.config.get("project")
        path: str = self.config.get("path", ".gaia/objectives.yaml")

        if project is None:
            return HookResult.failure_result(
                error_message="No project in ObjectiveUpdateHook config",
            )

        try:
            project.save_atomic(path)
            logger.info(
                f"ObjectiveUpdateHook saved objectives to {path}",
                extra={"project_id": project.project_id},
            )
            return HookResult.success_result(
                metadata={"path": path, "project_id": project.project_id},
            )
        except Exception as e:
            logger.error(f"ObjectiveUpdateHook save failed: {e}")
            return HookResult.failure_result(
                error_message=f"Failed to save objectives: {e}",
            )
