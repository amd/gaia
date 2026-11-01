# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Reads a document's images after its text is already searchable.

Indexing a document keeps its text and queues its image pages here. One worker
per GAIA process reads them with the vision model, adds what it finds to the
document's cache, and steps aside while a chat turn is running. Progress lives
in a state file beside the cache, so a restart carries on, and a file lock keeps
two GAIA processes sharing a cache from reading the same page.

A worker thread rather than its own process: frozen builds can't start a Python
worker, and the agent that owns the chat already holds everything this needs.
"""

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.utils.file_lock import FileLockTimeout, file_lock, write_bytes_atomic
from gaia.utils.turn_activity import turn_running

logger = logging.getLogger(__name__)

STATE_SUFFIX = "_images.json"
SOURCE_INFIX = "_images_source"
#: One page at a time across every process sharing this cache.
_LOCK_NAME = "rag-image-pass"
_LOCK_WAIT_S = 2.0
_IDLE_POLL_S = 1.0
#: How long a missing vision model is left before it is tried again.
_BLOCKED_RETRY_S = 300.0
_RATE_MEMORY = 0.2

READING = "reading"
DONE = "done"
BLOCKED = "blocked"

_workers: Dict[str, threading.Thread] = {}
_workers_lock = threading.Lock()


class VisionUnavailable(RuntimeError):
    """The vision model can't be reached; every page would fail the same way."""


def _state_path(cache_dir: str, key: str) -> Path:
    return Path(cache_dir) / f"{key}{STATE_SUFFIX}"


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning("Ignoring unreadable image pass state %s: %s", path, e)
        return None


def _save(path: Path, state: Dict[str, Any]) -> None:
    write_bytes_atomic(str(path), json.dumps(state, ensure_ascii=False).encode("utf-8"))


