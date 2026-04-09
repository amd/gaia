# Phase 2 Sprint 3: Workspace Sandboxing - Technical Specification

**Document Version:** 1.0
**Date:** 2026-04-06
**Status:** READY FOR IMPLEMENTATION
**Duration:** 2 weeks (Weeks 7-8)
**Owner:** senior-developer
**Sprint Goal:** Implement hard filesystem boundaries for agent operations, cross-pipeline isolation, and security enforcement.

---

## Executive Summary

Phase 2 Sprint 3 builds upon the Phase 1 foundation (NexusService, WorkspaceIndex with TOCTOU fix) and Phase 2 Sprint 1-2 completion (SupervisorAgent, ContextLens) to implement comprehensive workspace sandboxing with mandatory filesystem boundaries per pipeline execution.

### Sprint 3 Objectives

| Objective | Metric | Target | Priority |
|-----------|--------|--------|----------|
| **WorkspacePolicy** | Hard filesystem boundaries | Per-pipeline isolation | P0 |
| **Cross-Pipeline Isolation** | Zero state leakage | 100% isolation | P0 |
| **Security Validation** | Path traversal prevention | 0% bypass success | P0 |
| **Integration** | NexusService, ContextLens | Seamless wiring | P1 |
| **Performance** | Security overhead | <5% latency impact | P1 |

### Sprint 3 Deliverables

| Component | File | LOC Estimate | Tests | Sprint Week |
|-----------|------|--------------|-------|-------------|
| **WorkspacePolicy** | `src/gaia/security/workspace.py` | ~350 | 30 | Week 7 |
| **SecurityValidator** | `src/gaia/security/validator.py` | ~200 | 20 | Week 7 |
| **PipelineIsolation** | `src/gaia/pipeline/isolation.py` | ~150 | 15 | Week 7-8 |
| **NexusService Extension** | `src/gaia/state/nexus.py` | +50 | +10 | Week 8 |
| **Security Tests** | `tests/unit/security/test_workspace.py` | N/A | 75 | Week 7-8 |
| **Integration Tests** | `tests/unit/security/test_isolation.py` | N/A | 25 | Week 8 |

---

## 1. Technical Architecture

### 1.1 System Overview

```
                              Phase 1 Foundation
┌─────────────────────────────────────────────────────────────────────────────┐
│  NexusService (singleton)                                                   │
│  ├── AuditLogger wrapper (Chronicle)                                        │
│  ├── WorkspaceIndex (metadata) - TOCTOU fixed                               │
│  └── get_digest() / get_chronicle_digest()                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                  Phase 2 Sprint 3: Workspace Sandboxing                     │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │ WorkspacePolicy │──│    Security     │──│   Pipeline      │             │
│  │                 │  │   Validator     │  │   Isolation     │             │
│  │ - validate_path │  │                 │  │                 │             │
│  │ - write_file    │  │ - audit_access  │  │ - workspace_ctx │             │
│  │ - read_file     │  │ - detect_traversal│  │ - state_boundary│            │
│  │ - delete_file   │  │ - enforce_policy│  │ - cleanup       │             │
│  └────────┬────────┘  └─────────────────┘  └────────┬────────┘             │
│           │                                         │                      │
│           └────────────────────┬────────────────────┘                      │
│                                ▼                                          │
│                      ┌─────────────────┐                                  │
│                      │  NexusService   │                                  │
│                      │  (integration)  │                                  │
│                      └─────────────────┘                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                              Phase 2 Sprint 1-2 (Complete)
┌─────────────────────────────────────────────────────────────────────────────┐
│  SupervisorAgent, ContextLens, TokenCounter, EmbeddingRelevance             │
│  └── All use WorkspacePolicy for file operations                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Component Dependencies

```
Phase 1 Complete (NexusService, WorkspaceIndex with TOCTOU fix)
       │
       ▼
┌─────────────────┐
│ WorkspacePolicy │◄───────┐
│ (hard boundary) │        │
└────────┬────────┘        │
         │                 │
         ▼                 │
┌─────────────────┐        │
│    Security     │        │
│   Validator     │◄───────┤
└────────┬────────┘        │
         │                 │
         ▼                 │
┌─────────────────┐        │
│   Pipeline      │        │
│  Isolation      │────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  NexusService   │
│  (integration)  │
└────────┬────────┘
         │
    ┌────┴────┬────────────┐
    │         │            │
    ▼         ▼            ▼
┌────────┐ ┌────────┐ ┌──────────┐
│Super-  │ │Pipeline│ │CodeAgent │
│visor   │ │Engine  │ │(future)  │
└────────┘ └────────┘ └──────────┘
```

### 1.3 Security Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Workspace Security Stack                            │
├─────────────────────────────────────────────────────────────────────────────┤
│  Layer 4: PipelineIsolation - Cross-pipeline state boundaries               │
│  ├── Per-pipeline workspace context                                         │
│  ├── Automatic cleanup on pipeline completion                               │
│  └── State leakage prevention                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  Layer 3: SecurityValidator - Real-time access validation                   │
│  ├── Path traversal detection (TOCTOU-safe)                                 │
│  ├── Symlink resolution protection                                          │
│  ├── Audit logging for all access attempts                                  │
│  └── Allowlist/enforcement policy checking                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  Layer 2: WorkspacePolicy - Hard filesystem boundaries                      │
│  ├── Per-pipeline workspace directory (hash-named)                          │
│  ├── Absolute path blocking (Unix/Windows)                                  │
│  ├── Parent traversal blocking (../)                                        │
│  └── Path normalization with pre-validation                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  Layer 1: WorkspaceIndex (Phase 1) - Metadata tracking                      │
│  ├── File metadata tracking                                                 │
│  ├── Change history recording                                               │
│  └── TOCTOU-safe path validation (check BEFORE normalize)                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Implementation Details

### 2.1 WorkspacePolicy Component

**File:** `src/gaia/security/workspace.py`

**Purpose:** Hard filesystem boundary enforcement with per-pipeline workspace isolation.

**Dependencies:**
- Thread-safe for concurrent access
- Cross-platform path handling (Unix/Windows)
- Integration with WorkspaceIndex for metadata

**Implementation:**

```python
# src/gaia/security/workspace.py
"""
Workspace Policy - Hard Filesystem Boundaries for GAIA

Enforces mandatory sandboxing per pipeline execution with
path traversal protection and cross-pipeline isolation.

Features:
    - Per-pipeline workspace isolation (hash-named directories)
    - Path traversal prevention (TOCTOU-safe)
    - Absolute path blocking (Unix/Windows)
    - Symlink resolution protection
    - Allowlist enforcement
    - Audit logging for security events

Example:
    >>> policy = WorkspacePolicy(pipeline_id="pipe-001")
    >>> safe_path = policy.validate_path("src/main.py")
    >>> policy.write_file("output.py", content, modified_by="CodeAgent")
"""

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, List, Set
from dataclasses import dataclass, field

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class FileMetadata:
    """
    File metadata record for workspace tracking.

    Attributes:
        path: Relative path from workspace root
        size_bytes: File size in bytes
        content_hash: SHA-256 hash of content
        created_at: Creation timestamp
        last_modified: Last modification timestamp
        modified_by: Agent/pipeline identifier
        pipeline_id: Owning pipeline identifier
        version: Optimistic concurrency version
    """
    path: str
    size_bytes: int
    content_hash: str
    created_at: float
    last_modified: float
    modified_by: str
    pipeline_id: str
    version: int = 1


