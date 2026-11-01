# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Reads a document's images after its text is already searchable.

Indexing a document keeps its text and queues its image pages here. One worker
per GAIA process reads them with the vision model, adds what it finds to the
document's cache, and steps aside while a chat turn is running. Progress lives
in a state file beside the cache, so a restart carries on.

Two locks: the state lock is only ever held for a quick read or write, so asking
about progress or stopping a document never waits on the model. The reader lock
is held for a whole page, so two GAIA processes sharing a cache read one at a
time and never the same page.

A worker thread rather than its own process: frozen builds can't start a Python
worker, and the agent that owns the chat already holds everything this needs.
"""

import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.utils.file_lock import FileLockTimeout, file_lock, write_bytes_atomic
from gaia.utils.turn_activity import turn_running

logger = logging.getLogger(__name__)

STATE_SUFFIX = "_images.json"
SOURCE_INFIX = "_images_source"
_KEY = re.compile(r"^[0-9a-f]{32}_[0-9a-f]{8}$")
_CACHE_NAME = re.compile(r"^[0-9a-f_]+\.json$")
_SUFFIX = re.compile(r"^\.[a-z0-9]{1,8}$")
_STATE_LOCK = "rag-image-state"
_READER_LOCK = "rag-image-reader"
_READER_WAIT_S = 2.0
_IDLE_POLL_S = 1.0
#: One vision read evicts the chat's prompt from the model, which adds about 50 s
#: to the next answer, so reading stays away until the user has stopped asking.
_AFTER_TURN_S = 300.0
#: How long a document waits after the vision model turns out to be missing.
_BLOCKED_RETRY_S = 300.0
#: How long a page waits after a failed read, before another go.
_FAILURE_RETRY_S = 30.0
#: Attempts before a page is given up on. Most failures are the model loading.
_PAGE_ATTEMPTS = 3
_RATE_MEMORY = 0.2
_READ_RETRIES = 5

READING = "reading"
DONE = "done"
BLOCKED = "blocked"

_workers: Dict[str, threading.Thread] = {}
_workers_lock = threading.Lock()
_parsed: Dict[str, Tuple[int, str]] = {}


class ReadPaused(Exception):
    """A question came in part way through a page; the page is read again later."""


class VisionUnavailable(RuntimeError):
    """The vision model can't be reached; every page would fail the same way."""


def _state_path(cache_dir: str, key: str) -> Path:
    if not _KEY.match(key):
        raise ValueError(f"Not an image pass key: {key!r}")
    return Path(cache_dir) / f"{key}{STATE_SUFFIX}"


def _source_path(cache_dir: str, state: Dict[str, Any]) -> Path:
    """The private copy of a document, always beside the cache."""
    suffix = state["suffix"]
    if not _SUFFIX.match(suffix):
        raise ValueError(f"Not a document suffix: {suffix!r}")
    return _state_path(cache_dir, state["key"]).with_name(
        f"{state['key']}{SOURCE_INFIX}{suffix}"
    )


def source_file(cache_dir: str, state: Dict[str, Any]) -> str:
    return str(_source_path(cache_dir, state))


def cache_file(cache_dir: str, state: Dict[str, Any]) -> str:
    """The document's chunk cache, always beside the state file."""
    name = state["cache_name"]
    if not _CACHE_NAME.match(name):
        raise ValueError(f"Not a chunk cache name: {name!r}")
    return str(Path(cache_dir) / name)


def _load(path: Path) -> Optional[Dict[str, Any]]:
    """The state at *path*, or None when there is none.

    Windows refuses a read while the writer renames over the file, so that is
    tried again. A read that keeps failing raises: "no state" would drop pages.
    Parsed text is kept by modified time, so polling thousands of documents
    doesn't read thousands of files.
    """
    for attempt in range(_READ_RETRIES):
        try:
            modified = path.stat().st_mtime_ns
            seen = _parsed.get(str(path))
            if seen is None or seen[0] != modified:
                seen = (modified, path.read_text(encoding="utf-8"))
                _parsed[str(path)] = seen
            return json.loads(seen[1])
        except FileNotFoundError:
            _parsed.pop(str(path), None)
            return None
        except PermissionError:
            if attempt == _READ_RETRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
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
    """Queue *pages* of a freshly extracted document, with a private copy of it.

    The copy matters: the library indexes a temporary upload it deletes as soon
    as the text pass ends. A fresh extraction replaces the document's cache, so
    it also replaces any earlier reading of it.
    """
    path = _state_path(cache_dir, key)
    state = {
        "key": key,
        "name": name,
        "suffix": Path(file_path).suffix.lower(),
        "cache_name": Path(cache_path).name,
        "queued_at": time.time(),
        "pages": sorted(pages, key=lambda p: (not p["short"], p["page"])),
        "done": [],
        "failed": {},
        "attempts": {},
        "revision": 0,
        "state": READING,
        "seconds_per_page": None,
    }
    with file_lock(Path(cache_dir) / _STATE_LOCK):
        shutil.copyfile(file_path, _source_path(cache_dir, state))
        _save(path, state)
    return state


def status(cache_dir: str, key: Optional[str]) -> Optional[Dict[str, Any]]:
    """How far *key*'s images have got, or None if it never had any queued."""
    return _load(_state_path(cache_dir, key)) if key else None


