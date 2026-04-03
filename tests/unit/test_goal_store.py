# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for GoalStore — structured store for agent goals and tasks.

Tests cover:
  - Basic goal CRUD (create, get, list, delete)
  - Goal state machine (approve, reject, cancel, status updates)
  - Source behavior (user / agent_inferred / agent_scheduled)
  - Task CRUD (add, get, update_task_status)
  - Agent-loop helpers (get_actionable_goals, get_pending_approval,
    get_next_task, is_goal_complete)
  - Priority ordering (high → medium → low → oldest first)
  - Stats / dashboard aggregates
  - Thread safety (concurrent reads and writes)

All tests use temp-file SQLite — no real ~/.gaia directory touched.
"""

import threading
import time

import pytest

from gaia.agents.base.goal_store import Goal, GoalStore, Task

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """Fresh GoalStore backed by a temp DB for each test."""
    gs = GoalStore(db_path=tmp_path / "goals.db")
    yield gs
    gs.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_goal(store: GoalStore, title="Test goal", **kwargs) -> Goal:
    return store.create_goal(title=title, description="desc", **kwargs)


def _make_task(store: GoalStore, goal_id: str, desc="Do something") -> Task:
    return store.add_task(goal_id, desc)


# ===========================================================================
# 1. Goal CRUD
# ===========================================================================


class TestGoalCRUD:

    def test_create_user_goal_starts_queued(self, store):
        goal = _make_goal(store, source="user")
        assert goal.status == "queued"
        assert goal.approved_for_auto is True
        assert goal.source == "user"

    def test_create_agent_inferred_starts_pending_approval(self, store):
        goal = _make_goal(store, source="agent_inferred")
        assert goal.status == "pending_approval"
        assert goal.approved_for_auto is False

    def test_create_agent_scheduled_starts_queued(self, store):
        goal = _make_goal(store, source="agent_scheduled", approved_for_auto=True)
        assert goal.status == "queued"

    def test_get_goal_returns_goal(self, store):
        created = _make_goal(store)
        fetched = store.get_goal(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.title == created.title

    def test_get_goal_missing_returns_none(self, store):
        assert store.get_goal("nonexistent-id") is None

    def test_list_goals_empty(self, store):
        assert store.list_goals() == []

    def test_list_goals_returns_all(self, store):
        _make_goal(store, title="A")
        _make_goal(store, title="B")
        assert len(store.list_goals()) == 2

    def test_list_goals_filter_by_status(self, store):
        g1 = _make_goal(store, source="agent_inferred")  # pending_approval
        _make_goal(store, source="user")  # queued
        result = store.list_goals(status="pending_approval")
        assert len(result) == 1
        assert result[0].id == g1.id

    def test_list_goals_filter_approved_only(self, store):
        _make_goal(store, source="agent_inferred")  # not approved
        _make_goal(store, source="user")  # approved
        result = store.list_goals(approved_only=True)
        assert len(result) == 1
        assert result[0].approved_for_auto is True

    def test_delete_goal_removes_it(self, store):
        goal = _make_goal(store)
        store.delete_goal(goal.id)
        assert store.get_goal(goal.id) is None

    def test_delete_cascades_to_tasks(self, store):
        goal = _make_goal(store)
        store.add_task(goal.id, "Task 1")
        store.delete_goal(goal.id)
        # Tasks should be gone (CASCADE)
        assert store.get_goal_tasks(goal.id) == []


# ===========================================================================
# 2. Goal State Machine
# ===========================================================================


class TestGoalStateMachine:

    def test_approve_goal(self, store):
        goal = _make_goal(store, source="agent_inferred")
        assert goal.status == "pending_approval"
        approved = store.approve_goal(goal.id)
        assert approved.status == "queued"
        assert approved.approved_for_auto is True

    def test_reject_goal(self, store):
        goal = _make_goal(store, source="agent_inferred")
        rejected = store.reject_goal(goal.id)
        assert rejected.status == "rejected"

    def test_cancel_goal(self, store):
        goal = _make_goal(store, source="user")
        cancelled = store.cancel_goal(goal.id)
        assert cancelled.status == "cancelled"

    def test_update_goal_status_with_notes(self, store):
        goal = _make_goal(store)
        updated = store.update_goal_status(
            goal.id, "in_progress", progress_notes="Started."
        )
        assert updated.status == "in_progress"
        assert updated.progress_notes == "Started."

    def test_approve_nonexistent_returns_none(self, store):
        assert store.approve_goal("no-such-id") is None

    def test_reject_nonexistent_returns_none(self, store):
        assert store.reject_goal("no-such-id") is None


# ===========================================================================
# 3. Task CRUD
# ===========================================================================


class TestTaskCRUD:

    def test_add_task_starts_queued(self, store):
        goal = _make_goal(store)
        task = store.add_task(goal.id, "Do something")
        assert task.status == "queued"
        assert task.goal_id == goal.id
        assert task.result is None

    def test_get_task(self, store):
        goal = _make_goal(store)
        task = store.add_task(goal.id, "Check it")
        fetched = store.get_task(task.id)
        assert fetched is not None
        assert fetched.id == task.id

    def test_get_task_missing_returns_none(self, store):
        assert store.get_task("no-such-id") is None

    def test_update_task_status(self, store):
        goal = _make_goal(store)
        task = store.add_task(goal.id, "Work")
        updated = store.update_task_status(task.id, "in_progress")
        assert updated.status == "in_progress"

    def test_update_task_with_result(self, store):
        goal = _make_goal(store)
        task = store.add_task(goal.id, "Write file")
        updated = store.update_task_status(
            task.id, "completed", result="Written 42 lines."
        )
        assert updated.status == "completed"
        assert updated.result == "Written 42 lines."

    def test_get_goal_tasks_ordered_by_index(self, store):
        goal = _make_goal(store)
        store.add_task(goal.id, "Third", order_index=2)
        store.add_task(goal.id, "First", order_index=0)
        store.add_task(goal.id, "Second", order_index=1)
        tasks = store.get_goal_tasks(goal.id)
        assert [t.order_index for t in tasks] == [0, 1, 2]
        assert tasks[0].description == "First"


# ===========================================================================
# 4. Agent-loop Helpers
# ===========================================================================


class TestAgentLoopHelpers:

    def test_get_pending_approval_returns_inferred(self, store):
        _make_goal(store, source="user")  # queued — not pending
        g2 = _make_goal(store, source="agent_inferred")  # pending_approval
        pending = store.get_pending_approval()
        assert len(pending) == 1
        assert pending[0].id == g2.id

    def test_get_actionable_goals_returns_approved_queued(self, store):
        _make_goal(store, source="agent_inferred")  # not approved
        g2 = _make_goal(store, source="user")  # approved, queued
        actionable = store.get_actionable_goals()
        assert len(actionable) == 1
        assert actionable[0].id == g2.id

    def test_get_actionable_goals_includes_in_progress(self, store):
        goal = _make_goal(store, source="user")
        store.update_goal_status(goal.id, "in_progress")
        actionable = store.get_actionable_goals()
        assert len(actionable) == 1

    def test_get_actionable_goals_excludes_completed(self, store):
        goal = _make_goal(store, source="user")
        store.update_goal_status(goal.id, "completed")
        assert store.get_actionable_goals() == []

    def test_get_next_task_returns_first_queued(self, store):
        goal = _make_goal(store)
        t1 = store.add_task(goal.id, "First", order_index=0)
        store.add_task(goal.id, "Second", order_index=1)
        next_task = store.get_next_task(goal.id)
        assert next_task is not None
        assert next_task.id == t1.id

    def test_get_next_task_skips_in_progress(self, store):
        goal = _make_goal(store)
        t1 = store.add_task(goal.id, "A", order_index=0)
        t2 = store.add_task(goal.id, "B", order_index=1)
        store.update_task_status(t1.id, "in_progress")
        # in_progress is not in ('queued','blocked') so it's skipped
        next_task = store.get_next_task(goal.id)
        assert next_task is not None
        assert next_task.id == t2.id

    def test_get_next_task_no_tasks_returns_none(self, store):
        goal = _make_goal(store)
        assert store.get_next_task(goal.id) is None

    def test_is_goal_complete_false_when_tasks_pending(self, store):
        goal = _make_goal(store)
        store.add_task(goal.id, "Not done")
        assert store.is_goal_complete(goal.id) is False

    def test_is_goal_complete_true_when_all_done(self, store):
        goal = _make_goal(store)
        t1 = store.add_task(goal.id, "A")
        t2 = store.add_task(goal.id, "B")
        store.update_task_status(t1.id, "completed")
        store.update_task_status(t2.id, "cancelled")
        assert store.is_goal_complete(goal.id) is True

    def test_is_goal_complete_false_no_tasks(self, store):
        goal = _make_goal(store)
        # No tasks — not considered complete
        assert store.is_goal_complete(goal.id) is False


# ===========================================================================
# 5. Priority Ordering
# ===========================================================================


class TestPriorityOrdering:

    def test_high_priority_returned_first(self, store):
        _make_goal(store, title="Low", source="user", priority="low")
        _make_goal(store, title="High", source="user", priority="high")
        _make_goal(store, title="Medium", source="user", priority="medium")
        goals = store.get_actionable_goals()
        assert goals[0].priority == "high"
        assert goals[1].priority == "medium"
        assert goals[2].priority == "low"

    def test_same_priority_ordered_oldest_first(self, store):
        g1 = _make_goal(store, title="First", source="user", priority="medium")
        time.sleep(0.01)
        g2 = _make_goal(store, title="Second", source="user", priority="medium")
        goals = store.get_actionable_goals()
        assert goals[0].id == g1.id
        assert goals[1].id == g2.id


# ===========================================================================
# 6. Stats
# ===========================================================================


class TestStats:

    def test_stats_empty(self, store):
        stats = store.get_stats()
        assert stats["goals"] == {}
        assert stats["tasks"] == {}

    def test_stats_counts_by_status(self, store):
        _make_goal(store, source="user")  # queued
        _make_goal(store, source="user")  # queued
        _make_goal(store, source="agent_inferred")  # pending_approval
        stats = store.get_stats()
        assert stats["goals"].get("queued", 0) == 2
        assert stats["goals"].get("pending_approval", 0) == 1

    def test_stats_tasks_counted(self, store):
        goal = _make_goal(store)
        store.add_task(goal.id, "A")
        store.add_task(goal.id, "B")
        stats = store.get_stats()
        assert stats["tasks"].get("queued", 0) == 2


# ===========================================================================
# 7. Thread Safety
# ===========================================================================


class TestThreadSafety:

    def test_concurrent_goal_creation(self, store):
        errors = []

        def create_goals():
            try:
                for i in range(10):
                    store.create_goal(
                        f"Goal {i} {threading.current_thread().name}", "desc"
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create_goals) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent writes raised: {errors}"
        goals = store.list_goals()
        assert len(goals) == 40  # 4 threads × 10 goals

    def test_concurrent_read_write(self, store):
        goal = _make_goal(store)
        errors = []

        def reader():
            try:
                for _ in range(20):
                    store.get_goal(goal.id)
            except Exception as exc:
                errors.append(exc)

        def writer():
            try:
                for _ in range(5):
                    store.add_task(goal.id, "Task")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent read/write raised: {errors}"
