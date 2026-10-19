# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Messages the user sends to a chat turn that is still running.

The running in-process agent drains its follow-up queue at each step boundary
(``Agent._drain_followups``). An inbox reports each message the moment the
agent takes it, so the chat records it only once the agent has it, and hands
back whatever the turn never took so the chat can send it as the next turn.
"""

import queue
import threading
from typing import Any, Callable

from gaia.logger import get_logger

logger = get_logger(__name__)


class FollowUpInbox(queue.Queue):
    """An agent's follow-up queue that reports each message the agent takes."""

    def __init__(self, on_take: Callable[[str], None]):
        super().__init__()
        self._on_take = on_take

    def get(self, block: bool = True, timeout: float | None = None) -> str:
        text = super().get(block, timeout)
        self._on_take(text)
        return text

    def take_untaken(self) -> list[str]:
        """Empty the inbox without reporting, for a turn that has ended."""
        left = []
        while True:
            try:
                left.append(super().get(block=False))
            except queue.Empty:
                return left


# One lock for wiring, unwiring and delivery, so a message is either in a live
# inbox or refused — never put into one that nothing will drain or return.
_lock = threading.Lock()
_inboxes: dict[str, tuple[Any, FollowUpInbox]] = {}


def open_inbox(
    session_id: str, agent: Any, on_take: Callable[[str], None]
) -> FollowUpInbox:
    """Wire a fresh inbox into *agent* for the turn it is about to run."""
    inbox = FollowUpInbox(on_take)
    with _lock:
        agent._followup_queue = inbox
        _inboxes[session_id] = (agent, inbox)
    return inbox


def close_inbox(session_id: str, agent: Any, inbox: FollowUpInbox) -> list[str]:
    """Unwire *inbox* and return the messages its turn never took."""
    with _lock:
        if _inboxes.get(session_id, (None, None))[1] is inbox:
            del _inboxes[session_id]
        # Identity-guarded: the cached agent's next turn may already own it.
        if getattr(agent, "_followup_queue", None) is inbox:
            agent._followup_queue = None
    left = inbox.take_untaken()
    if left:
        logger.info(
            "Turn for session %s ended before taking %d follow-up(s); "
            "returning them to the chat",
            session_id[:8],
            len(left),
        )
    return left


def deliver(session_id: str, text: str) -> bool:
    """Hand *text* to the session's running turn. False when none can take it."""
    with _lock:
        entry = _inboxes.get(session_id)
        if entry is None:
            return False
        agent, _ = entry
        return bool(agent.queue_followup(text))
