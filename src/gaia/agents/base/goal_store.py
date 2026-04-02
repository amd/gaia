# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GoalStore: Structured store for agent goals and tasks.

Separate from MemoryStore (knowledge base) because goals/tasks are
operational work-queue state — they need relational hierarchy, explicit
state transitions, and must never be rewritten by LLM consolidation.

Single database (~/.gaia/goals.db) with two tables:
- goals: High-level objectives with source, approval, and priority tracking
- tasks: Discrete, completable steps belonging to a goal

Goal state machine:

    [agent_inferred] ──► pending_approval ──► queued ──► in_progress ──► completed
                                          └──► rejected               └──► failed
    [user / scheduled] ─────────────────► queued                     └──► cancelled
                                               └──► cancelled

Task state machine:

    queued ──► in_progress ──► completed
         └──► blocked ──────► in_progress (unblocked)
                          └──► failed
                          └──► cancelled

Thread-safe via threading.Lock.
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

GoalStatus = Literal[
    "pending_approval",  # agent-inferred; awaiting user approve/reject
    "queued",            # approved and waiting to start
    "in_progress",       # agent is actively working on this goal
    "completed",         # all tasks done successfully
    "failed",            # attempted but could not complete
    "rejected",          # user declined the agent-inferred goal
    "cancelled",         # abandoned after being queued or in-progress
]

TaskStatus = Literal[
    "queued",       # waiting to start
    "in_progress",  # currently executing
    "completed",    # done successfully
    "failed",       # could not complete
    "blocked",      # waiting on a dependency or external resource
    "cancelled",    # abandoned before completion
]

GoalSource = Literal[
    "user",              # set explicitly by the user
    "agent_inferred",    # agent observed context and inferred this goal
    "agent_scheduled",   # follow-up created by the agent from a completed goal
]

AgentMode = Literal[
    "manual",       # no autonomous activity — chat only
    "goal_driven",  # execute user-approved goals in background
    "autonomous",   # agent creates and executes its own goals
]

Priority = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """A discrete, completable step belonging to a goal."""

    id: str
    goal_id: str
    description: str
    status: TaskStatus
    order_index: int
    result: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class Goal:
    """A high-level objective the agent should work toward."""

    id: str
    title: str
    description: str
    status: GoalStatus
    source: GoalSource
    #: Minimum agent mode required to auto-execute this goal.
    mode_required: AgentMode
    #: True once the user (or "always accept" rule) has approved execution.
    approved_for_auto: bool
    priority: Priority
    progress_notes: str
    created_at: str
    updated_at: str
    tasks: List[Task] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    migrated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id                TEXT    PRIMARY KEY,
    title             TEXT    NOT NULL,
    description       TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'pending_approval',
    source            TEXT    NOT NULL DEFAULT 'user',
    mode_required     TEXT    NOT NULL DEFAULT 'goal_driven',
    approved_for_auto INTEGER NOT NULL DEFAULT 0,
    priority          TEXT    NOT NULL DEFAULT 'medium',
    progress_notes    TEXT    NOT NULL DEFAULT '',
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_goals_status    ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_source    ON goals(source);
CREATE INDEX IF NOT EXISTS idx_goals_approved  ON goals(approved_for_auto, status);
CREATE INDEX IF NOT EXISTS idx_goals_priority  ON goals(priority, status);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT    PRIMARY KEY,
    goal_id     TEXT    NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    description TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'queued',
    order_index INTEGER NOT NULL DEFAULT 0,
    result      TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_goal_id ON tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
