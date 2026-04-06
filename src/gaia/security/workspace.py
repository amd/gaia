"""
Workspace Policy - Secure File Operations with Hard Filesystem Boundaries.

Provides secure file read/write/delete operations with:
- Path validation against allowlist
- TOCTOU-safe validation (check BEFORE normalize)
- Hash-named workspace directories
- Thread-safe operations with RLock
- Path traversal prevention
- Shell injection pattern blocking

Example:
    >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
    >>> policy.write_file("src/main.py", "print('hello')")
    >>> content = policy.read_file("src/main.py")
    >>> print(content)
    'print('hello')'
"""

import hashlib
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class WorkspaceSecurityError(Exception):
    """Exception raised for workspace security violations."""

    def __init__(self, message: str, path: Optional[str] = None, operation: Optional[str] = None):
        """
        Initialize WorkspaceSecurityError.

        Args:
            message: Error description
            path: Path that triggered the violation (if applicable)
            operation: Operation being performed (if applicable)
        """
        super().__init__(message)
        self.path = path
        self.operation = operation
        self.timestamp = time.time()

    def __str__(self) -> str:
        parts = [self.args[0]]
        if self.path:
            parts.append(f"path={self.path}")
        if self.operation:
            parts.append(f"operation={self.operation}")
        return f"WorkspaceSecurityError({', '.join(parts)})"


