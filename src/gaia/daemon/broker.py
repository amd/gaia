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
  caller can surface a ``switching model…`` status instead of freezing, and
  ``on_update`` reports its place in the queue for as long as it waits.
- **Queued is not dead.** A holder that renews its lease keeps it while it
  renews, up to the budget it declared for its call (``max_hold_s``; without
  one, :data:`DEFAULT_LEASE_TTL_S`, and never past :data:`DEFAULT_MAX_HOLD_S`).
  One that stops renewing is presumed gone and reclaimed after
  :data:`DEFAULT_HEARTBEAT_TTL_S`. A renewal proves the holder is alive, not
  that its request progresses, so the budget is what bounds a wedged one. A
  waiter waits while the holder ahead keeps its lease, and only an explicit
  ``timeout`` or ``cancel`` ends its wait early.
- **No starvation.** A background request waiting longer than
  :data:`AGING_S` ranks as interactive, so a stream of turns cannot hold back
  background work forever.

The broker is designed to be later-extractable to its own process (§0.35.5 #3):
it owns no daemon state beyond the slot itself and takes an injectable clock so
its queue logic is deterministic under test.
"""

from __future__ import annotations

import math
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
# a routine event to swallow. Applies to holders that do not renew (older
# clients); a renewing holder is governed by the two limits below instead.
DEFAULT_LEASE_TTL_S = 900.0

#: A renewing holder that has not renewed for this long has exited or hung.
DEFAULT_HEARTBEAT_TTL_S = 60.0

#: The ceiling on the hold a renewing holder may declare: the longest local
#: request budget (3600 s) plus margin for a cold load and a retry.
DEFAULT_MAX_HOLD_S = 2 * 3600.0

#: A background request queued this long ranks as interactive.
AGING_S = 600.0


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
    """A release or renewal named a lease that no longer holds the slot."""


class LeaseCancelledError(BrokerError):
    """The requester gave up (disconnected or cancelled) before it was granted."""


@dataclass(frozen=True)
class Lease:
    """The exclusive right to occupy the single model slot for one model.

    ``holder`` is the caller label (an agent_id for a sidecar, ``"host"`` for a
    host-side load) — used only for legibility in logs and status.
    ``renewable`` leases are kept alive by :meth:`ModelSlotBroker.renew`.
    """

    lease_id: str
    model: str
    priority: LeasePriority
    holder: str
    granted_at: float
    renewable: bool = False
    max_hold_s: Optional[float] = None


@dataclass
class _Waiter:
    model: str
    priority: LeasePriority
    holder: str
    seq: int
    enqueued_at: float


class ModelSlotBroker:
    """Serializes model-slot access across processes with priority queueing.

    Thread-safe: every method takes the internal condition's lock. ``acquire``
    blocks the calling thread until the slot is free *and* this request is the
    highest-ranked waiter — so it must be called from a worker thread (the daemon
    runs the lease route on its own thread limiter), never the event loop.
    """

    def __init__(
        self,
        *,
        lease_ttl_s: float = DEFAULT_LEASE_TTL_S,
        heartbeat_ttl_s: float = DEFAULT_HEARTBEAT_TTL_S,
        max_hold_s: float = DEFAULT_MAX_HOLD_S,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self._ttl = lease_ttl_s
        self._heartbeat_ttl = heartbeat_ttl_s
        self._max_hold = max_hold_s
        self._now = time_fn
        self._cv = threading.Condition()
        self._active: Optional[Lease] = None
        self._renewed_at = 0.0
        # The model presumed resident in the slot — seeded from the last granted
        # lease. Drives hot-model affinity: a waiter for THIS model needs no
        # evict+reload, so it wins a priority tie.
        self._loaded_model: Optional[str] = None
        self._waiters: List[_Waiter] = []
        self._seq = 0

    # -- public API -----------------------------------------------------------

    @property
    def heartbeat_ttl_s(self) -> float:
        """How long a renewable lease survives without a :meth:`renew`."""
        return self._heartbeat_ttl

    def acquire(
        self,
        model: str,
        *,
        priority=LeasePriority.BACKGROUND,
        holder: str = "host",
        timeout: Optional[float] = None,
        on_wait: Optional[Callable[[str], None]] = None,
        renewable: bool = False,
        max_hold_s: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
        on_update: Optional[Callable[[dict], None]] = None,
        update_every_s: float = 2.0,
    ) -> Lease:
        """Block until this request owns the slot, then return its :class:`Lease`.

        Args:
            model: the model this lease will load/use.
            priority: interactive requests jump ahead of *queued* background ones.
            holder: caller label for logs/status (agent_id, or ``"host"``).
            timeout: max seconds to wait for the slot; ``None`` waits for as
                long as the holder ahead keeps its lease.
            on_wait: called once, with a human-readable reason, if the request
                cannot be granted immediately — the hook the caller uses to
                surface a ``switching model…`` status instead of a frozen UI.
            renewable: the caller will :meth:`renew` the lease while it holds
                it, and keeps it for as long as it does, up to *max_hold_s*.
            max_hold_s: the longest the caller's work under this lease can
                legitimately take (its call budget). A renewing holder still
                holding it then is wedged, and loses it. Capped at the
                broker's ``max_hold_s``; defaults to its fixed TTL.
            cancel: set it, then call :meth:`wake`, when the requester gives
                up; the request leaves the queue instead of being granted to
                nobody.
            on_update: called with the request's queue status when it starts
                waiting and every *update_every_s* after. It runs under the
                broker lock, so it must not block.

        Raises:
            LeaseTimeoutError: the slot did not free within *timeout*.
            LeaseCancelledError: *cancel* was set before the grant.
        """
        priority = LeasePriority.parse(priority)
        deadline = None if timeout is None else self._now() + timeout
        with self._cv:
            self._seq += 1
            waiter = _Waiter(
                model=model,
                priority=priority,
                holder=holder,
                seq=self._seq,
                enqueued_at=self._now(),
            )
            self._waiters.append(waiter)
            try:
                notified_wait = False
                next_update = self._now()
                while True:
                    self._reclaim_if_stale_locked()
                    if cancel is not None and cancel.is_set():
                        self._leave_queue_locked(waiter)
                        raise LeaseCancelledError(
                            f"model-slot lease for '{model}' (holder '{holder}') "
                            "was cancelled by its requester while queued"
                        )
                    if self._active is None and self._best_waiter_locked() is waiter:
                        return self._grant_locked(waiter, renewable, max_hold_s)
                    if not notified_wait and on_wait is not None:
                        on_wait(self._wait_reason_locked(waiter))
                    notified_wait = True
                    now = self._now()
                    if on_update is not None and now >= next_update:
                        on_update(self._queue_status_locked(waiter))
                        next_update = now + update_every_s
                    remaining = None if deadline is None else deadline - now
                    if remaining is not None and remaining <= 0:
                        status = self._queue_status_locked(waiter)
                        self._leave_queue_locked(waiter)
                        raise LeaseTimeoutError(
                            f"model-slot lease for '{model}' (holder '{holder}', "
                            f"priority {priority.name.lower()}) was not granted "
                            f"within its {timeout:g}s wait limit. "
                            f"{describe_queue(status)} The model is busy, not "
                            "broken: wait longer, or retry when that request ends."
                        )
                    remaining = _earliest(remaining, self._expires_in_locked())
                    if on_update is not None:
                        remaining = _earliest(remaining, max(0.0, next_update - now))
                    self._cv.wait(remaining)
            finally:
                if waiter in self._waiters:  # a callback raised: leave the queue
                    self._leave_queue_locked(waiter)

    def wake(self) -> None:
        """Wake every waiter so each re-checks its ``cancel`` event."""
        with self._cv:
            self._cv.notify_all()

    def renew(self, lease_id: str, *, holder: Optional[str] = None) -> Optional[float]:
        """Record a heartbeat; return seconds until the lease lapses (or ``None``).

        Raises:
            LeaseNotHeldError: *lease_id* no longer holds the slot — it was
                released, or reclaimed after its holder stopped renewing — or
                *holder* is given and is not its holder.
        """
        with self._cv:
            self._reclaim_if_stale_locked()
            self._check_held_locked(lease_id, holder)
            self._renewed_at = self._now()
            return self._expires_in_locked()

    def release(self, lease_id: str, *, holder: Optional[str] = None) -> None:
        """Release the slot held by *lease_id* and wake the next waiter.

        Raises:
            LeaseNotHeldError: *lease_id* is not the lease currently holding the
                slot (double-release, or a stale id after a TTL reclaim) — loud,
                because a caller that thinks it still holds the slot when it does
                not is a bug that would corrupt serialization. Also raised when
                *holder* is given and is not its holder.
        """
        with self._cv:
            self._check_held_locked(lease_id, holder)
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
            now = self._now()
            return {
                "active": (
                    {
                        "lease_id": self._active.lease_id,
                        "model": self._active.model,
                        "holder": self._active.holder,
                        "priority": self._active.priority.name.lower(),
                        "held_for_s": max(0.0, now - self._active.granted_at),
                        "renewable": self._active.renewable,
                        "expires_in_s": self._expires_in_locked(),
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
                        "waited_s": max(0.0, now - w.enqueued_at),
                    }
                    for w in sorted(self._waiters, key=self._rank_locked, reverse=True)
                ],
            }

    # -- internals (all called under self._cv) --------------------------------

    def _grant_locked(
        self, waiter: _Waiter, renewable: bool, max_hold_s: Optional[float]
    ) -> Lease:
        if renewable:
            max_hold_s = min(max_hold_s or self._ttl, self._max_hold)
        else:
            max_hold_s = None
        lease = Lease(
            lease_id=secrets.token_urlsafe(16),
            model=waiter.model,
            priority=waiter.priority,
            holder=waiter.holder,
            granted_at=self._now(),
            renewable=renewable,
            max_hold_s=max_hold_s,
        )
        self._active = lease
        self._renewed_at = lease.granted_at
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

    def _leave_queue_locked(self, waiter: _Waiter) -> None:
        self._waiters.remove(waiter)
        self._cv.notify_all()

    def _rank_locked(self, w: _Waiter) -> tuple:
        """Sort key: priority desc, hot-model affinity desc, FIFO (seq) asc.

        Expressed so ``max()`` picks the winner: higher priority first; on a
        tie, the waiter whose model is already loaded (affinity) wins; on a
        further tie, the earliest arrival (lowest seq → negated) wins. A
        background waiter queued for :data:`AGING_S` counts as interactive.
        """
        affinity = 1 if w.model == self._loaded_model else 0
        priority = int(w.priority)
        if self._now() - w.enqueued_at >= AGING_S:
            priority = max(priority, int(LeasePriority.INTERACTIVE))
        return (priority, affinity, -w.seq)

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

    def _queue_status_locked(self, waiter: _Waiter) -> dict:
        """Where *waiter* stands: its place in line and who holds the slot."""
        ranked = sorted(self._waiters, key=self._rank_locked, reverse=True)
        now = self._now()
        active = self._active
        return {
            "position": ranked.index(waiter) + 1,
            "queue_length": len(ranked),
            "model": waiter.model,
            "waited_s": max(0.0, now - waiter.enqueued_at),
            "holder": active.holder if active else None,
            "holder_model": active.model if active else None,
            "held_for_s": max(0.0, now - active.granted_at) if active else None,
            "reason": self._wait_reason_locked(waiter),
        }

    def _check_held_locked(self, lease_id: str, holder: Optional[str]) -> None:
        active = self._active
        if (
            active is not None
            and active.lease_id == lease_id
            and (holder is None or holder == active.holder)
        ):
            return
        # Never the active lease's id: any caller who saw it could release it.
        held = f"'{active.holder}' for '{active.model}'" if active else "nobody"
        raise LeaseNotHeldError(
            f"lease '{lease_id}' is not a lease this caller holds on the model "
            f"slot (it is held by {held}). It was already released, or reclaimed "
            "because its holder stopped renewing it or outlived its TTL — "
            "acquire a fresh lease before using the model again."
        )

    def _expires_in_locked(self) -> Optional[float]:
        """Seconds until the active lease is reclaimed; ``None`` if it never is."""
        if self._active is None:
            return None
        if self._active.renewable:
            expires_at = min(
                self._renewed_at + self._heartbeat_ttl,
                self._active.granted_at + self._active.max_hold_s,
            )
        else:
            expires_at = self._active.granted_at + self._ttl
        if not math.isfinite(expires_at):
            return None
        return max(0.0, expires_at - self._now())

    def _reclaim_if_stale_locked(self) -> None:
        if self._active is None or self._expires_in_locked() != 0.0:
            return
        held_for = self._now() - self._active.granted_at
        if not self._active.renewable:
            why = (
                f"held for {held_for:.0f}s >= TTL {self._ttl:.0f}s — the holder "
                "likely crashed without releasing"
            )
        elif held_for < self._active.max_hold_s:
            why = (
                f"not renewed for {self._heartbeat_ttl:.0f}s — the holder has "
                "likely exited or hung"
            )
        else:
            why = (
                f"held for {held_for:.0f}s, past its {self._active.max_hold_s:.0f}s "
                "budget — the holder is alive but its request is not finishing"
            )
        logger.warning(
            "broker: reclaiming lease %s (model '%s', holder '%s'): %s. This is "
            "a leaked lease; investigate the holder.",
            self._active.lease_id,
            self._active.model,
            self._active.holder,
            why,
        )
        self._active = None
        self._cv.notify_all()


def _earliest(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def describe_queue(status: dict) -> str:
    """One sentence on where a queued request stands, from its queue status."""
    place = f"#{status['position']} of {status['queue_length']} waiting"
    if status.get("holder") is None:
        return f"It was {place}, behind a higher-priority request."
    return (
        f"The slot is held by '{status['holder']}' running "
        f"'{status['holder_model']}' (for {status['held_for_s']:.0f}s); this "
        f"request was {place}."
    )