def register(
    cache_dir: str,
    key: str,
    file_path: str,
    name: str,
    cache_path: str,
    pages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Queue *pages* of a document, keeping its own copy of the file.

    The copy matters: the library indexes a temporary upload it deletes as soon
    as the text pass ends. Registering a document twice keeps its progress.
    """
    path = _state_path(cache_dir, key)
    existing = _load(path)
    if existing is not None:
        return existing
    source = Path(cache_dir) / f"{key}{SOURCE_INFIX}{Path(file_path).suffix.lower()}"
    shutil.copyfile(file_path, source)
    state = {
        "key": key,
        "name": name,
        "file_path": file_path,
        "source": str(source),
        "cache_path": cache_path,
        "queued_at": time.time(),
        "pages": sorted(pages, key=lambda p: (not p["short"], p["page"])),
        "done": [],
        "failed": {},
        "revision": 0,
        "state": READING,
        "seconds_per_page": None,
    }
    _save(path, state)
    return state


def status(cache_dir: str, key: Optional[str]) -> Optional[Dict[str, Any]]:
    """How far *key*'s images have got, or None if it never had any queued."""
    return _load(_state_path(cache_dir, key)) if key else None


def _pending(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    finished = set(state["done"]) | {int(p) for p in state["failed"]}
    return [p for p in state["pages"] if p["page"] not in finished]


def progress(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What a person needs to know, or None once there's nothing left to say."""
    if state is None:
        return None
    total = len(state["pages"])
    left = len(_pending(state))
    if left == 0:
        return None
    rate = state.get("seconds_per_page")
    return {
        "key": state["key"],
        "name": state["name"],
        "pages_done": total - left,
        "pages_total": total,
        "state": state["state"],
        "reason": state.get("reason"),
        "seconds_left": round(rate * left) if rate else None,
    }


def _all_states(cache_dir: str) -> List[Dict[str, Any]]:
    folder = Path(cache_dir)
    if not folder.is_dir():
        return []
    states = (_load(path) for path in folder.glob(f"*{STATE_SUFFIX}"))
    return [state for state in states if state is not None]


def overview(cache_dir: str) -> Dict[str, Any]:
    """Everything still being read, for a status line."""
    running = [p for p in map(progress, _all_states(cache_dir)) if p]
    known = [p["seconds_left"] for p in running if p["seconds_left"] is not None]
    return {
        "documents": len(running),
        "pages_done": sum(p["pages_done"] for p in running),
        "pages_total": sum(p["pages_total"] for p in running),
        "seconds_left": sum(known) if len(known) == len(running) and known else None,
        "blocked": next((p["reason"] for p in running if p["reason"]), None),
        "names": sorted(p["name"] for p in running),
        "details": running,
    }


def rename(cache_dir: str, key: str, name: str) -> None:
    """Show *name*, the user's name for a file indexed from a temporary copy."""
    path = _state_path(cache_dir, key)
    with file_lock(Path(cache_dir) / _LOCK_NAME):
        state = _load(path)
        if state is not None and state["name"] != name:
            state["name"] = name
            _save(path, state)


def cancel(cache_dir: str, key: str) -> bool:
    """Stop reading *key*'s images and drop its copy; its text stays searchable."""
    path = _state_path(cache_dir, key)
    with file_lock(Path(cache_dir) / _LOCK_NAME):
        state = _load(path)
        if state is None:
            return False
        Path(state["source"]).unlink(missing_ok=True)
        path.unlink(missing_ok=True)
    return True


def _next_page(cache_dir: str) -> Optional[tuple]:
    """The most useful page left anywhere: pages short of text, oldest first."""
    now = time.time()
    candidates = []
    for state in _all_states(cache_dir):
        if state["state"] == BLOCKED and now < state.get("retry_at", 0):
            continue
        pending = _pending(state)
        if pending:
            page = pending[0]
            rank = (not page["short"], state["queued_at"], page["page"])
            candidates.append((rank, state["key"], page))
    if not candidates:
        return None
    _, key, page = min(candidates, key=lambda candidate: candidate[0])
    return key, page


def _finish(cache_dir: str, state: Dict[str, Any]) -> None:
    state["state"] = DONE
    Path(state["source"]).unlink(missing_ok=True)
    _save(_state_path(cache_dir, state["key"]), state)


def _block(cache_dir: str, key: str, reason: str) -> None:
    path = _state_path(cache_dir, key)
    state = _load(path)
    if state is None:
        return
    state.update(state=BLOCKED, reason=reason, retry_at=time.time() + _BLOCKED_RETRY_S)
    _save(path, state)
    logger.warning("Images of %s can't be read yet: %s", state["name"], reason)


def _read_one(
    cache_dir: str, key: str, page: Dict[str, Any], read_page: Callable
) -> None:
    """Read one page; a page that fails is recorded and the rest carry on."""
    path = _state_path(cache_dir, key)
    state = _load(path)
    if state is None:
        return
    started = time.monotonic()
    number = page["page"]
    added = 0
    try:
        added = read_page(state, number)
    except VisionUnavailable as e:
        _block(cache_dir, key, str(e))
        return
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(
            "Image reading failed on %s page %s: %s", state["name"], number, e
        )
        state = _load(path) or state
        state["failed"][str(number)] = str(e)
    else:
        state = _load(path) or state
        state["done"].append(number)
    elapsed = time.monotonic() - started
    rate = state.get("seconds_per_page")
    state["seconds_per_page"] = (
        elapsed if rate is None else rate + (elapsed - rate) * _RATE_MEMORY
    )
    state["revision"] += 1 if added else 0
    state.update(state=READING, reason=None)
    if _pending(state):
        _save(path, state)
    else:
        _finish(cache_dir, state)


def _work(cache_dir: str, read_page: Callable) -> None:
    lock = Path(cache_dir) / _LOCK_NAME
    while True:
        while turn_running():
            time.sleep(_IDLE_POLL_S)
        # Taken page by page, so one process can't hold the others out for hours.
        try:
            with file_lock(lock, timeout=_LOCK_WAIT_S):
                job = _next_page(cache_dir)
                if job is None:
                    with _workers_lock:
                        _workers.pop(cache_dir, None)
                    return
                _read_one(cache_dir, job[0], job[1], read_page)
        except FileLockTimeout:
            continue


def ensure_running(cache_dir: str, read_page: Callable) -> None:
    """Start this process's worker if there's work and none is running."""
    with _workers_lock:
        worker = _workers.get(cache_dir)
        if worker is not None and worker.is_alive():
            return
        worker = threading.Thread(
            target=_work,
            args=(cache_dir, read_page),
            name="rag-image-pass",
            daemon=True,
        )
        _workers[cache_dir] = worker
        worker.start()
