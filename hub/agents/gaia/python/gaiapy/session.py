# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Session management for Chat Agent: transcripts and indexed documents.
"""

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia import config as gaia_config

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


class SessionCorruptError(RuntimeError):
    """A session file exists but cannot be parsed into a ChatSession."""


def validate_session_id(session_id: Any) -> None:
    """Raise ValueError unless ``session_id`` is safe to use as a file name."""
    if (
        not isinstance(session_id, str)
        or not _SESSION_ID_RE.fullmatch(session_id)
        or not session_id.strip(".")
    ):
        raise ValueError(
            f"Invalid session_id {session_id!r}: use 1-128 characters from "
            "A-Z, a-z, 0-9, '.', '_' and '-' (not only dots)."
        )


def default_session_dir() -> Path:
    """Default session directory: ``<GAIA_CONFIG_DIR>/sessions``."""
    return Path(gaia_config.GAIA_CONFIG_DIR) / "sessions"


@dataclass
class ChatSession:
    """Chat session with history and indexed documents."""

    session_id: str
    created_at: str
    updated_at: str
    indexed_documents: List[str]
    watched_directories: List[str]
    chat_history: List[Dict[str, str]]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict:
        """Convert session to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "ChatSession":
        """Create session from dictionary."""
        # Sessions saved by older releases still carry this unused key.
        data = {k: v for k, v in data.items() if k != "path_permissions"}
        return cls(**data)


class SessionManager:
    """Manage chat session persistence."""

    def __init__(self, session_dir: Optional[str] = None, auto_cleanup: bool = True):
        """
        Initialize session manager with optional automatic cleanup.

        Args:
            session_dir: Directory to store session files
                (default: ``~/.gaia/sessions``, honouring ``GAIA_CONFIG_DIR``)
            auto_cleanup: Automatically clean up old sessions on init (default: True)
        """
        self.session_dir = (
            Path(session_dir).expanduser() if session_dir else default_session_dir()
        )
        self.session_dir.mkdir(parents=True, exist_ok=True)

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

    def _session_path(self, session_id: str) -> Path:
        """Return the file for ``session_id``, rejecting ids that could escape the dir."""
        validate_session_id(session_id)
        base = self.session_dir.resolve()
        path = (base / f"{session_id}.json").resolve()
        if path.parent != base:
            raise ValueError(
                f"Invalid session_id {session_id!r}: resolves outside {base}."
            )
        return path

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
        self._session_path(session_id)

        now = datetime.now().isoformat()
        session = ChatSession(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            indexed_documents=[],
            watched_directories=[],
            chat_history=[],
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
        session_file = self._session_path(session.session_id)
        try:
            session.updated_at = datetime.now().isoformat()
            # Write-then-rename so a crash mid-save never truncates the old file.
            fd, tmp_name = tempfile.mkstemp(
                dir=session_file.parent, prefix=f".{session_file.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(session.to_dict(), f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, session_file)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise

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
            ChatSession if found, None if no file exists for ``session_id``

        Raises:
            ValueError: If ``session_id`` is not a safe file name.
            SessionCorruptError: If the file exists but cannot be parsed.
        """
        session_file = self._session_path(session_id)

        if not session_file.exists():
            logger.warning(f"Session not found: {session_id}")
            return None

        try:
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            session = ChatSession.from_dict(data)
        except (OSError, ValueError, TypeError, AttributeError) as e:
            raise SessionCorruptError(
                f"Session file {session_file} is unreadable or corrupt ({e}). "
                "Move or delete it to start a fresh session with this id; it "
                "was left untouched."
            ) from e

        logger.info(f"Loaded session: {session_id}")
        return session

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
        session_file = self._session_path(session_id)
        try:
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
            return {"error": str(e), "total_deleted": 0, "remaining_sessions": 0}  # type: ignore[dict-item]
