# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent grants ledger at ``~/.gaia/connectors/grants.json``.

Schema::

    {
      "<connector_id>": {
        "<namespaced_agent_id>": ["<scope-1>", "<scope-2>"]
      }
    }

Where ``namespaced_agent_id`` is ``builtin:<id>`` for built-in agents,
``custom:<sha256-prefix>:<id>`` for custom agents under
``~/.gaia/agents/`` (per plan amendment A9), ``installed:<id>`` for
wheel-installed agents, and ``native:<id>`` for binary agents.

Atomicity guarantees:

- Writes go to a unique tempfile via ``tempfile.mkstemp(dir=parent)``,
  then ``os.replace(tmp, final)`` — POSIX atomic, Windows best-effort
  via ``MoveFileEx(MOVEFILE_REPLACE_EXISTING)``. ``os.rename`` would
  raise on Windows when the destination exists.
- The tempfile is opened with ``0o600`` from the start (``O_EXCL`` mode
  on the file descriptor) so there is no window where the file briefly
  has a default mode.
- A per-process ``asyncio.Lock`` serializes concurrent writes from the
  same event loop. Cross-process concurrency is documented as a v1
  limitation in ``connections/__init__.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Dict, List

from gaia.connectors.errors import ConnectorsError

logger = logging.getLogger(__name__)


# Read at import time. Tests monkeypatch ``Path.home`` BEFORE this module is
# imported (or after — see test conftest); the runtime helper ``_grants_path``
# evaluates ``Path.home()`` on every call so it sees the latest patched value.
GRANTS_FILE = Path.home() / ".gaia" / "connectors" / "grants.json"


# Per-process write lock. Both an asyncio.Lock and a threading.Lock are
# needed because grant_agent is sync but may be invoked from multiple
# threads (CLI worker thread + UI server thread + test driver). The
# threading.Lock is sufficient; the asyncio.Lock would only matter for
# native-async callers, which serialize anyway under our usage pattern.
_write_lock = threading.Lock()


def _grants_path() -> Path:
    """Resolve the grants path on each call so tests can ``monkeypatch.setattr``
    on ``Path.home`` after import."""
    return Path.home() / ".gaia" / "connectors" / "grants.json"


