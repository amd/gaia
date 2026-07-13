# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Daemon process entrypoint: bind loopback port, mint token, serve, register.

Run as ``python -m gaia.daemon`` (the client spawns it detached). This process IS
the daemon — its pid is what instance.json records — so ``start_or_attach`` can
assert the file it reads back belongs to the child it launched.

Order matters: uvicorn binds the port during its startup phase; instance.json is
written from a startup hook so the registry only appears once the port is actually
accepting. On shutdown the registry is removed (guarded by pid so a newer daemon's
file is never clobbered).
"""

from __future__ import annotations

import os
import secrets
import socket
import time

from gaia.daemon.app import create_app
from gaia.daemon.constants import HOST, RESERVED_PORT
from gaia.daemon.errors import DaemonStartError
from gaia.daemon.instance import DaemonInstance, remove_instance, write_instance
from gaia.logger import get_logger

logger = get_logger(__name__)


def _find_free_port(host: str = HOST) -> int:
    """OS-assigned free loopback port. Never returns the reserved 4001."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
        if port != RESERVED_PORT:
            return port
    raise DaemonStartError(
        "could not find a free loopback port for the daemon after 50 attempts — "
        "the ephemeral port range may be exhausted; check `gaia daemon logs`."
    )


def run(host: str = HOST) -> None:
    """Start the daemon and serve until shutdown. Blocks."""
    import uvicorn

    port = _find_free_port(host)
    token = secrets.token_urlsafe(32)
    pid = os.getpid()
    started_at = time.time()

    def _register() -> None:
        write_instance(
            DaemonInstance(
                pid=pid, port=port, token=token, host=host, started_at=started_at
            )
        )
        logger.info("daemon: registered instance pid=%s port=%s", pid, port)

    def _deregister() -> None:
        remove_instance(only_pid=pid)
        logger.info("daemon: deregistered instance pid=%s", pid)

    app = create_app(
        token=token,
        port=port,
        pid=pid,
        started_at=started_at,
        on_startup=_register,
        on_shutdown=_deregister,
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    # Give the shutdown route a handle to trigger a graceful exit.
    app.state.server = server

    logger.info("daemon: starting on http://%s:%s (pid=%s)", host, port, pid)
    server.run()


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m gaia.daemon`
    run()