def _pending(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    finished = set(state["done"]) | {int(p) for p in state["failed"]}
    return [p for p in state["pages"] if p["page"] not in finished]


def progress(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Where a document's reading stands, or None once nothing is left to read."""
    if state is None:
        return None
    left = len(_pending(state))
    if left == 0:
        return None
    total = len(state["pages"])
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
    states = _all_states(cache_dir)
    running = [p for p in map(progress, states) if p]
    rates = [
        state["seconds_per_page"]
        for state in states
        if state.get("seconds_per_page") and _pending(state)
    ]
    if rates:
        # A document nothing has been read from yet goes at its neighbours' speed.
        average = sum(rates) / len(rates)
        for p in running:
            if p["seconds_left"] is None:
                left = p["pages_total"] - p["pages_done"]
                p["seconds_left"] = round(average * left)
    return {
        "documents": len(running),
        "pages_done": sum(p["pages_done"] for p in running),
        "pages_total": sum(p["pages_total"] for p in running),
        "seconds_left": sum(p["seconds_left"] for p in running) if rates else None,
        "blocked": next((p["reason"] for p in running if p["reason"]), None),
        "names": sorted(p["name"] for p in running),
        "details": running,
        # Documents reading gave up on pages of, by key: they stay incomplete.
        "gave_up": {
            state["key"]: len(state["failed"])
            for state in states
            if state["failed"] and not _pending(state)
        },
    }


def rename(cache_dir: str, key: str, name: str) -> None:
    """Show *name*, the user's name for a file indexed from a temporary copy."""
    path = _state_path(cache_dir, key)
    with file_lock(Path(cache_dir) / _STATE_LOCK):
        state = _load(path)
        if state is not None and state["name"] != name:
            state["name"] = name
            _save(path, state)


def cancel(cache_dir: str, key: str) -> bool:
    """Stop reading *key*'s images; its text and anything read so far stay.

    The state stays too, emptied, so opening the document again doesn't queue
    what the user just stopped.
    """
    path = _state_path(cache_dir, key)
    with file_lock(Path(cache_dir) / _STATE_LOCK):
        state = _load(path)
        if state is None or not _pending(state):
            return False
        _source_path(cache_dir, state).unlink(missing_ok=True)
        state.update(pages=[], done=[], failed={}, state=DONE, reason=None)
        _save(path, state)
    return True


def _next_page(cache_dir: str) -> Optional[Tuple[str, Dict[str, Any]]]:
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


def _block(state: Dict[str, Any], reason: str, retry_in: float) -> None:
    state.update(state=BLOCKED, reason=reason, retry_at=time.time() + retry_in)
    logger.warning("Images of %s can't be read yet: %s", state["name"], reason)


def _commit(cache_dir: str, key: str, update: Callable[[Dict[str, Any]], None]) -> None:
    """Apply *update*; a document stopped or cleared meanwhile stays that way."""
    path = _state_path(cache_dir, key)
    with file_lock(Path(cache_dir) / _STATE_LOCK):
        state = _load(path)
        if state is None or not state["pages"]:
            return
        update(state)
        if not _pending(state):
            state.update(state=DONE, reason=None)
            _source_path(cache_dir, state).unlink(missing_ok=True)
        _save(path, state)


def give_way() -> None:
    """Between two images: stop reading this page if a question has come in."""
    if turn_running():
        raise ReadPaused()


def _read_one(
    cache_dir: str, key: str, page: Dict[str, Any], read_page: Callable
) -> None:
    """Read one page. A failure is tried again; one that keeps failing is recorded."""
    state = status(cache_dir, key)
    if state is None:
        return
    number = page["page"]
    started = time.monotonic()

    try:
        added = read_page(state, number)
    except ReadPaused:
        return
    except VisionUnavailable as e:
        reason = str(e)
        _commit(cache_dir, key, lambda s: _block(s, reason, _BLOCKED_RETRY_S))
        return
    except Exception as e:  # pylint: disable=broad-except
        reason = str(e)
        logger.warning(
            "Image reading failed on %s page %s: %s", state["name"], number, e
        )

        def failed(s: Dict[str, Any]) -> None:
            tries = s["attempts"].get(str(number), 0) + 1
            s["attempts"][str(number)] = tries
            if tries >= _PAGE_ATTEMPTS:
                s["failed"][str(number)] = reason
            else:
                _block(s, reason, _FAILURE_RETRY_S)

        _commit(cache_dir, key, failed)
        return

    elapsed = time.monotonic() - started

    def read(s: Dict[str, Any]) -> None:
        s["done"].append(number)
        rate = s.get("seconds_per_page")
        s["seconds_per_page"] = (
            elapsed if rate is None else rate + (elapsed - rate) * _RATE_MEMORY
        )
        s["revision"] += 1 if added else 0
        s.update(state=READING, reason=None)

    _commit(cache_dir, key, read)


def _work(cache_dir: str, read_page: Callable) -> None:
    while True:
        try:
            while turn_running(_AFTER_TURN_S):
                time.sleep(_IDLE_POLL_S)
            # Decided under the state lock, so a document queued as this worker
            # stops is either seen here or starts a new worker.
            with file_lock(Path(cache_dir) / _STATE_LOCK):
                if not any(_pending(s) for s in _all_states(cache_dir)):
                    with _workers_lock:
                        _workers.pop(cache_dir, None)
                    return
            job = None
            try:
                with file_lock(Path(cache_dir) / _READER_LOCK, timeout=_READER_WAIT_S):
                    job = _next_page(cache_dir)
                    if job is not None:
                        _read_one(cache_dir, job[0], job[1], read_page)
            except FileLockTimeout:
                continue
            if job is None:
                # Everything left is waiting for its retry time.
                time.sleep(_IDLE_POLL_S)
        except Exception:  # pylint: disable=broad-except
            # Stopping here would leave every document saying "reading" forever.
            logger.exception("Image reading hit an error and will try again")
            time.sleep(_FAILURE_RETRY_S)


def ensure_running(cache_dir: str, read_page: Callable) -> None:
    """Start this process's worker if none is running."""
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
