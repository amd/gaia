"""
SupervisorRegistry — Central registry for supervision components.

Provides a thread-safe registry for supervisor instances organized by role:
- register/unregister supervisors by role name
- lookup supervisors by role
- statistics about registered supervisors

Example:
    >>> registry = SupervisorRegistry()
    >>> registry.register("git", git_supervisor)
    >>> supervisor = registry.get("git")
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SupervisorRegistry:
    """
    Thread-safe registry for supervisor instances.

    Supervisors are organized by role (e.g., "git", "quality", "ci").
    Each role can have at most one supervisor.
    """

    def __init__(self) -> None:
        """Initialize an empty supervisor registry."""
        self._supervisors: Dict[str, Any] = {}
        self._lock = threading.RLock()
        logger.info("SupervisorRegistry initialized")

    def register(self, role: str, supervisor: Any) -> None:
        """
        Register a supervisor under the given role.

        If a supervisor is already registered under this role,
        it will be replaced.

        Args:
            role: Role identifier (e.g., "git", "quality").
            supervisor: Supervisor instance to register.
        """
        with self._lock:
            old = self._supervisors.get(role)
            self._supervisors[role] = supervisor
            if old is not None:
                logger.info(
                    f"SupervisorRegistry: replaced supervisor for role '{role}'"
                )
            else:
                logger.info(
                    f"SupervisorRegistry: registered supervisor for role '{role}'"
                )

    def unregister(self, role: str) -> bool:
        """
        Remove a supervisor from the registry.

        Args:
            role: Role identifier to remove.

        Returns:
            True if a supervisor was removed, False if role was not found.
        """
        with self._lock:
            if role in self._supervisors:
                del self._supervisors[role]
                logger.info(
                    f"SupervisorRegistry: unregistered supervisor for role '{role}'"
                )
                return True
            logger.warning(
                f"SupervisorRegistry: cannot unregister — role '{role}' not found"
            )
            return False

    def get(self, role: str) -> Optional[Any]:
        """
        Get a supervisor by role.

        Args:
            role: Role identifier.

        Returns:
            Supervisor instance, or None if not found.
        """
        with self._lock:
            return self._supervisors.get(role)

    def has(self, role: str) -> bool:
        """
        Check if a supervisor is registered for a role.

        Args:
            role: Role identifier.

        Returns:
            True if a supervisor is registered, False otherwise.
        """
        with self._lock:
            return role in self._supervisors

    def get_all_roles(self) -> List[str]:
        """
        Get all registered role names.

        Returns:
            List of role identifier strings.
        """
        with self._lock:
            return list(self._supervisors.keys())

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get registry statistics.

        Returns:
            Dictionary with role count and list of registered roles.
        """
        with self._lock:
            return {
                "registered_count": len(self._supervisors),
                "roles": list(self._supervisors.keys()),
            }
