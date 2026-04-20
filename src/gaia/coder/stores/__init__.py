# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SQLite stores backing gaia-coder's durable state.

This package contains one module per persistent store listed in §15.1 of
``docs/plans/coder-agent.mdx``:

========================  ======================  =============================
Module                    File on disk            Purpose
========================  ======================  =============================
``em_inbox``              ``em_inbox.db``         EM input queue (§4.5)
``tasks``                 ``tasks.db``            Task queue (§6.3)
``feedback``              ``feedback.db``         Feedback queue (§7.3)
``spend``                 ``spend.db``            Cost ledger (§6.6)
``audit``                 ``audit.log.db``        Tool-call audit log (§15.1)
``ci_history``            ``ci_history.db``       CI workflow duration cache (§6.2)
``memory``                ``memory.db``           Hybrid SQL+FAISS memory (§6.8)
``paused_tasks``          ``paused-tasks/*.json`` Paused-task snapshots (§7.5)
``self_edits_log``        ``self-edits.log``      Self-edit JSONL log (§6.4)
``learnings_log``         ``learnings.log``       Promotion-candidate log (§4.6)
========================  ======================  =============================

Each SQLite module exposes ``DDL``, ``create_tables(conn)``, ``open_store(path)``,
and typed ``insert_row`` / ``get_row`` / ``update_row`` / ``list_rows`` helpers.
FAISS index wiring for ``memory`` is explicitly Phase 10 work and not present
here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from gaia.coder.stores import (  # noqa: F401 — re-exported for convenience
    audit,
    ci_history,
    em_inbox,
    feedback,
    learnings_log,
    memory,
    paused_tasks,
    self_edits_log,
    spend,
    tasks,
)

__all__ = [
    "audit",
    "ci_history",
    "em_inbox",
    "feedback",
    "learnings_log",
    "memory",
    "paused_tasks",
    "self_edits_log",
    "spend",
    "tasks",
    "create_all_stores",
]


# Canonical layout of ``~/.gaia/coder/`` for the seven SQLite stores plus the
# three non-SQL stores. Keys match store-module names; the values are relative
# paths under the ``root`` passed to :func:`create_all_stores`.
_SQLITE_LAYOUT: dict[str, str] = {
    "em_inbox": "em_inbox.db",
    "tasks": "tasks.db",
    "feedback": "feedback.db",
    "spend": "spend.db",
    "audit": "audit.log.db",
    "ci_history": "ci_history.db",
    "memory": "memory.db",
}

_NON_SQL_LAYOUT: dict[str, str] = {
    "paused_tasks": "paused-tasks",
    "self_edits_log": "self-edits.log",
    "learnings_log": "learnings.log",
}


def create_all_stores(root: str | Path) -> Dict[str, Path]:
    """Create every store under ``root`` and return a ``{store_name: path}`` map.

    * For SQLite stores, the database file is opened (creating it if missing)
      with the canonical PRAGMAs, the DDL is applied, and the connection is
      closed. The returned path points at the ``.db`` file.
    * For non-SQL stores, the target directory / log file's parent directory
      is created. The returned path points at the directory (paused-tasks) or
      the log file (self-edits.log, learnings.log) — callers do not have to
      touch the file before appending.

    The function is idempotent: re-running it on the same ``root`` is a no-op
    for existing tables and files.
    """
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}

    sqlite_modules = {
        "em_inbox": em_inbox,
        "tasks": tasks,
        "feedback": feedback,
        "spend": spend,
        "audit": audit,
        "ci_history": ci_history,
        "memory": memory,
    }
    for name, relative in _SQLITE_LAYOUT.items():
        db_path = root_path / relative
        conn = sqlite_modules[name].open_store(db_path)
        try:
            conn.commit()
        finally:
            conn.close()
        paths[name] = db_path

    # paused-tasks/ is a directory of per-task JSON snapshots.
    paused_dir = root_path / _NON_SQL_LAYOUT["paused_tasks"]
    paused_dir.mkdir(parents=True, exist_ok=True)
    paths["paused_tasks"] = paused_dir

    # self-edits.log and learnings.log are files — make sure their parent
    # directory exists but do not create empty files (so an empty log is
    # distinguishable from "never written").
    for name in ("self_edits_log", "learnings_log"):
        log_path = root_path / _NON_SQL_LAYOUT[name]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        paths[name] = log_path

    return paths
