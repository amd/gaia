# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Log a client that hung up as what it is, not as a server error.

When a client closes its socket abruptly (the terminal app quitting, a browser
tab closing), asyncio's transport can raise ``ConnectionResetError`` inside its
own connection-lost callback; on Windows this is WinError 10054 from the
proactor loop. No request handler sees that exception, so the loop's default
handler logs it as an ERROR with a traceback.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from gaia.logger import get_logger

logger = get_logger(__name__)

#: The peer closed or reset the connection; nothing failed on this side.
_HANGUPS = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


def quiet_client_hangups(loop: asyncio.AbstractEventLoop) -> None:
    """Log *loop*'s client-hangup errors at INFO; everything else is unchanged.

    Any other error still goes to the handler *loop* already had, or to
    asyncio's default one, which logs it as an ERROR.
    """
    previous = loop.get_exception_handler()

    def handle(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
        error = context.get("exception")
        # A task or future that died on a reset is GAIA's own call failing.
        if isinstance(error, _HANGUPS) and not (
            "task" in context or "future" in context
        ):
            logger.info("A client closed its connection: %s", error)
            return
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handle)


__all__ = ["quiet_client_hangups"]
