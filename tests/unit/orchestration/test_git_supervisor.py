"""
Tests for GitSupervisor — CircuitBreaker-protected git operations.

24 tests covering:
- Initialization and defaults
- Branch creation (success/failure/circuit open)
- Commit (with files / all changes / failure)
- Push (success/failure/circuit open)
- PR creation (success/failure/circuit open)
- Rollback (success/failure)
- Changed file detection (success/empty result/failure)
- Operation log and statistics
- Thread safety
"""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.orchestration.supervisors.git import GitOperation, GitSupervisor
from gaia.resilience.circuit_breaker import CircuitBreakerConfig


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def supervisor():
    """Create a GitSupervisor with a mock repo path."""
    return GitSupervisor(
        repo_path=Path("/tmp/test-repo"),
        git_user_name="Test User",
        git_user_email="test@example.com",
    )


@pytest.fixture
def circuit_breaker_config():
    """Create a custom CircuitBreakerConfig for testing."""
    return CircuitBreakerConfig(
        failure_threshold=2,
        recovery_timeout=1.0,
        success_threshold=1,
    )


@pytest.fixture
def supervisor_custom_cb(circuit_breaker_config):
    """Create a GitSupervisor with a custom CircuitBreakerConfig."""
    return GitSupervisor(
        repo_path=Path("/tmp/test-repo"),
        circuit_breaker_config=circuit_breaker_config,
    )


# ============================================================================
# GitOperation Tests (3 tests)
# ============================================================================


class TestGitOperation:
    """Tests for the GitOperation dataclass."""

    def test_to_dict_includes_all_fields(self):
        """to_dict() should include all fields as JSON-safe values."""
        op = GitOperation(
            operation="commit",
            branch="main",
            message="test commit",
            success=True,
            timestamp="2024-01-01T00:00:00+00:00",
        )
        d = op.to_dict()
        assert d["operation"] == "commit"
        assert d["branch"] == "main"
        assert d["message"] == "test commit"
        assert d["success"] is True
        assert d["error"] is None

    def test_to_dict_with_error(self):
        """to_dict() should include error when present."""
        op = GitOperation(
            operation="push",
            branch="feature",
            message="push failed",
            success=False,
            timestamp="2024-01-01T00:00:00+00:00",
            error="connection refused",
        )
        d = op.to_dict()
        assert d["error"] == "connection refused"
        assert d["success"] is False

    def test_timestamp_is_iso_string(self):
        """GitOperation.timestamp must be an ISO format string."""
        iso_time = datetime.now(timezone.utc).isoformat()
        op = GitOperation(
            operation="test",
            branch="main",
            message="test",
            success=True,
            timestamp=iso_time,
        )
        assert isinstance(op.timestamp, str)


# ============================================================================
# GitSupervisor Initialization Tests (3 tests)
# ============================================================================


class TestGitSupervisorInit:
    """Tests for GitSupervisor initialization."""

    def test_default_initialization(self, supervisor):
        """GitSupervisor should initialize with default values."""
        assert supervisor._git_user_name == "Test User"
        assert supervisor._git_user_email == "test@example.com"
        assert supervisor._circuit_breaker.is_closed
        assert len(supervisor._operation_log) == 0

    def test_custom_circuit_breaker_config(self, supervisor_custom_cb):
        """GitSupervisor should use custom CircuitBreakerConfig."""
        cb = supervisor_custom_cb._circuit_breaker
        assert cb.config.failure_threshold == 2
        assert cb.config.recovery_timeout == 1.0
        assert cb.config.success_threshold == 1

    def test_get_operation_log_empty(self, supervisor):
        """get_operation_log() should return empty list initially."""
        assert supervisor.get_operation_log() == []


# ============================================================================
# create_branch Tests (3 tests)
# ============================================================================


