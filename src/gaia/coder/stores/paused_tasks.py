# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``paused-tasks/<task-id>.json`` — one JSON snapshot per paused task.

Backs §7.5 (self-fix pause/resume) of ``docs/plans/coder-agent.mdx``. When the
agent pauses a task — either because she detects a self-bug or because the EM
says "pause" — she persists everything needed to resume it to a JSON file
under ``paused-tasks/``. Each file is self-describing so a later agent
instance (possibly on a different ``loop_version``) can still parse it.

**Not SQLite.** JSON is the right shape here because snapshots are read
whole, written whole, never queried, and need to round-trip arbitrary tool
histories without schema migrations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _validate_task_id(task_id: str) -> None:
    """Refuse path-separator characters in ``task_id`` to keep reads/writes contained."""
    if not task_id:
        raise ValueError("task_id must be non-empty")
    if "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
        raise ValueError(
            f"task_id must not contain path separators or be '.'/'..': {task_id!r}"
        )


def snapshot_path(root: Path, task_id: str) -> Path:
    """Return the canonical path of a snapshot for ``task_id`` under ``root``.

    ``root`` is the ``paused-tasks/`` directory; the returned path is
    ``root / f"{task_id}.json"``.
    """
    _validate_task_id(task_id)
    return Path(root) / f"{task_id}.json"


def write_snapshot(root: Path, task_id: str, data: dict[str, Any]) -> Path:
    """Write ``data`` as a pretty-printed JSON snapshot for ``task_id``.

    Returns the absolute :class:`Path` of the written file. The parent
    directory is created if missing. Writes are atomic via ``.tmp`` rename.
    """
    _validate_task_id(task_id)
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    final_path = root_path / f"{task_id}.json"
    tmp_path = final_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path


def read_snapshot(root: Path, task_id: str) -> dict[str, Any]:
    """Read the JSON snapshot for ``task_id``.

    Raises :class:`FileNotFoundError` if no snapshot exists — callers should
    surface that loudly (per CLAUDE.md "no silent fallbacks") rather than
    treating it as "no paused state".
    """
    _validate_task_id(task_id)
    path = Path(root) / f"{task_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no paused-task snapshot at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_snapshots(root: Path) -> list[str]:
    """Return task_ids of every snapshot present under ``root`` (sorted)."""
    root_path = Path(root)
    if not root_path.exists():
        return []
    return sorted(p.stem for p in root_path.iterdir() if p.suffix == ".json")
