# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read-only views of what the agent remembers and did.

Two stores feed learning: the Agent UI chat database (each turn's question,
answer and tool steps) and the memory database (what the agent learned about
the user, every tool call's outcome, and what the user asked it to forget).
Both are opened read-only; a missing store reads as empty, since a fresh
install has neither yet.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.learning.errors import LearningError


@dataclass
class ToolStep:
    tool: str
    detail: str
    result: str
    success: Optional[bool]


@dataclass
class Turn:
    session_id: str
    question_id: int
    question: str
    answer: str
    asked_at: str
    answered_at: str
    steps: List[ToolStep] = field(default_factory=list)


@dataclass
class Fact:
    id: str
    category: str
    content: str
    created_at: str
    updated_at: str = ""
    confidence: float = 0.5
    entity: Optional[str] = None


@dataclass
class ToolCall:
    session_id: str
    tool_name: str
    args: Dict[str, Any]
    result_summary: str
    success: bool
    error: str
    timestamp: str


@dataclass
class History:
    turns: List[Turn]
    facts: List[Fact]
    tool_calls: List[ToolCall]
    forgotten: List[str]

    @property
    def empty(self) -> bool:
        return not (self.turns or self.facts or self.tool_calls)


def _connect_ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise LearningError(f"Could not open {path} read-only: {e}") from e
    conn.row_factory = sqlite3.Row
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _has_table(conn, table):
        return False
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _memory_off(conn: sqlite3.Connection) -> bool:
    """The user turned memory off in the Agent UI: their chats are not to be
    remembered, so they are not to be learned either."""
    if not _has_table(conn, "settings"):
        return False
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'memory_enabled'"
    ).fetchone()
    return row is not None and row[0] != "true"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def parse_ts(raw: Optional[str]) -> Optional[datetime]:
    """Both stamp formats the stores use: ISO 8601 with an offset, and
    SQLite's ``datetime('now')`` (UTC, no offset)."""
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _since(raw: Optional[str], since: datetime) -> bool:
    ts = parse_ts(raw)
    return ts is not None and ts >= since


def _steps(raw: Optional[str]) -> List[ToolStep]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    steps = []
    for s in data if isinstance(data, list) else []:
        if not isinstance(s, dict) or s.get("type") != "tool" or not s.get("tool"):
            continue
        steps.append(
            ToolStep(
                tool=str(s["tool"]),
                detail=str(s.get("detail") or ""),
                result=str(s.get("result") or ""),
                success=s.get("success"),
            )
        )
    return steps


def read_turns(chat_db: Path, since: datetime) -> List[Turn]:
    """Each user question paired with the assistant reply that followed it."""
    conn = _connect_ro(chat_db)
    if conn is None:
        return []
    try:
        if not _has_table(conn, "messages"):
            return []
        if _memory_off(conn):
            return []
        private = (
            "AND session_id NOT IN (SELECT id FROM sessions WHERE private = 1) "
            if _has_column(conn, "sessions", "private")
            else ""
        )
        rows = conn.execute(
            "SELECT id, session_id, role, content, created_at, agent_steps "
            "FROM messages WHERE role IN ('user','assistant') "
            + private
            + "ORDER BY session_id, id"
        ).fetchall()
    finally:
        conn.close()
    turns: List[Turn] = []
    pending: Optional[sqlite3.Row] = None
    for row in rows:
        if row["role"] == "user":
            pending = row
            continue
        if pending is None or pending["session_id"] != row["session_id"]:
            continue
        if row["content"].strip() and _since(pending["created_at"], since):
            turns.append(
                Turn(
                    session_id=row["session_id"],
                    question_id=pending["id"],
                    question=pending["content"],
                    answer=row["content"],
                    asked_at=pending["created_at"],
                    answered_at=row["created_at"],
                    steps=_steps(row["agent_steps"]),
                )
            )
        pending = None
    return turns


def read_memory(memory_db: Path, since: datetime) -> tuple:
    """(facts, tool_calls, forgotten) from the memory database.

    Facts flagged sensitive never leave this function: a LoRA memorises what
    it is trained on, and a sensitive memory must not end up in weights. Only
    facts the user taught (``user``, or the agent's ``remember`` tool) count;
    ones extracted automatically from a conversation may carry text the agent
    read from a web page or email, and training would make it permanent.
    """
    conn = _connect_ro(memory_db)
    if conn is None:
        return [], [], []
    try:
        facts: List[Fact] = []
        if _has_table(conn, "knowledge"):
            for r in conn.execute(
                "SELECT id, category, content, created_at, updated_at, confidence, entity "
                "FROM knowledge WHERE COALESCE(sensitive,0)=0 "
                "AND source IN ('user','tool') "
                "AND superseded_by IS NULL "
                "AND category IN ('preference','fact','note','skill','error')"
            ).fetchall():
                facts.append(
                    Fact(
                        id=r["id"],
                        category=r["category"],
                        content=r["content"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"] or r["created_at"],
                        confidence=float(r["confidence"] or 0.5),
                        entity=r["entity"],
                    )
                )
        calls: List[ToolCall] = []
        if _has_table(conn, "tool_history"):
            for r in conn.execute(
                "SELECT session_id, tool_name, args, result_summary, success, "
                "error, timestamp FROM tool_history ORDER BY id"
            ).fetchall():
                if not _since(r["timestamp"], since):
                    continue
                try:
                    args = json.loads(r["args"]) if r["args"] else {}
                except json.JSONDecodeError:
                    args = {}
                calls.append(
                    ToolCall(
                        session_id=r["session_id"],
                        tool_name=r["tool_name"],
                        args=args if isinstance(args, dict) else {},
                        result_summary=r["result_summary"] or "",
                        success=bool(r["success"]),
                        error=r["error"] or "",
                        timestamp=r["timestamp"],
                    )
                )
        forgotten: List[str] = []
        if _has_table(conn, "forgotten"):
            forgotten = [
                r["content"]
                for r in conn.execute("SELECT content FROM forgotten").fetchall()
            ]
    finally:
        conn.close()
    return facts, calls, forgotten


def read_history(chat_db: Path, memory_db: Path, since: datetime) -> History:
    facts, calls, forgotten = read_memory(memory_db, since)
    return History(
        turns=read_turns(chat_db, since),
        facts=facts,
        tool_calls=calls,
        forgotten=forgotten,
    )


def default_chat_db() -> Path:
    from gaia.ui.database import default_db_path

    return default_db_path()


def default_memory_db() -> Path:
    from gaia.agents.base.memory_store import resolve_memory_db_path

    return resolve_memory_db_path()


def last_activity(chat_db: Path, memory_db: Path) -> Optional[datetime]:
    """When the user last talked to the agent, from either store."""
    stamps: List[datetime] = []
    for path, sql in (
        (
            chat_db,
            "SELECT created_at FROM messages WHERE role='user' "
            "ORDER BY id DESC LIMIT 1",
        ),
        (
            memory_db,
            "SELECT timestamp FROM conversations WHERE role='user' "
            "ORDER BY id DESC LIMIT 1",
        ),
    ):
        conn = _connect_ro(path)
        if conn is None:
            continue
        try:
            table = "messages" if path == chat_db else "conversations"
            if _has_table(conn, table):
                row = conn.execute(sql).fetchone()
                ts = parse_ts(row[0]) if row else None
                if ts is not None:
                    stamps.append(ts)
        finally:
            conn.close()
    return max(stamps) if stamps else None
