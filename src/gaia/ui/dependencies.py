# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""FastAPI dependency injection for GAIA Agent UI.

Provides ``Depends``-compatible callables to retrieve shared resources
(database, tunnel manager, indexing tasks) from ``app.state``.
"""

from fastapi import Request

from .database import ChatDatabase
from .tunnel import TunnelManager


def get_db(request: Request) -> ChatDatabase:
    """Return the ChatDatabase instance stored on ``app.state``."""
    return request.app.state.db


def get_tunnel(request: Request) -> TunnelManager:
    """Return the TunnelManager instance stored on ``app.state``."""
    return request.app.state.tunnel


def get_indexing_tasks(request: Request) -> dict:
    """Return the dict of active background indexing tasks."""
    return request.app.state.indexing_tasks


def get_upload_locks(request: Request) -> dict:
    """Return the dict of per-file upload locks."""
    return request.app.state.upload_locks
