# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Everything one chat produced, for the session panel's Outputs (#5277).

Built from what is already recorded per turn — each answer's turn summary
(files, images, indexed documents, memories, schedules) and the undo
snapshots (created, edited and deleted files, and whether the turn was
undone) — plus the documents added to the chat. Nothing is polled: the panel
asks when it opens and when a turn ends.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.base import file_snapshots

from .database import ChatDatabase
from .turn_summary import is_openable

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})
#: Documents and data meant to leave GAIA: reports, tables, slides.
_EXPORT_SUFFIXES = frozenset(
    {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv", ".odt", ".ods", ".odp", ".rtf"}
)


def _file_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _EXPORT_SUFFIXES:
        return "export"
    return "file"


def _ts(value: Optional[str]) -> Optional[str]:
    """One UTC format for every source: ``YYYY-MM-DD HH:MM:SS``."""
    return value.replace("T", " ")[:19] if value else None


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _item(
    kind: str,
    name: str,
    *,
    message_id: Optional[int],
    created_at: Optional[str],
    path: Optional[str] = None,
    detail: Optional[str] = None,
    change: Optional[str] = None,
    undone: bool = False,
    ref: Optional[str] = None,
) -> Dict[str, Any]:
    exists = os.path.exists(path) if path else None
    if undone:
        state = "undone"
    elif path and change != "deleted" and not exists:
        state = "gone"
    else:
        state = "present"
    on_disk = bool(path) and kind in ("file", "export", "image", "document")
    return {
        "id": f"{kind}:{message_id}:{_key(path) if path else ref or name}",
        "kind": kind,
        "name": name,
        "path": path,
        "detail": detail,
        "message_id": message_id,
        "created_at": _ts(created_at),
        "change": change,
        "state": state,
        # Opening runs nothing: scripts and web pages only show in their folder.
        "openable": on_disk and state == "present" and is_openable(path),
        "revealable": on_disk and state == "present" and bool(exists),
    }


def list_outputs(db: ChatDatabase, session_id: str) -> Dict[str, Any]:
    """Everything the chat produced, newest first."""
    times = db.get_message_times(session_id)
    turns = file_snapshots.list_turns(session_id)
    # Per answer: what each file went through, and whether the turn was undone.
    changes: Dict[tuple, Dict[str, Any]] = {}
    for turn in turns:
        for f in turn["files"]:
            changes[(turn["message_id"], _key(f["path"]))] = {
                "path": f["path"],
                # A running turn reports "pending" until it ends.
                "change": f["change"] if f["change"] != "pending" else None,
                "undone": bool(turn["restored_at"]),
                "at": turn["finished_at"] or turn["started_at"],
            }

    items: List[Dict[str, Any]] = []
    newest_file: Dict[str, Dict[str, Any]] = {}

    def add_file(path: str, message_id: Optional[int], at: Optional[str]) -> None:
        recorded = changes.pop((message_id, _key(path)), None)
        item = _item(
            _file_kind(path),
            Path(path).name,
            path=path,
            message_id=message_id,
            created_at=at,
            change=recorded["change"] if recorded else None,
            undone=bool(recorded and recorded["undone"]),
        )
        # One row per file, at the latest turn that touched it.
        current = newest_file.get(_key(path))
        order = (item["created_at"] or "", item["message_id"] or 0)
        if current is None or order >= (
            current["created_at"] or "",
            current["message_id"] or 0,
        ):
            newest_file[_key(path)] = item

    seen_docs = set()
    for answer in db.get_turn_summaries(session_id):
        mid, at, summary = answer["id"], answer["created_at"], answer["summary"]
        for f in summary.get("files") or []:
            add_file(f["path"], mid, at)
        for out in summary.get("outputs") or []:
            if out["kind"] == "document" and out.get("path"):
                seen_docs.add(_key(out["path"]))
            if out["kind"] == "image" and out.get("path"):
                changes.pop((mid, _key(out["path"])), None)
            items.append(
                _item(
                    out["kind"],
                    out["name"],
                    path=out.get("path"),
                    detail=out.get("detail"),
                    message_id=mid,
                    created_at=at,
                    ref=out.get("ref"),
                )
            )
    # Files a turn changed that its summary doesn't list: deletions, and work
    # whose summary was not recorded.
    for (mid, _), recorded in list(changes.items()):
        add_file(recorded["path"], mid, times.get(mid) if mid else recorded["at"])
    items.extend(newest_file.values())

    for doc in db.get_session_documents(session_id):
        path = doc.get("filepath")
        if not path or _key(path) in seen_docs:
            continue
        items.append(
            _item(
                "document",
                doc.get("filename") or Path(path).name,
                path=path,
                message_id=None,
                created_at=doc.get("indexed_at"),
                detail="Added to this chat",
            )
        )

    items.sort(
        key=lambda i: (i["created_at"] or "", i["message_id"] or 0), reverse=True
    )
    return {"session_id": session_id, "items": items}
