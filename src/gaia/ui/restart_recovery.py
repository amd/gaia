# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Say what a restart, crash or shutdown cut short, instead of losing it.

A chat turn and a document index both run in a server process's memory. When
that process ends mid-way, the database is left with a question that has no
reply, or a document that reads "indexing" forever. This module records a turn
while it runs (:class:`TurnJournal`) and turns whatever an ended process left
unfinished into something the user can see and act on
(:func:`recover_interrupted_work`).

Nothing is re-run: a half-finished turn may already have run tools, and
running them again is not safe. The user is told and can send again.

Each in-progress row names the process that owns it, so this is safe to run
at any time and from any server sharing the database: work whose process is
still alive is left alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from typing import Callable, Dict, Optional

from .database import ChatDatabase
from .process_owner import owner_is_gone

logger = logging.getLogger(__name__)

INTERRUPTED_TURN_NOTICE = (
    "This answer was interrupted when GAIA closed. Send again to retry."
)
CANCELLED_TURN_NOTICE = (
    "This answer was interrupted before it finished. Send again to retry."
)
ENDED_WITHOUT_REPLY_NOTICE = (
    "This answer ended before GAIA could save a reply. Send again to retry."
)
INTERRUPTED_INDEXING_ERROR = (
    "Indexing was interrupted when GAIA closed. Click Retry to index it again."
)

#: How often a running turn's streamed text is written to the database.
PARTIAL_SAVE_INTERVAL = 2.0
#: How often a running server looks for work another, ended server left behind.
SWEEP_INTERVAL = 30.0


class TurnJournal:
    """What one chat turn has shown the user so far, kept in the database.

    Fed every SSE frame the turn emits. ``chunk`` text accumulates and an
    ``answer`` replaces it, which is what the user saw on screen. The last
    error the turn showed is kept too, so a turn that ends on an error without
    saving a reply is closed with that error rather than a generic notice.
    """

    def __init__(
        self,
        db: ChatDatabase,
        session_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        interval: Optional[float] = None,
    ):
        self._db = db
        self._session_id = session_id
        self._clock = clock
        self._interval = PARTIAL_SAVE_INTERVAL if interval is None else interval
        self._text = ""
        self._saved = ""
        self._saved_at = clock()
        self._answered = False
        self.error: Optional[str] = None

    def observe(self, sse_data: str) -> None:
        for line in sse_data.splitlines():
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[len("data: ") :])
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            content = event.get("content")
            if not isinstance(content, str):
                continue
            kind = event.get("type")
            if kind == "chunk":
                self._text += content
            elif kind == "answer":
                self._text = content
                self._answered = True
            elif kind in ("error", "agent_error") and content.strip():
                self.error = content
        if self._clock() - self._saved_at >= self._interval:
            try:
                self.flush()
            except (sqlite3.Error, RuntimeError) as exc:
                # The reply itself still streams and is saved when it ends.
                logger.warning(
                    "Could not save the streamed text of the turn in session "
                    "%s (%s); if GAIA closes before it ends, the text from the "
                    "last save is kept",
                    self._session_id[:8],
                    exc,
                )

    def flush(self) -> None:
        self._saved_at = self._clock()
        if self._text != self._saved:
            self._db.save_turn_partial(self._session_id, self._text)
            self._saved = self._text

    def close(self) -> None:
        """End the turn normally. If it saved no reply, keep what it showed:
        its final answer as is, or else its text and why it ended."""
        self.flush()
        if self.error:
            notice = self.error
        elif self._answered and self._text.strip():
            notice = ""
        else:
            notice = ENDED_WITHOUT_REPLY_NOTICE
        self._db.close_turn(self._session_id, notice)


def recover_interrupted_work(db: ChatDatabase) -> Dict[str, int]:
    """Close the turns and indexes whose server process has ended.

    Each interrupted turn gets a reply holding what it had streamed and
    :data:`INTERRUPTED_TURN_NOTICE`; each interrupted document is marked failed
    with :data:`INTERRUPTED_INDEXING_ERROR`, which the document library offers
    Retry for. Returns how many of each were closed.
    """
    turns = 0
    for row in db.turns_in_progress():
        if not owner_is_gone(row["owner"]):
            continue
        if db.close_turn(row["session_id"], INTERRUPTED_TURN_NOTICE) is not None:
            turns += 1
    documents = len(db.fail_unfinished_indexing(INTERRUPTED_INDEXING_ERROR))
    if turns or documents:
        logger.info(
            "Closed work an ended GAIA process left unfinished: %d chat "
            "turn(s), %d document index(es)",
            turns,
            documents,
        )
    return {"turns": turns, "documents": documents}


async def sweep_interrupted_work(db: ChatDatabase, interval: float = SWEEP_INTERVAL):
    """Re-run the recovery while this server runs.

    A server that starts while the previous one is still shutting down sees
    that one's turns as live; they are closed here once it has exited.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            recover_interrupted_work(db)
        except Exception:  # pylint: disable=broad-except
            # Keep sweeping: one bad pass must not end recovery for the session.
            logger.exception(
                "Could not check the chat database for work an ended GAIA "
                "process left unfinished; trying again in %ds",
                interval,
            )
