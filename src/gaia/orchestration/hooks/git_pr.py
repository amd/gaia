"""
GitPRHook — Auto-create pull request on ORCHESTRATOR_COMPLETE.

When all objectives reach a terminal state (COMPLETED or CANCELLED),
creates a pull request summarizing the work.
"""

import logging
from typing import Any, Dict, Optional

from gaia.hooks.base import BaseHook, HookContext, HookPriority, HookResult

from gaia.orchestration.models import ObjectiveStatus, ProjectObjectives
from gaia.orchestration.supervisors.git import GitSupervisor

logger = logging.getLogger(__name__)


class GitPRHook(BaseHook):
    """
    Hook that auto-creates a PR when all objectives reach a terminal state.

    Listens for ORCHESTRATOR_COMPLETE events. If all objectives are
    COMPLETED or CANCELLED, builds a PR body with a summary table and
    creates a pull request via the GitSupervisor.

    Configuration:
        config["git_supervisor"]: GitSupervisor instance for git operations
        config["project"]: ProjectObjectives instance to inspect
        config["target_branch"]: Target branch for the PR (default: "main")

    Example:
        >>> hook = GitPRHook(config={
        ...     "git_supervisor": my_git_supervisor,
        ...     "project": my_project,
        ...     "target_branch": "develop",
        ... })
        >>> orchestrator.hook_registry.register(hook)
    """

    name = "git_pr"
    event = "ORCHESTRATOR_COMPLETE"
    priority = HookPriority.NORMAL
    blocking = False
    description = "Auto-create PR when all objectives finish"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)

    async def execute(self, context: HookContext) -> HookResult:
        """
        Create a PR if all objectives are terminal.

        Args:
            context: Hook context

        Returns:
            HookResult with PR URL in inject_context (if created)
        """
        git_supervisor: Optional[GitSupervisor] = self.config.get("git_supervisor")
        project: Optional[ProjectObjectives] = self.config.get("project")
        target_branch: str = self.config.get("target_branch", "main")

        if git_supervisor is None or project is None:
            logger.debug(
                "GitPRHook: missing git_supervisor or project in config, skipping"
            )
            return HookResult.success_result(
                metadata={"pr_created": False},
            )

        try:
            # Check if all objectives are terminal
            all_terminal = all(
                o.status in (ObjectiveStatus.COMPLETED, ObjectiveStatus.CANCELLED)
                for o in project.objectives
            )

            if not all_terminal:
                incomplete = [
                    o for o in project.objectives
                    if o.status not in (ObjectiveStatus.COMPLETED, ObjectiveStatus.CANCELLED)
                ]
                logger.info(
                    f"GitPRHook: {len(incomplete)} objectives not terminal, skipping PR"
                )
                return HookResult.success_result(
                    metadata={"pr_created": False, "reason": "objectives_not_terminal"},
                )

            if not project.objectives:
                logger.info("GitPRHook: no objectives in project, skipping PR")
                return HookResult.success_result(
                    metadata={"pr_created": False, "reason": "empty_project"},
                )

            # Build PR body — table of all objectives
            pr_title = f"Complete project: {project.name or 'Untitled'}"
            pr_body = self._build_pr_body(project)

            pr_url = git_supervisor.create_pr(
                title=pr_title,
                body=pr_body,
                target_branch=target_branch,
            )

            if pr_url:
                logger.info(
                    f"GitPRHook created PR: {pr_url}",
                    extra={"project_id": project.project_id},
                )
                return HookResult.success_result(
                    inject_context={"pr_url": pr_url},
                    metadata={"pr_created": True, "pr_url": pr_url},
                )
            else:
                logger.warning(
                    "GitPRHook failed to create PR",
                    extra={"project_id": project.project_id},
                )
                return HookResult.failure_result(
                    error_message="Failed to create PR",
                )

        except Exception as e:
            logger.error(f"GitPRHook execution failed: {e}")
            return HookResult.failure_result(
                error_message=f"GitPRHook error: {e}",
            )

    @staticmethod
    def _build_pr_body(project: ProjectObjectives) -> str:
        """
        Build a markdown PR body with a table of objectives.

        Args:
            project: The ProjectObjectives instance.

        Returns:
            Markdown string for the PR body.
        """
        lines = [
            f"## Project: {project.name or 'Untitled'}",
            "",
            "| ID | Title | Status |",
            "|----|-------|--------|",
        ]

        for obj in project.objectives:
            lines.append(
                f"| {obj.objective_id} | {obj.title} | {obj.status.value} |"
            )

        lines.append("")
        lines.append(f"**Total:** {len(project.objectives)} objectives")

        return "\n".join(lines)
