# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Persistent task list captured from email triage (#1605).

Triage already extracts ``action_items`` and returns them inline; this module
persists those items as durable task rows so they survive the response —
triage becomes follow-through, not just analysis. Rows live in the
``email_tasks`` table of the same SQLite the action log uses
(``EmailAgentConfig.resolved_db_path()``), each linked back to the source
message via ``message_id``.

L8 seam (#1521): the cross-agent Task & To-Do store does not exist in-tree
yet. ``record_action_items`` is the single write entry point the REST surface
calls; when the shared ``TaskStore`` lands, this function becomes the adapter
that forwards each task with ``source_ref=message_id`` instead of writing the
local table. Consumers should not query ``email_tasks`` directly outside this
module.

Dedup invariant: at most one task per ``(message_id, normalized description)``.
Re-triaging a message re-extracts the same action items; those are skipped,
never duplicated. Normalization collapses whitespace and lowercases, so
wording must match — a genuinely reworded item is a new task. A UNIQUE index
backs the invariant at the schema level.

All public helpers are pure functions taking a ``DatabaseMixin``-typed first
argument, mirroring ``action_store``. They never reach into the agent class.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import at runtime
    from gaia_agent_email.contract import ActionItem

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

EMAIL_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS email_tasks (
    task_id          TEXT PRIMARY KEY,
    message_id       TEXT NOT NULL,
    description      TEXT NOT NULL,
    description_norm TEXT NOT NULL,
    due_hint         TEXT,
    item_type        TEXT NOT NULL DEFAULT 'text',
    url              TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       REAL NOT NULL,
    completed_at     REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_tasks_msg_desc
    ON email_tasks(message_id, description_norm);
CREATE INDEX IF NOT EXISTS idx_email_tasks_status
    ON email_tasks(status);
"""


def init_schema(db) -> None:
    """Create the ``email_tasks`` table if it doesn't exist. Idempotent."""
    db.execute(EMAIL_TASKS_DDL)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def normalize_description(description: str) -> str:
    """The dedup key for a task description: whitespace-collapsed, lowercased."""
    return " ".join(description.split()).lower()


def record_action_items(
    db, *, message_id: str, items: Iterable["ActionItem"]
) -> List[str]:
    """Persist triage action items as tasks linked to ``message_id``.

    Items already captured for this message (same normalized description) are
    skipped, so re-triaging a message is idempotent. Returns the task_ids of
    the rows actually created — ``[]`` when every item was a duplicate.

    The pre-insert dedup check and the insert are not atomic, so two
    concurrent triages of the same message can both pass the check and race
    the UNIQUE index. That race is the dedup invariant firing, not a real
    failure: ``sqlite3.IntegrityError`` from the ``idx_email_tasks_msg_desc``
    constraint is caught and treated as a duplicate-skip. Any other
    exception is not ours to interpret and propagates.
    """
    if not message_id:
        raise ValueError(
            "record_action_items requires a non-empty message_id — a task "
            "without a source-message back-reference cannot be traced to its "
            "email (#1605)."
        )
    existing = {
        row["description_norm"]
        for row in db.query(
            "SELECT description_norm FROM email_tasks WHERE message_id = :m",
            {"m": message_id},
        )
    }
    created: List[str] = []
    for item in items:
        norm = normalize_description(item.description)
        if norm in existing:
            continue
        existing.add(norm)
        task_id = uuid.uuid4().hex
        try:
            db.insert(
                "email_tasks",
                {
                    "task_id": task_id,
                    "message_id": message_id,
                    "description": item.description,
                    "description_norm": norm,
                    "due_hint": item.due_hint,
                    "item_type": item.type,
                    "url": item.url,
                    "status": "open",
                    "created_at": time.time(),
                    "completed_at": None,
                },
            )
        except sqlite3.IntegrityError:
            # A concurrent triage of the same message won the race and
            # already recorded this (message_id, description_norm) pair —
            # the UNIQUE index invariant, not an error.
            continue
        created.append(task_id)
    return created


def list_tasks(
    db, *, message_id: Optional[str] = None, status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return task rows (oldest first), optionally filtered by source message
    and/or status ('open' / 'done')."""
    clauses = []
    params: Dict[str, Any] = {}
    if message_id is not None:
        clauses.append("message_id = :m")
        params["m"] = message_id
    if status is not None:
        clauses.append("status = :s")
        params["s"] = status
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.query(
        f"SELECT * FROM email_tasks{where} ORDER BY created_at, task_id", params
    )
    return rows or []


def mark_task_done(db, *, task_id: str) -> None:
    """Mark a task done. Idempotent — the first completion timestamp is kept."""
    db.update(
        "email_tasks",
        {"status": "done", "completed_at": time.time()},
        "task_id = :id AND status != 'done'",
        {"id": task_id},
    )


__all__ = [
    "EMAIL_TASKS_DDL",
    "init_schema",
    "list_tasks",
    "mark_task_done",
    "normalize_description",
    "record_action_items",
]
