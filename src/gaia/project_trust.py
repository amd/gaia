# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which project folders the user has let GAIA read guidance from.

A cloned repository can carry a ``CLAUDE.md``, an ``AGENTS.md`` and
``.claude/skills`` that GAIA puts in the model's prompt (see
:mod:`gaia.project_context`). Like Claude Code, GAIA asks once per folder before
doing that, and remembers the answer here: ``project_trust.json`` beside
``config.json``, one entry per folder, ``trusted`` or ``declined``.

A folder with no entry is unknown. Whoever can ask a person does; everything
else (``gaia -p``, a pipe, a test) treats it as not loaded, so nothing hangs.

Writes take the same lock and atomic replace as ``config.json``
(:mod:`gaia.utils.file_lock`), so two sessions answering at once keep both
answers.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Union

from gaia.config import GaiaConfig
from gaia.logger import get_logger
from gaia.utils.file_lock import (
    FileLockError,
    file_lock,
    lock_dir_unwritable,
    write_text_atomic,
)

log = get_logger(__name__)

STORE_NAME = "project_trust.json"

TRUSTED = "trusted"
DECLINED = "declined"
DECISIONS = (TRUSTED, DECLINED)


class ProjectTrustError(Exception):
    """The trust store cannot be updated; the message says what to do."""


def trust_store_path() -> Path:
    """``project_trust.json``, in the folder that holds ``config.json``."""
    return GaiaConfig.config_path().with_name(STORE_NAME)


def folder_key(folder: Union[str, "os.PathLike[str]"]) -> str:
    """*folder* as the store spells it: absolute, links resolved, no trailing slash.

    ``normcase`` makes ``C:\\Work\\Repo`` and ``c:/work/repo/`` one folder on
    Windows and leaves POSIX paths, which are case-sensitive, alone.
    """
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(folder))))


def _read_text(store: Path) -> Optional[str]:
    try:
        return store.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _parse(text: Optional[str], store: Path) -> Dict[str, dict]:
    """The store's entries; raises ProjectTrustError when it is not a JSON object."""
    if text is None:
        return {}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        doc = e
    if not isinstance(doc, dict):
        raise ProjectTrustError(
            f"{store} is not a valid JSON object ({doc}). Fix it by hand, or delete "
            "it to forget every answer; GAIA will ask again."
        )
    return doc


def _entries() -> Dict[str, dict]:
    """Every saved answer. An unreadable store reads as empty, so GAIA asks again."""
    store = trust_store_path()
    if not store.parent.is_dir():
        return {}
    try:
        try:
            with file_lock(store):
                return _parse(_read_text(store), store)
        except FileLockError as e:
            if not lock_dir_unwritable(e):
                raise
            # A read-only folder: no GAIA process can replace the file mid-read.
            return _parse(_read_text(store), store)
    except (OSError, UnicodeDecodeError, ProjectTrustError) as e:
        log.warning("Ignoring the project trust store %s: %s", store, e)
        return {}


def folder_trust(folder: Union[str, "os.PathLike[str]"]) -> Optional[str]:
    """``TRUSTED``, ``DECLINED``, or ``None`` when the user was never asked."""
    entry = _entries().get(folder_key(folder))
    decision = entry.get("decision") if isinstance(entry, dict) else None
    return decision if decision in DECISIONS else None


def remember_folder_trust(
    folder: Union[str, "os.PathLike[str]"], decision: str
) -> None:
    """Save *decision* for *folder*, keeping every other folder's answer.

    Raises:
        ProjectTrustError: the store cannot be locked, read or written.
    """
    if decision not in DECISIONS:
        raise ValueError(
            f"decision must be one of {', '.join(DECISIONS)}: {decision!r}"
        )
    store = trust_store_path()
    try:
        with file_lock(store):
            entries = _parse(_read_text(store), store)
            entries[folder_key(folder)] = {
                "decision": decision,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            write_text_atomic(store, json.dumps(entries, indent=2) + "\n")
    except (OSError, UnicodeDecodeError) as e:
        raise ProjectTrustError(f"Cannot save the answer to {store}: {e}") from e


__all__ = [
    "DECISIONS",
    "DECLINED",
    "ProjectTrustError",
    "STORE_NAME",
    "TRUSTED",
    "folder_key",
    "folder_trust",
    "remember_folder_trust",
    "trust_store_path",
]