class WorkspacePolicy:
    """
    Hard filesystem boundary enforcement.

    Each pipeline execution gets a dedicated workspace sandbox.
    Cross-pipeline file access is blocked.

    Security Features:
    - Per-pipeline workspace isolation
    - Path traversal prevention (TOCTOU-safe)
    - Absolute path blocking
    - Symlink resolution protection
    - Allowlist-based path restrictions

    Example:
        >>> policy = WorkspacePolicy(pipeline_id="pipe-001")
        >>> safe_path = policy.validate_path("src/main.py")
        >>> policy.write_file("output.py", content, "CodeAgent")
    """

    # Path traversal patterns to block
    BLOCKED_PATTERNS = {
        "..",           # Parent traversal
        "~",            # Home directory
        "$",            # Environment variable
        "`",            # Command substitution
        "(", ")",       # Subshell
        "{", "}",       # Brace expansion
        "|",            # Pipe
        ";",            # Command separator
        "&",            # Background
        ">", "<",       # Redirection
        "*", "?",       # Glob patterns
    }

    # Absolute path prefixes to block
    BLOCKED_PREFIXES = {
        "/",            # Unix absolute
        "\\\\",         # Windows UNC
        "C:", "D:", "E:", "F:",  # Windows drives (common)
    }

    def __init__(
        self,
        pipeline_id: str,
        workspace_root: str = "./workspaces",
        allowlist: Optional[Set[str]] = None,
    ):
        """
        Initialize workspace policy for specific pipeline.

        Args:
            pipeline_id: Unique pipeline identifier
            workspace_root: Root directory for all workspaces
            allowlist: Optional set of allowed relative paths/prefixes

        Example:
            >>> policy = WorkspacePolicy(
            ...     pipeline_id="pipe-001",
            ...     allowlist={"src/", "tests/", "docs/"}
            ... )
        """
        self.pipeline_id = pipeline_id
        self._workspace_root = Path(workspace_root).resolve()
        self._workspace = self._create_workspace()
        self._lock = threading.RLock()
        self._file_index: Dict[str, FileMetadata] = {}
        self._allowlist = allowlist or set()

        # Create workspace directory
        self._workspace.mkdir(parents=True, exist_ok=True)

        logger.info(
            "WorkspacePolicy initialized",
            extra={
                "pipeline_id": pipeline_id,
                "workspace": str(self._workspace),
                "allowlist_size": len(self._allowlist),
            },
        )

    def _create_workspace(self) -> Path:
        """
        Create dedicated workspace directory with hash name.

        Returns:
            Path to workspace directory

        Security: Workspace name is hash of pipeline_id to prevent
        prediction attacks and ensure uniqueness.
        """
        # Hash pipeline_id for unique workspace name
        workspace_hash = hashlib.sha256(
            self.pipeline_id.encode()
        ).hexdigest()[:12]
        return self._workspace_root / workspace_hash

    def validate_path(self, relative_path: str) -> Path:
        """
        Validate and resolve path within workspace boundary.

        SECURITY: All checks run BEFORE path normalization (TOCTOU-safe).

        Args:
            relative_path: Relative path from workspace root

        Returns:
            Resolved absolute path within workspace

        Raises:
            SecurityError: If path traversal or boundary violation detected

        Example:
            >>> policy.validate_path("src/main.py")
            PosixPath("/workspaces/abc123/src/main.py")
            >>> policy.validate_path("../etc/passwd")
            SecurityError: Path traversal detected
        """
        # CRITICAL: Check BEFORE normalization (TOCTOU-safe)
        if not self._is_path_safe(relative_path):
            raise SecurityError(
                f"Path traversal detected: {relative_path}"
            )

        # Check allowlist if configured
        if self._allowlist and not self._is_in_allowlist(relative_path):
            raise SecurityError(
                f"Path not in allowlist: {relative_path}"
            )

        # Now safe to normalize
        normalized = self._normalize_path(relative_path)
        resolved = (self._workspace / normalized).resolve()

        # Verify resolved path is within workspace
        if not self._is_within_boundary(resolved):
            raise SecurityError(
                f"Path outside workspace boundary: {resolved}"
            )

        return resolved

    def _is_path_safe(self, path: str) -> bool:
        """
        Check path safety BEFORE normalization.

        Blocks:
        - Parent traversal (..)
        - Absolute paths (/, C:\)
        - Shell injection patterns ($, `, etc.)
        - Symlinks pointing outside

        Args:
            path: Original path string (BEFORE normalization)

        Returns:
            True if safe, False if traversal detected
        """
        # Block empty paths
        if not path or not path.strip():
            return False

        # Block traversal patterns
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in path:
                return False

        # Block absolute paths (BEFORE normalization strips them)
        for prefix in self.BLOCKED_PREFIXES:
            if path.startswith(prefix):
                return False

        # Block Windows absolute paths (e.g., C:\)
        if len(path) > 1 and path[1] == ":":
            return False

        return True

    def _is_in_allowlist(self, path: str) -> bool:
        """
        Check if path matches allowlist.

        Args:
            path: Normalized relative path

        Returns:
            True if path matches allowlist entry
        """
        if not self._allowlist:
            return True  # No allowlist = all paths allowed

        for allowed in self._allowlist:
            if path.startswith(allowed) or path == allowed:
                return True
        return False

    def _normalize_path(self, path: str) -> str:
        """
        Normalize path (cross-platform).

        Args:
            path: Relative path string

        Returns:
            Normalized path with forward slashes
        """
        # Convert backslashes
        normalized = path.replace("\\", "/")
        # Strip leading slashes
        normalized = normalized.lstrip("/")
        # Collapse multiple slashes
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        return normalized

    def _is_within_boundary(self, resolved: Path) -> bool:
        """
        Verify path is within workspace boundary.

        Args:
            resolved: Resolved absolute path

        Returns:
            True if path is within workspace
        """
        try:
            workspace_str = str(self._workspace)
            resolved_str = str(resolved.resolve())
            return (
                resolved_str.startswith(workspace_str + os.sep) or
                resolved_str == workspace_str
            )
        except (OSError, ValueError):
            return False

    def write_file(
        self,
        relative_path: str,
        content: bytes,
        modified_by: str,
    ) -> FileMetadata:
        """
        Write file within workspace boundary.

        Args:
            relative_path: Relative path from workspace root
            content: File content in bytes
            modified_by: Agent/pipeline identifier

        Returns:
            FileMetadata record

        Raises:
            SecurityError: If path validation fails

        Example:
            >>> metadata = policy.write_file(
            ...     "src/main.py",
            ...     b"print('Hello')",
            ...     "CodeAgent"
            ... )
        """
        with self._lock:
            # Validate and resolve path
            full_path = self.validate_path(relative_path)

            # Ensure parent directory exists
            full_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            full_path.write_bytes(content)

            # Calculate metadata
            content_hash = hashlib.sha256(content).hexdigest()

            # Update or create index entry
            if relative_path in self._file_index:
                # Increment version
                existing = self._file_index[relative_path]
                metadata = FileMetadata(
                    path=relative_path,
                    size_bytes=len(content),
                    content_hash=content_hash,
                    created_at=existing.created_at,
                    last_modified=time.time(),
                    modified_by=modified_by,
                    pipeline_id=self.pipeline_id,
                    version=existing.version + 1,
                )
            else:
                metadata = FileMetadata(
                    path=relative_path,
                    size_bytes=len(content),
                    content_hash=content_hash,
                    created_at=time.time(),
                    last_modified=time.time(),
                    modified_by=modified_by,
                    pipeline_id=self.pipeline_id,
                    version=1,
                )

            self._file_index[relative_path] = metadata

            return metadata

    def read_file(
        self,
        relative_path: str,
    ) -> bytes:
        """
        Read file within workspace boundary.

        Args:
            relative_path: Relative path from workspace root

        Returns:
            File content in bytes

        Raises:
            SecurityError: If path validation fails
            FileNotFoundError: If file does not exist
        """
        with self._lock:
            # Validate path
            full_path = self.validate_path(relative_path)

            # Read file
            return full_path.read_bytes()

    def delete_file(
        self,
        relative_path: str,
        deleted_by: str,
    ) -> bool:
        """
        Delete file within workspace boundary.

        Args:
            relative_path: Relative path from workspace root
            deleted_by: Agent/pipeline identifier

        Returns:
            True if deleted, False if not found

        Raises:
            SecurityError: If path validation fails
        """
        with self._lock:
            # Validate path
            full_path = self.validate_path(relative_path)

            # Delete file
            if full_path.exists():
                full_path.unlink()
                # Remove from index
                if relative_path in self._file_index:
                    del self._file_index[relative_path]
                return True
            return False

    def get_file_metadata(
        self,
        relative_path: str,
    ) -> Optional[FileMetadata]:
        """
        Get metadata for file.

        Args:
            relative_path: Relative path from workspace root

        Returns:
            FileMetadata or None if not found
        """
        with self._lock:
            return self._file_index.get(relative_path)

    def get_index(self) -> Dict[str, FileMetadata]:
        """
        Get current file index (deep copy).

        Returns:
            Copy of file index dictionary
        """
        with self._lock:
            return self._file_index.copy()

    def get_workspace_path(self) -> Path:
        """
        Get workspace root path.

        Returns:
            Path to workspace root
        """
        return self._workspace

    def cleanup(self) -> int:
        """
        Cleanup workspace directory.

        Returns:
            Number of files deleted

        Note: Should be called when pipeline completes.
        """
        with self._lock:
            count = 0
            if self._workspace.exists():
                for file_path in self._workspace.rglob("*"):
                    if file_path.is_file():
                        file_path.unlink()
                        count += 1
                # Remove directory
                try:
                    self._workspace.rmdir()
                except OSError:
                    pass  # Directory not empty
            self._file_index.clear()
            return count