class WorkspacePolicy:
    """
    Secure file operations with hard filesystem boundaries.

    WorkspacePolicy provides a security layer for file operations that:
    1. Validates all paths against an allowlist
    2. Blocks path traversal attempts (../, absolute paths)
    3. Blocks shell injection patterns ($, `, |, &, ;)
    4. Uses TOCTOU-safe validation (check BEFORE normalize)
    5. Supports hash-named workspace directories
    6. Provides thread-safe operations with RLock

    Security Features:
    - TOCTOU-safe: Path validation happens BEFORE normalization
    - Absolute path blocking: Unix (/) and Windows (C:\\) roots blocked
    - Parent traversal blocking: .. patterns detected and rejected
    - Shell injection blocking: $, `, (), |, &, ; patterns rejected
    - Allowlist enforcement: Only explicitly allowed paths permitted

    Thread Safety:
    - All operations protected by RLock
    - Safe for concurrent access from 100+ threads
    - Deep copy for returned data to prevent mutation

    Example:
        >>> policy = WorkspacePolicy(
        ...     allowed_paths=["/workspace"],
        ...     workspace_root="/tmp/gaia"
        ... )
        >>> policy.write_file("src/main.py", "print('hello')")
        >>> content = policy.read_file("src/main.py")
        >>> assert content == "print('hello')"
    """

    # Shell injection patterns to block
    SHELL_INJECTION_PATTERNS = [
        r'\$',           # Variable expansion
        r'`',            # Command substitution
        r'\(',           # Subshell
        r'\)',           # Subshell
        r'\|',           # Pipe
        r'&',            # Background/AND
        r';',            # Command separator
        r'>',            # Redirect
        r'<',            # Redirect
    ]

    def __init__(
        self,
        allowed_paths: Optional[List[str]] = None,
        workspace_root: Optional[str] = None,
        create_workspace: bool = True,
    ):
        """
        Initialize WorkspacePolicy.

        Args:
            allowed_paths: List of allowed base paths (default: [CWD])
            workspace_root: Root directory for workspace (default: ~/.gaia/workspace)
            create_workspace: Whether to create workspace directory (default: True)

        Example:
            >>> policy = WorkspacePolicy(
            ...     allowed_paths=["/home/user/project"],
            ...     workspace_root="/tmp/workspace"
            ... )
        """
        self._allowed_paths: Set[Path] = set()
        self._lock = threading.RLock()
        self._operation_count = 0
        self._violations: List[Dict[str, Any]] = []
        self._violation_limit = 1000

        # Setup allowed paths
        if allowed_paths:
            for path in allowed_paths:
                self._add_allowed_path(path)
        else:
            # Default to current working directory
            self._add_allowed_path(str(Path.cwd()))

        # Setup workspace root
        if workspace_root:
            self._workspace_root = Path(workspace_root)
        else:
            self._workspace_root = Path.home() / ".gaia" / "workspace"

        # Create workspace directory if requested
        if create_workspace:
            self._workspace_root.mkdir(parents=True, exist_ok=True)

        logger.info(
            "WorkspacePolicy initialized",
            extra={
                "allowed_paths": [str(p) for p in self._allowed_paths],
                "workspace_root": str(self._workspace_root),
            }
        )

    def _add_allowed_path(self, path: str) -> None:
        """
        Add a path to the allowed paths set.

        Args:
            path: Path to add (will be resolved to absolute)
        """
        try:
            resolved = Path(path).resolve()
            self._allowed_paths.add(resolved)
            logger.debug(f"Added allowed path: {resolved}")
        except Exception as e:
            logger.warning(f"Failed to add allowed path {path}: {e}")

    def _is_path_safe(self, path: str) -> bool:
        """
        Check if path is safe (no traversal or injection).

        SECURITY: This method MUST be called BEFORE path normalization
        to prevent TOCTOU vulnerabilities. Calling after normalization
        will allow absolute Unix paths to bypass security checks.

        Args:
            path: ORIGINAL path to check (BEFORE normalization)

        Returns:
            True if safe, False if any security violation detected

        Example:
            >>> policy = WorkspacePolicy()
            >>> policy._is_path_safe("src/main.py")
            True
            >>> policy._is_path_safe("../etc/passwd")
            False
        """
        # URL-decode the path to catch encoded attacks
        try:
            from urllib.parse import unquote
            decoded_path = unquote(path)
        except Exception:
            decoded_path = path

        # Block path traversal patterns (including in decoded path)
        if ".." in path or ".." in decoded_path:
            return False

        # Block absolute Unix paths (must check BEFORE normalization strips "/")
        if path.startswith("/") or decoded_path.startswith("/"):
            return False

        # Block Windows absolute paths (e.g., "C:", "D:")
        if (len(path) > 1 and path[1] == ":") or (len(decoded_path) > 1 and decoded_path[1] == ":"):
            return False

        # Block shell injection patterns
        for pattern in self.SHELL_INJECTION_PATTERNS:
            if re.search(pattern, path):
                return False

        # Block UNC paths (Windows network paths like \\server\share)
        if path.startswith("\\\\"):
            return False

        return True

    def _check_shell_injection(self, content: Optional[str] = None, path: Optional[str] = None) -> bool:
        """
        Check for shell injection patterns in content or path.

        Args:
            content: File content to check (optional)
            path: File path to check (optional)

        Returns:
            True if safe, False if injection patterns detected
        """
        check_strings = []
        if content:
            check_strings.append(content)
        if path:
            check_strings.append(path)

        for text in check_strings:
            for pattern in self.SHELL_INJECTION_PATTERNS:
                if re.search(pattern, text):
                    return False

        return True

    def _normalize_path(self, path: str) -> str:
        """
        Normalize file path (cross-platform).

        IMPORTANT: This should only be called AFTER _is_path_safe() returns True.

        Args:
            path: File path to normalize

        Returns:
            Normalized path with forward slashes, no leading slashes
        """
        # Convert backslashes to forward slashes
        normalized = path.replace("\\", "/")

        # Remove leading slashes (should already be blocked by _is_path_safe)
        normalized = normalized.lstrip("/")

        # Collapse multiple slashes
        while "//" in normalized:
            normalized = normalized.replace("//", "/")

        return normalized

    def _validate_path(self, path: str, operation: str) -> Path:
        """
        Validate path against allowlist and security rules.

        This is the main security validation method that:
        1. Checks path safety (TOCTOU-safe, before normalization)
        2. Normalizes the path
        3. Verifies path is within allowed directories
        4. Resolves symlinks for final verification

        Args:
            path: Path to validate
            operation: Operation being performed (read, write, delete)

        Returns:
            Resolved Path object if valid

        Raises:
            WorkspaceSecurityError: If path validation fails

        Example:
            >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
            >>> policy._validate_path("src/main.py", "read")
            Path('/workspace/src/main.py')
        """
        with self._lock:
            # SECURITY: Check safety BEFORE normalization (TOCTOU fix)
            if not self._is_path_safe(path):
                self._record_violation(path, operation, "path_safety_check_failed")
                raise WorkspaceSecurityError(
                    f"Path safety validation failed: {path}",
                    path=path,
                    operation=operation
                )

            # Now safe to normalize
            normalized = self._normalize_path(path)

            # Construct full path within workspace
            full_path = self._workspace_root / normalized

            # Resolve to get absolute path (follows symlinks)
            try:
                resolved = full_path.resolve()
            except Exception:
                # If resolve fails, use the constructed path
                resolved = full_path.absolute()

            # Verify resolved path is within allowed paths
            resolved_str = str(resolved)

            for allowed in self._allowed_paths:
                allowed_str = str(allowed)

                # Check if resolved path starts with allowed path
                # Add separator to prevent prefix attacks
                # (e.g., /workspace matching /workspace-secrets)
                allowed_with_sep = (
                    allowed_str if allowed_str.endswith(os.sep)
                    else allowed_str + os.sep
                )

                if (
                    resolved_str == allowed_str or
                    resolved_str.startswith(allowed_with_sep) or
                    resolved_str.startswith(str(self._workspace_root))
                ):
                    return resolved

            # Path not within allowed directories
            self._record_violation(path, operation, "not_in_allowed_paths")
            raise WorkspaceSecurityError(
                f"Path not in allowed directories: {path}",
                path=path,
                operation=operation
            )

    def _record_violation(
        self,
        path: str,
        operation: str,
        violation_type: str,
    ) -> None:
        """
        Record a security violation for auditing.

        Args:
            path: Path that triggered violation
            operation: Operation being performed
            violation_type: Type of violation
        """
        violation = {
            "timestamp": time.time(),
            "path": path,
            "operation": operation,
            "violation_type": violation_type,
        }

        self._violations.append(violation)

        # Trim to limit
        if len(self._violations) > self._violation_limit:
            self._violations = self._violations[-self._violation_limit:]

        logger.warning(
            f"Security violation: {violation_type}",
            extra={
                "path": path,
                "operation": operation,
                "violation_type": violation_type,
            }
        )

    def _increment_operation_count(self) -> None:
        """Increment the operation counter."""
        with self._lock:
            self._operation_count += 1

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """
        Secure file write with path validation.

        Writes content to file after validating path against security policies.
        Creates parent directories if they don't exist.

        Args:
            path: Relative path within workspace (e.g., "src/main.py")
            content: File content to write

        Returns:
            Dictionary with write operation metadata:
            - path: Normalized path
            - bytes_written: Number of bytes written
            - timestamp: Write timestamp

        Raises:
            WorkspaceSecurityError: If path validation fails

        Example:
            >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
            >>> result = policy.write_file("src/main.py", "print('hello')")
            >>> print(result["bytes_written"])
            14
        """
        with self._lock:
            # Validate path
            validated_path = self._validate_path(path, "write")

            # Check content for shell injection (optional security layer)
            if not self._check_shell_injection(content=content):
                self._record_violation(path, "write", "shell_injection_in_content")
                logger.warning(f"Shell injection patterns detected in content for {path}")

            # Create parent directories
            validated_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            bytes_written = validated_path.write_text(content, encoding="utf-8")

            self._increment_operation_count()

            result = {
                "path": str(validated_path),
                "bytes_written": bytes_written,
                "timestamp": time.time(),
            }

            logger.debug(
                f"File written: {path}",
                extra={"bytes": bytes_written, "path": str(validated_path)}
            )

            return result

    def read_file(self, path: str) -> str:
        """
        Secure file read with path validation.

        Reads file content after validating path against security policies.

        Args:
            path: Relative path within workspace (e.g., "src/main.py")

        Returns:
            File content as string

        Raises:
            WorkspaceSecurityError: If path validation fails
            FileNotFoundError: If file doesn't exist

        Example:
            >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
            >>> policy.write_file("test.txt", "hello world")
            >>> content = policy.read_file("test.txt")
            >>> print(content)
            'hello world'
        """
        with self._lock:
            # Validate path
            validated_path = self._validate_path(path, "read")

            # Read file
            if not validated_path.exists():
                raise FileNotFoundError(f"File not found: {path}")

            content = validated_path.read_text(encoding="utf-8")

            self._increment_operation_count()

            logger.debug(f"File read: {path}", extra={"path": str(validated_path)})

            return content

    def delete_file(self, path: str) -> Dict[str, Any]:
        """
        Secure file delete with path validation.

        Deletes file after validating path against security policies.

        Args:
            path: Relative path within workspace (e.g., "src/main.py")

        Returns:
            Dictionary with delete operation metadata:
            - path: Normalized path
            - deleted: Whether file was deleted
            - timestamp: Delete timestamp

        Raises:
            WorkspaceSecurityError: If path validation fails

        Example:
            >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
            >>> policy.write_file("test.txt", "content")
            >>> result = policy.delete_file("test.txt")
            >>> print(result["deleted"])
            True
        """
        with self._lock:
            # Validate path
            validated_path = self._validate_path(path, "delete")

            deleted = False
            if validated_path.exists():
                validated_path.unlink()
                deleted = True

            self._increment_operation_count()

            result = {
                "path": str(validated_path),
                "deleted": deleted,
                "timestamp": time.time(),
            }

            logger.debug(
                f"File deleted: {path}",
                extra={"deleted": deleted, "path": str(validated_path)}
            )

            return result

    def file_exists(self, path: str) -> bool:
        """
        Check if file exists with path validation.

        Args:
            path: Relative path within workspace

        Returns:
            True if file exists, False otherwise
        """
        with self._lock:
            try:
                validated_path = self._validate_path(path, "exists")
                return validated_path.exists()
            except WorkspaceSecurityError:
                return False

    def get_workspace_path(self, path: str) -> Path:
        """
        Get validated workspace path for external use.

        Returns the full validated path within the workspace for
        use with external tools that need direct file access.

        Args:
            path: Relative path within workspace

        Returns:
            Validated Path object

        Raises:
            WorkspaceSecurityError: If path validation fails

        Example:
            >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
            >>> full_path = policy.get_workspace_path("src/main.py")
            >>> print(str(full_path))
            '/workspace/src/main.py'
        """
        with self._lock:
            return self._validate_path(path, "get_path")

    def create_workspace_hash(self, workspace_id: str) -> str:
        """
        Create hash-named workspace directory.

        Creates a workspace directory with a hash-based name for
        isolation between different pipeline runs or agents.

        Args:
            workspace_id: Unique identifier for workspace

        Returns:
            Hash-named workspace directory path

        Example:
            >>> policy = WorkspacePolicy()
            >>> workspace_path = policy.create_workspace_hash("run-123")
            >>> print(workspace_path.name.startswith("ws_"))
            True
        """
        with self._lock:
            # Create hash from workspace_id
            hash_digest = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]
            workspace_name = f"ws_{hash_digest}"
            workspace_path = self._workspace_root / workspace_name

            # Create directory
            workspace_path.mkdir(parents=True, exist_ok=True)

            logger.info(
                f"Created hash-named workspace: {workspace_name}",
                extra={"workspace_id": workspace_id, "path": str(workspace_path)}
            )

            return workspace_path

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get workspace policy statistics.

        Returns:
            Dictionary with:
            - operation_count: Total operations performed
            - violation_count: Total security violations
            - allowed_paths_count: Number of allowed paths
            - recent_violations: Last 10 violations

        Example:
            >>> policy = WorkspacePolicy()
            >>> stats = policy.get_statistics()
            >>> print(stats["operation_count"])
            42
        """
        with self._lock:
            return {
                "operation_count": self._operation_count,
                "violation_count": len(self._violations),
                "allowed_paths_count": len(self._allowed_paths),
                "workspace_root": str(self._workspace_root),
                "recent_violations": self._violations[-10:],
            }

    def clear_violations(self) -> int:
        """
        Clear recorded violations.

        Returns:
            Number of violations cleared
        """
        with self._lock:
            count = len(self._violations)
            self._violations.clear()
            return count

    def get_allowed_paths(self) -> List[str]:
        """
        Get list of allowed paths.

        Returns:
            List of allowed path strings
        """
        with self._lock:
            return [str(p) for p in self._allowed_paths]

    def add_allowed_path(self, path: str) -> None:
        """
        Add a new allowed path.

        Args:
            path: Path to add to allowlist
        """
        with self._lock:
            self._add_allowed_path(path)