"""

_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# GoalStore
# ---------------------------------------------------------------------------


class GoalStore:
    """Structured store for agent goals and tasks with hierarchy and state tracking.

    Goals are high-level objectives; tasks are discrete ordered steps within a goal.

    This store is intentionally separate from MemoryStore so the LLM cannot
    rewrite work-queue state during consolidation the way it rewrites knowledge.

    Usage::

        store = GoalStore()

        # User sets a goal
        goal = store.create_goal("Refactor auth module", "...", source="user")
        store.add_task(goal.id, "Extract JWT validation class", order_index=0)
        store.add_task(goal.id, "Write unit tests",             order_index=1)
        store.add_task(goal.id, "Update API docs",              order_index=2)

        # Agent loop: get what to work on
        for goal in store.get_actionable_goals():
            task = store.get_next_task(goal.id)
            store.update_task_status(task.id, "in_progress")
            # ... do work ...
            store.update_task_status(task.id, "completed", result="Done.")
            if store.is_goal_complete(goal.id):
                store.update_goal_status(goal.id, "completed")
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".gaia" / "goals.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA_SQL)
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY migrated_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version(version, migrated_at) VALUES (?, ?)",
                    (_SCHEMA_VERSION, _now_iso()),
                )
                conn.commit()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            goal_id=row["goal_id"],
            description=row["description"],
            status=row["status"],
            order_index=row["order_index"],
            result=row["result"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_goal(self, row: sqlite3.Row, include_tasks: bool = True) -> Goal:
        goal = Goal(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            source=row["source"],
            mode_required=row["mode_required"],
            approved_for_auto=bool(row["approved_for_auto"]),
            priority=row["priority"],
            progress_notes=row["progress_notes"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        if include_tasks:
            goal.tasks = self._get_tasks_unlocked(goal.id)
        return goal

    def _get_tasks_unlocked(self, goal_id: str) -> List[Task]:
        """Fetch tasks without acquiring the lock (caller must hold it)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE goal_id = ? ORDER BY order_index ASC",
            (goal_id,),
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    # ------------------------------------------------------------------
    # Goal CRUD
    # ------------------------------------------------------------------

    def create_goal(
        self,
        title: str,
        description: str,
        source: GoalSource = "user",
        mode_required: AgentMode = "goal_driven",
        priority: Priority = "medium",
        approved_for_auto: bool = False,
    ) -> Goal:
        """Create a new goal and return it.

        - ``source="user"`` → status starts as ``queued``, auto-approved.
        - ``source="agent_inferred"`` → status starts as ``pending_approval``,
          not approved until the user explicitly accepts.
        - ``source="agent_scheduled"`` → status starts as ``queued``,
          inherits approval from the parent goal that spawned it.
        """
        goal_id = str(uuid4())
        now = _now_iso()

        if source == "agent_inferred":
            status: GoalStatus = "pending_approval"
            approved_for_auto = False
        else:
            status = "queued"
            if source == "user":
                approved_for_auto = True  # user intent is implicit approval

        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO goals
                   (id, title, description, status, source, mode_required,
                    approved_for_auto, priority, progress_notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
                (
                    goal_id, title, description, status, source, mode_required,
                    int(approved_for_auto), priority, now, now,
                ),
            )
            conn.commit()

        logger.debug(
            "[GoalStore] created goal %s (%r) source=%s status=%s",
            goal_id, title, source, status,
        )
        return self.get_goal(goal_id)

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Return a goal by ID, with its tasks, or None if not found."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
            return self._row_to_goal(row) if row else None

    def list_goals(
        self,
        status: Optional[GoalStatus] = None,
        source: Optional[GoalSource] = None,
        approved_only: bool = False,
        include_tasks: bool = True,
    ) -> List[Goal]:
        """Return goals matching optional filters, ordered by priority then age."""
        clauses: List[str] = []
        params: List = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if approved_only:
            clauses.append("approved_for_auto = 1")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM goals {where} "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "created_at ASC"
        )

        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_goal(r, include_tasks=include_tasks) for r in rows]

    def update_goal_status(
        self,
        goal_id: str,
        status: GoalStatus,
        progress_notes: Optional[str] = None,
    ) -> Optional[Goal]:
        """Update a goal's status and optionally append progress notes."""
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            if progress_notes is not None:
                conn.execute(
                    "UPDATE goals SET status=?, progress_notes=?, updated_at=? WHERE id=?",
                    (status, progress_notes, now, goal_id),
                )
            else:
                conn.execute(
                    "UPDATE goals SET status=?, updated_at=? WHERE id=?",
                    (status, now, goal_id),
                )
            conn.commit()
        return self.get_goal(goal_id)

    def approve_goal(self, goal_id: str) -> Optional[Goal]:
        """Approve an agent-inferred goal: move to queued and set approved_for_auto."""
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE goals SET status='queued', approved_for_auto=1, updated_at=? WHERE id=?",
                (now, goal_id),
            )
            conn.commit()
        logger.debug("[GoalStore] approved goal %s", goal_id)
        return self.get_goal(goal_id)

    def reject_goal(self, goal_id: str) -> Optional[Goal]:
        """Reject an agent-inferred goal."""
        return self.update_goal_status(goal_id, "rejected")

    def cancel_goal(self, goal_id: str) -> Optional[Goal]:
        """Cancel a queued or in-progress goal."""
        return self.update_goal_status(goal_id, "cancelled")

    def delete_goal(self, goal_id: str) -> None:
        """Hard-delete a goal and all its tasks (CASCADE)."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
            conn.commit()

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def add_task(
        self,
        goal_id: str,
        description: str,
        order_index: int = 0,
    ) -> Task:
        """Add a task to an existing goal. New tasks start as ``queued``."""
        task_id = str(uuid4())
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO tasks
                   (id, goal_id, description, status, order_index, result, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', ?, NULL, ?, ?)""",
                (task_id, goal_id, description, order_index, now, now),
            )
            conn.execute(
                "UPDATE goals SET updated_at=? WHERE id=?", (now, goal_id)
            )
            conn.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Optional[Task]:
        """Return a task by ID or None."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            return self._row_to_task(row) if row else None

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: Optional[str] = None,
    ) -> Optional[Task]:
        """Update a task's status and optionally record the outcome."""
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            if result is not None:
                conn.execute(
                    "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=?",
                    (status, result, now, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                    (status, now, task_id),
                )
            # Touch the parent goal's updated_at
            conn.execute(
                "UPDATE goals SET updated_at=? "
                "WHERE id=(SELECT goal_id FROM tasks WHERE id=?)",
                (now, task_id),
            )
            conn.commit()
        return self.get_task(task_id)

    def get_goal_tasks(self, goal_id: str) -> List[Task]:
        """Return all tasks for a goal ordered by index."""
        with self._lock:
            return self._get_tasks_unlocked(goal_id)

    # ------------------------------------------------------------------
    # Agent-loop helpers
    # ------------------------------------------------------------------

    def get_pending_approval(self) -> List[Goal]:
        """Goals waiting for the user to approve or reject."""
        return self.list_goals(status="pending_approval")

    def get_actionable_goals(self) -> List[Goal]:
        """Approved goals that are queued or in-progress — ready for the agent loop.

        Ordered by priority (high → medium → low) then creation time (oldest first).
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT * FROM goals
                   WHERE approved_for_auto = 1
                     AND status IN ('queued', 'in_progress')
                   ORDER BY
                     CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                     created_at ASC"""
            ).fetchall()
            return [self._row_to_goal(r) for r in rows]

    def get_next_task(self, goal_id: str) -> Optional[Task]:
        """Return the lowest-index queued or unblocked task for a goal."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT * FROM tasks
                   WHERE goal_id=? AND status IN ('queued', 'blocked')
                   ORDER BY order_index ASC LIMIT 1""",
                (goal_id,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def is_goal_complete(self, goal_id: str) -> bool:
        """True when every task is completed or cancelled (none pending/active)."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN status IN ('completed','cancelled') THEN 1 ELSE 0 END) AS done
                   FROM tasks WHERE goal_id=?""",
                (goal_id,),
            ).fetchone()
            if not row or row["total"] == 0:
                return False
            return row["total"] == row["done"]

    # ------------------------------------------------------------------
    # Stats / dashboard
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return aggregate status counts for goals and tasks (dashboard use)."""
        with self._lock:
            conn = self._get_conn()
            goal_rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM goals GROUP BY status"
            ).fetchall()
            task_rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM tasks GROUP BY status"
            ).fetchall()
        return {
            "goals": {r["status"]: r["n"] for r in goal_rows},
            "tasks":  {r["status"]: r["n"] for r in task_rows},
        }

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current local time as ISO 8601 string with timezone offset."""
    return datetime.now().astimezone().isoformat()