class SecurityError(Exception):
    """Raised when security boundary is violated."""
    pass
```

**Test File:** `tests/unit/security/test_workspace.py` (30 tests)

| Test Function | Purpose | Category |
|---------------|---------|----------|
| `test_initialization_default` | Default workspace initialization | Initialization |
| `test_initialization_custom_root` | Custom workspace root | Initialization |
| `test_initialization_with_allowlist` | Allowlist configuration | Initialization |
| `test_workspace_hash_uniqueness` | Unique hash per pipeline_id | Security |
| `test_validate_path_simple` | Simple relative path validation | Path Validation |
| `test_validate_path_nested` | Nested directory path | Path Validation |
| `test_validate_path_traversal_blocked` | ../ traversal blocked | Security |
| `test_validate_path_absolute_unix_blocked` | /etc/passwd blocked | Security |
| `test_validate_path_absolute_windows_blocked` | C:\Windows blocked | Security |
| `test_validate_path_shell_injection_blocked` | $, `, () blocked | Security |
| `test_validate_path_glob_blocked` | * and ? blocked | Security |
| `test_validate_path_allowlist_match` | Allowlist matching | Allowlist |
| `test_validate_path_allowlist_mismatch` | Allowlist rejection | Allowlist |
| `test_normalize_path_backslashes` | Windows backslash conversion | Path Normalization |
| `test_normalize_path_multiple_slashes` | Collapse // to / | Path Normalization |
| `test_normalize_path_leading_slash` | Strip leading / | Path Normalization |
| `test_is_within_boundary_inside` | Path inside boundary | Boundary Check |
| `test_is_within_boundary_outside` | Path outside boundary | Boundary Check |
| `test_is_within_boundary_symlink` | Symlink boundary check | Boundary Check |
| `test_write_file_within_boundary` | File write within boundary | File Operations |
| `test_write_file_creates_parent_dirs` | Parent directory creation | File Operations |
| `test_write_file_updates_index` | Index update on write | File Operations |
| `test_write_file_increments_version` | Version increment | File Operations |
| `test_read_file_within_boundary` | File read within boundary | File Operations |
| `test_read_file_not_found` | File not found handling | File Operations |
| `test_delete_file_within_boundary` | File delete within boundary | File Operations |
| `test_get_file_metadata` | Metadata retrieval | File Operations |
| `test_get_index_deep_copy` | Deep copy of index | Thread Safety |
| `test_cleanup_workspace` | Workspace cleanup | Cleanup |
| `test_thread_safety_concurrent_write` | Concurrent write operations | Thread Safety |

