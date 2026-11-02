# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What this process is indexing right now, and how long it will take.

Indexing threads report pages read and chunks embedded. From those reports the
tracker measures this machine's speed per phase and estimates the time left for
everything still running or queued, including phases that haven't started.

An estimate is only given once it is measured: with too few samples the state is
``estimating``, with some phases unmeasured it is ``at_least`` (a lower bound),
and when nothing has moved for too long it is ``stalled`` with no number at all.
"""

import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

QUEUED = "queued"
READING = "reading"
EMBEDDING = "embedding"

ESTIMATING = "estimating"
READY = "ready"
AT_LEAST = "at_least"
WAITING = "waiting"
STALLED = "stalled"

_LIVE = ("queued", "running")

#: Windows of measured work before a rate is believed.
MIN_SAMPLES = 3
#: Shortest measured window; single pages are too noisy to time alone.
SAMPLE_WINDOW_S = 2.0
#: Pages and chunks a rate averages over, so one image page moves it only a little.
PAGE_MEMORY = 50.0
CHUNK_MEMORY = 500.0
#: How fast the shown estimate follows the computed one.
SHOWN_MEMORY_S = 120.0
#: Silence this long, or this many expected steps, counts as a stall. An image
#: page can take a minute on its own, so the floor is well above that.
STALL_MIN_S = 120.0
STALL_STEPS = 8


class _Rate:
    """Seconds per unit over the recent units of work, older ones fading out."""

    def __init__(self, memory: float):
        self._memory = memory
        self._seconds = 0.0
        self._units = 0.0
        self.samples = 0

    def add(self, seconds: float, units: float) -> None:
        if units <= 0:
            return
        fade = math.exp(-units / self._memory)
        self._seconds = self._seconds * fade + seconds
        self._units = self._units * fade + units
        self.samples += 1

    @property
    def value(self) -> Optional[float]:
        return self._seconds / self._units if self._units else None

    @property
    def ready(self) -> bool:
        return self.samples >= MIN_SAMPLES


@dataclass
class _Shown:
    """The number last shown, so the next one counts down from it."""

    seconds: Optional[float] = None
    at: float = 0.0

    def follow(self, computed: float, now: float) -> float:
        if self.seconds is None:
            self.seconds = computed
        else:
            elapsed = now - self.at
            counted_down = max(0.0, self.seconds - elapsed)
            weight = 1 - math.exp(-elapsed / SHOWN_MEMORY_S)
            self.seconds = counted_down + weight * (computed - counted_down)
        self.at = now
        return self.seconds

    def forget(self) -> None:
        self.seconds = None


@dataclass
class _Doc:
    key: str
    name: str
    path: Optional[str]
    size: int
    added_at: float
    state: str = QUEUED
    phase: str = QUEUED
    done: int = 0
    total: Optional[int] = None
    pages: Optional[int] = None
    chunks: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    moved_at: float = 0.0
    waiting: bool = False
    # Where the sampling window opened.
    mark_phase: str = QUEUED
    mark_done: int = 0
    mark_at: float = 0.0
    shown: _Shown = field(default_factory=_Shown)

    @property
    def live(self) -> bool:
        return self.state in _LIVE


def _same_file(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


class IndexTracker:
    """Thread-safe record of one batch of documents being indexed.

    A batch is everything added until nothing is left running. It stays readable
    after it ends, so failures remain visible, until dismissed or a new one starts.
    Measured rates outlive batches: they describe the machine, not the documents.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._docs: Dict[str, _Doc] = {}
        self._batch_id: Optional[str] = None
        self._shown = _Shown()
        self._percent = 0
        self._last_move = 0.0
        self._measure_nothing()

    # ── Reports from the indexing path ──────────────────────────────────

    def enqueue(
        self, key: str, name: str, path: Optional[str] = None, size: int = 0
    ) -> None:
        """Add a document to the batch. Adding a running one changes nothing."""
        with self._lock:
            now = self._clock()
            doc = self._docs.get(key)
            if doc is not None and doc.live:
                return
            if self._batch_id is None or not any(d.live for d in self._docs.values()):
                self._docs = {}
                self._batch_id = uuid.uuid4().hex
                self._percent = 0
                self._last_move = now
            self._docs[key] = _Doc(
                key=key,
                name=name,
                path=_same_file(path) if path else None,
                size=size,
                added_at=now,
                moved_at=now,
            )
            self._work_changed()

    def set_pages(self, key: str, pages: Optional[int]) -> None:
        with self._lock:
            doc = self._docs.get(key)
            if doc is not None and doc.live:
                doc.pages = pages or None
                self._work_changed()

    def report(self, key: str, phase: str, done: int, total: Optional[int]) -> None:
        """Note how far *key* has got. Late reports from a stopped index are ignored."""
        with self._lock:
            doc = self._docs.get(key)
            if doc is None or not doc.live:
                return
            now = self._clock()
            if doc.state == QUEUED:
                doc.state = "running"
                doc.started_at = now
                # A slot filled changes how fast the rest will go.
                self._shown.forget()
            moved = phase != doc.phase or done != doc.done
            if phase != doc.mark_phase:
                self._enter_phase(doc, phase, done, total, now)
            elif done > doc.mark_done and now - doc.mark_at >= SAMPLE_WINDOW_S:
                self._rates[phase].add(now - doc.mark_at, done - doc.mark_done)
                doc.mark_done, doc.mark_at = done, now
            doc.phase, doc.done, doc.total = phase, done, total
            if moved or doc.waiting:
                doc.moved_at = self._last_move = now
            doc.waiting = False

    def _enter_phase(
        self, doc: _Doc, phase: str, done: int, total: Optional[int], now: float
    ) -> None:
        if phase == EMBEDDING and total:
            doc.chunks = total
            if doc.pages:
                seen = total / doc.pages
                self._chunks_per_page = (
                    seen
                    if self._chunks_per_page is None
                    else (self._chunks_per_page + seen) / 2
                )
        doc.mark_phase, doc.mark_done, doc.mark_at = phase, done, now

    def waiting(self, key: str) -> None:
        """*key* is in line for a model: not a stall, and not a speed to measure."""
        with self._lock:
            doc = self._docs.get(key)
            if doc is None or not doc.live:
                return
            now = self._clock()
            doc.waiting = True
            doc.moved_at = self._last_move = doc.mark_at = now

    def finish(self, key: str, outcome: str, error: Optional[str] = None) -> None:
        """End *key* as ``complete``, ``failed`` or ``cancelled``. The first call wins."""
        with self._lock:
            doc = self._docs.get(key)
            if doc is None or not doc.live:
                return
            now = self._clock()
            began = doc.started_at if doc.started_at is not None else doc.added_at
            if (
                outcome == "complete"
                and not doc.pages
                and doc.size
                and now - began >= SAMPLE_WINDOW_S
            ):
                self._bytes.add(now - began, doc.size)
            doc.state = outcome
            doc.error = error
            self._last_move = now
            self._work_changed()

    def dismiss(self, batch_id: str) -> None:
        """Forget a finished batch. One still running can't be dismissed."""
        with self._lock:
            if batch_id == self._batch_id and not any(
                d.live for d in self._docs.values()
            ):
                self._docs = {}
                self._batch_id = None

    def reset(self) -> None:
        """Forget everything, measured rates included."""
        with self._lock:
            self._docs = {}
            self._batch_id = None
            self._shown = _Shown()
            self._measure_nothing()

    def _measure_nothing(self) -> None:
        self._rates = {READING: _Rate(PAGE_MEMORY), EMBEDDING: _Rate(CHUNK_MEMORY)}
        self._chunks_per_page: Optional[float] = None
        # For files with no page count: whole-file seconds per byte. Sizes vary
        # too much for a fixed memory, so a run of a dozen files is as good as any.
        self._bytes = _Rate(math.inf)

    def _work_changed(self) -> None:
        # More or less work is real news: show it now rather than ease into it.
        self._shown.forget()
        self._percent = 0
        for doc in self._docs.values():
            doc.shown.forget()

    # ── Reads ───────────────────────────────────────────────────────────

    def key_for(self, path: str) -> Optional[str]:
        """The key of a running index of the file at *path*, or None."""
        wanted = _same_file(path)
        with self._lock:
            for doc in self._docs.values():
                if doc.live and doc.path == wanted:
                    return doc.key
        return None

    def progress(self, key: str) -> Optional[dict]:
        """How far a running or queued index has got, or None."""
        with self._lock:
            doc = self._docs.get(key)
            if doc is None or not doc.live:
                return None
            return self._progress(doc, self._clock())

    def overview(self) -> dict:
        """Everything in the current batch, for one combined view."""
        with self._lock:
            now = self._clock()
            docs = list(self._docs.values())
            live = [d for d in docs if d.live]
            running = [d for d in live if d.state == "running"]
            seconds, estimate = self._overall_estimate(live, running, now)
            return {
                "batch_id": self._batch_id,
                "active": bool(live),
                "total": len(docs),
                "done": sum(d.state == "complete" for d in docs),
                "failed": sum(d.state == "failed" for d in docs),
                "cancelled": sum(d.state == "cancelled" for d in docs),
                "current_id": running[0].key if running else None,
                "current": running[0].name if running else None,
                "percent": self._overall_percent(docs),
                "seconds_left": seconds,
                "estimate": estimate,
                "documents": [
                    {
                        "id": d.key,
                        "filename": d.name,
                        "state": d.state,
                        "progress": self._progress(d, now) if d.live else None,
                        "error": d.error,
                    }
                    for d in docs
                ],
            }

    # ── Estimates ───────────────────────────────────────────────────────

    def _left(self, doc: _Doc) -> Tuple[float, bool]:
        """Seconds of work left in *doc*, and whether all of it could be measured."""
        reading, embedding = self._rates[READING], self._rates[EMBEDDING]
        if doc.phase == EMBEDDING:
            if not doc.total or not embedding.ready:
                return 0.0, False
            return max(0, doc.total - doc.done) * embedding.value, True
        if not doc.pages:
            if not (self._bytes.ready and doc.size):
                return 0.0, False
            ran = self._clock() - doc.started_at if doc.started_at else 0.0
            return max(0.0, doc.size * self._bytes.value - ran), True
        seconds, measured = 0.0, True
        if reading.ready:
            seconds += max(0, doc.pages - doc.done) * reading.value
        else:
            measured = False
        chunks = doc.chunks
        if chunks is None and self._chunks_per_page is not None:
            chunks = doc.pages * self._chunks_per_page
        if chunks is not None and embedding.ready:
            seconds += chunks * embedding.value
        else:
            measured = False
        return seconds, measured

    def _stall_after(self) -> float:
        steps = [r.value for r in self._rates.values() if r.ready]
        return max(STALL_MIN_S, STALL_STEPS * max(steps, default=0.0))

    def _progress(self, doc: _Doc, now: float) -> dict:
        seconds, estimate = None, ESTIMATING
        if doc.state == "running":
            if doc.waiting:
                estimate = WAITING
            elif now - doc.moved_at > self._stall_after():
                estimate = STALLED
            else:
                left, measured = self._left(doc)
                if left > 0:
                    seconds = round(doc.shown.follow(left, now))
                    estimate = READY if measured else AT_LEAST
            if seconds is None:
                doc.shown.forget()
        began = doc.started_at if doc.started_at is not None else now
        return {
            "phase": doc.phase,
            "done": doc.done,
            "total": doc.total,
            "seconds_left": seconds,
            "estimate": estimate,
            "elapsed_seconds": int(now - began),
        }

    def _overall_estimate(
        self, live: List[_Doc], running: List[_Doc], now: float
    ) -> Tuple[Optional[int], str]:
        if not live:
            return None, ESTIMATING
        if running and all(d.waiting for d in running):
            self._shown.forget()
            return None, WAITING
        if now - self._last_move > self._stall_after():
            self._shown.forget()
            return None, STALLED
        lefts = [self._left(d) for d in live]
        work = sum(seconds for seconds, _ in lefts)
        if work <= 0:
            self._shown.forget()
            return None, ESTIMATING
        # Each running document takes a slot, and none finishes faster than its own work.
        computed = max(
            work / max(1, len(running)), max(seconds for seconds, _ in lefts)
        )
        shown = round(self._shown.follow(computed, now))
        return shown, READY if all(measured for _, measured in lefts) else AT_LEAST

    def _reading_share(self) -> float:
        """Of a typical document's time, the part that is reading."""
        reading, embedding = self._rates[READING], self._rates[EMBEDDING]
        if not (reading.ready and embedding.ready and self._chunks_per_page):
            return 0.5
        per_page = embedding.value * self._chunks_per_page
        return reading.value / (reading.value + per_page)

    def _finished_share(self, doc: _Doc, share: float) -> float:
        if doc.state == "complete":
            return 1.0
        if doc.phase == READING and doc.total:
            return share * min(1.0, doc.done / doc.total)
        if doc.phase == EMBEDDING:
            part = min(1.0, doc.done / doc.total) if doc.total else 0.0
            return share + (1 - share) * part
        return 0.0

    def _overall_percent(self, docs: List[_Doc]) -> Optional[int]:
        counted = [d for d in docs if d.live or d.state == "complete"]
        if not counted:
            return None
        known = [d.pages for d in counted if d.pages]
        usual = sum(known) / len(known) if known else 1.0
        weights = [d.pages or usual for d in counted]
        share = self._reading_share()
        finished = sum(
            w * self._finished_share(d, share) for d, w in zip(counted, weights)
        )
        percent = math.floor(100 * finished / sum(weights))
        if any(d.live for d in counted):
            percent = min(99, percent)
        # A bar that slides back reads as lost work.
        self._percent = max(self._percent, percent)
        return self._percent


index_tracker = IndexTracker()
