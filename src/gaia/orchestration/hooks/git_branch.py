"""
GitBranchHook — Auto-create feature branch on OBJECTIVE_START.

Creates a git branch named `obj/{id}-{slug}` when an objective starts,
enabling isolated work per objective.
"""

import logging
import re
from typing import Any, Dict, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult

from gaia.orchestration.supervisors.git import GitSupervisor

logger = logging.getLogger(__name__)


class GitBranchHook(BaseHook):
    """
    Hook that auto-creates a feature branch when an objective starts.

    Listens for OBJECTIVE_START events and creates a branch named
    `obj/{objective_id}-{slugified-title}`.

    Configuration:
        config["git_supervisor"]: GitSupervisor instance for git operations

    Example:
        >>> hook = GitBranchHook(config={
        ...     "git_supervisor": my_git_supervisor,
        ... })
        >>> orchestrator.hook_registry.register(hook)
    """

    name = "git_branch"
    event = "OBJECTIVE_START"
    priority = HookPriority.HIGH
    blocking = False
    description = "Auto-create feature branch on objective start"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

    @staticmethod
    def _slugify(title: str) -> str:
        """
        Convert a title to a URL-safe slug.

        Args:
            title: The objective title to slugify.

        Returns:
            Lowercase slug with hyphens, max 50 chars.
        """
        slug = re.sub(r'[^a-z0-9\s-]', '', title.lower().strip())
        slug = re.sub(r'\s+', '-', slug)
        slug = slug.strip('-')
        return slug[:50]

    async def execute(self, context: HookContext) -> HookResult:
        """
        Create a feature branch for the objective.

        Args:
            context: Hook context with objective data

        Returns:
            HookResult with branch name in inject_context
        """
        git_supervisor: Optional[GitSupervisor] = self.config.get("git_supervisor")

        if git_supervisor is None:
            logger.debug("GitBranchHook: no git_supervisor in config, skipping")
            return HookResult.success_result(skipped=True)

        try:
            objective_id = context.data.get("objective_id", "unknown")
            objective_title = context.data.get("objective_title", "untitled")

            slug = self._slugify(objective_title)
            branch_name = f"obj/{objective_id}-{slug}"

            created = git_supervisor.create_branch(branch_name)

            if created:
                logger.info(
                    f"GitBranchHook created branch: {branch_name}",
                    extra={"objective_id": objective_id},
                )
                return HookResult.success_result(
                    inject_context={"_git_branch": branch_name},
                    metadata={"branch": branch_name, "created": True},
                )
            else:
                logger.warning(
                    f"GitBranchHook failed to create branch: {branch_name}",
                    extra={"objective_id": objective_id},
                )
                return HookResult.failure_result(
                    error_message=f"Failed to create branch: {branch_name}",
                )

        except Exception as e:
            logger.error(f"GitBranchHook execution failed: {e}")
            return HookResult.failure_result(
                error_message=f"GitBranchHook error: {e}",
            )
