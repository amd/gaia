# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Export who may call each Agent UI API route, for the mobile app.

A paired phone reaches the device listener, which refuses some routes. The app
reads the committed ``routeAccess.gen.ts`` to show "change this on your PC"
instead of a control that can only fail. Classes come from each route's guard
dependencies (``gaia.ui.security``); a check made inside a handler is not seen
and stays ``remote``.

Usage::

    # Regenerate:
    python -m gaia.ui.export_route_access

    # Drift check — non-zero exit if the committed file is stale:
    python -m gaia.ui.export_route_access --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

from fastapi.routing import APIRoute

ARTIFACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "mobile"
    / "src"
    / "api"
    / "routeAccess.gen.ts"
)

REGENERATE_HINT = "python -m gaia.ui.export_route_access"


def _guard_classes():
    from gaia.ui import security

    # In precedence order: a route with two guards takes the first one's class.
    return (
        (security.require_local_pc, "pc"),
        (security.refuse_on_device_listener, "not_on_listener"),
        (security.require_device_listener, "pair"),
        (security.require_device, "device_self"),
    )


def _api_routes(routes):
    """Every ``APIRoute``, through the app's lazily included routers."""
    for route in routes:
        lazy = False
        for name in ("effective_candidates", "effective_low_priority_routes"):
            method = getattr(route, name, None)
            if callable(method):
                yield from _api_routes(method())
                lazy = True
        if lazy:
            continue
        original = getattr(route, "original_route", route)
        if isinstance(original, APIRoute):
            yield original
        elif hasattr(route, "routes"):
            yield from _api_routes(route.routes)


def build_access() -> Dict[str, str]:
    """``{"METHOD /path": class}`` for every ``/api/`` and ``/v1/`` route."""
    from gaia.ui.server import create_app

    app = create_app(db_path=":memory:")
    guards = _guard_classes()
    access: Dict[str, str] = {}
    for route in _api_routes(app.routes):
        if not route.path.startswith(("/api/", "/v1/")):
            continue
        calls = {d.call for d in route.dependant.dependencies}
        cls = next((name for guard, name in guards if guard in calls), "remote")
        for method in route.methods:
            access[f"{method} {route.path}"] = cls
    return dict(sorted(access.items()))


def render(access: Dict[str, str]) -> str:
    lines = [
        "// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.",
        "// SPDX-License-Identifier: MIT",
        "",
        "// Generated from the backend's route guards; do not edit.",
        f"// Regenerate from the repo root: {REGENERATE_HINT}",
        "",
        "export type RouteAccessClass = "
        + " | ".join(
            f"'{c}'" for c in sorted({n for _, n in _guard_classes()} | {"remote"})
        )
        + ";",
        "",
        "export const ROUTE_ACCESS: Readonly<Record<string, RouteAccessClass>> = {",
    ]
    for key, cls in access.items():
        lines.append(f"    '{key}': '{cls}',")
    lines += ["};", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed route access file is stale",
    )
    args = parser.parse_args(argv)

    rendered = render(build_access())
    if args.check:
        committed = (
            ARTIFACT_PATH.read_text(encoding="utf-8") if ARTIFACT_PATH.exists() else ""
        )
        if committed != rendered:
            print(
                f"{ARTIFACT_PATH} is out of date with the Agent UI route guards.\n"
                f"Regenerate from the repo root: {REGENERATE_HINT}",
                file=sys.stderr,
            )
            return 1
        return 0

    ARTIFACT_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Wrote {ARTIFACT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
