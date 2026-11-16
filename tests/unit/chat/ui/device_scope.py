# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Hand the app a request as the device listener does once device auth passed."""

from __future__ import annotations

from gaia.ui.security import (
    DEVICE_AUTH_SCOPE_KEY,
    DEVICE_SCOPE_KEY,
    DevicePrincipal,
    ListenerScopeMiddleware,
)

PC = ("127.0.0.1", 50000)
ALLOWED = DevicePrincipal(device_id="d0a1", allowed=True)
PENDING = DevicePrincipal(device_id="d0a2", allowed=False)


def on_listener(app, device=None):
    """*app* behind the listener's stamp, as *device* (or no device)."""

    async def authenticated(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, DEVICE_AUTH_SCOPE_KEY: True}
            if device is not None:
                scope[DEVICE_SCOPE_KEY] = device
        await app(scope, receive, send)

    return ListenerScopeMiddleware(authenticated)
