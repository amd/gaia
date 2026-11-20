# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Move the library's files out of the documents folder every chat shares.

The Agent UI used to keep its copy of each dragged-in document in GAIA's
documents folder, which every chat is granted, so any chat could read the
documents attached to any other. Copies now live in
:func:`~gaia.ui.utils.document_copies_dir`, where a chat opens only the ones
attached to it. This moves the library's files from the shared folder there,
once.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict, List

from gaia.logger import get_logger
from gaia.upload_names import reserve_copy_path

from .database import ChatDatabase
from .utils import document_copies_dir, managed_documents_dir

logger = get_logger(__name__)

#: Set once every library file has left the shared folder.
MOVED_SETTING = "document_copies_moved"


def _in_shared_folder(stored: str, shared: Path) -> bool:
    path = Path(stored)
    if path.is_symlink() or not path.is_file():
        return False
    return Path(os.path.realpath(path)).parent == shared


def move_library_files_out_of_shared_folder(db: ChatDatabase) -> List[str]:
    """Move each library file sitting directly in GAIA's documents folder.

    That includes a file the agent saved there that the user then added to
    the library: it becomes private to the chats it is attached to, like any
    library document. Runs until a pass moves everything, then never again,
    so a file added to the library from there later stays where it is.

    Returns:
        A line per file that could not be moved; empty when all were. Each is
        logged as an error and retried at the next start.
    """
    if db.get_setting(MOVED_SETTING) == "1":
        return []
    shared = Path(os.path.realpath(managed_documents_dir()))
    copies = document_copies_dir()
    failures: List[str] = []
    moved: Dict[str, Path] = {}
    for doc in db.list_documents():
        stored = doc.get("filepath") or ""
        if not stored:
            continue
        key = os.path.normcase(os.path.abspath(stored))
        if key in moved:
            # Another row named the same file; it follows the file.
            db.update_document_path(doc["id"], str(moved[key]))
            continue
        if not _in_shared_folder(stored, shared):
            continue
        source = Path(stored)
        try:
            copies.mkdir(parents=True, exist_ok=True)
            target = reserve_copy_path(
                copies,
                source.stem,
                source.suffix,
                taken=lambda p, h=doc["file_hash"]: db.path_held_by_other_content(
                    str(p), h
                ),
            )
        except OSError as e:
            failures.append(f"{source}: {e}")
            continue
        try:
            os.replace(source, target)
        except OSError as e:
            target.unlink(missing_ok=True)
            failures.append(f"{source}: {e}")
            continue
        try:
            db.update_document_path(doc["id"], str(target))
        except sqlite3.Error as e:
            # The row must name where the file is; put the file back.
            try:
                os.replace(target, source)
            except OSError as back:
                failures.append(
                    f"{source}: the library could not be updated ({e}), and "
                    f"the file could not be put back ({back}); it is now at "
                    f"{target}. Move it back to {source} by hand"
                )
                continue
            failures.append(f"{source}: the library could not be updated ({e})")
            continue
        moved[key] = target
        logger.info("Moved library file %s to %s", source, target)
    if failures:
        logger.error(
            "%d library file(s) are still in GAIA's documents folder %s, where "
            "every chat can read them: %s. Close any program that has them "
            "open and restart GAIA to move them.",
            len(failures),
            shared,
            "; ".join(failures),
        )
        return failures
    db.set_setting(MOVED_SETTING, "1")
    return []
