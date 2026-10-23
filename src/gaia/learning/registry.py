# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every adapter the learning cycle has trained, and which one is in use.

Each adapter lives in ``<gaia_home>/learning/adapters/<id>/`` with its GGUF and
a ``manifest.json``. ``state.json`` maps each base model to its active adapter
id. Statuses:

- ``candidate`` — trained, not yet judged
- ``active`` — loaded with its base model
- ``archived`` — passed the gate once, replaced by a newer one (can be rolled
  back to)
- ``rejected`` — did not beat the model it would have replaced
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.learning.errors import LearningError
from gaia.learning.paths import adapters_dir, state_path
from gaia.utils.file_lock import file_lock, write_text_atomic

STATUSES = ("candidate", "active", "archived", "rejected")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise LearningError(f"Could not read {path}: {e}.") from e
    if not isinstance(data, dict):
        raise LearningError(f"{path} is not a JSON object.")
    return data


class AdapterRegistry:
    def __init__(self, root: Optional[Path] = None, state: Optional[Path] = None):
        self.root = root or adapters_dir()
        self.state_file = state or state_path()

    # -- reading ------------------------------------------------------------

    def _manifest_path(self, adapter_id: str) -> Path:
        if not adapter_id or any(c in adapter_id for c in "/\\.:"):
            raise LearningError(f"{adapter_id!r} is not an adapter id.")
        return self.root / adapter_id / "manifest.json"

    def get(self, adapter_id: str) -> Dict[str, Any]:
        path = self._manifest_path(adapter_id)
        if not path.exists():
            raise LearningError(
                f"No adapter {adapter_id!r}. `gaia learn list` shows the adapters."
            )
        return _read_json(path)

    def list(self) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        out = []
        for child in self.root.iterdir():
            manifest = child / "manifest.json"
            if manifest.exists():
                out.append(_read_json(manifest))
        return sorted(out, key=lambda m: m["created_at"], reverse=True)

    def state(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {"active": {}}
        data = _read_json(self.state_file)
        data.setdefault("active", {})
        return data

    def active_id(self, base_model: str) -> Optional[str]:
        return self.state()["active"].get(base_model)

    def active(self, base_model: str) -> Optional[Dict[str, Any]]:
        aid = self.active_id(base_model)
        return self.get(aid) if aid else None

    def gguf_path(self, adapter_id: str) -> Path:
        return self.root / adapter_id / "adapter.gguf"

    # -- writing ------------------------------------------------------------

    def new_dir(self, adapter_id: str) -> Path:
        path = self._manifest_path(adapter_id).parent
        if path.exists():
            raise LearningError(f"Adapter directory {path} already exists.")
        path.mkdir(parents=True)
        return path

    def save(self, manifest: Dict[str, Any]) -> None:
        if manifest.get("status") not in STATUSES:
            raise LearningError(f"Unknown adapter status {manifest.get('status')!r}.")
        path = self._manifest_path(manifest["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path):
            write_text_atomic(path, json.dumps(manifest, indent=2))

    def _set_status(self, adapter_id: str, status: str, **extra: Any) -> Dict[str, Any]:
        m = self.get(adapter_id)
        m["status"] = status
        m.update(extra)
        self.save(m)
        return m

    def activate(self, adapter_id: str) -> Dict[str, Any]:
        """Make *adapter_id* the active adapter for its base model. The one it
        replaces is archived, so it can be rolled back to."""
        m = self.get(adapter_id)
        if not self.gguf_path(adapter_id).exists():
            raise LearningError(
                f"Adapter {adapter_id} has no adapter.gguf at "
                f"{self.gguf_path(adapter_id)}; it can't be loaded. Delete it with "
                f"`gaia learn delete {adapter_id}`."
            )
        with file_lock(self.state_file):
            state = self.state()
            previous = state["active"].get(m["base_model"])
            if previous and previous != adapter_id:
                self._set_status(previous, "archived")
            state["active"][m["base_model"]] = adapter_id
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(self.state_file, json.dumps(state, indent=2))
        return self._set_status(
            adapter_id, "active", activated_at=datetime.now(timezone.utc).isoformat()
        )

    def deactivate(self, base_model: str) -> Optional[str]:
        """Serve *base_model* without an adapter. Returns the id that was active."""
        with file_lock(self.state_file):
            state = self.state()
            previous = state["active"].pop(base_model, None)
            if previous is None:
                return None
            write_text_atomic(self.state_file, json.dumps(state, indent=2))
        self._set_status(previous, "archived")
        return previous

    def retire_holding(self, base_model: str, item_ids: List[str]) -> Optional[str]:
        """Deactivate *base_model*'s adapter if it learned any of *item_ids*.

        A forgotten item must stop shaping answers now, not once a later
        adapter happens to win the gate. Returns the deactivated id.
        """
        active = self.active(base_model)
        if not active or not item_ids:
            return None
        learned = {i["id"] for i in active.get("items", [])}
        if learned.isdisjoint(item_ids):
            return None
        return self.deactivate(base_model)

    def reject(self, adapter_id: str, reason: str) -> Dict[str, Any]:
        return self._set_status(adapter_id, "rejected", decision=reason)

    def delete(self, adapter_id: str) -> None:
        m = self.get(adapter_id)
        if self.active_id(m["base_model"]) == adapter_id:
            raise LearningError(
                f"{adapter_id} is the active adapter for {m['base_model']}. "
                "Deactivate it or activate another one first."
            )
        shutil.rmtree(self._manifest_path(adapter_id).parent)

    def prune(self, keep: int) -> List[str]:
        """Delete all but the newest *keep* non-active adapters."""
        active = set(self.state()["active"].values())
        others = [m for m in self.list() if m["id"] not in active]
        removed = []
        for m in others[keep:]:
            shutil.rmtree(self._manifest_path(m["id"]).parent)
            removed.append(m["id"])
        return removed
