# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Connector state store at ``~/.gaia/connectors/state.json``.

Holds non-secret connector configuration and metadata — the cheap source
of truth for "which connectors have been configured at all" that the catalog
UI queries WITHOUT touching the OS keyring (plan amendment A12).

Schema::

    {
      "<connector_id>": {
        "configured": true,
        "account_id": "<email or opaque id>",
        "scopes": ["<scope-1>", ...],
        "last_tested_at": "<ISO-8601 or null>",
        "non_secret_fields": {"<key>": "<value>", ...}
      }
    }

Atomicity uses the same ``tempfile.mkstemp`` + ``os.replace`` pattern as
``grants.py`` so cross-process readers always see a complete snapshot.

The keyring (``store.py``) is the authoritative source for secrets.
``state.json`` holds only the metadata needed to render the catalog UI
without a keyring round-trip.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.connectors.errors import ConnectorsError

logger = logging.getLogger(__name__)

STATE_FILE = Path.home() / ".gaia" / "connectors" / "state.json"

_write_lock = threading.Lock()


def _state_path() -> Path:
    """Resolve on each call so tests can monkeypatch ``Path.home``."""
    return Path.home() / ".gaia" / "connectors" / "state.json"


def _ensure_parent(path: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(parent, 0o700)
        except OSError as e:
            logger.warning("state: could not chmod %s: %s", parent, e)


def load_state() -> Dict[str, Dict[str, Any]]:
    """Read and return the full state dict. Returns {} if no file exists."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConnectorsError(
            f"Connector state at {path} is corrupted ({e.msg} at line {e.lineno}). "
            f"Delete to reset: rm {path}"
        ) from e
    if not isinstance(data, dict):
        raise ConnectorsError(
            f"Connector state at {path} has unexpected top-level type "
            f"{type(data).__name__!r} (expected object). Delete to reset: rm {path}"
        )
    return data


def _save_state_locked(data: Dict[str, Dict[str, Any]]) -> None:
    """Write ``data`` to state.json atomically. Must hold ``_write_lock``."""
    path = _state_path()
    _ensure_parent(path)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".state_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if sys.platform != "win32":
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_connector_state(connector_id: str) -> Optional[Dict[str, Any]]:
    """Return the state dict for ``connector_id``, or ``None`` if absent."""
    return load_state().get(connector_id)


def set_connector_state(
    connector_id: str,
    *,
    configured: bool,
    account_id: Optional[str] = None,
    scopes: Optional[List[str]] = None,
    last_tested_at: Optional[str] = None,
    non_secret_fields: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Upsert the state entry for ``connector_id``.

    Merges with any existing entry — only the keys you pass are updated.
    """
    with _write_lock:
        data = load_state()
        entry = data.get(connector_id, {})
        entry["configured"] = configured
        if account_id is not None:
            entry["account_id"] = account_id
        if scopes is not None:
            entry["scopes"] = list(scopes)
        if last_tested_at is not None:
            entry["last_tested_at"] = last_tested_at
        if non_secret_fields is not None:
            entry["non_secret_fields"] = dict(non_secret_fields)
        data[connector_id] = entry
        _save_state_locked(data)
    logger.debug(
        "state: updated connector_id=%s configured=%s", connector_id, configured
    )


def clear_connector_state(connector_id: str) -> None:
    """Remove the state entry for ``connector_id``. Idempotent."""
    with _write_lock:
        data = load_state()
        if connector_id in data:
            del data[connector_id]
            _save_state_locked(data)
            logger.debug("state: cleared connector_id=%s", connector_id)


def list_configured_ids() -> List[str]:
    """Return connector_ids that have ``configured=True`` in state.json."""
    return [
        cid for cid, entry in load_state().items() if entry.get("configured", False)
    ]
