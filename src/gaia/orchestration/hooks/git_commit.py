"""
GitCommitHook — Auto-commit objectives YAML on OBJECTIVE_COMPLETE.

Commits the objectives YAML file after an objective completes successfully.
"""

import logging
from typing import Any, Dict, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult

from gaia.orchestration.supervisors.git import GitSupervisor

logger = logging.getLogger(__name__)


class GitCommitHook(BaseHook):
    """
    Hook that auto-commits the objectives YAML after an objective completes.

    Listens for OBJECTIVE_COMPLETE events and commits the objectives file
    with a descriptive commit message.

    Configuration:
        config["git_supervisor"]: GitSupervisor instance for git operations
        config["objectives_path"]: Path to objectives YAML file
            (default: ".gaia/objectives.yaml")

    Example:
        >>> hook = GitCommitHook(config={
        ...     "git_supervisor": my_git_supervisor,
        ...     "objectives_path": ".gaia/objectives.yaml",
        ... })
        >>> orchestrator.hook_registry.register(hook)
    """

    name = "git_commit"
    event = "OBJECTIVE_COMPLETE"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Auto-commit objectives YAML on objective completion"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

    async def execute(self, context: HookContext) -> HookResult:
        """
        Commit the objectives YAML file.

        Args:
            context: Hook context with objective data

        Returns:
            HookResult with commit status in metadata
        """
        git_supervisor: Optional[GitSupervisor] = self.config.get("git_supervisor")
        objectives_path: str = self.config.get(
            "objectives_path", ".gaia/objectives.yaml"
        )

        if git_supervisor is None:
            logger.debug("GitCommitHook: no git_supervisor in config, skipping")
            return HookResult.success_result(
                metadata={"committed": False},
            )

        try:
            objective_id = context.data.get("objective_id", "unknown")
            objective_title = context.data.get("objective_title", "untitled")

            commit_msg = (
                f"chore(objectives): complete {objective_title} ({objective_id})"
            )

            committed = git_supervisor.commit(commit_msg, files=[objectives_path])

            if committed:
                logger.info(
                    f"GitCommitHook committed: {commit_msg}",
                    extra={"objective_id": objective_id},
                )
            else:
                logger.warning(
                    f"GitCommitHook failed to commit: {commit_msg}",
                    extra={"objective_id": objective_id},
                )

            return HookResult.success_result(
                metadata={"committed": committed},
            )

        except Exception as e:
            logger.error(f"GitCommitHook execution failed: {e}")
            return HookResult.failure_result(
                error_message=f"GitCommitHook error: {e}",
            )
