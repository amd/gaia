# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Session management for Chat Agent with path validation and history.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PathPermission:
    """Track permission for a path."""

    path: str
    allowed: bool
    timestamp: str
    recursive: bool = False


@dataclass
class ChatSession:
    """Chat session with history and indexed documents."""

    session_id: str
    created_at: str
    updated_at: str
    indexed_documents: List[str]
    watched_directories: List[str]
    chat_history: List[Dict[str, str]]
    path_permissions: Dict[str, PathPermission]
    metadata: Dict[str, any]

    def to_dict(self) -> Dict:
        """Convert session to dictionary."""
        data = asdict(self)
        # Convert PathPermission objects to dicts
        data["path_permissions"] = {
            path: asdict(perm) for path, perm in self.path_permissions.items()
        }
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "ChatSession":
        """Create session from dictionary."""
        # Convert path_permissions dicts back to PathPermission objects
        if "path_permissions" in data:
            data["path_permissions"] = {
                path: PathPermission(**perm_dict)
                for path, perm_dict in data["path_permissions"].items()
            }
        return cls(**data)


class SessionManager:
    """Manage chat sessions with path validation and persistence."""

    def __init__(self, session_dir: str = ".gaia/sessions", auto_cleanup: bool = True):
        """
        Initialize session manager with optional automatic cleanup.

        Args:
            session_dir: Directory to store session files
            auto_cleanup: Automatically clean up old sessions on init (default: True)
        """
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Cache directory for path permissions
        self.permissions_file = self.session_dir / "path_permissions.json"
        self.path_permissions: Dict[str, PathPermission] = {}
        self._load_permissions()

        # Automatic session cleanup to prevent accumulation
        if auto_cleanup:
            try:
                cleanup_stats = self.cleanup_old_sessions(
                    max_age_days=30, max_sessions=50
                )
                if cleanup_stats.get("total_deleted", 0) > 0:
                    logger.info(
                        f"Auto-cleanup: Removed {cleanup_stats['total_deleted']} old sessions"
                    )
            except Exception as e:
                logger.warning(f"Auto-cleanup failed: {e}")

    def _load_permissions(self):
        """Load cached path permissions."""
        if self.permissions_file.exists():
            try:
                with open(self.permissions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.path_permissions = {
                        path: PathPermission(**perm_dict)
                        for path, perm_dict in data.items()
                    }
                logger.info(
                    f"Loaded {len(self.path_permissions)} cached path permissions"
                )
            except Exception as e:
                logger.error(f"Error loading path permissions: {e}")
                self.path_permissions = {}

    def _save_permissions(self):
        """Save path permissions to cache."""
        try:
            data = {path: asdict(perm) for path, perm in self.path_permissions.items()}
            with open(self.permissions_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved {len(self.path_permissions)} path permissions")
        except Exception as e:
            logger.error(f"Error saving path permissions: {e}")

    def validate_path(
        self, path: str, operation: str = "access", prompt_user: bool = True
    ) -> bool:
        """
        Validate if path access is allowed with user confirmation.

        Args:
            path: Path to validate
            operation: Operation type ('read', 'write', 'index', 'watch')
            prompt_user: If True, prompt user for confirmation if not cached

        Returns:
            True if access is allowed, False otherwise
        """
        try:
            # Resolve to absolute path
            resolved_path = str(Path(path).resolve())

            # Check cache first
            if resolved_path in self.path_permissions:
                perm = self.path_permissions[resolved_path]
                logger.debug(
                    f"Using cached permission for {resolved_path}: {perm.allowed}"
                )
                return perm.allowed

            # If not cached and prompting disabled, deny by default
            if not prompt_user:
                logger.warning(
                    f"Path not in cache and prompting disabled: {resolved_path}"
                )
                return False

            # Prompt user for confirmation
            print(f"\n{'='*60}")
            print("Path Access Request")
            print(f"{'='*60}")
            print(f"Path: {resolved_path}")
            print(f"Operation: {operation}")
            print(f"{'='*60}")

            response = (
                input("Allow access to this path? (yes/no/always): ").strip().lower()
            )

            if response in ["yes", "y", "always", "a"]:
                allowed = True
                # Cache the decision
                self.path_permissions[resolved_path] = PathPermission(
                    path=resolved_path,
                    allowed=True,
                    timestamp=datetime.now().isoformat(),
                    recursive=False,
                )
                self._save_permissions()
                print("✅ Access granted and cached for future use")
            else:
                allowed = False
                # Cache denial as well
                self.path_permissions[resolved_path] = PathPermission(
                    path=resolved_path,
                    allowed=False,
                    timestamp=datetime.now().isoformat(),
                    recursive=False,
                )
                self._save_permissions()
                print("❌ Access denied and cached")

            return allowed

        except Exception as e:
            logger.error(f"Error validating path {path}: {e}")
            return False

    def validate_directory(
        self, directory: str, operation: str = "watch", prompt_user: bool = True
    ) -> bool:
        """
        Validate directory access with recursive option.

        Args:
            directory: Directory path to validate
            operation: Operation type
            prompt_user: If True, prompt user for confirmation

        Returns:
            True if access is allowed
        """
        try:
            resolved_dir = str(Path(directory).resolve())

            # Check if this directory or any parent has cached permission
            for cached_path, perm in self.path_permissions.items():
                try:
                    if perm.recursive and Path(resolved_dir).is_relative_to(
                        cached_path
                    ):
                        logger.debug(f"Using recursive permission from {cached_path}")
                        return perm.allowed
                except (ValueError, TypeError):
                    continue

            # Check exact match
            if resolved_dir in self.path_permissions:
                return self.path_permissions[resolved_dir].allowed

            # Prompt user
            if not prompt_user:
                return False

            print(f"\n{'='*60}")
            print("Directory Access Request")
            print(f"{'='*60}")
            print(f"Directory: {resolved_dir}")
            print(f"Operation: {operation}")
            print(f"{'='*60}")

            response = (
                input("Allow access? (yes/no/always/recursive): ").strip().lower()
            )

            if response in ["yes", "y", "always", "a", "recursive", "r"]:
                recursive = response in ["recursive", "r"]
                self.path_permissions[resolved_dir] = PathPermission(
                    path=resolved_dir,
                    allowed=True,
                    timestamp=datetime.now().isoformat(),
                    recursive=recursive,
                )
                self._save_permissions()
                print(
                    f"✅ Access granted ({'recursive' if recursive else 'single directory'})"
                )
                return True
            else:
                self.path_permissions[resolved_dir] = PathPermission(
                    path=resolved_dir,
                    allowed=False,
                    timestamp=datetime.now().isoformat(),
                    recursive=False,
                )
                self._save_permissions()
                print("❌ Access denied")
                return False

        except Exception as e:
            logger.error(f"Error validating directory {directory}: {e}")
            return False

    def create_session(self, session_id: Optional[str] = None) -> ChatSession:
        """
        Create a new chat session.

        Args:
            session_id: Optional session ID, generated if not provided

        Returns:
            New ChatSession instance
        """
        if session_id is None:
            session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        now = datetime.now().isoformat()
        session = ChatSession(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            indexed_documents=[],
            watched_directories=[],
            chat_history=[],
            path_permissions=dict(self.path_permissions),  # Copy current permissions
            metadata={},
        )

        logger.info(f"Created new session: {session_id}")
        return session

    def save_session(self, session: ChatSession) -> bool:
        """
        Save session to disk.

        Args:
            session: ChatSession to save

        Returns:
            True if successful
        """
        try:
            session.updated_at = datetime.now().isoformat()
            session_file = self.session_dir / f"{session.session_id}.json"

            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, indent=2)

            logger.info(f"Saved session: {session.session_id}")
            return True

        except Exception as e:
            logger.error(f"Error saving session {session.session_id}: {e}")
            return False

    def load_session(self, session_id: str) -> Optional[ChatSession]:
        """
        Load session from disk.

        Args:
            session_id: Session ID to load

        Returns:
            ChatSession if found, None otherwise
        """
        try:
            session_file = self.session_dir / f"{session_id}.json"

            if not session_file.exists():
                logger.warning(f"Session not found: {session_id}")
                return None

            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            session = ChatSession.from_dict(data)
            logger.info(f"Loaded session: {session_id}")
            return session

        except Exception as e:
            logger.error(f"Error loading session {session_id}: {e}")
            return None

    def list_sessions(self) -> List[Dict[str, str]]:
        """
        List all available sessions.

        Returns:
            List of session metadata (id, created_at, updated_at)
        """
        sessions = []

        try:
            for session_file in self.session_dir.glob("session_*.json"):
                try:
                    with open(session_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    sessions.append(
                        {
                            "session_id": data["session_id"],
                            "created_at": data["created_at"],
                            "updated_at": data["updated_at"],
                            "num_documents": len(data.get("indexed_documents", [])),
                            "num_messages": len(data.get("chat_history", [])),
                        }
                    )
                except Exception as e:
                    logger.error(f"Error reading session file {session_file}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error listing sessions: {e}")

        return sorted(sessions, key=lambda x: x["updated_at"], reverse=True)

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session ID to delete

        Returns:
            True if successful
        """
        try:
            session_file = self.session_dir / f"{session_id}.json"

            if session_file.exists():
                session_file.unlink()
                logger.info(f"Deleted session: {session_id}")
                return True
            else:
                logger.warning(f"Session not found: {session_id}")
                return False

        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}")
            return False

    def cleanup_old_sessions(
        self, max_age_days: int = 30, max_sessions: int = 50
    ) -> Dict[str, int]:
        """
        Clean up old sessions to prevent unbounded growth.

        Removes sessions that are:
        1. Older than max_age_days (TTL-based cleanup)
        2. Beyond max_sessions limit (keep only most recent)

        Args:
            max_age_days: Maximum age in days before deletion (default: 30)
            max_sessions: Maximum number of sessions to keep (default: 50)

        Returns:
            Dictionary with cleanup statistics
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=max_age_days)
            sessions = []

            # Collect all sessions with metadata
            for session_file in self.session_dir.glob("session_*.json"):
                try:
                    with open(session_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    updated_at = datetime.fromisoformat(data["updated_at"])
                    sessions.append(
                        {
                            "file": session_file,
                            "session_id": data["session_id"],
                            "updated_at": updated_at,
                        }
                    )
                except Exception as e:
                    logger.error(f"Error reading session {session_file}: {e}")
                    continue

            # Sort by update time (newest first)
            sessions.sort(key=lambda x: x["updated_at"], reverse=True)

            deleted_old = 0
            deleted_excess = 0

            # Delete sessions older than TTL
            for session in sessions:
                if session["updated_at"] < cutoff_time:
                    try:
                        session["file"].unlink()
                        deleted_old += 1
                        logger.info(f"Deleted old session: {session['session_id']}")
                    except Exception as e:
                        logger.error(
                            f"Failed to delete session {session['session_id']}: {e}"
                        )

            # Keep only most recent sessions up to max_sessions
            if len(sessions) > max_sessions:
                excess_sessions = sessions[max_sessions:]
                for session in excess_sessions:
                    try:
                        if session[
                            "file"
                        ].exists():  # Might have been deleted in TTL cleanup
                            session["file"].unlink()
                            deleted_excess += 1
                            logger.info(
                                f"Deleted excess session: {session['session_id']}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to delete session {session['session_id']}: {e}"
                        )

            total_deleted = deleted_old + deleted_excess
            remaining = len(sessions) - total_deleted

            logger.info(
                "Session cleanup complete: "
                f"deleted {total_deleted} sessions ({deleted_old} old, {deleted_excess} excess), "
                f"{remaining} remaining"
            )

            return {
                "deleted_old": deleted_old,
                "deleted_excess": deleted_excess,
                "total_deleted": total_deleted,
                "remaining_sessions": remaining,
                "max_age_days": max_age_days,
                "max_sessions": max_sessions,
            }

        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")
            return {"error": str(e), "total_deleted": 0, "remaining_sessions": 0}

    def clear_path_permissions(self):
        """Clear all cached path permissions."""
        self.path_permissions.clear()
        self._save_permissions()
        logger.info("Cleared all path permissions")
