# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Read the OS credential store without letting a permission prompt hang a request.

On macOS a keychain item created by an earlier install does not list a new
Python binary, so reading it opens an "allow access" dialog and the read blocks
until the user answers. A request handler that reads it directly hangs for as
long as the dialog stays open, and a UI polling that handler spins forever.

:func:`read_within` runs the read on a worker thread and waits a bounded time.
If the read is still blocked it raises :class:`CredentialStoreWaiting`, which
callers report as its own state — never as "no key". Each slot has at most one
read in flight: later calls wait on the same read and take its result once the
user answers, so polling never piles up threads or dialogs.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, Dict, Optional, TypeVar

from gaia.logger import get_logger

log = get_logger(__name__)

T = TypeVar("T")

#: How long a request waits on the credential store before reporting the wait.
WAIT_SECONDS = 2.0

_lock = threading.Lock()
_in_flight: Dict[str, "Future[object]"] = {}


class CredentialStoreWaiting(Exception):
    """The OS has not answered a credential-store read; it is likely asking the user."""


def waiting_message(what: str, platform: str = "") -> str:
    """What the user should do while the OS holds a read of *what* for permission."""
    platform = platform or sys.platform
    if platform == "darwin":
        return (
            f"macOS is asking GAIA for keychain access to read {what}. Look for a "
            "system dialog and click Always Allow; this updates once you answer."
        )
    return (
        f"The OS credential store has not answered a read of {what}; it may be "
        "locked or asking for permission. Look for a system dialog and unlock "
        "or allow it; this updates once you answer."
    )


def _run(future: "Future[object]", read: Callable[[], object]) -> None:
    try:
        future.set_result(read())
    except Exception as e:  # noqa: BLE001
        # Not swallowed: the waiter's ``future.result()`` re-raises it.
        future.set_exception(e)


def read_within(
    slot: str, read: Callable[[], T], what: str, timeout: Optional[float] = None
) -> T:
    """``read()``, or :class:`CredentialStoreWaiting` if it is still blocked after *timeout*.

    *slot* names the credential-store entry; calls with the same slot share one
    in-flight read. *what* names the entry in the user-facing message.
    *timeout* defaults to :data:`WAIT_SECONDS`.
    """
    timeout = WAIT_SECONDS if timeout is None else timeout
    with _lock:
        future = _in_flight.get(slot)
        started = future is None
        if started:
            future = Future()
            _in_flight[slot] = future
            threading.Thread(
                target=_run,
                args=(future, read),
                name=f"credential-read:{slot}",
                daemon=True,
            ).start()
    try:
        result = future.result(timeout=timeout)
    except FutureTimeoutError as e:
        if started:
            log.warning(
                "Reading %s from the OS credential store has not returned after "
                "%.1f s; the OS is likely showing a permission dialog. Reporting "
                "it as waiting until the user answers.",
                what,
                timeout,
            )
        raise CredentialStoreWaiting(waiting_message(what)) from e
    finally:
        if future.done():
            with _lock:
                if _in_flight.get(slot) is future:
                    del _in_flight[slot]
    return result  # type: ignore[return-value]


def discard(slot: str) -> None:
    """Forget *slot*'s read, so the next call reads the store afresh.

    For a key the user just saved or removed: an earlier read that nobody
    collected would otherwise be served as the current value.
    """
    with _lock:
        _in_flight.pop(slot, None)


def in_flight_count() -> int:
    """Reads still waiting on the OS. For tests and diagnostics."""
    with _lock:
        return len(_in_flight)


__all__ = [
    "CredentialStoreWaiting",
    "WAIT_SECONDS",
    "discard",
    "in_flight_count",
    "read_within",
    "waiting_message",
]
