# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/api/hybrid/*`` on the daemon, so the TUI needs no Agent UI server.

The same router the Agent UI serves, at the same paths and shapes, behind the
daemon token. Its settings live in config.json; its stats read the Agent UI's
chat database, opened here on first use.
"""

from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, Depends

from gaia.logger import get_logger

log = get_logger(__name__)


def build_hybrid_router(token: str) -> APIRouter:
    """The Agent UI's hybrid routes, authenticated with the daemon token."""
    from gaia.daemon.app import build_require_token
    from gaia.ui.routers import hybrid

    router = APIRouter(dependencies=[Depends(build_require_token(token))])
    router.include_router(hybrid.router)
    return router


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
