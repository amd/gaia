# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Keep a Windows server listening when a client resets a queued connection.

A connection that is reset while it waits in the listen backlog makes the next
``AcceptEx`` fail with WinError 64. asyncio's proactor loop answers any failed
accept by closing the listening socket, so one client hanging up at the wrong
moment leaves the server running but unreachable until it is restarted.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any, Dict

from gaia.logger import get_logger

logger = get_logger(__name__)

#: ERROR_NETNAME_DELETED, ERROR_CONNECTION_ABORTED, WSAECONNABORTED, WSAECONNRESET.
_CLIENT_RESET_WINERRORS = frozenset({64, 1236, 10053, 10054})

_GUARDED = "_gaia_accept_survives_resets"
# Private names from CPython's asyncio/windows_events.py (loop._proactor, accept_coro).
_ACCEPT_TASK = "IocpProactor.accept.<locals>.accept_coro"


def _is_client_reset(error: BaseException) -> bool:
    return (
        isinstance(error, OSError)
        and getattr(error, "winerror", None) in _CLIENT_RESET_WINERRORS
    )


def _is_accept_task(future: Any) -> bool:
    get_coro = getattr(future, "get_coro", None)
    coro = get_coro() if get_coro is not None else None
    return getattr(coro, "__qualname__", "") == _ACCEPT_TASK


def listener_is_guarded(loop: asyncio.AbstractEventLoop) -> bool:
    """Whether :func:`keep_listening_after_client_resets` has armed *loop*."""
    return bool(getattr(getattr(loop, "_proactor", None), _GUARDED, False))


def keep_listening_after_client_resets(loop: asyncio.AbstractEventLoop) -> bool:
    """Make *loop* accept the next connection when one was reset before accept.

    Returns ``False`` for a loop with no proactor (selector loops skip a reset
    connection on their own). Every other accept failure still reaches asyncio
    unchanged, which logs it and closes the listener.
    """
    proactor = getattr(loop, "_proactor", None)
    if proactor is None:
        return False
    if getattr(proactor, _GUARDED, False):
        return True

    accept_once = proactor.accept

    def accept(listener: socket.socket) -> "asyncio.Future[Any]":
        outer: "asyncio.Future[Any]" = loop.create_future()
        pending = [accept_once(listener)]

        def settle(inner: "asyncio.Future[Any]") -> None:
            if outer.done():
                # The server stopped while a connection was arriving.
                if not inner.cancelled() and inner.exception() is None:
                    inner.result()[0].close()
                return
            if inner.cancelled():
                outer.cancel()
                return
            error = inner.exception()
            if error is None:
                outer.set_result(inner.result())
                return
            if not _is_client_reset(error) or listener.fileno() == -1:
                outer.set_exception(error)
                return
            logger.info("A client reset its connection before it was accepted")
            try:
                pending[0] = accept_once(listener)
            except OSError as rearm_error:
                outer.set_exception(rearm_error)
                return
            pending[0].add_done_callback(settle)

        def stop(done: "asyncio.Future[Any]") -> None:
            if done.cancelled():
                pending[0].cancel()

        pending[0].add_done_callback(settle)
        outer.add_done_callback(stop)
        return outer

    proactor.accept = accept
    setattr(proactor, _GUARDED, True)

    previous = loop.get_exception_handler()

    def handle(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
        # asyncio's own accept task ends with the reset this guard re-armed after.
        if _is_client_reset(context.get("exception")) and _is_accept_task(
            context.get("future")
        ):
            return
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handle)
    return True


__all__ = ["keep_listening_after_client_resets", "listener_is_guarded"]
