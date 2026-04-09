"""
Nexus Service - Unified State Management for GAIA

Provides a singleton state service that unifies state management across
Agent and Pipeline systems. Wraps AuditLogger for Chronicle functionality
and provides token-efficient context summarization via get_digest().

Features:
    - Thread-safe singleton pattern (double-checked locking)
    - Event commit via AuditLogger integration
    - Token-efficient digest generation for LLM context
    - Deep copy snapshot for mutation-safe state access
    - Workspace index integration for metadata tracking

Example:
    >>> nexus = NexusService.get_instance()
    >>> nexus.commit(
    ...     agent_id="CodeAgent",
    ...     event_type="tool_execution",
    ...     payload={"tool": "file_write", "path": "src/main.py"}
    ... )
    >>> snapshot = nexus.get_snapshot()
    >>> digest = nexus.get_digest(max_tokens=1000)
"""

import copy
import threading
import time
import uuid
import json
import hashlib
from typing import Any, Dict, List, Optional

from gaia.pipeline.audit_logger import AuditLogger, AuditEventType
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class NexusService:
    """
    Unified state service for GAIA Agent and Pipeline systems.

    The NexusService provides a singleton state management layer that:
    1. Wraps AuditLogger for tamper-proof event logging (Chronicle)
    2. Maintains workspace metadata index (Workspace)
    3. Provides token-efficient context summarization (Context Lens)
    4. Ensures thread-safe concurrent access

    This implementation addresses RC#2 (Tool Implementations Missing) by
    providing a unified state layer that both Agent and Pipeline systems
    can share, eliminating dual architecture inconsistencies.

    Thread Safety:
        - Double-checked locking singleton pattern
        - RLock protection for all state mutations
        - Deep copy on reads to prevent external mutation

    Example:
        >>> nexus = NexusService.get_instance()
        >>> nexus.commit(
        ...     agent_id="ChatAgent",
        ...     event_type="user_message",
        ...     payload={"content": "Hello"}
        ... )
        >>> state = nexus.get_snapshot()
        >>> print(state["event_count"])
        1
    """

    _instance: Optional["NexusService"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "NexusService":
        """
        Thread-safe singleton instance creation.

        Uses double-checked locking pattern to ensure only one instance
        exists even under concurrent access from multiple threads.

        Returns:
            The singleton NexusService instance

        Example:
            >>> n1 = NexusService()
            >>> n2 = NexusService()
            >>> assert n1 is n2  # Same instance
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """
        Initialize singleton instance.

        Idempotent initialization - only runs once per singleton lifetime.
        Thread-safe via _initialized flag and singleton pattern.

        Example:
            >>> nexus = NexusService()
            >>> nexus._initialized
            True
        """
        if self._initialized:
            return

        with self._lock:
            if not self._initialized:
                self._audit_logger = AuditLogger(logger_id="nexus-service")
                self._workspace = WorkspaceIndex.get_instance()
                self._state_lock = threading.RLock()
                self._event_cache: List[Dict[str, Any]] = []
                self._cache_max_size = 1000
                self._initialized = True

                logger.info(
                    "NexusService initialized",
                    extra={
                        "singleton": True,
                        "audit_logger": id(self._audit_logger),
                        "workspace": id(self._workspace),
                    }
                )

    @classmethod
    def get_instance(cls) -> "NexusService":
        """
        Get singleton instance (explicit method).

        Returns:
            The singleton NexusService instance

        Example:
            >>> nexus = NexusService.get_instance()
            >>> nexus.commit("agent", "test", {})
        """
        return cls()

    @classmethod
    def reset_instance(cls) -> None:
        """
        Reset singleton instance (for testing only).

        WARNING: Should only be used in test environments.
        Clears all state and allows fresh initialization.

        Example:
            >>> NexusService.reset_instance()
            >>> new_nexus = NexusService()
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance._cleanup()
            cls._instance = None

    def _cleanup(self) -> None:
        """Cleanup resources (for testing/reset)."""
        if hasattr(self, "_workspace"):
            self._workspace.clear()
        if hasattr(self, "_event_cache"):
            self._event_cache.clear()
        self._initialized = False

    def commit(
        self,
        agent_id: str,
        event_type: str,
        payload: Dict[str, Any],
        phase: Optional[str] = None,
        loop_id: Optional[str] = None,
    ) -> str:
        """
        Commit event to Chronicle (via AuditLogger).

        Records a state-changing event in the tamper-proof audit log.
        Events are immutable once committed and form a hash chain.

        Args:
            agent_id: Identifier of the agent committing the event
            event_type: Type of event (e.g., "tool_execution", "state_change")
            payload: Event-specific data dictionary
            phase: Optional pipeline phase (e.g., "PLANNING", "EXECUTION")
            loop_id: Optional loop iteration ID for iterative processes

        Returns:
            Unique event ID string

        Example:
            >>> nexus = NexusService.get_instance()
            >>> event_id = nexus.commit(
            ...     agent_id="CodeAgent",
            ...     event_type="file_created",
            ...     payload={"path": "src/main.py", "lines": 42},
            ...     phase="EXECUTION"
            ... )
            >>> print(event_id)
            'evt-xxxxxxxxxxxx'
        """
        with self._state_lock:
            # Map string event_type to AuditEventType if possible
            audit_event_type = self._map_to_audit_event_type(event_type)

            event = {
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "agent_id": agent_id,
                "event_type": event_type,
                "payload": payload,
                "phase": phase,
                "loop_id": loop_id,
            }

            # Log to AuditLogger with appropriate event type
            self._audit_logger.log(
                event_type=audit_event_type,
                agent_id=agent_id,
                phase=phase,
                loop_id=loop_id,
                **payload
            )

            # Cache for fast snapshot access
            self._event_cache.append(event)
            if len(self._event_cache) > self._cache_max_size:
                self._event_cache = self._event_cache[-self._cache_max_size:]

            # Update workspace index if payload contains file metadata
            self._update_workspace_from_event(event)

            logger.debug(
                f"Committed event: {event_type}",
                extra={
                    "event_id": event["id"],
                    "agent_id": agent_id,
                    "phase": phase,
                }
            )

            return event["id"]

    def _map_to_audit_event_type(self, event_type: str) -> AuditEventType:
        """
        Map string event type to AuditEventType enum.

        Provides intelligent mapping from generic event types to the
        appropriate AuditEventType for categorization.

        Args:
            event_type: String event type identifier

        Returns:
            Corresponding AuditEventType enum value
        """
        mapping = {
            "pipeline_start": AuditEventType.PIPELINE_START,
            "pipeline_complete": AuditEventType.PIPELINE_COMPLETE,
            "phase_enter": AuditEventType.PHASE_ENTER,
            "phase_exit": AuditEventType.PHASE_EXIT,
            "agent_selected": AuditEventType.AGENT_SELECTED,
            "agent_executed": AuditEventType.AGENT_EXECUTED,
            "tool_executed": AuditEventType.TOOL_EXECUTED,
            "tool_execution": AuditEventType.TOOL_EXECUTED,
            "quality_evaluated": AuditEventType.QUALITY_EVALUATED,
            "decision_made": AuditEventType.DECISION_MADE,
            "defect_discovered": AuditEventType.DEFECT_DISCOVERED,
            "defect_remediated": AuditEventType.DEFECT_REMEDIATED,
            "loop_back": AuditEventType.LOOP_BACK,
        }
        return mapping.get(event_type.lower(), AuditEventType.DECISION_MADE)

    def _update_workspace_from_event(self, event: Dict[str, Any]) -> None:
        """
        Update workspace index based on event payload.

        Automatically tracks file metadata when events involve file operations.

        Args:
            event: Event dictionary with payload containing file info
        """
        payload = event.get("payload", {})

        # Track file operations
        if "path" in payload:
            metadata = {
                "agent_id": event["agent_id"],
                "event_id": event["id"],
                "timestamp": event["timestamp"],
                "event_type": event["event_type"],
            }

            # Add file-specific metadata
            if "lines" in payload:
                metadata["lines"] = payload["lines"]
            if "size_bytes" in payload:
                metadata["size_bytes"] = payload["size_bytes"]
            if "content_hash" in payload:
                metadata["content_hash"] = payload["content_hash"]

            self._workspace.track_file(
                path=payload["path"],
                metadata=metadata
            )

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Return deep copy of state (mutation-safe).

        Provides a complete snapshot of the current state including:
        - Chronicle: Recent events from audit log
        - Workspace: Current workspace file index
        - Summary: Aggregate statistics

        The deep copy ensures external code cannot mutate internal state.

        Returns:
            Dictionary containing complete state snapshot

        Example:
            >>> nexus = NexusService.get_instance()
            >>> nexus.commit("agent", "test", {"key": "value"})
            >>> snapshot = nexus.get_snapshot()
            >>> assert "chronicle" in snapshot
            >>> assert "workspace" in snapshot
        """
        with self._state_lock:
            # Deep copy to prevent external mutation
            return copy.deepcopy({
                "chronicle": self._event_cache.copy(),
                "workspace": self._workspace.get_index(),
                "summary": {
                    "total_events": len(self._event_cache),
                    "workspace_files": len(self._workspace.get_index().get("files", {})),
                    "timestamp": time.time(),
                }
            })

    def get_digest(
        self,
        max_tokens: int = 1000,
        include_phases: Optional[List[str]] = None,
        include_agents: Optional[List[str]] = None,
    ) -> str:
        """
        Generate token-efficient context digest for LLM prompts.

        Creates a summarized view of recent state that fits within
        token constraints while preserving critical information.

        Uses hierarchical summarization:
        1. Recent events (most recent first)
        2. Phase summaries (if include_phases specified)
        3. Agent activity summaries (if include_agents specified)
        4. Workspace summary (file counts, recent changes)

        Args:
            max_tokens: Maximum tokens to target (rough estimate)
            include_phases: Filter to specific phases if specified
            include_agents: Filter to specific agents if specified

        Returns:
            Formatted string digest for LLM context

        Example:
            >>> nexus = NexusService.get_instance()
            >>> nexus.commit("CodeAgent", "file_created", {"path": "main.py"})
            >>> digest = nexus.get_digest(max_tokens=500)
            >>> print(digest[:200])
            '## Recent Events...'
        """
        with self._state_lock:
            # Get filtered events
            events = self._event_cache.copy()

            if include_agents:
                events = [e for e in events if e["agent_id"] in include_agents]

            if include_phases:
                events = [e for e in events if e.get("phase") in include_phases]

            # Build digest incrementally
            digest_parts = []
            token_count = 0

            # Recent events section
            digest_parts.append("## Recent Events")
            token_count += 4  # Approximate token count for header

            recent_events = list(reversed(events[-10:]))  # Last 10 events
            for event in recent_events:
                if token_count >= max_tokens:
                    break

                event_summary = self._format_event_digest(event)
                digest_parts.append(event_summary)
                token_count += self._estimate_tokens(event_summary)

            # Workspace summary
            workspace = self._workspace.get_index()
            if workspace.get("files"):
                digest_parts.append("\n## Workspace")
                file_count = len(workspace["files"])
                digest_parts.append(f"Files tracked: {file_count}")
                token_count += 10

                # Recent file changes (last 5)
                recent_files = list(workspace["files"].items())[-5:]
                for path, meta in recent_files:
                    if token_count >= max_tokens:
                        break
                    file_summary = f"  - {path} (modified: {meta.get('timestamp', 'N/A')})"
                    digest_parts.append(file_summary)
                    token_count += self._estimate_tokens(file_summary)

            return "\n".join(digest_parts)

    def get_chronicle_digest(
        self,
        max_events: int = 15,
        max_tokens: int = 3500,
        include_phases: Optional[List[str]] = None,
        include_agents: Optional[List[str]] = None,
    ) -> str:
        """
        Generate token-efficient digest from full Chronicle (AuditLogger).

        Delegates to AuditLogger.get_digest() for complete hash-chain
        verified event summarization. Use this when you need the full
        audit trail with phase summaries and loop iterations.

        Args:
            max_events: Maximum number of recent events (default: 15)
            max_tokens: Target maximum token count (default: 3500)
            include_phases: Filter to specific phases if specified
            include_agents: Filter to specific agents if specified

        Returns:
            Formatted string digest from full Chronicle

        Example:
            >>> nexus = NexusService.get_instance()
            >>> nexus.commit("CodeAgent", "phase_enter", {"phase": "PLANNING"})
            >>> digest = nexus.get_chronicle_digest(max_events=10, max_tokens=2000)
        """
        return self._audit_logger.get_digest(
            max_events=max_events,
            max_tokens=max_tokens,
            include_phases=include_phases,
            include_agents=include_agents,
        )

    def _format_event_digest(self, event: Dict[str, Any]) -> str:
        """
        Format single event for digest output.

        Args:
            event: Event dictionary

        Returns:
            Formatted string representation
        """
        parts = [
            f"[{event.get('phase', 'N/A')}] ",
            f"{event['agent_id']}: ",
            f"{event['event_type']}",
        ]

        payload = event.get('payload', {})
        if payload:
            # Include key payload info (limit length)
            key_info = ", ".join(
                f"{k}={str(v)[:30]}" for k, v in list(payload.items())[:3]
            )
            parts.append(f" ({key_info})")

        return "".join(parts)

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        Simple estimation: ~4 characters per token for English.

        Args:
            text: String to estimate tokens for

        Returns:
            Approximate token count
        """
        return len(text) // 4

    def get_state_hash(self) -> str:
        """
        Compute hash of current state for integrity verification.

        Returns:
            SHA-256 hash of current state snapshot

        Example:
            >>> hash1 = nexus.get_state_hash()
            >>> nexus.commit("agent", "test", {})
            >>> hash2 = nexus.get_state_hash()
            >>> assert hash1 != hash2
        """
        with self._state_lock:
            snapshot = {
                "event_count": len(self._event_cache),
                "workspace_version": self._workspace.get_version(),
                "last_event": self._event_cache[-1] if self._event_cache else None,
            }
            canonical = json.dumps(snapshot, sort_keys=True, default=str)
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get_agent_history(
        self,
        agent_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get event history for specific agent.

        Args:
            agent_id: Agent identifier to filter by
            limit: Maximum number of events to return

        Returns:
            List of events for the specified agent
        """
        with self._state_lock:
            events = [e for e in self._event_cache if e["agent_id"] == agent_id]
            return list(reversed(events[-limit:]))

    def get_phase_summary(self, phase: str) -> Dict[str, Any]:
        """
        Get summary of events within a specific phase.

        Args:
            phase: Phase name to summarize

        Returns:
            Dictionary with phase statistics and events
        """
        with self._state_lock:
            phase_events = [e for e in self._event_cache if e.get("phase") == phase]

            return {
                "phase": phase,
                "event_count": len(phase_events),
                "agents_involved": list(set(e["agent_id"] for e in phase_events)),
                "events": phase_events[-20:],  # Last 20 events
                "first_event": phase_events[0] if phase_events else None,
                "last_event": phase_events[-1] if phase_events else None,
            }

    def _get_context_lens(self):
        """
        Get or create ContextLens instance (lazy initialization).

        Lazily initializes ContextLens to avoid importing dependencies
        unless actually needed. This enables graceful degradation if
        ContextLens or TokenCounter are not available.

        Returns:
            ContextLens instance for optimized context generation

        Example:
            >>> nexus = NexusService.get_instance()
            >>> lens = nexus._get_context_lens()
        """
        # Use object's dict for instance-level attribute
        if not hasattr(self, '_context_lens') or self._context_lens is None:
            from gaia.state.context_lens import ContextLens
            self._context_lens = ContextLens(self)
        return self._context_lens

    def _get_workspace_policy(self):
        """
        Get or create WorkspacePolicy instance (lazy initialization).

        Lazily initializes WorkspacePolicy to avoid importing dependencies
        unless actually needed. This enables graceful degradation if
        security module is not available.

        Returns:
            WorkspacePolicy instance for secure file operations

        Example:
            >>> nexus = NexusService.get_instance()
            >>> policy = nexus._get_workspace_policy()
        """
        if not hasattr(self, '_workspace_policy') or self._workspace_policy is None:
            try:
                from gaia.security.workspace import WorkspacePolicy
                self._workspace_policy = WorkspacePolicy()
            except ImportError:
                logger.warning("WorkspacePolicy not available - security module not installed")
                self._workspace_policy = None
        return self._workspace_policy

    def get_workspace_policy(self):
        """
        Get WorkspacePolicy instance for secure file operations.

        Returns:
            WorkspacePolicy instance or None if not available

        Example:
            >>> nexus = NexusService.get_instance()
            >>> policy = nexus.get_workspace_policy()
            >>> if policy:
            ...     policy.write_file("test.txt", "content")
        """
        return self._get_workspace_policy()

    def validate_workspace_access(self, path: str, operation: str) -> bool:
        """
        Validate workspace access using WorkspacePolicy.

        Args:
            path: Path to validate
            operation: Operation type (read, write, delete)

        Returns:
            True if access allowed, False otherwise

        Example:
            >>> nexus = NexusService.get_instance()
            >>> if nexus.validate_workspace_access("src/main.py", "write"):
            ...     # Access allowed
            ...     pass
        """
        policy = self._get_workspace_policy()
        if policy is None:
            # If policy not available, allow by default (graceful degradation)
            return True

        try:
            policy._validate_path(path, operation)
            return True
        except Exception:
            return False

    def secure_write_file(self, path: str, content: str) -> Dict[str, Any]:
        """
        Secure file write with workspace policy validation.

        Args:
            path: Path to write file
            content: File content

        Returns:
            Write result dictionary

        Raises:
            WorkspaceSecurityError: If path validation fails

        Example:
            >>> nexus = NexusService.get_instance()
            >>> result = nexus.secure_write_file("test.txt", "content")
        """
        policy = self._get_workspace_policy()
        if policy is None:
            raise RuntimeError("WorkspacePolicy not available")
        return policy.write_file(path, content)

    def secure_read_file(self, path: str) -> str:
        """
        Secure file read with workspace policy validation.

        Args:
            path: Path to read file

        Returns:
            File content

        Raises:
            WorkspaceSecurityError: If path validation fails
            FileNotFoundError: If file doesn't exist

        Example:
            >>> nexus = NexusService.get_instance()
            >>> content = nexus.secure_read_file("test.txt")
        """
        policy = self._get_workspace_policy()
        if policy is None:
            raise RuntimeError("WorkspacePolicy not available")
        return policy.read_file(path)

    def get_optimized_context(
        self,
        agent_id: str,
        max_tokens: int = 2000,
        use_relevance: bool = True,
        include_phases: Optional[List[str]] = None,
        include_agents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate optimized context with smart prioritization.

        Uses ContextLens for:
        1. Token-accurate context generation (tiktoken)
        2. Relevance-based event scoring
        3. Hierarchical summarization
        4. Budget enforcement

        Args:
            agent_id: Target agent identifier
            max_tokens: Maximum token budget (default: 2000)
            use_relevance: Enable relevance scoring (default: True)
            include_phases: Filter to specific phases (None = all)
            include_agents: Filter to specific agents (None = all)

        Returns:
            Dictionary with:
            - digest: Formatted context string
            - metadata: Context metadata (tokens, events, timing)
            - events: List of included events

        Example:
            >>> nexus = NexusService.get_instance()
            >>> context = nexus.get_optimized_context(
            ...     agent_id="CodeAgent",
            ...     max_tokens=2000,
            ...     use_relevance=True
            ... )
            >>> print(context["metadata"].total_tokens)
        """
        context_lens = self._get_context_lens()
        return context_lens.get_context(
            agent_id=agent_id,
            max_tokens=max_tokens,
            use_relevance=use_relevance,
            include_phases=include_phases,
            include_agents=include_agents,
        )

    def get_enhanced_chronicle_digest(
        self,
        max_events: int = 15,
        max_tokens: int = 3500,
        include_phases: Optional[List[str]] = None,
        include_agents: Optional[List[str]] = None,
        use_relevance: bool = False,
        agent_id: Optional[str] = None,
    ) -> str:
        """
        Generate enhanced Chronicle digest with relevance scoring.

        Extension of get_chronicle_digest() with:
        - Accurate token counting (tiktoken)
        - Optional relevance-based prioritization

        Args:
            max_events: Maximum number of events (default: 15)
            max_tokens: Maximum token budget (default: 3500)
            include_phases: Phase filter (None = all)
            include_agents: Agent filter (None = all)
            use_relevance: Enable relevance scoring (default: False)
            agent_id: Target agent for relevance (required if use_relevance=True)

        Returns:
            Formatted digest string

        Example:
            >>> nexus = NexusService.get_instance()
            >>> digest = nexus.get_enhanced_chronicle_digest(
            ...     max_events=20,
            ...     max_tokens=3000,
            ...     use_relevance=True,
            ...     agent_id="CodeAgent"
            ... )
        """
        context_lens = self._get_context_lens()
        return context_lens.get_chronicle_digest(
            max_events=max_events,
            max_tokens=max_tokens,
            include_phases=include_phases,
            include_agents=include_agents,
            use_relevance=use_relevance,
            agent_id=agent_id,
        )


class WorkspaceIndex:
    """
    Workspace metadata index for spatial state tracking.

    Maintains an index of all files and resources touched during
    agent/pipeline execution, with path traversal prevention and
    change tracking.

    Features:
        - File metadata tracking (path, size, hash, timestamps)
        - Path traversal prevention (security)
        - Change history per file
        - Workspace version tracking

    Example:
        >>> workspace = WorkspaceIndex.get_instance()
        >>> workspace.track_file(
        ...     path="src/main.py",
        ...     metadata={"lines": 42, "agent_id": "CodeAgent"}
        ... )
        >>> index = workspace.get_index()
    """

    _instance: Optional["WorkspaceIndex"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "WorkspaceIndex":
        """Thread-safe singleton instance creation."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize workspace index."""
        if self._initialized:
            return

        with self._lock:
            if not self._initialized:
                self._files: Dict[str, Dict[str, Any]] = {}
                self._change_history: Dict[str, List[Dict[str, Any]]] = {}
                self._version = 0
                self._lock_rw = threading.RLock()
                self._initialized = True

                logger.info("WorkspaceIndex initialized")

    @classmethod
    def get_instance(cls) -> "WorkspaceIndex":
        """Get singleton instance."""
        return cls()

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance._cleanup()
            cls._instance = None

    def _cleanup(self) -> None:
        """Cleanup resources."""
        self._files.clear()
        self._change_history.clear()
        self._version = 0
        self._initialized = False

    def get_version(self) -> int:
        """
        Get current workspace version.

        Increments on each modification for optimistic concurrency.

        Returns:
            Integer version number
        """
        with self._lock_rw:
            return self._version

    def track_file(
        self,
        path: str,
        metadata: Dict[str, Any],
    ) -> None:
        """
        Track a file in the workspace index.

        Records file metadata and adds to change history.
        Path traversal is prevented for security.

        Args:
            path: File path to track (normalized, no traversal)
            metadata: File metadata (size, lines, hash, etc.)

        Example:
            >>> workspace = WorkspaceIndex.get_instance()
            >>> workspace.track_file("src/main.py", {"lines": 42})
        """
        with self._lock_rw:
            # SECURITY FIX: Check safety BEFORE normalization (TOCTOU fix)
            if not self._is_path_safe(path):
                logger.warning(
                    f"Path traversal attempt blocked: {path}"
                )
                return
            # Now safe to normalize
            normalized_path = self._normalize_path(path)

            # Initialize change history if new file
            if normalized_path not in self._change_history:
                self._change_history[normalized_path] = []

            # Record change
            change_record = {
                "timestamp": time.time(),
                "metadata": metadata.copy(),
                "version": self._version + 1,
            }
            self._change_history[normalized_path].append(change_record)

            # Update file index
            self._files[normalized_path] = {
                "path": normalized_path,
                "last_modified": time.time(),
                "change_count": len(self._change_history[normalized_path]),
                **metadata,
            }

            self._version += 1

            logger.debug(
                f"Tracked file: {normalized_path}",
                extra={"version": self._version}
            )

    def _normalize_path(self, path: str) -> str:
        """
        Normalize file path (cross-platform).

        Args:
            path: File path to normalize

        Returns:
            Normalized path with forward slashes
        """
        # Convert backslashes to forward slashes
        normalized = path.replace("\\", "/")
        # Remove leading slashes
        normalized = normalized.lstrip("/")
        # Collapse multiple slashes
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        return normalized

    def _is_path_safe(self, path: str) -> bool:
        """
        Check if path is safe (no traversal).

        SECURITY: This method MUST be called BEFORE path normalization
        to prevent TOCTOU vulnerabilities. Calling after normalization
        will allow absolute Unix paths to bypass security checks.

        Args:
            path: ORIGINAL path to check (BEFORE normalization)

        Returns:
            True if safe, False if traversal detected
        """
        # Block path traversal patterns
        if ".." in path:
            return False
        # Block absolute Unix paths (must check BEFORE normalization strips "/")
        if path.startswith("/"):
            return False
        # Block Windows absolute paths (e.g., "C:")
        if len(path) > 1 and path[1] == ":":
            return False
        return True

    def get_index(self) -> Dict[str, Any]:
        """
        Get complete workspace index.

        Returns deep copy to prevent external mutation.

        Returns:
            Dictionary with files and metadata

        Example:
            >>> index = workspace.get_index()
            >>> print(index["files"])
        """
        with self._lock_rw:
            return copy.deepcopy({
                "files": self._files.copy(),
                "version": self._version,
                "total_files": len(self._files),
            })

    def get_file_metadata(self, path: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for specific file.

        Args:
            path: File path to look up

        Returns:
            File metadata or None if not found
        """
        with self._lock_rw:
            normalized = self._normalize_path(path)
            return self._files.get(normalized)

    def get_change_history(
        self,
        path: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get change history for specific file.

        Args:
            path: File path
            limit: Maximum number of changes to return

        Returns:
            List of change records (most recent first)
        """
        with self._lock_rw:
            normalized = self._normalize_path(path)
            history = self._change_history.get(normalized, [])
            return list(reversed(history[-limit:]))

    def clear(self) -> None:
        """Clear all workspace data."""
        with self._lock_rw:
            self._files.clear()
            self._change_history.clear()
            self._version = 0
            logger.info("WorkspaceIndex cleared")
