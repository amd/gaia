# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The routes a paired device may call; the listener refuses every other one.

A route is closed to devices until it is listed here. Each feature that a
phone needs opens its own routes, reviewed in the change that adds them, so a
new route never reaches the home network by default.
"""

from __future__ import annotations

import re
from typing import FrozenSet, Iterable, Iterator, List, Tuple

from fastapi.routing import APIRoute
from starlette.routing import compile_path

#: ``"METHOD /path"`` for every route the device listener serves an allowed
#: device. The pairing route is served before device auth and isn't listed.
DEVICE_ROUTES: FrozenSet[str] = frozenset(
    {
        # The device itself, and what it polls to show the PC is up.
        "DELETE /api/devices/me",
        "GET /api/health",
        "GET /api/system/status",
        # Chats and their messages.
        "GET /api/sessions",
        "POST /api/sessions",
        "GET /api/sessions/{session_id}",
        "GET /api/sessions/{session_id}/messages",
        "GET /api/sessions/events",
        # A turn: send it and read its stream, stop it, answer its prompts.
        "POST /api/chat/send",
        "GET /api/chat/active",
        "GET /api/chat/attach",
        "POST /api/chat/cancel",
        "POST /api/chat/confirm-tool",
        "POST /api/chat/user-input",
        # Schedules and tasks, read-only.
        "GET /api/schedules",
        "GET /api/schedules/{name:path}",
        "GET /api/schedules/{name:path}/results",
        "GET /api/goals",
        "GET /api/goals/{goal_id}",
    }
)


def iter_api_routes(routes: Iterable) -> Iterator[APIRoute]:
    """Every ``APIRoute``, through the app's lazily included routers."""
    for route in routes:
        lazy = False
        for name in ("effective_candidates", "effective_low_priority_routes"):
            method = getattr(route, name, None)
            if callable(method):
                yield from iter_api_routes(method())
                lazy = True
        if lazy:
            continue
        original = getattr(route, "original_route", route)
        if isinstance(original, APIRoute):
            yield original
        elif hasattr(route, "routes"):
            yield from iter_api_routes(route.routes)


class DeviceRouteTable:
    """Decides whether a device request reaches only routes in *allowed*.

    A request is let through only when at least one route matches it and
    every route that matches is allowed, so a listed template never opens an
    unlisted route that shares its shape (``/api/sessions/search``).
    """

    def __init__(self, app, allowed: FrozenSet[str] = DEVICE_ROUTES) -> None:
        self._app = app
        self._allowed = allowed
        self._table: List[Tuple[str, "re.Pattern[str]", str]] = []
        self._built = False

    def _build(self) -> None:
        for route in iter_api_routes(self._app.routes):
            if not route.path.startswith(("/api/", "/v1/")):
                continue
            regex = compile_path(route.path)[0]
            for method in route.methods:
                self._table.append((method, regex, f"{method} {route.path}"))
        self._built = True

    def allows(self, method: str, path: str) -> bool:
        if not self._built:
            self._build()
        matched = [
            key for m, regex, key in self._table if m == method and regex.match(path)
        ]
        return bool(matched) and all(key in self._allowed for key in matched)
