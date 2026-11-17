# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/api/hybrid/*`` on the daemon, so the TUI needs no Agent UI server.

The same router the Agent UI serves, at the same paths and shapes, behind the
daemon token. Its settings live in config.json; its stats read the Agent UI's
chat database, opened here on first use.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from gaia.logger import get_logger

log = get_logger(__name__)


def build_hybrid_router(token: str) -> APIRouter:
    """The Agent UI's hybrid routes, authenticated with the daemon token."""
    from gaia.daemon.app import build_require_token
    from gaia.ui.routers import hybrid

    router = APIRouter(dependencies=[Depends(build_require_token(token))])
    router.include_router(hybrid.router)
    return router


#: The paths whose answers the TUI shows as sent.
HYBRID_PREFIX = "/api/hybrid"


def _tui_words(value: Any) -> Any:
    from gaia.llm.hybrid_templates import tui_text

    if isinstance(value, str):
        return tui_text(value)
    if isinstance(value, list):
        return [_tui_words(v) for v in value]
    if isinstance(value, dict):
        return {k: _tui_words(v) for k, v in value.items()}
    return value


async def tui_wording(request: Request, call_next):
    """The hybrid routes' advice in the TUI's words: its screens, not Settings."""
    response = await call_next(request)
    if not request.url.path.startswith(HYBRID_PREFIX):
        return response
    if response.headers.get("content-type", "").split(";")[0] != "application/json":
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    headers = {
        k: v for k, v in response.headers.items() if k.lower() != "content-length"
    }
    return JSONResponse(
        _tui_words(json.loads(body)), status_code=response.status_code, headers=headers
    )


class ChatDatabaseOpener:
    """Opens the Agent UI's chat database once, on the first request that needs it."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._db = None
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            if self._db is None:
                from gaia.ui.database import ChatDatabase

                self._db = ChatDatabase(self._db_path)
                log.info("Daemon opened the chat database for /api/hybrid.")
            return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
