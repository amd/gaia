# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the Goals & Tasks REST API router.

Endpoints covered:
  GET    /api/goals/stats
  GET    /api/goals/pending-approval
  GET    /api/goals
  POST   /api/goals
  GET    /api/goals/{id}
  PUT    /api/goals/{id}/approve
  PUT    /api/goals/{id}/reject
  PUT    /api/goals/{id}/cancel
  PUT    /api/goals/{id}/status
  DELETE /api/goals/{id}
  POST   /api/goals/{id}/tasks
  PUT    /api/goals/{id}/tasks/{task_id}
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui.routers.goals as goals_router_mod
from gaia.agents.base.goal_store import GoalStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def goal_store(tmp_path):
    gs = GoalStore(db_path=tmp_path / "goals_test.db")
    yield gs
    gs.close()


@pytest.fixture
def client(goal_store):
    app = FastAPI()
    app.include_router(goals_router_mod.router)
    goals_router_mod._store = goal_store

    with TestClient(app) as c:
        yield c

    goals_router_mod._store = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_goal(client, title="Test goal", source="user", priority="medium"):
    resp = client.post("/api/goals", json={
        "title": title, "description": "desc", "priority": priority,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_task(client, goal_id, desc="Do something"):
    resp = client.post(f"/api/goals/{goal_id}/tasks", json={"description": desc})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# Stats
# ===========================================================================


class TestStats:

    def test_stats_empty(self, client):
        resp = client.get("/api/goals/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "goals" in data
        assert "tasks" in data

    def test_stats_reflects_created_goals(self, client):
        _create_goal(client, title="A")
        _create_goal(client, title="B")
        resp = client.get("/api/goals/stats")
        data = resp.json()
        assert data["goals"].get("queued", 0) == 2


# ===========================================================================
# List Goals
# ===========================================================================


class TestListGoals:

    def test_list_empty(self, client):
        resp = client.get("/api/goals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["goals"] == []
        assert data["total"] == 0

    def test_list_returns_created_goals(self, client):
        _create_goal(client, title="Alpha")
        _create_goal(client, title="Beta")
        resp = client.get("/api/goals")
        data = resp.json()
        assert data["total"] == 2

    def test_list_filter_by_status(self, client, goal_store):
        # Create an agent-inferred goal (pending_approval)
        goal_store.create_goal("Inferred", "desc", source="agent_inferred")
        _create_goal(client, title="User goal")  # queued
        resp = client.get("/api/goals?status=pending_approval")
        data = resp.json()
        assert data["total"] == 1
        assert data["goals"][0]["status"] == "pending_approval"

    def test_list_goals_include_tasks(self, client):
        goal = _create_goal(client)
        _add_task(client, goal["id"], "Task A")
        resp = client.get("/api/goals")
        goals = resp.json()["goals"]
        assert len(goals[0]["tasks"]) == 1


# ===========================================================================
# Create Goal
# ===========================================================================


class TestCreateGoal:

    def test_create_returns_201(self, client):
        resp = client.post("/api/goals", json={"title": "New goal", "description": "desc"})
        assert resp.status_code == 201

    def test_create_goal_fields(self, client):
        resp = client.post("/api/goals", json={
            "title": "Refactor auth", "description": "Split JWT logic",
            "priority": "high",
        })
        data = resp.json()
        assert data["title"] == "Refactor auth"
        assert data["status"] == "queued"
        assert data["source"] == "user"
        assert data["approved_for_auto"] is True
        assert data["priority"] == "high"

    def test_create_empty_title_rejected(self, client):
        resp = client.post("/api/goals", json={"title": "  ", "description": "desc"})
        assert resp.status_code == 422

    def test_create_invalid_priority_rejected(self, client):
        resp = client.post("/api/goals", json={
            "title": "X", "description": "Y", "priority": "critical",
        })
        assert resp.status_code == 422


# ===========================================================================
# Get Goal
# ===========================================================================


class TestGetGoal:

    def test_get_existing_goal(self, client):
        created = _create_goal(client)
        resp = client.get(f"/api/goals/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]

    def test_get_missing_goal_returns_404(self, client):
        resp = client.get("/api/goals/no-such-id")
        assert resp.status_code == 404


# ===========================================================================
# Approve / Reject / Cancel
# ===========================================================================


class TestGoalActions:

    def test_approve_inferred_goal(self, client, goal_store):
        goal = goal_store.create_goal("Inferred", "desc", source="agent_inferred")
        resp = client.put(f"/api/goals/{goal.id}/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["approved_for_auto"] is True

    def test_reject_inferred_goal(self, client, goal_store):
        goal = goal_store.create_goal("Inferred", "desc", source="agent_inferred")
        resp = client.put(f"/api/goals/{goal.id}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_cancel_queued_goal(self, client):
        created = _create_goal(client)
        resp = client.put(f"/api/goals/{created['id']}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_approve_missing_returns_404(self, client):
        assert client.put("/api/goals/no-such/approve").status_code == 404

    def test_reject_missing_returns_404(self, client):
        assert client.put("/api/goals/no-such/reject").status_code == 404

    def test_cancel_missing_returns_404(self, client):
        assert client.put("/api/goals/no-such/cancel").status_code == 404


# ===========================================================================
# Delete Goal
# ===========================================================================


class TestDeleteGoal:

    def test_delete_goal_returns_204(self, client):
        created = _create_goal(client)
        resp = client.delete(f"/api/goals/{created['id']}")
        assert resp.status_code == 204

    def test_delete_goal_removes_it(self, client):
        created = _create_goal(client)
        client.delete(f"/api/goals/{created['id']}")
        assert client.get(f"/api/goals/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, client):
        assert client.delete("/api/goals/no-such").status_code == 404


# ===========================================================================
# Tasks
# ===========================================================================


class TestTasks:

    def test_add_task_returns_201(self, client):
        goal = _create_goal(client)
        resp = client.post(f"/api/goals/{goal['id']}/tasks",
                           json={"description": "First step"})
        assert resp.status_code == 201
        task = resp.json()
        assert task["description"] == "First step"
        assert task["status"] == "queued"

    def test_add_task_empty_description_rejected(self, client):
        goal = _create_goal(client)
        resp = client.post(f"/api/goals/{goal['id']}/tasks",
                           json={"description": "  "})
        assert resp.status_code == 422

    def test_add_task_to_missing_goal_returns_404(self, client):
        resp = client.post("/api/goals/no-such/tasks",
                           json={"description": "A task"})
        assert resp.status_code == 404

    def test_update_task_status(self, client):
        goal = _create_goal(client)
        task = _add_task(client, goal["id"])
        resp = client.put(f"/api/goals/{goal['id']}/tasks/{task['id']}",
                          json={"status": "in_progress"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_update_task_with_result(self, client):
        goal = _create_goal(client)
        task = _add_task(client, goal["id"])
        resp = client.put(f"/api/goals/{goal['id']}/tasks/{task['id']}",
                          json={"status": "completed", "result": "Done in 3s."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"] == "Done in 3s."

    def test_update_task_auto_completes_goal(self, client):
        """When the last task completes, the goal should be auto-completed."""
        goal = _create_goal(client)
        task = _add_task(client, goal["id"])
        client.put(f"/api/goals/{goal['id']}/tasks/{task['id']}",
                   json={"status": "completed"})
        resp = client.get(f"/api/goals/{goal['id']}")
        assert resp.json()["status"] == "completed"

    def test_update_task_invalid_status_rejected(self, client):
        goal = _create_goal(client)
        task = _add_task(client, goal["id"])
        resp = client.put(f"/api/goals/{goal['id']}/tasks/{task['id']}",
                          json={"status": "flying"})
        assert resp.status_code == 422

    def test_update_missing_task_returns_404(self, client):
        goal = _create_goal(client)
        resp = client.put(f"/api/goals/{goal['id']}/tasks/no-task",
                          json={"status": "completed"})
        assert resp.status_code == 404


# ===========================================================================
# Pending Approval
# ===========================================================================


class TestPendingApproval:

    def test_pending_approval_returns_inferred_only(self, client, goal_store):
        goal_store.create_goal("Inferred", "desc", source="agent_inferred")
        _create_goal(client, title="User goal")
        resp = client.get("/api/goals/pending-approval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["goals"][0]["source"] == "agent_inferred"

    def test_pending_approval_empty(self, client):
        _create_goal(client)
        resp = client.get("/api/goals/pending-approval")
        assert resp.json()["total"] == 0
