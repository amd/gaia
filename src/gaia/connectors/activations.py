# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent activations ledger at ``~/.gaia/connectors/activations.json``.

Activations are the second axis of the connectors authorization model, and
they apply to **MCP-server connectors only**. The first axis is
:mod:`gaia.connectors.grants` — "agent X is *allowed* to use connector Y's
credentials" — and it applies to every connector type. Activations answer
a narrower question: "agent X *currently has* MCP connector Y's tools
surfaced in its toolset."

A tool from an MCP connector is visible to an agent if and only if::

    grant exists for (connector_id, agent_id)  AND  is_agent_active(...)

OAuth connectors (e.g. Google) have no MCP tool surface — agents reach them
through native Python ``@tool`` functions that call ``get_credential_sync``
directly — so this ledger does not apply to them. The orchestration layer
(:func:`gaia.connectors.api.activate` / :func:`gaia.connectors.api.deactivate`)
enforces that invariant by rejecting non-``mcp_server`` connector ids with
``ConfigurationError``; this module itself stays type-agnostic so the ledger
remains a pure key/value store.

Activations default to **False** when absent — least-privilege opt-in. The
ledger only stores explicit True/False entries; an unknown ``(connector_id,
agent_id)`` pair returns ``is_agent_active == False``.

Schema::

    {
      "version": 1,
      "activations": {
        "<connector_id>": {
          "<namespaced_agent_id>": true
        }
      }
    }

``version`` reserves a forward-compat lever for future schema evolution.

Atomicity guarantees mirror :mod:`gaia.connectors.grants`:

- Writes go to a unique tempfile via ``tempfile.mkstemp(dir=parent)``,
  then ``os.replace(tmp, final)`` — POSIX atomic, Windows best-effort
  via ``MoveFileEx(MOVEFILE_REPLACE_EXISTING)``.
- Tempfile opened with mode ``0o600`` from creation (``O_EXCL``).
- Per-process ``threading.Lock`` serializes load-modify-save under
  concurrent callers (CLI worker + UI server + tests).
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


# Module constant — resolved at import time. Tests use the function path
# ``_activations_path()`` to pick up monkeypatched ``Path.home`` at call time.
ACTIVATIONS_FILE = Path.home() / ".gaia" / "connectors" / "activations.json"


SCHEMA_VERSION = 1


# Per-process write lock. ``threading.Lock`` is sufficient for the CLI worker
# thread + UI server thread + test driver case; async callers serialize on
# the same lock through ``asyncio.to_thread``.
_write_lock = threading.Lock()


def _activations_path() -> Path:
    """Resolve the activations path on each call so tests can monkeypatch
    ``Path.home`` after import."""
    return Path.home() / ".gaia" / "connectors" / "activations.json"


