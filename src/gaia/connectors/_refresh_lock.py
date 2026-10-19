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
from typing import Any, AsyncIterator, Callable, Iterator, TypeVar

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
    return Path.home() / ".gaia" / "connectors" / f"{provider_id}.refresh.lock"


def _open(provider_id: str) -> tuple[int, Path]:
    path = lock_path(provider_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600), path


def _timeout_error(provider_id: str, path: Path) -> ConnectorsError:
    return ConnectorsError(
        f"Another GAIA process has held the {provider_id} connection lock for "
        f"over {LOCK_TIMEOUT_SECONDS:.0f}s (lock held on {path}). Retry; if it "
        "keeps happening, look for a stuck `gaia daemon` or Agent UI process."
    )


@asynccontextmanager
async def held(provider_id: str) -> AsyncIterator[None]:
    """Hold ``provider_id``'s connection lock from async code."""
    fd, path = _open(provider_id)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while not try_lock(fd):
            if time.monotonic() >= deadline:
                raise _timeout_error(provider_id, path)
            await asyncio.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


@contextmanager
def held_sync(provider_id: str) -> Iterator[None]:
    """Hold ``provider_id``'s connection lock from sync code."""
    fd, path = _open(provider_id)
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while not try_lock(fd):
            if time.monotonic() >= deadline:
                raise _timeout_error(provider_id, path)
            time.sleep(_POLL_SECONDS)
        try:
            yield
        finally:
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
