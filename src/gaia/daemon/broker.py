# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The host-owned model-slot broker (Agent UI v2, V2-11 · §0.12).

Lemonade is **single-tenant per model slot**: only one model is resident at a
time, and a second process loading a different model *evicts* the first. The only
guard that ever existed was an *in-process* ``threading.Lock`` in
``lemonade_client.py`` — nothing arbitrated loads across processes, so two active
sidecars (or a sidecar plus the host-side embedder) race-evict each other exactly
like the concurrent-eval failures CLAUDE.md documents.

:class:`ModelSlotBroker` is the cross-process arbiter. It hands out **one lease at
a time** — a lease is the exclusive right to occupy the model slot — so loads
serialize instead of racing. It is deliberately backend-agnostic: it never talks
to Lemonade itself. It only decides *who goes next*; the lease holder performs the
actual load/inference and then releases.

Design (§0.12, refined by §0.35.5):

- **Serialization, not preemption.** v1 is priority *queueing* — a foreground
  request jumps ahead of *queued* background requests. It never interrupts a lease
  already granted (you cannot cleanly pause a llama-server generation — §0.35.5).
- **Interactive > background priority.** A background autonomous brief must not
  make the user's interactive turn wait behind it.
- **Hot-model affinity.** When two waiters tie on priority, the one whose model is
  already loaded wins — avoiding a needless evict+reload.
- **Legibility.** When a request has to wait, an ``on_wait`` callback fires so the
  caller can surface a ``switching model…`` status instead of freezing.