def _ensure_parent(path: Path) -> None:
    """Create the parent directory with mode 0700 if missing (POSIX)."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(parent, 0o700)
        except OSError as e:
            logger.warning("activations: could not chmod %s: %s", parent, e)


def _empty_ledger() -> Dict[str, object]:
    return {"version": SCHEMA_VERSION, "activations": {}}


def _load_activations_raw() -> Dict[str, object]:
    """
    Read and return the full ledger document (including ``version``).

    Returns an empty ledger if the file does not exist.
    Raises ``ConnectorsError`` on malformed JSON or wrong top-level shape,
    with an actionable message naming the path and the ``rm`` recovery hint.
    """
    path = _activations_path()
    if not path.exists():
        return _empty_ledger()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConnectorsError(
            f"Activations ledger at {path} is corrupted ({e.msg} at line "
            f"{e.lineno}). Delete the file to reset every per-agent "
            f"activation:\n"
            f"  rm {path}\n"
            "You will need to re-activate connectors from Settings → "
            "Connectors → Active for, or via "
            "`gaia connectors activations activate ...`."
        ) from e
    except OSError as e:
        raise ConnectorsError(
            f"Could not read activations ledger at {path}: {e}. Check "
            "file permissions; the parent directory should be 0700 and "
            "the file 0600."
        ) from e
    if not isinstance(data, dict):
        raise ConnectorsError(
            f"Activations ledger at {path} has the wrong shape (expected "
            f"a JSON object). Delete with `rm {path}` to reset."
        )
    # Tolerate older / forward versions silently — readers must keep
    # working even if a future version adds new fields.
    activations = data.get("activations", {})
    if not isinstance(activations, dict):
        raise ConnectorsError(
            f"Activations ledger at {path} has the wrong shape "
            f"(expected an 'activations' object). Delete with "
            f"`rm {path}` to reset."
        )
    return data


def load_activations() -> Dict[str, Dict[str, bool]]:
    """
    Return ``{connector_id: {agent_id: bool}}``.

    Returns an empty dict if no file exists. Raises ``ConnectorsError`` on
    a corrupted ledger (same actionable-error template as grants).
    """
    data = _load_activations_raw()
    activations = data.get("activations", {})
    # Defensive copy — callers must not mutate our cached structure.
    result: Dict[str, Dict[str, bool]] = {}
    for connector_id, agents in activations.items():
        if not isinstance(agents, dict):
            # Skip malformed sub-entry rather than reject the whole file;
            # the corrupted-file path above already covered the common
            # cases. This guards against partial hand-edits.
            continue
        result[connector_id] = {
            agent_id: bool(active) for agent_id, active in agents.items()
        }
    return result


def _save_activations_locked(data: Dict[str, Dict[str, bool]]) -> None:
    """
    Write the activations ledger atomically. Caller MUST hold ``_write_lock``.

    The on-disk shape always includes ``version`` so future readers can
    detect schema migrations.
    """
    path = _activations_path()
    _ensure_parent(path)

    payload = {"version": SCHEMA_VERSION, "activations": data}
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".activations_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, indent=2)
        if sys.platform != "win32":
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
        if sys.platform != "win32":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def activate_agent(connector_id: str, agent_id: str) -> None:
    """
    Mark ``(connector_id, agent_id)`` active.

    Idempotent — re-activating an already-active pair is a no-op write.
    """
    with _write_lock:
        data = load_activations()
        data.setdefault(connector_id, {})[agent_id] = True
        _save_activations_locked(data)
    logger.debug(
        "activations: activated connector_id=%s agent_id=%s",
        connector_id,
        agent_id,
    )


def deactivate_agent(connector_id: str, agent_id: str) -> None:
    """
    Remove the active entry for ``(connector_id, agent_id)``. Idempotent.

    Deactivation deletes the explicit entry rather than writing ``False``
    so the absence-equals-inactive invariant remains the source of truth.
    """
    with _write_lock:
        data = load_activations()
        if connector_id in data and agent_id in data[connector_id]:
            del data[connector_id][agent_id]
            if not data[connector_id]:
                del data[connector_id]
            _save_activations_locked(data)
            logger.debug(
                "activations: deactivated connector_id=%s agent_id=%s",
                connector_id,
                agent_id,
            )


def revoke_all_activations_for(connector_id: str) -> List[str]:
    """
    Remove every agent activation for ``connector_id``.

    Called on connector ``disconnect()`` to prevent silent inheritance when
    a connector with the same id is later re-added — symmetric to
    :func:`gaia.connectors.grants.revoke_all_grants_for`.

    Returns the list of agent_ids whose activations were cleared.
    """
    with _write_lock:
        data = load_activations()
        if connector_id not in data:
            return []
        revoked = sorted(data[connector_id].keys())
        del data[connector_id]
        _save_activations_locked(data)
        logger.info(
            "activations: cleared all agent activations for connector_id=%s "
            "(%d agents: %s)",
            connector_id,
            len(revoked),
            revoked,
        )
        return revoked


def list_agent_activations(connector_id: str) -> Dict[str, bool]:
    """Return ``{agent_id: bool}`` for ``connector_id``, or empty dict."""
    return dict(load_activations().get(connector_id, {}))


def is_agent_active(connector_id: str, agent_id: str) -> bool:
    """
    Return True if ``agent_id`` is explicitly active for ``connector_id``.

    Absence in the ledger returns False — activations are opt-in.
    """
    return bool(load_activations().get(connector_id, {}).get(agent_id, False))


async def activate_agent_async(connector_id: str, agent_id: str) -> None:
    """Async wrapper around :func:`activate_agent` for native-async callers."""
    await asyncio.to_thread(activate_agent, connector_id, agent_id)


async def deactivate_agent_async(connector_id: str, agent_id: str) -> None:
    """Async wrapper around :func:`deactivate_agent`."""
    await asyncio.to_thread(deactivate_agent, connector_id, agent_id)


__all__ = [
    "ACTIVATIONS_FILE",
    "SCHEMA_VERSION",
    "activate_agent",
    "activate_agent_async",
    "deactivate_agent",
    "deactivate_agent_async",
    "is_agent_active",
    "list_agent_activations",
    "load_activations",
    "revoke_all_activations_for",
]
