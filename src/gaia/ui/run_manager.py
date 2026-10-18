# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Per-session background chat-run registry for the GAIA Agent UI.

A chat turn used to live and die with the SSE HTTP connection: when the
user clicked **New Task** (or switched sessions) mid-stream, the browser
aborted the SSE, the server cancelled the streaming generator, and the
in-flight answer was discarded with no record it had ever been running
(issue #1580 follow-up).

``RunManager`` makes each turn a first-class ``Run`` that owns its own
lifecycle independent of any HTTP connection:

* The agent producer + response persistence run inside a detached
  ``asyncio`` task, so a client disconnect no longer cancels or loses the
  run — it finishes server-side and persists to the DB.
* Every SSE event the run emits is appended to a replay ``buffer`` and
  fanned out to any number of live subscriber queues. A browser can
  *attach* to an in-flight run (on revisit) and receive the full history
  followed by live events.
* ``active_sessions()`` is the source of truth the sidebar polls to show
  which sessions are still running.

Threading note: ``Run.emit`` / ``subscribe`` / ``finish`` only mutate the
buffer and subscriber list, and are only ever called from coroutines on
the server event loop (the lifecycle task and the subscriber generators).
They never ``await`` between reading and writing that state, so no extra
locking is needed — the single-threaded event loop guarantees atomicity.
The agent itself runs in a separate producer thread that communicates via
the handler's thread-safe ``queue.Queue``; that boundary is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Sentinel pushed to a subscriber queue when the run is complete so the
# subscriber generator knows to stop awaiting and close cleanly.
DONE = object()

# Most a run keeps for re-attaching clients. Chunks merge and keepalives are
# dropped, so a normal reply stays far below this; past it the oldest
# progress is trimmed and the client is told.
MAX_REPLAY_BYTES = 8 * 1024 * 1024

# Chromium holds SSE frames under ~512 bytes until its buffer fills.
_FLUSH_BYTES = 512

_TRIMMED_NOTE = (
    'data: {"type": "status", "status": "info", "message": "Earlier progress '
    "of this reply is not shown here; the full answer is saved when it "
    'finishes."}\n\n'
)


def _padded(frame: str) -> str:
    if len(frame) >= _FLUSH_BYTES:
        return frame
    return frame + ": " + "x" * (_FLUSH_BYTES - len(frame) - 4) + "\n\n"


def _parse(frame: str) -> Optional[dict]:
    try:
        event = json.loads(frame[len("data: ") :])
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


class Run:
    """A single in-flight (or just-finished) chat turn for one session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        # The run's SSEOutputHandler, set by the lifecycle once constructed.
        # Held so an external cancel can signal the producer to bail.
        self.handler = None
        # What a re-attaching subscriber replays: every ``data:`` frame so
        # far, unpadded, with consecutive chunks merged (see ``_record``).
        self.buffer: List[str] = []
        self._buffer_bytes = 0
        self._last_event: Optional[dict] = None
        self._trimmed = False
        # Live subscriber queues. emit() fans out to each; finish() closes them.
        self.subscribers: List[asyncio.Queue] = []
        self.done = asyncio.Event()
        self.task: Optional[asyncio.Task] = None

    # ── Producer side (called from the lifecycle task) ────────────────
    def emit(self, data: str) -> None:
        """Record an SSE event and fan it out to all live subscribers."""
        self._record(data)
        for q in self.subscribers:
            q.put_nowait(data)

    def _record(self, data: str) -> None:
        """Keep what a re-attaching client needs to rebuild the reply, and no more.

        Keepalives and padding are dropped, consecutive plain chunks become
        one chunk, and a ``working`` status replaces the one just before it,
        as the client shows them. Past ``MAX_REPLAY_BYTES`` the oldest frames
        go, behind a note that says so.
        """
        line = data.split("\n", 1)[0]
        if not line.startswith("data: "):
            return
        event = _parse(line)
        prev = self._last_event
        merged = None
        if event is not None and prev is not None and self.buffer:
            if (
                event.get("type") == "chunk"
                and set(event) == {"type", "content"}
                and prev.get("type") == "chunk"
                and set(prev) == {"type", "content"}
            ):
                merged = {
                    "type": "chunk",
                    "content": (prev.get("content") or "")
                    + (event.get("content") or ""),
                }
            elif (
                event.get("type") == "status"
                and event.get("status") == "working"
                and prev.get("type") == "status"
                and prev.get("status") == "working"
            ):
                merged = event
        if merged is not None:
            self._buffer_bytes -= len(self.buffer.pop())
            event = merged
            line = f"data: {json.dumps(merged)}"
        frame = line + "\n\n"
        self.buffer.append(frame)
        self._buffer_bytes += len(frame)
        self._last_event = event
        if self._buffer_bytes > MAX_REPLAY_BYTES:
            self._trim()

    def _trim(self) -> None:
        if not self._trimmed:
            logger.warning(
                "Run %s: replay buffer passed %d bytes; trimming the oldest "
                "progress a re-attaching client would see",
                self.session_id[:8],
                MAX_REPLAY_BYTES,
            )
            self._trimmed = True
            self.buffer.insert(1, _TRIMMED_NOTE)
            self._buffer_bytes += len(_TRIMMED_NOTE)
        # Keep the first frame and the note; drop from just after them.
        while self._buffer_bytes > MAX_REPLAY_BYTES and len(self.buffer) > 3:
            self._buffer_bytes -= len(self.buffer.pop(2))

    def finish(self) -> None:
        """Mark the run complete and close out every live subscriber."""
        self.done.set()
        for q in self.subscribers:
            q.put_nowait(DONE)
        self.subscribers.clear()

    # ── Consumer side (called from subscriber generators) ─────────────
    def subscribe(self) -> asyncio.Queue:
        """Register a subscriber.

        Returns a queue preloaded with the full replay buffer. If the run
        has already finished, a ``DONE`` sentinel is appended so the caller
        drains the history then stops; otherwise the queue stays registered
        to receive live events.
        """
        q: asyncio.Queue = asyncio.Queue()
        for item in self.buffer:
            q.put_nowait(_padded(item))
        if self.done.is_set():
            q.put_nowait(DONE)
        else:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Drop a subscriber (e.g. its client disconnected).

        The run keeps going regardless — this only stops fanning events to
        the departed client.
        """
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass


class RunManager:
    """Registry of active chat runs, keyed by session id."""

    def __init__(self):
        self._runs: Dict[str, Run] = {}

    def is_running(self, session_id: str) -> bool:
        return session_id in self._runs

    def get(self, session_id: str) -> Optional[Run]:
        return self._runs.get(session_id)

    def active_sessions(self) -> List[str]:
        """Session ids with a currently-running turn (sidebar source of truth)."""
        return list(self._runs.keys())

    def start(
        self,
        session_id: str,
        make_lifecycle: Callable[[Run], Awaitable[None]],
    ) -> Run:
        """Create a run and launch its detached lifecycle task.

        ``make_lifecycle`` receives the ``Run`` and returns the coroutine
        that drives the producer and emits events via ``run.emit``. The
        coroutine runs to completion regardless of whether any client is
        still attached.

        Raises:
            RuntimeError: if a run is already active for this session. The
                caller (chat router) must guard with ``is_running`` first and
                return 409 — overlapping turns on one session would corrupt
                the cached agent's conversation state.
        """
        if session_id in self._runs:
            raise RuntimeError(
                f"A run is already active for session {session_id[:8]}; "
                "refusing to start an overlapping turn."
            )
        run = Run(session_id)
        self._runs[session_id] = run

        async def _drive() -> None:
            try:
                await make_lifecycle(run)
            except asyncio.CancelledError:
                logger.info("Run cancelled for session %s", session_id[:8])
                raise
            except Exception:  # pylint: disable=broad-except
                # The lifecycle owns its own user-facing error emission; this
                # is the backstop so a bug there can't wedge the registry.
                logger.exception(
                    "Unhandled error in run lifecycle for session %s",
                    session_id[:8],
                )
            finally:
                run.finish()
                self._runs.pop(session_id, None)

        run.task = asyncio.create_task(_drive())
        return run

    def cancel(self, session_id: str) -> bool:
        """Request cancellation of an active run (explicit Stop button).

        Sets the handler's cancelled flag; the producer observes it at its
        next step boundary and tears down. Returns False if no run is
        active for the session.
        """
        run = self._runs.get(session_id)
        if run is None:
            return False
        if run.handler is not None:
            run.handler.cancelled.set()
        return True


# Process-wide singleton used by the chat router and chat helpers.
run_manager = RunManager()
