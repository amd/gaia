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
from typing import Dict, List, Optional, Union

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


def list_folder_trust() -> Dict[str, dict]:
    """Every saved answer with its metadata.

    Unlike :func:`_entries`, this raises :class:`ProjectTrustError` when the
    store cannot be locked, read, or decoded as a JSON object, so callers (such
    as the CLI) can name the broken file and report the error directly rather
    than treating the store as empty.
    """
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
            return _parse(_read_text(store), store)
    except (OSError, UnicodeDecodeError) as e:
        raise ProjectTrustError(
            f"Cannot read the project trust store {store}: {e}"
        ) from e


def forget_folder_trust(
    folder: Optional[Union[str, "os.PathLike[str]"]] = None,
    *,
    all_entries: bool = False,
    missing_only: bool = False,
) -> List[str]:
    """Remove saved trust answers from the store under the file lock.

    Args:
        folder: specific folder whose answer to forget.
        all_entries: forget all saved answers.
        missing_only: forget only entries whose folders no longer exist on disk.

    Returns:
        List of folder keys that were removed.

    Raises:
        ProjectTrustError: the store cannot be locked, read, or written.
    """
    store = trust_store_path()
    try:
        with file_lock(store):
            entries = _parse(_read_text(store), store)
            if all_entries:
                removed = list(entries.keys())
                entries.clear()
            elif missing_only:
                removed = [k for k in list(entries.keys()) if not os.path.exists(k)]
                for k in removed:
                    del entries[k]
            elif folder is not None:
                key = folder_key(folder)
                if key in entries:
                    removed = [key]
                    del entries[key]
                else:
                    removed = []
            else:
                removed = []

            if removed:
                write_text_atomic(store, json.dumps(entries, indent=2) + "\n")
            return removed
    except (OSError, UnicodeDecodeError) as e:
        raise ProjectTrustError(f"Cannot update {store}: {e}") from e


def resolve_project_folder(
    folder: Optional[Union[str, "os.PathLike[str]"]] = None,
) -> str:
    """Resolve *folder* (or cwd) to the enclosing repository, else *folder* itself.

    Mirrors how a session picks the folder it asks about when no
    ``GAIA_PROJECT_ROOT`` or ``project_root`` is set: the nearest code
    repository below the home directory, otherwise the working directory.
    """
    if folder:
        target = Path(folder).expanduser().resolve()
        if not target.exists():
            raise FileNotFoundError(f"Project folder not found: {folder}")
        if not target.is_dir():
            raise ValueError(f"Project path is not a directory: {folder}")
    else:
        target = Path.cwd().resolve()

    from gaia.agents.base.project_map import is_agent_own_source, is_code_repository

    try:
        home = Path.home().resolve()
    except RuntimeError:
        home = None

    for ancestor in [target, *target.parents]:
        if home is not None and ancestor == home:
            break
        if ancestor == ancestor.parent:
            break
        if is_code_repository(ancestor):
            if not is_agent_own_source(ancestor):
                return str(ancestor)
            break

    return str(target)


def remember_folder_trust(
    folder: Union[str, "os.PathLike[str]"], decision: str
) -> None:
    """Save *decision* for *folder*, keeping every other folder's answer.

    ``trusted`` is refused inside an agent tool or a process one started, with
    no exemption: a session running there keeps a yes for itself only.

    Raises:
        ProjectTrustError: the store cannot be locked, read or written, or an
            agent tried to trust a folder.
    """
    if decision not in DECISIONS:
        raise ValueError(
            f"decision must be one of {', '.join(DECISIONS)}: {decision!r}"
        )
    if decision == TRUSTED:
        from gaia.skills.human_only import started_by_agent

        if started_by_agent():
            raise ProjectTrustError(
                f"Refused to trust {folder}: only you can trust a folder, from "
                "your own terminal (gaia project trust add)"
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
    "forget_folder_trust",
    "list_folder_trust",
    "remember_folder_trust",
    "resolve_project_folder",
    "trust_store_path",
]