class TestCreateBranch:
    """Tests for GitSupervisor.create_branch()."""

    def test_create_branch_success(self, supervisor):
        """create_branch() should return True when git commands succeed."""
        with patch.object(supervisor, "_run_git", return_value="main"):
            result = supervisor.create_branch("feature/auth")
        assert result is True

    def test_create_branch_no_current_branch(self, supervisor):
        """create_branch() should return False when current branch is unknown."""
        with patch.object(supervisor, "_get_current_branch", return_value=None):
            result = supervisor.create_branch("feature/auth")
        assert result is False

    def test_create_branch_with_base(self, supervisor):
        """create_branch() should use specified base branch."""
        with patch.object(supervisor, "_run_git", return_value="") as mock_git:
            result = supervisor.create_branch("feature/auth", base_branch="develop")
        assert result is True
        mock_git.assert_any_call(["checkout", "develop"])
        mock_git.assert_any_call(["checkout", "-b", "feature/auth"])


# ============================================================================
# commit Tests (3 tests)
# ============================================================================


class TestCommit:
    """Tests for GitSupervisor.commit()."""

    def test_commit_all_changes(self, supervisor):
        """commit() with no files should stage all changes."""
        with patch.object(supervisor, "_run_git", return_value="") as mock_git:
            with patch.object(supervisor, "_get_current_branch", return_value="main"):
                result = supervisor.commit("feat: add feature")
        assert result is True
        mock_git.assert_any_call(["add", "-A"])

    def test_commit_specific_files(self, supervisor):
        """commit() with files should stage only those files."""
        with patch.object(supervisor, "_run_git", return_value="") as mock_git:
            with patch.object(supervisor, "_get_current_branch", return_value="main"):
                result = supervisor.commit("fix: bug", files=["file1.py", "file2.py"])
        assert result is True
        mock_git.assert_any_call(["add", "file1.py"])
        mock_git.assert_any_call(["add", "file2.py"])

    def test_commit_failure_returns_false(self, supervisor):
        """commit() should return False when git commit fails."""
        with patch.object(supervisor, "_run_git", return_value=None):
            with patch.object(supervisor, "_get_current_branch", return_value="main"):
                result = supervisor.commit("feat: fail")
        assert result is False


# ============================================================================
# push Tests (3 tests)
# ============================================================================


class TestPush:
    """Tests for GitSupervisor.push()."""

    def test_push_success(self, supervisor):
        """push() should return True when git push succeeds."""
        with patch.object(supervisor, "_run_git", return_value="") as mock_git:
            with patch.object(supervisor, "_get_current_branch", return_value="main"):
                result = supervisor.push()
        assert result is True
        mock_git.assert_called_with(["push", "origin", "main"])

    def test_push_specific_branch(self, supervisor):
        """push() should use specified branch."""
        with patch.object(supervisor, "_run_git", return_value="") as mock_git:
            result = supervisor.push(branch="feature/auth")
        assert result is True
        mock_git.assert_called_with(["push", "origin", "feature/auth"])

    def test_push_failure_returns_false(self, supervisor):
        """push() should return False when git push fails."""
        with patch.object(supervisor, "_run_git", return_value=None):
            with patch.object(supervisor, "_get_current_branch", return_value="main"):
                result = supervisor.push()
        assert result is False


# ============================================================================
# create_pr Tests (3 tests)
# ============================================================================


class TestCreatePR:
    """Tests for GitSupervisor.create_pr()."""

    def test_create_pr_success(self, supervisor):
        """create_pr() should return PR URL on success."""
        pr_url = "https://github.com/example/repo/pull/42"
        with patch.object(supervisor, "_run_git", return_value=pr_url):
            with patch.object(supervisor, "_get_current_branch", return_value="feature"):
                result = supervisor.create_pr("Add feature", "Description here")
        assert result == pr_url

    def test_create_pr_failure_returns_none(self, supervisor):
        """create_pr() should return None on failure."""
        with patch.object(supervisor, "_run_git", return_value=None):
            with patch.object(supervisor, "_get_current_branch", return_value="feature"):
                result = supervisor.create_pr("Add feature", "Description")
        assert result is None

    def test_create_pr_no_current_branch(self, supervisor):
        """create_pr() should return None when current branch is unknown."""
        with patch.object(supervisor, "_get_current_branch", return_value=None):
            result = supervisor.create_pr("Add feature", "Description")
        assert result is None