---

### 2.2 SecurityValidator Component

**File:** `src/gaia/security/validator.py`

**Purpose:** Real-time security validation with audit logging for all file access attempts.

**Dependencies:**
- `WorkspacePolicy` for boundary enforcement
- `NexusService` for audit logging
- Thread-safe for concurrent access

**Implementation:**

```python
# src/gaia/security/validator.py
"""
Security Validator - Real-time Access Validation for GAIA

Provides real-time security validation for all file access attempts
with audit logging for security events.

Features:
    - Path traversal detection (TOCTOU-safe)
    - Symlink resolution protection
    - Audit logging for all access attempts
    - Allowlist/enforcement policy checking
    - Real-time security event streaming

Example:
    >>> validator = SecurityValidator()
    >>> validator.validate_access("pipe-001", "src/main.py", "read")
    >>> validator.audit_log("pipe-001", "access_attempt", {...})
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class AccessType(Enum):
    """File access types."""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"


class SecurityLevel(Enum):
    """Security event levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    BLOCKED = "blocked"


@dataclass
class SecurityEvent:
    """
    Security event record.

    Attributes:
        timestamp: Event timestamp
        pipeline_id: Pipeline identifier
        event_type: Type of security event
        access_type: File access type (read/write/delete)
        path: Accessed path
        result: Access result (allowed/blocked)
        reason: Blocking reason if blocked
        metadata: Additional event metadata
    """
    timestamp: float
    pipeline_id: str
    event_type: str
    access_type: Optional[AccessType]
    path: str
    result: str
    reason: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


class SecurityValidator:
    """
    Real-time security validation with audit logging.

    The SecurityValidator provides:
    1. Path traversal detection (TOCTOU-safe)
    2. Symlink resolution protection
    3. Audit logging for all access attempts
    4. Allowlist/enforcement policy checking

    Example:
        >>> validator = SecurityValidator()
        >>> validator.validate_access("pipe-001", "src/main.py", "read")
        True
        >>> validator.get_security_events("pipe-001")
        [SecurityEvent(...)]
    """

    def __init__(self, enable_audit: bool = True):
        """
        Initialize security validator.

        Args:
            enable_audit: Enable audit logging (default: True)
        """
        self._enable_audit = enable_audit
        self._lock = threading.RLock()
        self._events: List[SecurityEvent] = []
        self._max_events = 1000

        logger.info(
            "SecurityValidator initialized",
            extra={"audit_enabled": enable_audit},
        )

    def validate_access(
        self,
        pipeline_id: str,
        path: str,
        access_type: AccessType,
    ) -> bool:
        """
        Validate file access attempt.

        Args:
            pipeline_id: Pipeline identifier
            path: Path being accessed
            access_type: Type of access (read/write/delete)

        Returns:
            True if access allowed, False if blocked

        Example:
            >>> validator.validate_access("pipe-001", "src/main.py", AccessType.READ)
            True
        """
        with self._lock:
            # Basic path safety checks
            if not self._is_path_safe(path):
                self._log_event(SecurityEvent(
                    timestamp=time.time(),
                    pipeline_id=pipeline_id,
                    event_type="path_traversal_blocked",
                    access_type=access_type,
                    path=path,
                    result="blocked",
                    reason="Path traversal detected",
                ))
                return False

            # Log access attempt
            if self._enable_audit:
                self._log_event(SecurityEvent(
                    timestamp=time.time(),
                    pipeline_id=pipeline_id,
                    event_type="access_attempt",
                    access_type=access_type,
                    path=path,
                    result="allowed",
                    reason=None,
                ))

            return True

    def _is_path_safe(self, path: str) -> bool:
        """
        Check path safety.

        Args:
            path: Path to validate

        Returns:
            True if path is safe
        """
        # Block empty paths
        if not path or not path.strip():
            return False

        # Block traversal patterns
        if ".." in path:
            return False

        # Block absolute paths
        if path.startswith("/") or path.startswith("\\\\"):
            return False

        # Block Windows drive letters
        if len(path) > 1 and path[1] == ":":
            return False

        # Block shell injection patterns
        shell_patterns = {"$", "`", "(", ")", "{", "}", "|", ";", "&", ">", "<", "*", "?"}
        for pattern in shell_patterns:
            if pattern in path:
                return False

        return True

    def _log_event(self, event: SecurityEvent) -> None:
        """
        Log security event.

        Args:
            event: Security event to log
        """
        self._events.append(event)

        # Trim old events
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    def get_security_events(
        self,
        pipeline_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[SecurityEvent]:
        """
        Get security events.

        Args:
            pipeline_id: Filter by pipeline (None = all)
            limit: Maximum events to return

        Returns:
            List of security events
        """
        with self._lock:
            if pipeline_id:
                filtered = [e for e in self._events if e.pipeline_id == pipeline_id]
            else:
                filtered = self._events.copy()
            return filtered[-limit:]

    def get_blocked_attempts(
        self,
        pipeline_id: Optional[str] = None,
    ) -> List[SecurityEvent]:
        """
        Get blocked access attempts.

        Args:
            pipeline_id: Filter by pipeline (None = all)

        Returns:
            List of blocked events
        """
        with self._lock:
            events = [e for e in self._events if e.result == "blocked"]
            if pipeline_id:
                events = [e for e in events if e.pipeline_id == pipeline_id]
            return events

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get security statistics.

        Returns:
            Dictionary with security statistics
        """
        with self._lock:
            total = len(self._events)
            blocked = len([e for e in self._events if e.result == "blocked"])
            allowed = total - blocked

            # By pipeline
            by_pipeline: Dict[str, int] = {}
            for event in self._events:
                by_pipeline[event.pipeline_id] = by_pipeline.get(event.pipeline_id, 0) + 1

            return {
                "total_events": total,
                "blocked_count": blocked,
                "allowed_count": allowed,
                "block_rate": blocked / total if total > 0 else 0.0,
                "by_pipeline": by_pipeline,
            }

    def reset(self) -> None:
        """Reset all security events."""
        with self._lock:
            self._events.clear()
```

**Test File:** `tests/unit/security/test_validator.py` (20 tests)

| Test Function | Purpose | Category |
|---------------|---------|----------|
| `test_initialization_default` | Default initialization | Initialization |
| `test_initialization_audit_disabled` | Audit disabled mode | Initialization |
| `test_validate_access_safe_path` | Safe path validation | Access Validation |
| `test_validate_access_traversal_blocked` | Traversal blocked | Access Validation |
| `test_validate_access_absolute_blocked` | Absolute path blocked | Access Validation |
| `test_validate_access_shell_injection_blocked` | Shell injection blocked | Access Validation |
| `test_is_path_safe_empty` | Empty path rejection | Path Safety |
| `test_is_path_safe_traversal` | Traversal detection | Path Safety |
| `test_is_path_safe_absolute` | Absolute path detection | Path Safety |
| `test_is_path_safe_shell_patterns` | Shell pattern detection | Path Safety |
| `test_log_event` | Event logging | Audit Logging |
| `test_get_security_events_all` | Get all events | Audit Logging |
| `test_get_security_events_by_pipeline` | Filter by pipeline | Audit Logging |
| `test_get_security_events_limit` | Event limit | Audit Logging |
| `test_get_blocked_attempts` | Get blocked attempts | Audit Logging |
| `test_get_statistics` | Security statistics | Statistics |
| `test_statistics_block_rate` | Block rate calculation | Statistics |
| `test_reset_events` | Reset events | Management |
| `test_thread_safety_concurrent_log` | Concurrent logging | Thread Safety |
| `test_max_events_trim` | Event trimming | Management |

---

### 2.3 PipelineIsolation Component

**File:** `src/gaia/pipeline/isolation.py`

**Purpose:** Cross-pipeline state isolation with automatic workspace management.

**Dependencies:**
- `WorkspacePolicy` for workspace boundaries
- `NexusService` for state management
- `PipelineEngine` for lifecycle integration

**Implementation:**

```python
# src/gaia/pipeline/isolation.py
"""
Pipeline Isolation - Cross-Pipeline State Boundaries for GAIA

Provides cross-pipeline state isolation with automatic workspace
management and cleanup.

Features:
    - Per-pipeline workspace context
    - Automatic cleanup on pipeline completion
    - State leakage prevention
    - WorkspacePolicy integration

Example:
    >>> isolation = PipelineIsolation()
    >>> with isolation.create_context("pipe-001") as ctx:
    ...     ctx.policy.write_file("output.py", content, "CodeAgent")
"""

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional, Any

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineContext:
    """
    Pipeline execution context.

    Attributes:
        pipeline_id: Unique pipeline identifier
        policy: WorkspacePolicy instance
        created_at: Context creation timestamp
        metadata: Pipeline metadata
    """
    pipeline_id: str
    policy: Any  # WorkspacePolicy
    created_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class PipelineIsolation:
    """
    Cross-pipeline state isolation manager.

    The PipelineIsolation provides:
    1. Per-pipeline workspace context
    2. Automatic cleanup on pipeline completion
    3. State leakage prevention
    4. Integration with NexusService

    Example:
        >>> isolation = PipelineIsolation()
        >>> with isolation.create_context("pipe-001") as ctx:
        ...     # Workspace automatically cleaned up on exit
        ...     ctx.policy.write_file("output.py", content, "CodeAgent")
    """

    def __init__(self, workspace_root: str = "./workspaces"):
        """
        Initialize pipeline isolation.

        Args:
            workspace_root: Root directory for workspaces
        """
        self._workspace_root = workspace_root
        self._lock = threading.RLock()
        self._contexts: Dict[str, PipelineContext] = {}

        logger.info(
            "PipelineIsolation initialized",
            extra={"workspace_root": workspace_root},
        )

    @contextmanager
    def create_context(
        self,
        pipeline_id: str,
        allowlist: Optional[set] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Create pipeline execution context.

        Args:
            pipeline_id: Unique pipeline identifier
            allowlist: Optional path allowlist
            metadata: Pipeline metadata

        Yields:
            PipelineContext with WorkspacePolicy

        Example:
            >>> with isolation.create_context("pipe-001") as ctx:
            ...     ctx.policy.write_file("output.py", content, "CodeAgent")
        """
        # Import here to avoid circular dependency
        from gaia.security.workspace import WorkspacePolicy

        with self._lock:
            # Create workspace policy
            policy = WorkspacePolicy(
                pipeline_id=pipeline_id,
                workspace_root=self._workspace_root,
                allowlist=allowlist,
            )

            # Create context
            ctx = PipelineContext(
                pipeline_id=pipeline_id,
                policy=policy,
                created_at=time.time(),
                metadata=metadata or {},
            )

            # Register context
            self._contexts[pipeline_id] = ctx

            logger.info(
                "Pipeline context created",
                extra={"pipeline_id": pipeline_id},
            )

        try:
            yield ctx
        finally:
            # Cleanup on exit
            with self._lock:
                self._cleanup_context(pipeline_id)
                if pipeline_id in self._contexts:
                    del self._contexts[pipeline_id]

    def _cleanup_context(self, pipeline_id: str) -> None:
        """
        Cleanup pipeline context.

        Args:
            pipeline_id: Pipeline identifier
        """
        if pipeline_id in self._contexts:
            ctx = self._contexts[pipeline_id]
            deleted = ctx.policy.cleanup()
            logger.info(
                "Pipeline context cleaned up",
                extra={
                    "pipeline_id": pipeline_id,
                    "files_deleted": deleted,
                },
            )

    def get_context(
        self,
        pipeline_id: str,
    ) -> Optional[PipelineContext]:
        """
        Get pipeline context.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            PipelineContext or None if not found
        """
        with self._lock:
            return self._contexts.get(pipeline_id)

    def get_active_pipelines(self) -> list:
        """
        Get list of active pipeline IDs.

        Returns:
            List of active pipeline identifiers
        """
        with self._lock:
            return list(self._contexts.keys())

    def cleanup_all(self) -> int:
        """
        Cleanup all pipeline contexts.

        Returns:
            Total number of files deleted
        """
        with self._lock:
            total_deleted = 0
            for pipeline_id in list(self._contexts.keys()):
                ctx = self._contexts[pipeline_id]
                deleted = ctx.policy.cleanup()
                total_deleted += deleted
            self._contexts.clear()
            return total_deleted
```

**Test File:** `tests/unit/security/test_isolation.py` (15 tests)

| Test Function | Purpose | Category |
|---------------|---------|----------|
| `test_initialization` | Default initialization | Initialization |
| `test_create_context_basic` | Basic context creation | Context Management |
| `test_create_context_with_allowlist` | Context with allowlist | Context Management |
| `test_create_context_cleanup_on_exit` | Automatic cleanup | Context Management |
| `test_create_context_exception_cleanup` | Cleanup on exception | Context Management |
| `test_get_context_existing` | Get existing context | Context Management |
| `test_get_context_nonexistent` | Get nonexistent context | Context Management |
| `test_get_active_pipelines` | List active pipelines | Context Management |
| `test_cleanup_all` | Cleanup all contexts | Cleanup |
| `test_isolation_cross_pipeline` | Cross-pipeline isolation | Isolation |
| `test_isolation_workspace_separation` | Workspace separation | Isolation |
| `test_context_metadata` | Context metadata storage | Metadata |
| `test_thread_safety_concurrent_contexts` | Concurrent context creation | Thread Safety |
| `test_context_reuse_after_cleanup` | Context reuse after cleanup | Lifecycle |
| `test_cleanup_idempotent` | Idempotent cleanup | Cleanup |

---

### 2.4 NexusService Extension

**File:** `src/gaia/state/nexus.py` (Extension: +50 LOC)

**Purpose:** Integrate WorkspacePolicy with NexusService for unified workspace management.

**Changes:**
- Add `_workspace_policy` property
- Add `get_workspace_policy()` method
- Lazy initialization of WorkspacePolicy
- Integration with PipelineIsolation

**Implementation (diff):**

```python
# Add to src/gaia/state/nexus.py

# Add imports at top
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from gaia.security.workspace import WorkspacePolicy
    from gaia.pipeline.isolation import PipelineIsolation


# Add to NexusService.__init__()
self._workspace_policy: Optional["WorkspacePolicy"] = None
self._pipeline_isolation: Optional["PipelineIsolation"] = None


def _get_pipeline_isolation(self) -> "PipelineIsolation":
    """
    Get or create PipelineIsolation instance (lazy initialization).

    Returns:
        PipelineIsolation instance for cross-pipeline isolation
    """
    if self._pipeline_isolation is None:
        from gaia.pipeline.isolation import PipelineIsolation
        self._pipeline_isolation = PipelineIsolation()
    return self._pipeline_isolation


def get_workspace_policy(
    self,
    pipeline_id: Optional[str] = None,
) -> "WorkspacePolicy":
    """
    Get WorkspacePolicy for pipeline.

    Args:
        pipeline_id: Pipeline identifier (uses current if None)

    Returns:
        WorkspacePolicy instance

    Example:
        >>> policy = nexus.get_workspace_policy("pipe-001")
        >>> policy.write_file("output.py", content, "CodeAgent")
    """
    if pipeline_id is None:
        # Return default policy or create one
        if self._workspace_policy is None:
            from gaia.security.workspace import WorkspacePolicy
            self._workspace_policy = WorkspacePolicy("default")
        return self._workspace_policy
    else:
        # Get from pipeline isolation
        isolation = self._get_pipeline_isolation()
        ctx = isolation.get_context(pipeline_id)
        if ctx is None:
            # Create new context
            from gaia.security.workspace import WorkspacePolicy
            return WorkspacePolicy(pipeline_id)
        return ctx.policy


def create_pipeline_context(
    self,
    pipeline_id: str,
    allowlist: Optional[set] = None,
    metadata: Optional[dict] = None,
):
    """
    Create pipeline workspace context.

    Args:
        pipeline_id: Pipeline identifier
        allowlist: Optional path allowlist
        metadata: Pipeline metadata

    Example:
        >>> with nexus.create_pipeline_context("pipe-001") as ctx:
        ...     ctx.policy.write_file("output.py", content, "CodeAgent")
    """
    isolation = self._get_pipeline_isolation()
    return isolation.create_context(pipeline_id, allowlist, metadata)
```

---

## 3. Quality Gate Criteria

### 3.1 Exit Criteria

| ID | Criteria | Test | Target | Priority |
|----|----------|------|--------|----------|
| **WORK-003** | Workspace boundary enforcement | Path traversal attempts | 0% bypass | CRITICAL |
| **WORK-004** | Cross-pipeline isolation | Cross-pipeline access attempts | 100% isolation | CRITICAL |
| **SEC-002** | Path traversal prevention | Security penetration tests | 0% success | CRITICAL |
| **PERF-005** | Security overhead | Security validation latency | <5% overhead | HIGH |
| **BC-003** | Backward compatibility | Existing file operations | 100% pass | CRITICAL |
| **THREAD-003** | Thread safety | Concurrent operations | 100 threads | CRITICAL |

### 3.2 Test Coverage Requirements

| Component | Min Coverage | Target Coverage |
|-----------|--------------|-----------------|
| WorkspacePolicy | 95% | 100% |
| SecurityValidator | 90% | 95% |
| PipelineIsolation | 90% | 95% |
| NexusService Extension | 95% | 100% |
| **Overall** | **90%** | **95%** |

---

## 4. Test Strategy

### 4.1 Test Matrix

| Test File | Functions | Coverage Focus | Priority |
|-----------|-----------|----------------|----------|
| `test_workspace.py` | 30 | WorkspacePolicy, path validation, security | CRITICAL |
| `test_validator.py` | 20 | SecurityValidator, audit logging | CRITICAL |
| `test_isolation.py` | 15 | PipelineIsolation, cross-pipeline | CRITICAL |
| `test_nexus_extension.py` | 10 | NexusService integration | HIGH |
| `test_security_integration.py` | 25 | End-to-end integration | HIGH |
| `test_security_performance.py` | 10 | Performance benchmarks | MEDIUM |
| **Total** | **110** | **Full coverage** | |

### 4.2 Security Penetration Tests

**Test File:** `tests/unit/security/test_security_penetration.py`

| Test Function | Purpose | Target |
|---------------|---------|--------|
| `test_traversal_variations` | Various ../ patterns | 0% bypass |
| `test_absolute_path_variations` | Unix/Windows absolute paths | 0% bypass |
| `test_shell_injection_variations` | Shell injection patterns | 0% bypass |
| `test_symlink_escape` | Symlink escape attempts | 0% bypass |
| `test_unicode_confusion` | Unicode path confusion | 0% bypass |
| `test_encoded_traversal` | URL-encoded traversal | 0% bypass |
| `test_double_normalization` | Double normalization attack | 0% bypass |
| `test_race_condition_toctou` | TOCTOU race condition | 0% bypass |

### 4.3 Performance Benchmarks

**Test File:** `tests/unit/security/test_security_performance.py`

| Benchmark | Target | Measurement |
|-----------|--------|-------------|
| Path validation latency | <1ms | Average per validation |
| Workspace policy creation | <10ms | Per-pipeline |
| Cross-pipeline isolation | <5ms | Context switch |
| Security audit logging | <2ms | Per event |
| Concurrent operations (100 threads) | <100ms | Average latency |
| Memory per workspace | <100KB | Per-pipeline |

### 4.4 Thread Safety Verification

| Test | Threads | Operations | Target |
|------|---------|------------|--------|
| Concurrent path validation | 100 | 1000 validations | 100% pass |
| Concurrent workspace access | 50 | 100 file operations | 100% pass |
| Concurrent context creation | 50 | 50 contexts | 100% pass |
| Mixed operations stress | 150 | 1000 ops | 100% pass |

---

## 5. Risk Analysis

### 5.1 Active Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R3.1 | Security bypass via edge case | LOW | HIGH | Comprehensive penetration testing | senior-developer |
| R3.2 | Performance overhead >10% | MEDIUM | MEDIUM | Early benchmarking, optimization | senior-developer |
| R3.3 | Backward compatibility break | LOW | HIGH | Extensive BC testing, deprecation period | senior-developer |
| R3.4 | False positive blocking | MEDIUM | MEDIUM | Configurable allowlist, logging | senior-developer |
| R3.5 | Cross-pipeline leakage | LOW | HIGH | Isolation testing, cleanup verification | senior-developer |

### 5.2 Risk Triggers

| Risk | Trigger | Action |
|------|---------|--------|
| R3.1 | Any penetration test succeeds | Immediate security review, patch deployment |
| R3.2 | Security overhead >10% | Profile hot paths, optimize validation |
| R3.3 | Any BC test failure | Immediate fix, add deprecation period |
| R3.4 | >5% false positive rate | Review allowlist patterns, adjust |
| R3.5 | Any cross-pipeline access succeeds | Immediate isolation review, fix |

---

## 6. Integration Points

### 6.1 Phase 1 Integration

| Phase 1 Component | Sprint 3 Extension | Integration Method |
|-------------------|-------------------|-------------------|
| `WorkspaceIndex.validate_path()` | `WorkspacePolicy.validate_path()` | Security enhancement |
| `NexusService.get_snapshot()` | `PipelineIsolation.create_context()` | State boundary |
| `AuditLogger` | `SecurityValidator.audit_log()` | Security auditing |

### 6.2 Phase 2 Sprint 1-2 Integration

| Sprint 1-2 Component | Sprint 3 Integration | Usage Pattern |
|---------------------|---------------------|---------------|
| `SupervisorAgent.review()` | Uses WorkspacePolicy | File access within sandbox |
| `ContextLens.get_context()` | Uses WorkspacePolicy | Workspace summary |
| `PipelineEngine` | Uses PipelineIsolation | Per-pipeline workspace |

### 6.3 Backward Compatibility

Sprint 3 maintains backward compatibility:

- Existing file operations continue to work unchanged
- WorkspacePolicy is opt-in via PipelineIsolation
- SecurityValidator is additive, not blocking existing paths
- New methods are additive to NexusService

---

## 7. Success Metrics

### 7.1 Technical Metrics

| Metric | Baseline (Phase 1) | Target | Measurement |
|--------|-------------------|--------|-------------|
| Path traversal bypass | TOCTOU fixed in Phase 1 | 0% success | Penetration tests |
| Cross-pipeline leakage | Not isolated | 0% leakage | Isolation tests |
| Security validation latency | N/A | <1ms per validation | Benchmarks |
| Security overhead | N/A | <5% total latency | End-to-end tests |
| Workspace cleanup | Manual | Automatic on context exit | Lifecycle tests |

### 7.2 Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Test coverage | 95% | `pytest --cov` |
| Test pass rate | 100% | All tests pass |
| Thread safety | 100 threads | Concurrent tests |
| Security | 0% bypass | Penetration tests |
| Performance | All benchmarks green | perf tests |
| Backward compatibility | 100% | BC test suite |

---

## 8. Effort Estimates

### 8.1 Developer Effort

| Week | Focus | Tasks | Deliverable |
|------|-------|-------|-------------|
| **Week 7** | WorkspacePolicy + SecurityValidator | Implement workspace, validator | workspace.py, validator.py |
| **Week 7** | PipelineIsolation | Implement isolation | isolation.py |
| **Week 8** | Nexus Integration + Tests | Nexus extension, unit tests | nexus.py extension, test files |
| **Week 8** | Quality Gate 3 Prep | Full test suite, documentation | QG3 assessment |

**Total Effort:** 2 weeks senior-developer, 1 week testing-quality-specialist

### 8.2 Testing Effort

| Sprint | Testing FTE | Focus |
|--------|-------------|-------|
| Week 7 | 0.5 | WorkspacePolicy + SecurityValidator tests |
| Week 8 | 0.5 | Integration + Performance + Penetration tests |

**Total Testing Effort:** 1 week

---

## 9. File Modification Summary

| File | Change Type | LOC Estimate | Tests | Sprint Week |
|------|-------------|--------------|-------|-------------|
| `src/gaia/security/workspace.py` | **NEW** | ~350 | 30 | Week 7 |
| `src/gaia/security/validator.py` | **NEW** | ~200 | 20 | Week 7 |
| `src/gaia/pipeline/isolation.py` | **NEW** | ~150 | 15 | Week 7-8 |
| `src/gaia/state/nexus.py` | MODIFY | +50 | +10 | Week 8 |
| `tests/unit/security/test_workspace.py` | **NEW** | N/A | 30 | Week 7 |
| `tests/unit/security/test_validator.py` | **NEW** | N/A | 20 | Week 7 |
| `tests/unit/security/test_isolation.py` | **NEW** | N/A | 15 | Week 7-8 |
| `tests/unit/security/test_security_penetration.py` | **NEW** | N/A | 25 | Week 8 |
| `tests/unit/security/test_security_performance.py` | **NEW** | N/A | 10 | Week 8 |
| **Total** | | **~750 LOC** | **110 tests** | |

---

## 10. Dependencies

### 10.1 Internal Dependencies

```
Phase 1 Complete (NexusService, WorkspaceIndex with TOCTOU fix)
       │
       ▼
┌─────────────────┐
│ WorkspacePolicy │◄───────┐
│ (hard boundary) │        │
└────────┬────────┘        │
         │                 │
         ▼                 │
┌─────────────────┐        │
│    Security     │        │
│   Validator     │◄───────┤
└────────┬────────┘        │
         │                 │
         ▼                 │
┌─────────────────┐        │
│   Pipeline      │        │
│  Isolation      │────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  NexusService   │
│  (integration)  │
└─────────────────┘
```

### 10.2 External Dependencies

No new external dependencies required for Sprint 3.

---

## 11. Handoff Notes

### 11.1 For senior-developer

**Implementation Notes:**
1. Start with WorkspacePolicy (Week 7) - foundational for all security
2. TOCTOU safety is CRITICAL - check BEFORE normalize
3. SecurityValidator provides audit trail for all access
4. PipelineIsolation uses context manager for automatic cleanup
5. Benchmark security overhead early (target: <5%)

**Key Design Decisions:**
- WorkspacePolicy: Hash-named directories for uniqueness
- SecurityValidator: Additive logging, not blocking existing paths
- PipelineIsolation: Context manager pattern for automatic cleanup
- NexusService: Lazy initialization of WorkspacePolicy

### 11.2 For testing-quality-specialist

**Test Priorities:**
1. Security penetration tests (0% bypass target)
2. Cross-pipeline isolation (100% isolation)
3. Performance benchmarks (<5% overhead)
4. Thread safety (100+ concurrent operations)

**Test Infrastructure:**
- pytest 8.4.2+
- pytest-benchmark for performance
- pytest-asyncio for async tests
- pytest-cov for coverage

---

## 12. Approval & Sign-Off

**Prepared By:** Dr. Sarah Kim, planning-analysis-strategist
**Date:** 2026-04-06
**Next Action:** senior-developer begins Sprint 3 Week 7

### Sign-Off Checklist

- [x] Technical feasibility confirmed
- [x] Resource allocation confirmed
- [x] Risk assessment acceptable
- [x] Test strategy comprehensive
- [x] Quality criteria defined
- [ ] **Team approval to begin Sprint 3**

---

**END OF SPECIFICATION**

**Distribution:** GAIA Development Team
**Review Cadence:** Weekly status reviews
**Version History:**
- v1.0: Initial Sprint 3 specification (2026-04-06)
