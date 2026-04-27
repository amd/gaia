"""
GitSupervisor — Git operations with CircuitBreaker protection.

Provides safe, non-throwing git operations for the orchestration layer:
- Branch creation, commit, push, PR creation, rollback
- Changed file detection between branches
- Operation logging and statistics
- CircuitBreaker protection against cascading git failures

All public methods return False/None on failure and NEVER raise.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.exceptions import GitOperationError
from gaia.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Data Types
# ============================================================================


@dataclass
class GitOperation:
    """
    Record of a single git operation.

    Attributes:
        operation: Operation name (e.g., "create_branch", "commit")
        branch: Git branch the operation targeted
        message: Human-readable description
        success: Whether the operation succeeded
        timestamp: ISO-formatted timestamp of the operation
        error: Error description if operation failed
    """

    operation: str
    branch: str
    message: str
    success: bool
    timestamp: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "operation": self.operation,
            "branch": self.branch,
            "message": self.message,
            "success": self.success,
            "timestamp": self.timestamp,
            "error": self.error,
        }


# ============================================================================
# GitSupervisor
# ============================================================================


class GitSupervisor:
    """
    Manages git operations with CircuitBreaker protection.

    All public methods are safe: they return False or None on failure
    and never raise exceptions. Thread-safe with RLock.

    Example:
        >>> supervisor = GitSupervisor()
        >>> supervisor.create_branch("feature/new-auth")
        >>> supervisor.commit("feat: add auth module")
        >>> supervisor.push()
    """

    def __init__(
        self,
        repo_path: Path = Path("."),
        git_user_name: str = "GAIA Orchestrator",
        git_user_email: str = "orchestrator@gaia.local",
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        """
        Initialize GitSupervisor.

        Args:
            repo_path: Path to the git repository.
            git_user_name: Name for git commit author.
            git_user_email: Email for git commit author.
            circuit_breaker_config: Optional custom circuit breaker config.
                Defaults: failure_threshold=3, recovery_timeout=60s, success_threshold=2.
        """
        self._repo_path = repo_path
        self._git_user_name = git_user_name
        self._git_user_email = git_user_email
        self._lock = threading.RLock()
        self._operation_log: List[GitOperation] = []

        if circuit_breaker_config is None:
            circuit_breaker_config = CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=60.0,
                success_threshold=2,
            )
        self._circuit_breaker = CircuitBreaker(config=circuit_breaker_config)

        logger.info(
            "GitSupervisor initialized",
            extra={
                "repo_path": str(self._repo_path),
                "git_user_name": self._git_user_name,
                "git_user_email": self._git_user_email,
            },
        )

    # -----------------------------------------------------------------------
    # Public API — all methods return False/None on failure, never raise
    # -----------------------------------------------------------------------

    def create_branch(
        self, branch_name: str, base_branch: Optional[str] = None
    ) -> bool:
        """
        Create a new git branch from the specified base branch.

        Args:
            branch_name: Name of the branch to create.
            base_branch: Branch to branch from. Defaults to current branch.

        Returns:
            True if branch was created successfully, False otherwise.
        """
        def _do_create() -> bool:
            actual_base = base_branch
            if actual_base is None:
                actual_base = self._get_current_branch()
                if actual_base is None:
                    raise GitOperationError(
                        "create_branch",
                        "Could not determine current branch",
                        branch_name,
                    )
            self._run_git(["checkout", actual_base])
            result = self._run_git(["checkout", "-b", branch_name])
            if result is None:
                raise GitOperationError("create_branch", f"checkout -b failed", branch_name)
            return True

        return self._protected(
            "create_branch", _do_create, branch_name, f"Create branch '{branch_name}'"
        )

    def commit(self, message: str, files: Optional[List[str]] = None) -> bool:
        """
        Stage files and create a git commit.

        Args:
            message: Commit message.
            files: List of files to stage. If None, stages all changes.

        Returns:
            True if commit was successful, False otherwise.
        """

        def _do_commit() -> bool:
            if files:
                for f in files:
                    self._run_git(["add", f])
            else:
                self._run_git(["add", "-A"])

            result = self._run_git(
                [
                    "commit",
                    "-m",
                    message,
                    "--author",
                    f"{self._git_user_name} <{self._git_user_email}>",
                ]
            )
            if result is None:
                raise GitOperationError("commit", "commit failed", self._get_current_branch() or "")
            return True

        return self._protected(
            "commit", _do_commit, self._get_current_branch() or "", message
        )

    def push(self, remote: str = "origin", branch: Optional[str] = None) -> bool:
        """
        Push current branch to remote.

        Args:
            remote: Remote name.
            branch: Branch to push. Defaults to current branch.

        Returns:
            True if push was successful, False otherwise.
        """
        if branch is None:
            branch = self._get_current_branch()
            if branch is None:
                self._record_operation(
                    "push",
                    "unknown",
                    "Failed to determine current branch for push",
                    False,
                    "Could not determine current branch",
                )
                return False

        def _do_push() -> bool:
            result = self._run_git(["push", remote, branch])
            if result is None:
                raise GitOperationError("push", "push failed", branch)
            return True

        return self._protected(
            "push", _do_push, branch, f"Push '{branch}' to '{remote}'"
        )

    def create_pr(
        self,
        title: str,
        body: str,
        target_branch: str = "main",
        source_branch: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a pull request via git hub CLI (gh).

        Args:
            title: PR title.
            body: PR description.
            target_branch: Target branch for the PR.
            source_branch: Source branch. Defaults to current branch.

        Returns:
            PR URL if successful, None otherwise.
        """
        if source_branch is None:
            source_branch = self._get_current_branch()
            if source_branch is None:
                self._record_operation(
                    "create_pr",
                    "unknown",
                    "Failed to determine current branch for PR",
                    False,
                    "Could not determine current branch",
                )
                return None

        def _do_create_pr() -> Optional[str]:
            result = self._run_git(
                [
                    "hub",
                    "pull-request",
                    "-b",
                    target_branch,
                    "-h",
                    source_branch,
                    "-m",
                    title,
                    "-m",
                    body,
                ]
            )
            if result is None:
                raise GitOperationError("create_pr", "create PR failed", source_branch)
            return result.strip()

        outcome = self._protected(
            "create_pr",
            _do_create_pr,
            source_branch,
            f"Create PR: {title}",
            returns_str=True,
        )
        return outcome

    def rollback(self, branch_name: str, to_commit: str = "HEAD~1") -> bool:
        """
        Rollback a branch to a previous commit.

        Args:
            branch_name: Branch to rollback.
            to_commit: Commit to rollback to (default: HEAD~1).

        Returns:
            True if rollback was successful, False otherwise.
        """

        def _do_rollback() -> bool:
            self._run_git(["checkout", branch_name])
            result = self._run_git(["reset", "--hard", to_commit])
            if result is None:
                raise GitOperationError("rollback", "reset failed", branch_name)
            return True

        return self._protected(
            "rollback",
            _do_rollback,
            branch_name,
            f"Rollback '{branch_name}' to '{to_commit}'",
        )

    def detect_changed_files(
        self, source_branch: str, target_branch: str = "main"
    ) -> List[str]:
        """
        Detect files that differ between source and target branches.

        Args:
            source_branch: Branch to compare from.
            target_branch: Branch to compare against.

        Returns:
            List of changed file paths. Empty list on failure.
        """

        def _do_detect() -> List[str]:
            result = self._run_git(
                ["diff", "--name-only", f"{target_branch}...{source_branch}"]
            )
            if result is None:
                raise GitOperationError(
                    "detect_changed_files",
                    "diff failed",
                    source_branch,
                )
            return [f for f in result.strip().split("\n") if f]

        outcome = self._protected(
            "detect_changed_files",
            _do_detect,
            source_branch,
            f"Detect changed files between '{target_branch}' and '{source_branch}'",
            returns_list=True,
        )
        return outcome if outcome is not None else []

    # -----------------------------------------------------------------------
    # Logging and Statistics
    # -----------------------------------------------------------------------

    def get_operation_log(self) -> List[Dict[str, Any]]:
        """
        Get the operation log as a list of dictionaries.

        Returns:
            List of operation records as JSON-safe dicts.
        """
        with self._lock:
            return [op.to_dict() for op in self._operation_log]

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about git operations.

        Returns:
            Dictionary with total operations, successes, failures,
            and circuit breaker state.
        """
        with self._lock:
            total = len(self._operation_log)
            successes = sum(1 for op in self._operation_log if op.success)
            failures = total - successes
            return {
                "total_operations": total,
                "successes": successes,
                "failures": failures,
                "circuit_breaker_state": self._circuit_breaker.state,
            }

    # -----------------------------------------------------------------------
    # Protected Execution
    # -----------------------------------------------------------------------

    def _protected(
        self,
        operation: str,
        func: Callable[[], Any],
        branch: str,
        message: str,
        returns_str: bool = False,
        returns_list: bool = False,
    ) -> Any:
        """
        Execute a git operation through the CircuitBreaker.

        Logs both success and failure outcomes. All exceptions are caught
        internally — this method never raises.

        Args:
            operation: Operation name for logging.
            func: Callable that performs the git operation.
            branch: Branch the operation targets.
            message: Human-readable description.
            returns_str: If True, expect str return type.
            returns_list: If True, expect List[str] return type.

        Returns:
            Operation result (bool, str, List[str], or None) on success.
            False/None on failure.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            result = self._circuit_breaker.call(func)

            # Success
            self._record_operation(operation, branch, message, True, None)
            logger.info(
                f"GitSupervisor: {operation} succeeded on '{branch}' — {message}"
            )
            return result

        except CircuitOpenError as e:
            # Circuit is open — fail fast without executing
            self._record_operation(
                operation, branch, message, False, "Circuit breaker is open"
            )
            logger.warning(
                f"GitSupervisor: {operation} rejected — circuit breaker open"
            )
            return None if (returns_str or returns_list) else False

        except Exception as e:
            # Unexpected error
            error_msg = str(e)
            self._record_operation(operation, branch, message, False, error_msg)
            logger.error(
                f"GitSupervisor: {operation} failed on '{branch}' — {error_msg}"
            )
            return None if (returns_str or returns_list) else False

    # -----------------------------------------------------------------------
    # Internal Helpers
    # -----------------------------------------------------------------------

    def _run_git(self, args: List[str]) -> Optional[str]:
        """
        Execute a git command.

        Args:
            args: Git command arguments (without 'git' prefix).

        Returns:
            stdout string on success, None on failure.
        """
        try:
            result = subprocess.run(
                ["git"] + args,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self._repo_path),
            )
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning(
                    f"Git command failed: git {' '.join(args)} — {result.stderr.strip()}"
                )
                return None
        except subprocess.TimeoutExpired:
            logger.warning(f"Git command timed out: git {' '.join(args)}")
            return None
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.warning(f"Git command failed: git {' '.join(args)} — {e}")
            return None

    def _get_current_branch(self) -> Optional[str]:
        """
        Get the current git branch name.

        Returns:
            Branch name string, or None on failure.
        """
        result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if result is not None:
            return result.strip()
        return None

    def _record_operation(
        self,
        operation: str,
        branch: str,
        message: str,
        success: bool,
        error: Optional[str],
    ) -> None:
        """Record a git operation in the log (thread-safe)."""
        with self._lock:
            timestamp = datetime.now(timezone.utc).isoformat()
            self._operation_log.append(
                GitOperation(
                    operation=operation,
                    branch=branch,
                    message=message,
                    success=success,
                    timestamp=timestamp,
                    error=error,
                )
            )
