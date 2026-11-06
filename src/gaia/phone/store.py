# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Phone state on disk: settings, the phone book, codes, alerts and deliveries.

One SQLite file, ``<GAIA home>/phone/phone.db``. Alerts and deliveries live
here so a daemon restart resumes an open alert instead of losing or repeating
it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL,
    verified_at REAL NOT NULL,
    verified_by TEXT NOT NULL DEFAULT 'twilio'  -- provider whose code proved it
);
CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL,            -- add | remove
    number TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    contact_id INTEGER,               -- remove: the contact; add: the confirming one
    code_hash TEXT NOT NULL,
    confirm_hash TEXT,                -- second code, sent to an existing number
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending'  -- pending | done | failed
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    rule_key TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    urgent INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'open',   -- open | handled
    handled_at REAL,
    steps TEXT NOT NULL,                  -- notify | notify_text | notify_text_call
    text_due_at REAL,
    text_sent INTEGER NOT NULL DEFAULT 0,
    call_due_at REAL,
    calls_made INTEGER NOT NULL DEFAULT 0,
    reached INTEGER NOT NULL DEFAULT 0,
    held TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER,
    purpose TEXT NOT NULL,     -- alert | code | notice | test
    channel TEXT NOT NULL,     -- call | text
    contact_id INTEGER,
    number TEXT NOT NULL,
    state TEXT NOT NULL,       -- placing | placed | done | failed
    provider_sid TEXT,
    outcome TEXT NOT NULL DEFAULT '',
    reached INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS deliveries_state ON deliveries(state);
CREATE INDEX IF NOT EXISTS alerts_state ON alerts(state);
"""


class PhoneStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            columns = {
                r["name"] for r in self._db.execute("PRAGMA table_info(contacts)")
            }
            if "verified_by" not in columns:
                self._db.execute(
                    "ALTER TABLE contacts ADD COLUMN verified_by TEXT NOT NULL "
                    "DEFAULT 'twilio'"
                )
            self._db.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- settings -------------------------------------------------------

    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT data FROM settings WHERE id = 1").fetchone()
        return json.loads(row["data"]) if row else {}

    def put_settings(self, data: Dict[str, Any]) -> None:
        with self.tx() as db:
            db.execute(
                "INSERT INTO settings (id, data) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (json.dumps(data),),
            )

    # ---- phone book -----------------------------------------------------

    def contacts(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM contacts ORDER BY position, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def contact(self, contact_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM contacts WHERE id = ?", (contact_id,)
            ).fetchone()
        return dict(row) if row else None

    def contact_by_number(self, number: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM contacts WHERE number = ?", (number,)
            ).fetchone()
        return dict(row) if row else None

    def add_contact(self, number: str, label: str, *, verified_by: str) -> int:
        with self.tx() as db:
            pos = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM contacts")
            position = pos.fetchone()[0]
            cur = db.execute(
                "INSERT INTO contacts (number, label, position, verified_at, "
                "verified_by) VALUES (?, ?, ?, ?, ?)",
                (number, label, position, time.time(), verified_by),
            )
            return int(cur.lastrowid)

    def remove_contacts_verified_by(self, provider: str) -> int:
        with self.tx() as db:
            cur = db.execute("DELETE FROM contacts WHERE verified_by = ?", (provider,))
            return cur.rowcount

    def remove_contact(self, contact_id: int) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))

    def clear_contacts(self) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM contacts")

    # ---- verifications --------------------------------------------------

    def add_verification(self, row: Dict[str, Any]) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.tx() as db:
            db.execute(
                f"INSERT INTO verifications ({cols}) VALUES ({marks})",
                tuple(row.values()),
            )

    def verification(self, vid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM verifications WHERE id = ?", (vid,)
            ).fetchone()
        return dict(row) if row else None

    def update_verification(self, vid: str, **fields: Any) -> None:
        self._update("verifications", "id", vid, fields)

    def verifications_since(self, since: float) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM verifications WHERE created_at >= ?", (since,)
            ).fetchone()
        return int(row[0])

    # ---- alerts ---------------------------------------------------------

    def add_alert(self, row: Dict[str, Any]) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.tx() as db:
            cur = db.execute(
                f"INSERT INTO alerts ({cols}) VALUES ({marks})", tuple(row.values())
            )
            return int(cur.lastrowid)

    def alert(self, alert_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
        return dict(row) if row else None

    def open_alert_for(self, rule_key: str, since: float) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM alerts WHERE rule_key = ? AND state = 'open' "
                "AND last_seen_at >= ? ORDER BY id DESC LIMIT 1",
                (rule_key, since),
            ).fetchone()
        return dict(row) if row else None

    def alerts(
        self, *, open_only: bool = False, limit: int = 50
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM alerts"
        if open_only:
            sql += " WHERE state = 'open'"
        sql += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update_alert(self, alert_id: int, **fields: Any) -> None:
        self._update("alerts", "id", alert_id, fields)

    # ---- deliveries -----------------------------------------------------

    def add_delivery(self, row: Dict[str, Any]) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.tx() as db:
            cur = db.execute(
                f"INSERT INTO deliveries ({cols}) VALUES ({marks})", tuple(row.values())
            )
            return int(cur.lastrowid)

    def update_delivery(self, delivery_id: int, **fields: Any) -> None:
        self._update("deliveries", "id", delivery_id, fields)

    def deliveries(
        self,
        *,
        alert_id: Optional[int] = None,
        states: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM deliveries WHERE 1=1", []
        if alert_id is not None:
            sql += " AND alert_id = ?"
            args.append(alert_id)
        if states:
            sql += f" AND state IN ({', '.join('?' for _ in states)})"
            args += states
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count_deliveries(
        self,
        channel: str,
        since: float,
        *,
        source: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> int:
        sql = (
            "SELECT COUNT(*) FROM deliveries d LEFT JOIN alerts a ON a.id = d.alert_id "
            "WHERE d.channel = ? AND d.created_at >= ?"
        )
        args: List[Any] = [channel, since]
        if purpose is not None:
            sql += " AND d.purpose = ?"
            args.append(purpose)
        if source is not None:
            sql += " AND a.source = ?"
            args.append(source)
        with self._lock:
            return int(self._db.execute(sql, args).fetchone()[0])

    # ---- plumbing -------------------------------------------------------

    _COLUMNS: Dict[str, frozenset] = {}

    def _update(self, table: str, key: str, value: Any, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        allowed = self._columns(table)
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown {table} columns: {sorted(unknown)}")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self.tx() as db:
            db.execute(
                f"UPDATE {table} SET {assignments} WHERE {key} = ?",
                (*fields.values(), value),
            )

    def _columns(self, table: str) -> frozenset:
        if table not in self._COLUMNS:
            with self._lock:
                rows = self._db.execute(f"PRAGMA table_info({table})").fetchall()
            self._COLUMNS[table] = frozenset(r["name"] for r in rows)
        return self._COLUMNS[table]