The broker is designed to be later-extractable to its own process (§0.35.5 #3):
it owns no daemon state beyond the slot itself and takes an injectable clock so
its queue logic is deterministic under test.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

# A lease held longer than this is presumed abandoned (holder crashed without
# releasing) and reclaimed so the slot never deadlocks. Generous: a cold model
# load plus a long agent turn can legitimately run for minutes. Reclaim is
# logged LOUDLY — it means a holder leaked its lease, which is a bug to fix, not
# a routine event to swallow.
DEFAULT_LEASE_TTL_S = 900.0


class LeasePriority(IntEnum):
    """Higher value wins. Foreground turns preempt *queued* background jobs."""

    BACKGROUND = 0
    INTERACTIVE = 1

    @classmethod
    def parse(cls, value) -> "LeasePriority":
        """Coerce a string/int into a priority, failing loud on garbage.

        Accepts the enum, its int value, or the case-insensitive name
        (``"interactive"`` / ``"background"``). An unrecognized value raises —
        a silently-defaulted priority would let a background job masquerade as
        interactive (or vice-versa) and quietly defeat the queue.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):  # bool is an int subclass — reject explicitly
            raise ValueError(f"invalid lease priority {value!r}")
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError as e:
                raise ValueError(f"invalid lease priority {value!r}") from e
        if isinstance(value, str):
            try:
                return cls[value.strip().upper()]
            except KeyError as e:
                raise ValueError(
                    f"invalid lease priority {value!r}; expected one of "
                    "'interactive' or 'background'"
                ) from e
        raise ValueError(f"invalid lease priority {value!r}")


class BrokerError(Exception):
    """Base for model-slot broker failures."""


class LeaseTimeoutError(BrokerError):
    """A lease request waited past its timeout without being granted the slot."""


class LeaseNotHeldError(BrokerError):
    """A release named a lease that is not the one currently holding the slot."""


@dataclass(frozen=True)
class Lease:
    """The exclusive right to occupy the single model slot for one model.

    ``holder`` is the caller label (an agent_id for a sidecar, ``"host"`` for a
    host-side load) — used only for legibility in logs and status.
    """

    lease_id: str
    model: str
    priority: LeasePriority
    holder: str
    granted_at: float


@dataclass
class _Waiter:
    model: str
    priority: LeasePriority
    holder: str
    seq: int
    granted: bool = False
    lease: Optional[Lease] = None


class ModelSlotBroker:
    """Serializes model-slot access across processes with priority queueing.

    Thread-safe: every method takes the internal condition's lock. ``acquire``
    blocks the calling thread until the slot is free *and* this request is the
    highest-ranked waiter — so it must be called from a worker thread (the daemon
    runs the lease route in a threadpool), never the event loop.
    """

    def __init__(
        self,
        *,
        lease_ttl_s: float = DEFAULT_LEASE_TTL_S,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self._ttl = lease_ttl_s
        self._now = time_fn
        self._cv = threading.Condition()
        self._active: Optional[Lease] = None
        # The model presumed resident in the slot — seeded from the last granted
        # lease. Drives hot-model affinity: a waiter for THIS model needs no
        # evict+reload, so it wins a priority tie.
        self._loaded_model: Optional[str] = None
        self._waiters: List[_Waiter] = []
        self._seq = 0

    # -- public API -----------------------------------------------------------

    def acquire(
        self,
        model: str,
        *,
        priority=LeasePriority.BACKGROUND,
        holder: str = "host",
        timeout: Optional[float] = None,
        on_wait: Optional[Callable[[str], None]] = None,
    ) -> Lease:
        """Block until this request owns the slot, then return its :class:`Lease`.

        Args:
            model: the model this lease will load/use.
            priority: interactive requests jump ahead of *queued* background ones.
            holder: caller label for logs/status (agent_id, or ``"host"``).
            timeout: max seconds to wait for the slot; ``None`` waits forever.
            on_wait: called once, with a human-readable reason, if the request
                cannot be granted immediately — the hook the caller uses to
                surface a ``switching model…`` status instead of a frozen UI.

        Raises:
            LeaseTimeoutError: the slot did not free within *timeout*.
        """
        priority = LeasePriority.parse(priority)
        deadline = None if timeout is None else self._now() + timeout
        with self._cv:
            self._seq += 1
            waiter = _Waiter(
                model=model, priority=priority, holder=holder, seq=self._seq
            )
            self._waiters.append(waiter)
            notified_wait = False
            while True:
                self._reclaim_if_stale_locked()
                if self._active is None and self._best_waiter_locked() is waiter:
                    lease = self._grant_locked(waiter)
                    return lease
                if not notified_wait and on_wait is not None:
                    on_wait(self._wait_reason_locked(waiter))
                notified_wait = True
                if deadline is None:
                    self._cv.wait()
                else:
                    remaining = deadline - self._now()
                    if remaining <= 0:
                        self._waiters.remove(waiter)
                        self._cv.notify_all()
                        raise LeaseTimeoutError(
                            f"model-slot lease for '{model}' (holder '{holder}', "
                            f"priority {priority.name.lower()}) was not granted "
                            f"within {timeout}s. The slot is held by "
                            f"'{self._active.model if self._active else '?'}'. "
                            "Another load is taking longer than expected — check "
                            "the Lemonade server log, then retry."
                        )
                    self._cv.wait(remaining)

    def release(self, lease_id: str) -> None:
        """Release the slot held by *lease_id* and wake the next waiter.

        Raises:
            LeaseNotHeldError: *lease_id* is not the lease currently holding the
                slot (double-release, or a stale id after a TTL reclaim) — loud,
                because a caller that thinks it still holds the slot when it does
                not is a bug that would corrupt serialization.
        """
        with self._cv:
            if self._active is None or self._active.lease_id != lease_id:
                held = self._active.lease_id if self._active else None
                raise LeaseNotHeldError(
                    f"cannot release lease '{lease_id}': it is not the lease "
                    f"holding the model slot (current holder: {held}). It may "
                    "have already been released or reclaimed after its TTL — "
                    "acquire a fresh lease before using the model again."
                )
            logger.debug(
                "broker: released lease %s (model '%s', holder '%s')",
                lease_id,
                self._active.model,
                self._active.holder,
            )
            self._active = None
            self._cv.notify_all()

    def snapshot(self) -> dict:
        """Point-in-time broker state for status/observability (never blocks)."""
        with self._cv:
            return {
                "active": (
                    {
                        "lease_id": self._active.lease_id,
                        "model": self._active.model,
                        "holder": self._active.holder,
                        "priority": self._active.priority.name.lower(),
                        "held_for_s": max(0.0, self._now() - self._active.granted_at),
                    }
                    if self._active
                    else None
                ),
                "loaded_model": self._loaded_model,
                "waiting": [
                    {
                        "model": w.model,
                        "holder": w.holder,
                        "priority": w.priority.name.lower(),
                    }
                    for w in sorted(self._waiters, key=self._rank_locked, reverse=True)
                ],
            }

    # -- internals (all called under self._cv) --------------------------------

    def _grant_locked(self, waiter: _Waiter) -> Lease:
        lease = Lease(
            lease_id=secrets.token_urlsafe(16),
            model=waiter.model,
            priority=waiter.priority,
            holder=waiter.holder,
            granted_at=self._now(),
        )
        self._active = lease
        switching = self._loaded_model is not None and self._loaded_model != lease.model
        self._loaded_model = lease.model
        self._waiters.remove(waiter)
        logger.debug(
            "broker: granted lease %s (model '%s', holder '%s', priority %s)%s",
            lease.lease_id,
            lease.model,
            lease.holder,
            lease.priority.name.lower(),
            " [switching model]" if switching else "",
        )
        return lease

    def _rank_locked(self, w: _Waiter) -> tuple:
        """Sort key: priority desc, hot-model affinity desc, FIFO (seq) asc.

        Expressed so ``max()`` picks the winner: higher priority first; on a
        tie, the waiter whose model is already loaded (affinity) wins; on a
        further tie, the earliest arrival (lowest seq → negated) wins.
        """
        affinity = 1 if w.model == self._loaded_model else 0
        return (int(w.priority), affinity, -w.seq)

    def _best_waiter_locked(self) -> Optional[_Waiter]:
        if not self._waiters:
            return None
        return max(self._waiters, key=self._rank_locked)

    def _wait_reason_locked(self, waiter: _Waiter) -> str:
        if self._active is not None:
            if self._active.model != waiter.model:
                return (
                    f"switching model — the slot holds '{self._active.model}'; "
                    f"'{waiter.model}' is queued behind it"
                )
            return f"waiting for the model slot ('{waiter.model}' load in progress)"
        # Slot free but a higher-ranked waiter goes first.
        return (
            f"queued for the model slot behind a higher-priority request "
            f"('{waiter.model}')"
        )

    def _reclaim_if_stale_locked(self) -> None:
        if self._active is None:
            return
        held_for = self._now() - self._active.granted_at
        if held_for > self._ttl:
            logger.warning(
                "broker: reclaiming lease %s (model '%s', holder '%s') held "
                "for %.0fs > TTL %.0fs — the holder likely crashed without "
                "releasing. This is a leaked lease; investigate the holder.",
                self._active.lease_id,
                self._active.model,
                self._active.holder,
                held_for,
                self._ttl,
            )
            self._active = None
            self._cv.notify_all()
