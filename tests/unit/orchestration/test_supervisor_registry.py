"""
Tests for SupervisorRegistry — Central registry for supervision components.

11 tests covering:
- Initialization (empty registry)
- Register supervisor
- Register (replace existing)
- Unregister (success/not found)
- Get supervisor (exists/not found)
- Has role (exists/not found)
- Get all roles
- Get statistics
- Thread safety
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from gaia.orchestration.supervisors.registry import SupervisorRegistry


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def registry():
    """Create an empty SupervisorRegistry."""
    return SupervisorRegistry()


# ============================================================================
# Initialization Tests (1 test)
# ============================================================================


class TestInit:
    """Tests for SupervisorRegistry initialization."""

    def test_empty_initialization(self, registry):
        """Registry should start empty with 0 roles."""
        stats = registry.get_statistics()
        assert stats["registered_count"] == 0
        assert stats["roles"] == []


# ============================================================================
# Register Tests (2 tests)
# ============================================================================


class TestRegister:
    """Tests for SupervisorRegistry.register()."""

    def test_register_new_role(self, registry):
        """register() should add a supervisor under a new role."""
        mock_supervisor = MagicMock()
        registry.register("git", mock_supervisor)
        assert registry.has("git") is True
        assert registry.get("git") is mock_supervisor

    def test_register_replaces_existing(self, registry):
        """register() should replace an existing supervisor for the same role."""
        supervisor1 = MagicMock()
        supervisor2 = MagicMock()
        registry.register("git", supervisor1)
        registry.register("git", supervisor2)
        assert registry.get("git") is supervisor2


# ============================================================================
# Unregister Tests (2 tests)
# ============================================================================


class TestUnregister:
    """Tests for SupervisorRegistry.unregister()."""

    def test_unregister_existing_role(self, registry):
        """unregister() should remove supervisor and return True."""
        registry.register("git", MagicMock())
        result = registry.unregister("git")
        assert result is True
        assert registry.has("git") is False

    def test_unregister_nonexistent_role(self, registry):
        """unregister() should return False for unknown role."""
        result = registry.unregister("nonexistent")
        assert result is False


# ============================================================================
# Get/Has Tests (2 tests)
# ============================================================================


class TestGetHas:
    """Tests for SupervisorRegistry.get() and has()."""

    def test_get_nonexistent_role(self, registry):
        """get() should return None for unknown role."""
        assert registry.get("unknown") is None

    def test_has_false_for_empty(self, registry):
        """has() should return False for any role in empty registry."""
        assert registry.has("git") is False


# ============================================================================
# get_all_roles Tests (1 test)
# ============================================================================


class TestGetAllRoles:
    """Tests for SupervisorRegistry.get_all_roles()."""

    def test_get_all_roles_multiple(self, registry):
        """get_all_roles() should return all registered role names."""
        registry.register("git", MagicMock())
        registry.register("quality", MagicMock())
        registry.register("ci", MagicMock())
        roles = registry.get_all_roles()
        assert sorted(roles) == ["ci", "git", "quality"]


# ============================================================================
# get_statistics Tests (1 test)
# ============================================================================


class TestGetStatistics:
    """Tests for SupervisorRegistry.get_statistics()."""

    def test_statistics_after_registration(self, registry):
        """get_statistics() should reflect registered supervisors."""
        registry.register("git", MagicMock())
        registry.register("quality", MagicMock())
        stats = registry.get_statistics()
        assert stats["registered_count"] == 2
        assert sorted(stats["roles"]) == ["git", "quality"]


# ============================================================================
# Thread Safety Tests (1 test)
# ============================================================================


class TestThreadSafety:
    """Tests for thread safety of SupervisorRegistry."""

    def test_concurrent_register_and_get(self, registry):
        """Concurrent register/get operations should not cause race conditions."""
        errors = []

        def worker(worker_id: int):
            try:
                mock_sup = MagicMock()
                registry.register(f"role-{worker_id}", mock_sup)
                result = registry.get(f"role-{worker_id}")
                assert result is mock_sup
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        stats = registry.get_statistics()
        assert stats["registered_count"] == 10
