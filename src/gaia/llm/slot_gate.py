# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Admission to one local model's llama.cpp slots, for this process.

A chat model is served with ``--parallel 2 --kv-unified``: slot 0 holds the
conversation, slot 1 takes side work (memory extraction). Two sequences decode
together at nearly twice one's throughput, so side work should run *while* a
turn streams, not after it.

What makes that unsafe without care is the shared KV pool. Every slot is
promised the whole window, llama.cpp never evicts an active slot, and two
active requests that together outgrow the pool both fail ("Context size has
been exceeded"). So a request is admitted when:

* its slot is free (one request per slot — llama.cpp would queue it anyway),
* the active requests' estimated KV need, plus its own, fits the pool, and
* no model load is running or waiting.

A load is exclusive: it waits for every other active request to finish and
blocks new ones, because reloading the model under a live stream breaks it.

A hold is re-entrant per thread: the request path runs its pre-flight load
check inside its own hold. When that check decides to load, it takes exclusive
from inside the hold. The conversation slot keeps its hold while it waits; a
side-slot holder steps off its slot first, because two holders each waiting
for the other to finish would wait forever.

A stopped stream frees its hold when the client gives up on it, a moment
before the server stops decoding it; the token estimate's margin covers that.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict, Iterator, Optional, Tuple

#: Characters per token used to estimate a request's KV need. Low on purpose:
#: tool schemas and JSON tokenize densely, and an overestimate only costs a
#: wait, while an underestimate can overflow the pool.
CHARS_PER_TOKEN = 3

#: The conversation's slot: the one holder that may keep its slot while it
#: waits for a load.
CONVERSATION_SLOT = 0


def estimate_tokens(prompt_chars: int, max_new_tokens: int) -> int:
    """A request's worst-case KV need: its prompt plus everything it may emit."""
    return prompt_chars // CHARS_PER_TOKEN + max(0, int(max_new_tokens or 0))


class SlotGate:
    """Per-model admission control. One instance per local model per process."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        # slot -> (thread id, tokens); one active request per slot.
        self._active: Dict[int, Tuple[int, int]] = {}
        # thread id -> (its slot, or None for an exclusive-only holder; depth).
        self._holders: Dict[int, Tuple[Optional[int], int]] = {}
        self._exclusive: Optional[int] = None
        self._exclusive_waiting = 0

    # ── introspection ─────────────────────────────────────────────────────

    def active_tokens(self) -> int:
        with self._cv:
            return sum(tokens for _, tokens in self._active.values())

    def active_slots(self) -> Dict[int, int]:
        with self._cv:
            return {slot: tokens for slot, (_, tokens) in self._active.items()}

    def exclusive_pending(self) -> bool:
        """A load is running or waiting: new work should not extend a lease."""
        with self._cv:
            return self._exclusive is not None or self._exclusive_waiting > 0

    def held_by_current_thread(self) -> bool:
        with self._cv:
            return threading.get_ident() in self._holders

    # ── holds ─────────────────────────────────────────────────────────────

    def _admissible(self, slot: int, tokens: int, capacity: int) -> bool:
        if self._exclusive is not None or self._exclusive_waiting:
            return False
        if slot in self._active:
            return False
        used = sum(t for _, t in self._active.values())
        # A request bigger than the pool still runs, alone: refusing it would
        # hang it forever, and alone it fails (or fits) as it did before.
        return used == 0 or used + tokens <= capacity

    @contextmanager
    def hold(self, slot: int, tokens: int, capacity: int) -> Iterator[None]:
        """Hold *slot* for one request needing about *tokens* of the pool."""
        tid = threading.get_ident()
        with self._cv:
            held = self._holders.get(tid)
            if held is not None:
                held_slot, depth = held
                if held_slot is not None and held_slot != slot:
                    raise RuntimeError(
                        f"A request on llama.cpp slot {slot} was started inside "
                        f"a request still holding slot {held_slot} on the same "
                        "thread. Finish or close the outer request (for a "
                        "stream, close it) before starting another."
                    )
                self._holders[tid] = (held_slot, depth + 1)
                nested = True
            else:
                while not self._admissible(slot, tokens, capacity):
                    self._cv.wait()
                self._active[slot] = (tid, tokens)
                self._holders[tid] = (slot, 1)
                nested = False
        try:
            yield
        finally:
            with self._cv:
                held_slot, depth = self._holders[tid]
                if nested:
                    self._holders[tid] = (held_slot, depth - 1)
                else:
                    del self._holders[tid]
                    if self._active.get(slot, (None, 0))[0] == tid:
                        del self._active[slot]
                    self._cv.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the whole model: for a load, an unload, or an unpinned call."""
        tid = threading.get_ident()
        with self._cv:
            if self._exclusive == tid:
                slot, depth = self._holders[tid]
                self._holders[tid] = (slot, depth + 1)
                entered = False
                stepped_off: Optional[Tuple[int, int]] = None
            else:
                entered = True
                own = self._holders.get(tid)
                stepped_off = None
                if own is not None and own[0] not in (None, CONVERSATION_SLOT):
                    stepped_off = (own[0], self._active.pop(own[0])[1])
                    self._cv.notify_all()
                self._exclusive_waiting += 1
                try:
                    while self._exclusive is not None or any(
                        t != tid for t, _ in self._active.values()
                    ):
                        self._cv.wait()
                finally:
                    self._exclusive_waiting -= 1
                    self._cv.notify_all()
                self._exclusive = tid
                if own is None:
                    self._holders[tid] = (None, 1)
        try:
            yield
        finally:
            with self._cv:
                if not entered:
                    slot, depth = self._holders[tid]
                    self._holders[tid] = (slot, depth - 1)
                else:
                    self._exclusive = None
                    if self._holders.get(tid) == (None, 1):
                        del self._holders[tid]
                    if stepped_off is not None:
                        # Back onto the side slot before the request goes on.
                        slot, tokens = stepped_off
                        while slot in self._active or self._exclusive_waiting:
                            self._cv.wait()
                        self._active[slot] = (tid, tokens)
                self._cv.notify_all()


_GATES: Dict[str, SlotGate] = {}
_GATES_GUARD = threading.Lock()


def normalize_model(model: str) -> str:
    """The id a gate or shared lease is keyed on (``user.`` prefix ignored)."""
    return model[len("user.") :] if model.startswith("user.") else model


def gate_for(model: str) -> SlotGate:
    """The process-wide gate for local *model*."""
    with _GATES_GUARD:
        return _GATES.setdefault(normalize_model(model), SlotGate())


__all__ = [
    "CHARS_PER_TOKEN",
    "SlotGate",
    "estimate_tokens",
    "gate_for",
    "normalize_model",
]
