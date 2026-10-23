# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One record per learning cycle, in ``<gaia_home>/learning/runs/<id>/run.json``.

The record is rewritten as the cycle moves through its phases, so the CLI and
the Agent UI can show a live phase and training progress, and the morning
summary afterwards.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.learning.errors import LearningError
from gaia.learning.paths import runs_dir
from gaia.utils.file_lock import write_text_atomic

# Terminal statuses. "running" is the only other.
FINISHED = ("promoted", "rejected", "nothing_to_learn", "stopped", "failed", "skipped")


def new_run_id(now: Optional[datetime] = None, root: Optional[Path] = None) -> str:
    """Local-time id, sortable; suffixed when two runs start in one second."""
    base = (now or datetime.now(timezone.utc)).astimezone().strftime("%Y%m%d-%H%M%S")
    runs = root or runs_dir()
    candidate, n = base, 1
    while (runs / candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


class RunRecord:
    def __init__(self, run_id: str, root: Optional[Path] = None, **fields: Any):
        self.dir = (root or runs_dir()) / run_id
        self.data: Dict[str, Any] = {
            "id": run_id,
            "status": "running",
            "phase": "starting",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "progress": None,
            "error": None,
            **fields,
        }

    @property
    def path(self) -> Path:
        return self.dir / "run.json"

    def update(self, **fields: Any) -> None:
        self.data.update(fields)
        self.dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(self.data, indent=2, default=str))

    def finish(self, status: str, **fields: Any) -> None:
        if status not in FINISHED:
            raise LearningError(f"Unknown run status {status!r}.")
        self.update(
            status=status,
            phase="done",
            finished_at=datetime.now(timezone.utc).isoformat(),
            **fields,
        )

    def write_json(self, name: str, data: Any) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        write_text_atomic(path, json.dumps(data, indent=2, default=str))
        return path


def read_run(run_id: str, root: Optional[Path] = None) -> Dict[str, Any]:
    if not run_id or any(c in run_id for c in "/\\.:"):
        raise LearningError(f"{run_id!r} is not a run id.")
    path = (root or runs_dir()) / run_id / "run.json"
    if not path.exists():
        raise LearningError(f"No learning run {run_id!r}.")
    return json.loads(path.read_text(encoding="utf-8"))


def read_run_file(run_id: str, name: str, root: Optional[Path] = None) -> Any:
    read_run(run_id, root)
    path = (root or runs_dir()) / run_id / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(limit: int = 30, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    base = root or runs_dir()
    if not base.exists():
        return []
    out = []
    for child in sorted(base.iterdir(), reverse=True):
        path = child / "run.json"
        if path.exists():
            out.append(json.loads(path.read_text(encoding="utf-8")))
        if len(out) >= limit:
            break
    return out


def last_scheduled_start(root: Optional[Path] = None) -> Optional[datetime]:
    for run in list_runs(limit=50, root=root):
        if run.get("trigger") == "scheduled":
            return datetime.fromisoformat(run["started_at"])
    return None
