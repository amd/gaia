# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-provider lock every GAIA process takes to write a stored connection.

The daemon, the Agent UI and the CLI share one keyring. A token refresh, a
connect and a disconnect each read-modify-write the same slot, so all of them
hold this OS file lock (released by the OS if the holder dies) while they do.
"""

from __future__ import annotations

import asyncio
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
    # Scoped like the keyring it guards: per user (not per GAIA_HOME), per
    # provider (the slot is keyed by provider alone, store.py A10).
    return default_gaia_home() / "connectors" / f"{provider_id}.refresh.lock"


def _open(provider_id: str) -> int:
    path = lock_path(provider_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)


def timeout_error(provider_id: str) -> ConnectorsError:
    """The error for a connection lock not freed within the wait cap."""
    return ConnectorsError(
        f"The {provider_id} connection has been busy for over "
        f"{LOCK_TIMEOUT_SECONDS:.0f}s: another GAIA process, or another request "
        "in this one, is still refreshing or saving it (lock file "
        f"{lock_path(provider_id)}). Retry; if it keeps happening, look for a "
        "stuck `gaia daemon` or Agent UI process."
    )


@asynccontextmanager
async def held(
    provider_id: str, timeout: Optional[float] = None
) -> AsyncIterator[None]:
    """Hold ``provider_id``'s connection lock from async code.

    ``timeout`` defaults to :data:`LOCK_TIMEOUT_SECONDS`; a caller that already
    waited for another lock passes what is left of its budget.
    """
    fd = _open(provider_id)
    try:
        wait = LOCK_TIMEOUT_SECONDS if timeout is None else timeout
        deadline = time.monotonic() + wait
        while not try_lock(fd):
            if time.monotonic() >= deadline:
                raise timeout_error(provider_id)
            await asyncio.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


@contextmanager
def held_sync(provider_id: str) -> Iterator[None]:
    """Hold ``provider_id``'s connection lock from sync code.

    Never call it on an event loop thread: a holder on that loop could not
    run to release it.
    """
    fd = _open(provider_id)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while not try_lock(fd):
            if time.monotonic() >= deadline:
                raise timeout_error(provider_id)
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


@contextmanager
def try_held_sync(provider_id: str) -> Iterator[bool]:
    """Take ``provider_id``'s connection lock only if it is free now.

    Yields whether it was taken. It never waits, so unlike :func:`held_sync`
    it is safe on an event loop thread.
    """
    fd = _open(provider_id)
    try:
        acquired = try_lock(fd)
        try:
            yield acquired
        finally:
            if acquired:
                unlock(fd)
    finally:
        os.close(fd)


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
    """Run one keyring write for ``provider_id`` under its connection lock."""
    async with held(provider_id):
        return await run_to_completion(fn, *args, **kwargs)
