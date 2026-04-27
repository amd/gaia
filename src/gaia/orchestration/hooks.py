"""
GAIA Orchestration Hooks — Backward-compatible re-exports.

All hooks have been moved to the gaia.orchestration.hooks package.
This module re-exports them for backward compatibility.

Available hooks:
    - ObjectiveUpdateHook: Saves objectives YAML on PHASE_EXIT
    - TaskSpawnHook: Generates new objectives from defects/gaps
    - GitBranchHook: Auto-creates feature branches on objective start
    - GitCommitHook: Auto-commits objectives YAML on completion
    - GitPRHook: Auto-creates PRs when all objectives finish
    - GitRollbackHook: Rolls back branch on objective failure
"""

from gaia.orchestration.hooks import (
    GitBranchHook,
    GitCommitHook,
    GitPRHook,
    GitRollbackHook,
    ObjectiveUpdateHook,
    TaskSpawnHook,
)

__all__ = [
    "GitBranchHook",
    "GitCommitHook",
    "GitPRHook",
    "GitRollbackHook",
    "ObjectiveUpdateHook",
    "TaskSpawnHook",
]