def _ensure_parent(path: Path) -> None:
    """Create the parent directory with mode 0700 if missing (POSIX)."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        # mkdir's mode honors the umask; chmod explicitly to 0o700.
        try:
            os.chmod(parent, 0o700)
        except OSError as e:
            # Windows or restricted filesystems — not fatal; log and continue.
            logger.warning("grants: could not chmod %s: %s", parent, e)


def load_grants() -> Dict[str, Dict[str, List[str]]]:
    """
    Read and return the grants ledger. Returns an empty dict if no file.

    A corrupted file raises ``ConnectorsError`` with the path and the
    rm command for recovery (A7).
    """
    path = _grants_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConnectorsError(
            f"Grants ledger at {path} is corrupted ({e.msg} at line "
            f"{e.lineno}). Delete the file to reset all per-agent grants: "
            f"  rm {path}\n"
            "You will need to re-grant scopes from Settings → Connections "
            "or via `gaia connectors grants grant ...`."
        ) from e
    except OSError as e:
        raise ConnectorsError(
            f"Could not read grants ledger at {path}: {e}. Check file "
            "permissions; the parent directory should be 0700 and the "
            "file 0600."
        ) from e
    if not isinstance(data, dict):
        raise ConnectorsError(
            f"Grants ledger at {path} has the wrong shape (expected a "
            f"JSON object). Delete with `rm {path}` to reset."
        )
    return data


def _save_grants_locked(data: Dict[str, Dict[str, List[str]]]) -> None:
    """
    Write the grants ledger to disk atomically. Caller MUST hold ``_write_lock``.

    Tempfile is created with mode 0600 from the start.
    """
    path = _grants_path()
    _ensure_parent(path)

    # mkstemp returns an OS-level fd opened with O_EXCL — no other process
    # can attach to the same name. The fd is opened with mode 0600 by
    # mkstemp on POSIX.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".grants_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, indent=2)
        if sys.platform != "win32":
            # mkstemp sets 0600 on POSIX, but be defensive in case the
            # kernel returned a different mode (e.g. on tmpfs).
            os.chmod(tmp_path, 0o600)
        # os.replace is atomic on POSIX and best-effort atomic on Windows
        # (MoveFileEx with MOVEFILE_REPLACE_EXISTING).
        os.replace(tmp_path, path)
        # os.replace inherits the destination's prior mode (or umask for new
        # files); enforce 0600 on the destination explicitly so prior runs
        # with a permissive umask don't leave the grants file world-readable.
        if sys.platform != "win32":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except Exception:
        # Clean up the tempfile on any failure path so we don't leak.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def grant_agent(connector_id: str, agent_id: str, scopes: List[str]) -> None:
    """
    Grant ``agent_id`` (already namespaced) the given scopes for ``connector_id``.

    Overwrites any existing scopes for the same ``(connector_id, agent_id)`` pair.
    The full load-modify-save sequence is performed under the per-process
    write lock so concurrent grants from multiple threads don't lose updates.
    """
    with _write_lock:
        data = load_grants()
        data.setdefault(connector_id, {})[agent_id] = list(scopes)
        _save_grants_locked(data)
    logger.debug(
        "grants: granted connector_id=%s agent_id=%s scopes=%d",
        connector_id,
        agent_id,
        len(scopes),
    )


def revoke_agent_grant(connector_id: str, agent_id: str) -> None:
    """
    Remove an agent's grant for ``connector_id``. Idempotent — silently no-ops
    if the agent has no grant.
    """
    with _write_lock:
        data = load_grants()
        if connector_id in data and agent_id in data[connector_id]:
            del data[connector_id][agent_id]
            if not data[connector_id]:
                del data[connector_id]
            _save_grants_locked(data)
            logger.debug(
                "grants: revoked connector_id=%s agent_id=%s", connector_id, agent_id
            )


def revoke_all_grants_for(connector_id: str) -> List[str]:
    """
    Remove every agent grant for ``connector_id``.

    Called on connector ``disconnect()`` to prevent silent grant inheritance
    when a connector with the same id is later re-added (which would otherwise
    re-attach the previous user's consent to the new connector with no
    confirmation prompt — a real security bypass).

    Returns the list of agent_ids whose grants were revoked, useful for
    callers that want to log or audit the revocation.
    """
    with _write_lock:
        data = load_grants()
        if connector_id not in data:
            return []
        revoked = sorted(data[connector_id].keys())
        del data[connector_id]
        _save_grants_locked(data)
        logger.info(
            "grants: revoked all agent grants for connector_id=%s (%d agents: %s)",
            connector_id,
            len(revoked),
            revoked,
        )
        return revoked


def list_agent_grants(connector_id: str) -> Dict[str, List[str]]:
    """Return ``{agent_id: [scopes]}`` for ``connector_id``, or empty dict."""
    return dict(load_grants().get(connector_id, {}))


def check_agent_grant(
    connector_id: str, agent_id: str, required_scopes: List[str]
) -> bool:
    """
    Return True if ``agent_id`` has been granted a superset of
    ``required_scopes`` for ``connector_id``.
    """
    granted = set(list_agent_grants(connector_id).get(agent_id, []))
    return set(required_scopes) <= granted


# Public alias kept for the asyncio-friendly API. The underlying call is
# sync because file I/O on local disk is fast and the per-process write
# is rare. Callers in async code can use ``await asyncio.to_thread(...)``
# if they need to keep the loop unblocked under heavy concurrency.
async def grant_agent_async(
    connector_id: str, agent_id: str, scopes: List[str]
) -> None:
    """Async wrapper around ``grant_agent`` for native-async callers."""
    await asyncio.to_thread(grant_agent, connector_id, agent_id, scopes)
