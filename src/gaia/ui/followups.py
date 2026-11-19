# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Messages the user sends to a chat turn that is still running.

The running in-process agent drains its follow-up queue at each step boundary
(``Agent._drain_followups``). An inbox reports each message the moment the
agent takes it, so the chat records it only once the agent has it, and hands
back whatever the turn never took so the chat can send it as the next turn.
Each session keeps the outcome of its last such turn, so a chat that was not
listening when the turn ended can still learn what became of its messages.

A turn relayed to a sidecar agent gets a :class:`RelayInbox` instead. The
sidecar cannot say when its agent reads a message, so one it accepts counts as
taken at once; the sidecar pushes nothing back, so none is ever returned.
"""

import queue
import threading
from typing import Any, Callable

from gaia.logger import get_logger
from gaia.ui.utils import resolve_upload_links

logger = get_logger(__name__)


class FollowUpInbox(queue.Queue):
    """An agent's follow-up queue that reports each message the agent takes.

    The agent gets the text with its attachment links pointed at the files on
    disk; ``on_take`` and :meth:`take_untaken` get the text as the user sent it,
    which is what the chat records and matches against its queue.
    """

    def __init__(self, on_take: Callable[[str], None]):
        super().__init__()
        self._on_take = on_take
        self.taken: list[str] = []

    def put(self, item: str, block: bool = True, timeout: float | None = None):
        super().put((item, resolve_upload_links(item)), block, timeout)

    def get(self, block: bool = True, timeout: float | None = None) -> str:
        sent, for_agent = super().get(block, timeout)
        self.taken.append(sent)
        self._on_take(sent)
        return for_agent

    def take_untaken(self) -> list[str]:
        """Empty the inbox without reporting, for a turn that has ended."""
        left = []
        while True:
            try:
                left.append(super().get(block=False)[0])
            except queue.Empty:
                return left


class RelayInbox:
    """A relayed turn's follow-ups, handed to the sidecar run as they arrive.

    *send* posts one message to the sidecar run and raises when it is refused.
    Its own lock orders a send against the turn's close, so nothing is recorded
    as taken after the turn's outcome was written.
    """

    def __init__(self, send: Callable[[str], Any], on_take: Callable[[str], None]):
        self._send = send
        self._on_take = on_take
        self._lock = threading.Lock()
        self._closed = False
        self.taken: list[str] = []

    def relay(self, text: str) -> bool:
        """Send *text* to the sidecar run. False once the turn has ended."""
        with self._lock:
            if self._closed:
                return False
            self._send(text)
            self.taken.append(text)
            self._on_take(text)
            return True

    def close(self) -> list[str]:
        with self._lock:
            self._closed = True
            return list(self.taken)


# One lock for wiring, unwiring and delivery, so a message is either in a live
# inbox or refused — never put into one that nothing will drain or return.
_lock = threading.Lock()
_inboxes: dict[str, tuple[Any, FollowUpInbox | RelayInbox]] = {}
_last_outcome: dict[str, dict[str, list[str]]] = {}


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
    with _lock:
        _last_outcome[session_id] = {"taken": list(inbox.taken), "returned": left}
    if left:
        logger.info(
            "Turn for session %s ended before taking %d follow-up(s); "
            "returning them to the chat",
            session_id[:8],
            len(left),
        )
    return left


def open_relay_inbox(
    session_id: str, send: Callable[[str], Any], on_take: Callable[[str], None]
) -> RelayInbox:
    """Route the session's follow-ups to the sidecar run it is relaying."""
    inbox = RelayInbox(send, on_take)
    with _lock:
        _inboxes[session_id] = (None, inbox)
    return inbox


def close_relay_inbox(session_id: str, inbox: RelayInbox) -> None:
    """Stop routing follow-ups to the relayed turn and record what it took."""
    with _lock:
        if _inboxes.get(session_id, (None, None))[1] is inbox:
            del _inboxes[session_id]
    taken = inbox.close()
    with _lock:
        _last_outcome[session_id] = {"taken": taken, "returned": []}


def last_outcome(session_id: str) -> dict[str, list[str]]:
    """What the session's last finished turn did with the follow-ups it was handed."""
    with _lock:
        outcome = _last_outcome.get(session_id, {"taken": [], "returned": []})
        return {"taken": list(outcome["taken"]), "returned": list(outcome["returned"])}


def forget(session_id: str) -> None:
    """Drop a deleted session's follow-up record."""
    with _lock:
        _last_outcome.pop(session_id, None)


def deliver(session_id: str, text: str) -> bool:
    """Hand *text* to the session's running turn. False when none can take it.

    A relayed turn's send is an HTTP call, so it raises the sidecar's refusal
    (``SidecarError``) and must not run on the event loop.
    """
    with _lock:
        entry = _inboxes.get(session_id)
        if entry is None:
            return False
        agent, inbox = entry
        if not isinstance(inbox, RelayInbox):
            return bool(agent.queue_followup(text))
    # Outside the global lock: one slow sidecar must not stall every chat.
    return inbox.relay(text)
