"""
Pipeline Isolation - Context Manager for Isolated Pipeline Execution.

Provides isolated workspace contexts for pipeline execution with:
- Automatic workspace creation and cleanup
- Cross-pipeline state leakage prevention
- Hash-named isolation directories
- Thread-safe operations

Example:
    >>> with PipelineIsolation(pipeline_id="run-123") as isolation:
    ...     workspace_path = isolation.get_workspace_path("src/main.py")
    ...     # Write files in isolated context
    ...     workspace_path.write_text("print('hello')")
    >>> # Workspace automatically cleaned up on exit
"""

import hashlib
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class PipelineIsolationError(Exception):
    """Exception raised for pipeline isolation violations."""

    def __init__(self, message: str, pipeline_id: Optional[str] = None):
        """
        Initialize PipelineIsolationError.

        Args:
            message: Error description
            pipeline_id: Pipeline ID that triggered the error (if applicable)
        """
        super().__init__(message)
        self.pipeline_id = pipeline_id
        self.timestamp = time.time()

    def __str__(self) -> str:
        parts = [self.args[0]]
        if self.pipeline_id:
            parts.append(f"pipeline_id={self.pipeline_id}")
        return f"PipelineIsolationError({', '.join(parts)})"


class PipelineIsolation:
    """
    Context manager for isolated pipeline execution.

    PipelineIsolation provides:
    1. Automatic creation of isolated workspace directories
    2. Hash-named isolation for privacy
    3. Automatic cleanup on context exit
    4. Cross-pipeline state leakage prevention
    5. Thread-safe concurrent pipeline execution

    Features:
    - Hash-named workspace directories (ws_<hash>)
    - Automatic cleanup on context exit
    - Optional persistence for debugging
    - Thread-safe for concurrent pipelines
    - Statistics tracking for monitoring

    Thread Safety:
    - All operations protected by RLock
    - Safe for concurrent pipeline execution
    - Isolated state per pipeline

    Example:
        >>> with PipelineIsolation(
        ...     pipeline_id="run-123",
        ...     workspace_root="/tmp/isolated"
        ... ) as isolation:
        ...     path = isolation.get_workspace_path("src/main.py")
        ...     path.parent.mkdir(parents=True, exist_ok=True)
        ...     path.write_text("print('hello')")
        ...     print(path.exists())  # True
        >>> # Workspace cleaned up automatically
    """

    def __init__(
        self,
        pipeline_id: str,
        workspace_root: Optional[str] = None,
        persist: bool = False,
        cleanup_on_exit: bool = True,
    ):
        """
        Initialize PipelineIsolation.

        Args:
            pipeline_id: Unique identifier for this pipeline run
            workspace_root: Root directory for isolated workspaces
            persist: Whether to persist workspace after exit (default: False)
            cleanup_on_exit: Whether to cleanup on context exit (default: True)

        Example:
            >>> isolation = PipelineIsolation(
            ...     pipeline_id="run-123",
            ...     workspace_root="/tmp/workspaces"
            ... )
        """
        self._pipeline_id = pipeline_id
        self._persist = persist
        self._cleanup_on_exit = cleanup_on_exit
        self._lock = threading.RLock()

        # Setup workspace root
        if workspace_root:
            self._workspace_root = Path(workspace_root)
        else:
            self._workspace_root = Path.home() / ".gaia" / "isolated"

        # Create workspace root directory
        self._workspace_root.mkdir(parents=True, exist_ok=True)

        # Initialize statistics BEFORE creating workspace (order matters)
        self._stats = {
            "created_at": time.time(),
            "files_created": 0,
            "operations": 0,
        }

        # Track active pipelines for cross-pipeline leakage detection
        self._active_pipelines: Set[str] = set()

        # Generate hash-named workspace directory
        self._workspace_path = self._create_isolated_workspace()

        logger.info(
            "PipelineIsolation initialized",
            extra={
                "pipeline_id": pipeline_id,
                "workspace_path": str(self._workspace_path),
                "persist": persist,
            }
        )

    def _create_isolated_workspace(self) -> Path:
        """
        Create hash-named isolated workspace directory.

        Returns:
            Path to the created workspace directory
        """
        # Create hash from pipeline_id
        hash_digest = hashlib.sha256(self._pipeline_id.encode("utf-8")).hexdigest()[:16]
        workspace_name = f"ws_{hash_digest}"
        workspace_path = self._workspace_root / workspace_name

        # Create directory with unique suffix if exists
        if workspace_path.exists():
            timestamp = int(time.time() * 1000)
            workspace_name = f"ws_{hash_digest}_{timestamp}"
            workspace_path = self._workspace_root / workspace_name

        workspace_path.mkdir(parents=True, exist_ok=True)

        self._stats["workspace_path"] = str(workspace_path)

        return workspace_path

    def __enter__(self) -> "PipelineIsolation":
        """
        Enter the isolation context.

        Returns:
            Self for context manager usage

        Example:
            >>> with PipelineIsolation("run-123") as isolation:
            ...     # Use isolation
            ...     pass
        """
        with self._lock:
            self._active_pipelines.add(self._pipeline_id)
            logger.debug(
                f"Entered isolation context: {self._pipeline_id}",
                extra={"workspace": str(self._workspace_path)}
            )
            return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Exit the isolation context.

        Performs cleanup if cleanup_on_exit is True and persist is False.

        Args:
            exc_type: Exception type (if any)
            exc_val: Exception value (if any)
            exc_tb: Exception traceback (if any)

        Example:
            >>> with PipelineIsolation("run-123") as isolation:
            ...     # Work in isolation
            ...     pass
            >>> # Automatically cleaned up
        """
        with self._lock:
            self._active_pipelines.discard(self._pipeline_id)

            if self._cleanup_on_exit and not self._persist:
                self._cleanup()
            else:
                logger.info(
                    f"Pipeline workspace preserved: {self._workspace_path}",
                    extra={"pipeline_id": self._pipeline_id}
                )

            logger.debug(
                f"Exited isolation context: {self._pipeline_id}",
                extra={
                    "workspace": str(self._workspace_path),
                    "cleaned": self._cleanup_on_exit and not self._persist,
                }
            )

    def _cleanup(self) -> bool:
        """
        Clean up isolated workspace.

        Returns:
            True if cleanup successful, False otherwise
        """
        try:
            if self._workspace_path.exists():
                shutil.rmtree(self._workspace_path)
                logger.info(
                    f"Cleaned up workspace: {self._workspace_path}",
                    extra={"pipeline_id": self._pipeline_id}
                )
                return True
            return False
        except Exception as e:
            logger.error(
                f"Failed to cleanup workspace: {e}",
                extra={"pipeline_id": self._pipeline_id, "error": str(e)}
            )
            return False

    def get_workspace_path(self, path: str) -> Path:
        """
        Get path within isolated workspace.

        Args:
            path: Relative path within workspace

        Returns:
            Full path within isolated workspace

        Raises:
            PipelineIsolationError: If path attempts to escape workspace

        Example:
            >>> with PipelineIsolation("run-123") as isolation:
            ...     full_path = isolation.get_workspace_path("src/main.py")
            ...     print(str(full_path))
            '/home/user/.gaia/isolated/ws_abc123/src/main.py'
        """
        with self._lock:
            # Security check: prevent path traversal
            if ".." in path:
                raise PipelineIsolationError(
                    f"Path traversal not allowed: {path}",
                    pipeline_id=self._pipeline_id
                )

            # Normalize path
            normalized = path.replace("\\", "/").lstrip("/")

            # Construct full path
            full_path = self._workspace_root / self._workspace_path.name / normalized

            self._stats["operations"] += 1

            return full_path.absolute()

    def get_workspace_root(self) -> Path:
        """
        Get the root of the isolated workspace.

        Returns:
            Path to the isolated workspace root

        Example:
            >>> with PipelineIsolation("run-123") as isolation:
            ...     root = isolation.get_workspace_root()
            ...     print(str(root).startswith("/"))
            True
        """
        with self._lock:
            return self._workspace_path

    def get_pipeline_id(self) -> str:
        """
        Get the pipeline ID.

        Returns:
            Pipeline identifier string
        """
        return self._pipeline_id

    def is_active(self) -> bool:
        """
        Check if this isolation context is active.

        Returns:
            True if currently in context, False otherwise
        """
        with self._lock:
            return self._pipeline_id in self._active_pipelines

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get isolation statistics.

        Returns:
            Dictionary with:
            - pipeline_id: Pipeline identifier
            - workspace_path: Path to workspace
            - created_at: Creation timestamp
            - files_created: Number of files created
            - operations: Number of operations performed
            - is_active: Whether context is active

        Example:
            >>> with PipelineIsolation("run-123") as isolation:
            ...     stats = isolation.get_statistics()
            ...     print(stats["is_active"])
            True
        """
        with self._lock:
            return {
                **self._stats,
                "pipeline_id": self._pipeline_id,
                "is_active": self.is_active(),
            }

    def mark_persist(self, persist: bool = True) -> None:
        """
        Set persistence flag.

        Args:
            persist: Whether to persist workspace on exit

        Example:
            >>> with PipelineIsolation("run-123") as isolation:
            ...     isolation.mark_persist(True)  # Don't cleanup
        """
        with self._lock:
            self._persist = persist
            logger.debug(
                f"Persist flag set to: {persist}",
                extra={"pipeline_id": self._pipeline_id}
            )

    @staticmethod
    @contextmanager
    def temporary_isolation(pipeline_id: str, workspace_root: Optional[str] = None):
        """
        Create a temporary isolation context (class method).

        Convenience method for creating temporary isolated workspaces
        that are always cleaned up on exit.

        Args:
            pipeline_id: Unique identifier for this pipeline run
            workspace_root: Optional root directory for workspaces

        Yields:
            PipelineIsolation instance

        Example:
            >>> with PipelineIsolation.temporary_isolation("run-123") as iso:
            ...     path = iso.get_workspace_path("test.txt")
            ...     path.write_text("content")
        """
        isolation = PipelineIsolation(
            pipeline_id=pipeline_id,
            workspace_root=workspace_root,
            persist=False,
            cleanup_on_exit=True,
        )
        try:
            with isolation:
                yield isolation
        finally:
            # Ensure cleanup even on exception
            if not isolation._persist:
                isolation._cleanup()


