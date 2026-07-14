# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's versioned client API surface (``/daemon/v1/*``).

Skeleton scope: a token-guarded ``status`` route and a ``shutdown`` route. Later
issues mount the custody API (``/host/v1/*``), the model broker, and the scheduler
into this same app.

Every ``/daemon/v1`` route requires the client token minted at startup. A request
without a valid token gets a 401 whose detail names what failed, what to do, and
where to look — no silent fallback to an unauthenticated response.
"""

from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager
from typing import Callable, Optional

from gaia.daemon.constants import (
    API_PREFIX,
    AUTH_SCHEME,
    DAEMON_API_VERSION,
    SERVICE_ID,
)
from gaia.daemon.paths import instance_path


def create_app(
    *,
    token: str,
    port: int,
    pid: int,
    started_at: float,
    on_startup: Optional[Callable[[], None]] = None,
    on_shutdown: Optional[Callable[[], None]] = None,
):
    """Build the FastAPI app bound to this daemon's identity.

    *token*, *port*, *pid*, *started_at* are captured in the closure so the status
    payload and the auth check need no shared global state. *on_startup* /
    *on_shutdown* run inside the app lifespan — the daemon registers instance.json
    on startup (once the port is bound) and deregisters on shutdown.
    """
    from fastapi import Depends, FastAPI, Header, HTTPException

    @asynccontextmanager
    async def lifespan(_app):
        if on_startup is not None:
            on_startup()
        try:
            yield
        finally:
            if on_shutdown is not None:
                on_shutdown()

    app = FastAPI(
        title="GAIA Daemon",
        version=DAEMON_API_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def require_token(authorization: Optional[str] = Header(default=None)) -> None:
        where = f"the client token in {instance_path()}"
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Missing client token. Send the header "
                    f"'Authorization: {AUTH_SCHEME} <token>' using {where}. "
                    "If the daemon was restarted the token rotated — re-attach with "
                    "`gaia daemon status`."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != AUTH_SCHEME.lower() or not credential:
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Malformed Authorization header. Expected "
                    f"'{AUTH_SCHEME} <token>' using {where}."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        if not secrets.compare_digest(credential, token):
            raise HTTPException(
                status_code=401,
                detail=(
                    "Invalid client token. The token must match "
                    f"{where}. If the daemon restarted, re-read it via "
                    "`gaia daemon status`, or run `gaia daemon restart`."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )

    @app.get(f"{API_PREFIX}/status")
    def status(_: None = Depends(require_token)) -> dict:
        now = time.time()
        return {
            "service": SERVICE_ID,
            "api_version": DAEMON_API_VERSION,
            "pid": pid,
            "port": port,
            "host": "127.0.0.1",
            "started_at": started_at,
            "uptime_seconds": max(0.0, now - started_at),
        }

    @app.post(f"{API_PREFIX}/shutdown")
    def shutdown(_: None = Depends(require_token)) -> dict:
        # uvicorn polls should_exit in its serving loop; setting it triggers a
        # graceful drain + shutdown. The server object is attached in server.run().
        server = getattr(app.state, "server", None)
        if server is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Daemon shutdown is unavailable: no server handle attached. "
                    "Stop it with `gaia daemon stop` (which will terminate the pid), "
                    "or inspect `gaia daemon logs`."
                ),
            )
        server.should_exit = True
        return {"service": SERVICE_ID, "status": "stopping", "pid": pid}

    return app
