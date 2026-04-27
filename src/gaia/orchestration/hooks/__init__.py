"""
GAIA Orchestration Hooks Package.

Provides hooks for git automation and objective lifecycle management:
    - ObjectiveUpdateHook: Saves objectives YAML on completion
    - TaskSpawnHook: Generates remediation objectives from failures
    - GitBranchHook: Auto-creates feature branches on objective start
    - GitCommitHook: Auto-commits objectives YAML on completion
    - GitPRHook: Auto-creates PRs when all objectives finish
    - GitRollbackHook: Rolls back branch on objective failure
"""

from gaia.orchestration.hooks.git_branch import GitBranchHook
from gaia.orchestration.hooks.git_commit import GitCommitHook
from gaia.orchestration.hooks.git_pr import GitPRHook
from gaia.orchestration.hooks.git_rollback import GitRollbackHook
from gaia.orchestration.hooks.objective_update import ObjectiveUpdateHook
from gaia.orchestration.hooks.task_spawn import TaskSpawnHook

__all__ = [
    "GitBranchHook",
    "GitCommitHook",
    "GitPRHook",
    "GitRollbackHook",
    "ObjectiveUpdateHook",
    "TaskSpawnHook",
]