class PipelineIsolationManager:
    """
    Manager for multiple concurrent pipeline isolations.

    PipelineIsolationManager provides:
    1. Tracking of active pipeline isolations
    2. Cross-pipeline leakage detection
    3. Global statistics and monitoring
    4. Thread-safe concurrent management

    Example:
        >>> manager = PipelineIsolationManager()
        >>> with manager.create_isolation("run-123") as isolation:
        ...     # Work in isolation
        ...     pass
        >>> print(manager.get_statistics())
    """

    def __init__(self, workspace_root: Optional[str] = None):
        """
        Initialize PipelineIsolationManager.

        Args:
            workspace_root: Root directory for isolated workspaces
        """
        self._workspace_root = workspace_root
        self._active_isolations: Dict[str, PipelineIsolation] = {}
        self._lock = threading.RLock()
        self._stats = {
            "total_created": 0,
            "total_cleaned": 0,
            "leakage_attempts": 0,
        }

        logger.info("PipelineIsolationManager initialized")

    def create_isolation(
        self,
        pipeline_id: str,
        persist: bool = False,
    ) -> PipelineIsolation:
        """
        Create a new pipeline isolation.

        Args:
            pipeline_id: Unique identifier for this pipeline run
            persist: Whether to persist workspace after exit

        Returns:
            PipelineIsolation instance

        Raises:
            PipelineIsolationError: If pipeline_id already active
        """
        with self._lock:
            if pipeline_id in self._active_isolations:
                self._stats["leakage_attempts"] += 1
                raise PipelineIsolationError(
                    f"Pipeline already active: {pipeline_id}",
                    pipeline_id=pipeline_id
                )

            isolation = PipelineIsolation(
                pipeline_id=pipeline_id,
                workspace_root=self._workspace_root,
                persist=persist,
            )

            self._active_isolations[pipeline_id] = isolation
            self._stats["total_created"] += 1

            logger.debug(
                f"Created isolation for pipeline: {pipeline_id}",
                extra={"workspace": str(isolation.get_workspace_root())}
            )

            return isolation

    def remove_isolation(self, pipeline_id: str) -> None:
        """
        Remove a pipeline isolation from tracking.

        Args:
            pipeline_id: Pipeline ID to remove
        """
        with self._lock:
            if pipeline_id in self._active_isolations:
                del self._active_isolations[pipeline_id]
                self._stats["total_cleaned"] += 1
                logger.debug(f"Removed isolation for pipeline: {pipeline_id}")

    def get_active_count(self) -> int:
        """
        Get count of active isolations.

        Returns:
            Number of active pipeline isolations
        """
        with self._lock:
            return len(self._active_isolations)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get manager statistics.

        Returns:
            Dictionary with global statistics
        """
        with self._lock:
            return {
                **self._stats,
                "active_count": len(self._active_isolations),
                "active_pipeline_ids": list(self._active_isolations.keys()),
            }

    @contextmanager
    def isolation_context(self, pipeline_id: str, persist: bool = False):
        """
        Create managed isolation context with automatic tracking.

        Args:
            pipeline_id: Unique identifier for this pipeline run
            persist: Whether to persist workspace after exit

        Yields:
            PipelineIsolation instance

        Example:
            >>> manager = PipelineIsolationManager()
            >>> with manager.isolation_context("run-123") as iso:
            ...     path = iso.get_workspace_path("test.txt")
        """
        isolation = self.create_isolation(pipeline_id, persist)
        try:
            with isolation:
                yield isolation
        finally:
            self.remove_isolation(pipeline_id)
            if not persist:
                isolation._cleanup()