# ============================================================================
# rollback Tests (2 tests)
# ============================================================================


class TestRollback:
    """Tests for GitSupervisor.rollback()."""

    def test_rollback_success(self, supervisor):
        """rollback() should return True on success."""
        with patch.object(supervisor, "_run_git", return_value=""):
            result = supervisor.rollback("feature", "HEAD~1")
        assert result is True

    def test_rollback_failure_returns_false(self, supervisor):
        """rollback() should return False on failure."""
        with patch.object(supervisor, "_run_git", return_value=None):
            result = supervisor.rollback("feature")
        assert result is False


# ============================================================================
# detect_changed_files Tests (3 tests)
# ============================================================================


class TestDetectChangedFiles:
    """Tests for GitSupervisor.detect_changed_files()."""

    def test_detect_changed_files_success(self, supervisor):
        """detect_changed_files() should return list of changed files."""
        diff_output = "src/file1.py\nsrc/file2.py\nREADME.md\n"
        with patch.object(supervisor, "_run_git", return_value=diff_output):
            result = supervisor.detect_changed_files("feature", "main")
        assert result == ["src/file1.py", "src/file2.py", "README.md"]

    def test_detect_changed_files_empty(self, supervisor):
        """detect_changed_files() should return empty list when no changes."""
        with patch.object(supervisor, "_run_git", return_value="\n"):
            result = supervisor.detect_changed_files("feature")
        assert result == []

    def test_detect_changed_files_failure(self, supervisor):
        """detect_changed_files() should return empty list on failure."""
        with patch.object(supervisor, "_run_git", return_value=None):
            result = supervisor.detect_changed_files("feature")
        assert result == []


# ============================================================================
# CircuitBreaker Integration Tests (2 tests)
# ============================================================================


class TestCircuitBreakerIntegration:
    """Tests for CircuitBreaker protection in GitSupervisor."""

    def test_circuit_trips_after_threshold_failures(self, supervisor_custom_cb):
        """Circuit should trip to OPEN after failure_threshold failures."""
        sup = supervisor_custom_cb
        # 2 failures (threshold) should trip the circuit
        with patch.object(sup, "_run_git", return_value=None):
            with patch.object(sup, "_get_current_branch", return_value="main"):
                sup.commit("fail1")
                sup.commit("fail2")
        assert sup._circuit_breaker.is_open

    def test_circuit_open_returns_false(self, supervisor_custom_cb):
        """Operations should return False when circuit is open."""
        sup = supervisor_custom_cb
        sup._circuit_breaker.trip()  # Force open
        with patch.object(sup, "_run_git", return_value="") as mock_git:
            result = sup.create_branch("feature")
        assert result is False
        mock_git.assert_not_called()


# ============================================================================
# Statistics and Thread Safety Tests (2 tests)
# ============================================================================


class TestStatisticsAndThreadSafety:
    """Tests for statistics and thread safety."""

    def test_get_statistics(self, supervisor):
        """get_statistics() should return correct counts."""
        with patch.object(supervisor, "_run_git", return_value=""):
            with patch.object(supervisor, "_get_current_branch", return_value="main"):
                supervisor.commit("msg1")
                supervisor.commit("msg2")
        stats = supervisor.get_statistics()
        assert stats["total_operations"] == 2
        assert stats["successes"] == 2
        assert stats["failures"] == 0

    def test_thread_safety(self, supervisor):
        """Concurrent operations should not cause race conditions."""
        results = []

        def worker(worker_id: int):
            with patch.object(supervisor, "_run_git", return_value=""):
                with patch.object(
                    supervisor, "_get_current_branch", return_value="main"
                ):
                    success = supervisor.commit(f"commit-{worker_id}")
                    results.append(success)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(results)
        stats = supervisor.get_statistics()
        assert stats["total_operations"] == 10
