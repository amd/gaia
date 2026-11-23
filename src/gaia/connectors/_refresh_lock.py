# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The locks every GAIA process takes to write a stored connection.

The daemon, the Agent UI and the CLI share one keyring. A token refresh, a
connect and a disconnect each read-modify-write a stored account, so they hold
OS file locks (released by the OS if the holder dies) while they do.

Two levels: the provider lock covers writes that span accounts (adding or
removing one, replacing the OAuth client), and each account has its own lock
for its refresh. Take the provider lock first, then the account lock, never the
reverse.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator, Optional, TypeVar

from gaia.config import default_gaia_home
from gaia.connectors.errors import ConnectorsError
from gaia.daemon.lock import try_lock, unlock

_T = TypeVar("_T")

# A peer's token POST (10 s overall, tokens._TOKEN_POST_DEADLINE_SECONDS) plus
# its keyring writes, leaving room for our own refresh inside
# get_or_refresh_sync's 30 s bound (_loop.py).
LOCK_TIMEOUT_SECONDS = 15.0
_POLL_SECONDS = 0.05


def lock_path(provider_id: str) -> Path:
    """The provider lock: held by writes that touch more than one account, such
    as adding or removing an account or replacing the OAuth client."""
    # Scoped like the keyring it guards: per user (not per GAIA_HOME).
    return default_gaia_home() / "connectors" / f"{provider_id}.refresh.lock"


def account_lock_path(provider_id: str, slot: str) -> Path:
    """The lock for one stored account. Taken after the provider lock, never
    before it, so a refresh of one account never waits on another."""
    digest = hashlib.sha256(slot.encode("utf-8")).hexdigest()[:16]
    return default_gaia_home() / "connectors" / f"{provider_id}.account-{digest}.lock"


def _open(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)


def timeout_error(provider_id: str, path: Optional[Path] = None) -> ConnectorsError:
    """The error for a connection lock not freed within the wait cap."""
    return ConnectorsError(
        f"The {provider_id} connection has been busy for over "
        f"{LOCK_TIMEOUT_SECONDS:.0f}s: another GAIA process, or another request "
        "in this one, is still refreshing or saving it (lock file "
        f"{path or lock_path(provider_id)}). Retry; if it keeps happening, look "
        "for a stuck `gaia daemon` or Agent UI process."
    )


@asynccontextmanager
async def _held_path(
    provider_id: str, path: Path, timeout: Optional[float]
) -> AsyncIterator[None]:
    fd = _open(path)
    try:
        wait = LOCK_TIMEOUT_SECONDS if timeout is None else timeout
        deadline = time.monotonic() + wait
        while not try_lock(fd):
            if time.monotonic() >= deadline:
                raise timeout_error(provider_id, path)
            await asyncio.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


@contextmanager
def _held_path_sync(provider_id: str, path: Path) -> Iterator[None]:
    fd = _open(path)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while not try_lock(fd):
            if time.monotonic() >= deadline:
                raise timeout_error(provider_id, path)
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


@contextmanager
def _try_held_path_sync(path: Path) -> Iterator[bool]:
    fd = _open(path)
    try:
        acquired = try_lock(fd)
        try:
            yield acquired
        finally:
            if acquired:
                unlock(fd)
    finally:
        os.close(fd)


def held(provider_id: str, timeout: Optional[float] = None):
    """Hold ``provider_id``'s provider lock from async code.

    ``timeout`` defaults to :data:`LOCK_TIMEOUT_SECONDS`; a caller that already
    waited for another lock passes what is left of its budget.
    """
    return _held_path(provider_id, lock_path(provider_id), timeout)


def held_sync(provider_id: str):
    """Hold ``provider_id``'s provider lock from sync code.

    Never call it on an event loop thread: a holder on that loop could not
    run to release it.
    """
    return _held_path_sync(provider_id, lock_path(provider_id))


def try_held_sync(provider_id: str):
    """Take ``provider_id``'s provider lock only if it is free now; yields
    whether it was taken. It never waits, so it is safe on an event loop."""
    return _try_held_path_sync(lock_path(provider_id))


def held_account(provider_id: str, slot: str, timeout: Optional[float] = None):
    """Hold one account's lock from async code."""
    return _held_path(provider_id, account_lock_path(provider_id, slot), timeout)


def held_account_sync(provider_id: str, slot: str):
    """Hold one account's lock from sync code (never on an event loop thread)."""
    return _held_path_sync(provider_id, account_lock_path(provider_id, slot))


def try_held_account_sync(provider_id: str, slot: str):
    """Take one account's lock only if it is free now; yields whether it was."""
    return _try_held_path_sync(account_lock_path(provider_id, slot))


async def run_to_completion(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run a blocking keyring call in a thread, and finish it even if cancelled.

    A cancelled ``to_thread`` returns at once while the thread keeps writing;
    releasing the lock then would let a peer read a half-written blob. So a
    cancellation waits for the thread, then propagates.
    """
    task = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                continue
        raise


async def locked_write(
    provider_id: str, fn: Callable[..., _T], *args: Any, **kwargs: Any
) -> _T:
    """Run one blocking write for ``provider_id`` under its provider lock."""
    async with held(provider_id):
        return await run_to_completion(fn, *args, **kwargs)
