# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The pairing window: one single-use invitation at a time, held as a hash.

The PC opens a window and shows its invitation as a QR code. The first device
to redeem it closes the window. Every failed attempt is remembered for the PC
with its reason, while the device always sees the same refusal; an address
that fails too often is locked out for a while, without locking out others.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple, TypeVar

from . import (
    CLOSED_INVITATIONS_KEPT,
    DEVICE_NAME_MAX,
    INVITATION_BYTES,
    PAIR_FAILURE_WINDOW_SECONDS,
    PAIR_FAILURES_ALLOWED,
    PAIR_LOCKOUT_SECONDS,
    PAIR_TRACKED_PEERS,
    PAIRING_WINDOW_SECONDS,
    RECENT_FAILURES_KEPT,
    PairFailureReason,
)

T = TypeVar("T")

#: Embedding, override and isolate controls, and the bare marks: each can make
#: a name read as something else when the PC shows it.
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def sanitize_device_name(raw: str) -> str:
    """*raw* without control or bidi-control characters, spaces collapsed, capped.

    Raises:
        ValueError: Nothing printable is left.
    """
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in raw
        if ch not in _BIDI_CONTROLS
        and (ch.isspace() or unicodedata.category(ch) != "Cc")
    )
    name = " ".join(kept.split())[:DEVICE_NAME_MAX].strip()
    if not name:
        raise ValueError("device_name has no printable characters.")
    return name


def _digest(invitation: str) -> str:
    return hashlib.sha256(invitation.encode("utf-8")).hexdigest()


def _iso(when: float) -> str:
    return datetime.datetime.fromtimestamp(when, datetime.timezone.utc).isoformat(
        timespec="seconds"
    )


class PairingRefused(Exception):
    """A redemption failed. *reason* is for the PC only."""

    def __init__(self, reason: PairFailureReason, locked_out: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        # This failure is the one that locked the address out.
        self.locked_out = locked_out


@dataclass
class _Window:
    digest: str
    expires_at: float


class _PeerLimiter:
    """Locks out one address after too many failures, never everyone."""

    def __init__(self) -> None:
        self._failures: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._locked_until: Dict[str, float] = {}

    def locked(self, peer: str, now: float) -> bool:
        until = self._locked_until.get(peer)
        if until is None:
            return False
        if now >= until:
            del self._locked_until[peer]
            return False
        return True

    def fail(self, peer: str, now: float) -> bool:
        """Count a failure; True when it locks *peer* out."""
        times = self._failures.setdefault(peer, deque())
        self._failures.move_to_end(peer)
        times.append(now)
        while times and now - times[0] >= PAIR_FAILURE_WINDOW_SECONDS:
            times.popleft()
        while len(self._failures) > PAIR_TRACKED_PEERS:
            self._failures.popitem(last=False)
        if len(times) < PAIR_FAILURES_ALLOWED:
            return False
        del self._failures[peer]
        self._locked_until = {
            p: until for p, until in self._locked_until.items() if until > now
        }
        while len(self._locked_until) >= PAIR_TRACKED_PEERS:
            del self._locked_until[next(iter(self._locked_until))]
        self._locked_until[peer] = now + PAIR_LOCKOUT_SECONDS
        return True


class PairingWindow:
    """The PC's pairing window and the record of attempts that failed."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._window: Optional[_Window] = None
        self._closed: Deque[Tuple[str, PairFailureReason]] = deque(
            maxlen=CLOSED_INVITATIONS_KEPT
        )
        self._failures: Deque[dict] = deque(maxlen=RECENT_FAILURES_KEPT)
        self._limiter = _PeerLimiter()

    def open(self) -> Tuple[str, float]:
        """A new invitation and when it dies; any open one stops working."""
        invitation = (
            base64.urlsafe_b64encode(secrets.token_bytes(INVITATION_BYTES))
            .decode("ascii")
            .rstrip("=")
        )
        expires_at = self._clock() + PAIRING_WINDOW_SECONDS
        with self._lock:
            self._window = _Window(_digest(invitation), expires_at)
        return invitation, expires_at

    def close(self) -> None:
        with self._lock:
            self._window = None

    def expires_at(self) -> Optional[float]:
        """When the open window closes, or None when none is open."""
        with self._lock:
            self._expire(self._clock())
            return self._window.expires_at if self._window else None

    def failures(self) -> List[dict]:
        """Recent failed attempts, oldest first: ``{at, address, reason}``."""
        with self._lock:
            return list(self._failures)

    def _expire(self, now: float) -> None:
        if self._window and now >= self._window.expires_at:
            self._closed.append((self._window.digest, "expired"))
            self._window = None

    def _closed_reason(self, digest: str) -> Optional[PairFailureReason]:
        for closed, reason in self._closed:
            if hmac.compare_digest(closed, digest):
                return reason
        return None

    def redeem(self, invitation: str, peer: str, pair: Callable[[], T]) -> T:
        """Run *pair* if *invitation* opens the window, and close it.

        Raises:
            PairingRefused: The invitation doesn't open it, or *peer* is
                locked out. A failure in *pair* itself leaves the window open.
        """
        now = self._clock()
        with self._lock:
            if self._limiter.locked(peer, now):
                raise PairingRefused("rate_limited")
            digest = _digest(invitation)
            window = self._window
            if window and hmac.compare_digest(window.digest, digest):
                if now < window.expires_at:
                    paired = pair()
                    self._closed.append((digest, "used"))
                    self._window = None
                    return paired
            self._expire(now)
            reason: PairFailureReason = self._closed_reason(digest) or (
                "wrong" if self._window else "closed"
            )
            self._failures.append({"at": _iso(now), "address": peer, "reason": reason})
            locked_out = self._limiter.fail(peer, now)
            if locked_out:
                self._failures.append(
                    {"at": _iso(now), "address": peer, "reason": "rate_limited"}
                )
        raise PairingRefused(reason, locked_out=locked_out)
