"""
GitRollbackHook — Rollback branch on OBJECTIVE_FAILED.

When an objective fails, rolls back the associated feature branch
to undo any partial work.
"""

import logging
from typing import Any, Dict, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult

from gaia.orchestration.supervisors.git import GitSupervisor

logger = logging.getLogger(__name__)


class GitRollbackHook(BaseHook):
    """
    Hook that rolls back a feature branch when an objective fails.

    Listens for OBJECTIVE_FAILED events and rolls back the branch
    (identified via context.data["_git_branch"]) to the previous commit.

    Configuration:
        config["git_supervisor"]: GitSupervisor instance for git operations

    Example:
        >>> hook = GitRollbackHook(config={
        ...     "git_supervisor": my_git_supervisor,
        ... })
        >>> orchestrator.hook_registry.register(hook)
    """

    name = "git_rollback"
    event = "OBJECTIVE_FAILED"
    priority = HookPriority.HIGH
    blocking = False
    description = "Rollback branch on objective failure"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

    async def execute(self, context: HookContext) -> HookResult:
        """
        Rollback the branch associated with the failed objective.

        Args:
            context: Hook context with branch info in data["_git_branch"]

        Returns:
            HookResult with rollback status in metadata
        """
        git_supervisor: Optional[GitSupervisor] = self.config.get("git_supervisor")

        if git_supervisor is None:
            logger.debug("GitRollbackHook: no git_supervisor in config, skipping")
            return HookResult.success_result(
                metadata={"rolled_back": False},
            )

        try:
            branch_name = context.data.get("_git_branch")

            if not branch_name:
                logger.warning(
                    "GitRollbackHook: no _git_branch in context data, skipping"
                )
                return HookResult.success_result(
                    metadata={"rolled_back": False, "reason": "no_branch"},
                )

            objective_id = context.data.get("objective_id", "unknown")
            rolled_back = git_supervisor.rollback(branch_name, "HEAD~1")

            if rolled_back:
                logger.info(
                    f"GitRollbackHook rolled back: {branch_name}",
                    extra={"objective_id": objective_id},
                )
            else:
                logger.warning(
                    f"GitRollbackHook failed to rollback: {branch_name}",
                    extra={"objective_id": objective_id},
                )

            return HookResult.success_result(
                metadata={"rolled_back": rolled_back},
            )

        except Exception as e:
            logger.error(f"GitRollbackHook execution failed: {e}")
            return HookResult.failure_result(
                error_message=f"GitRollbackHook error: {e}",
            )
