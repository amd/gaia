# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``docs/sdk/sdks/agent-ui.mdx`` must document exactly the routes that exist.

#4255: 76 of 143 real routes appeared in neither Agent UI doc page, and five
documented ``/api/mcp/*`` endpoints didn't exist at all (that surface moved to
``/api/connectors``). Both directions are checked: an accordion titled
``METHOD /path`` for a path the app no longer serves is exactly as misleading
as a real route with no accordion at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from gaia.ui.server import create_app

pytestmark = pytest.mark.allow_network  # see test_ui_request_guard.py docstring

REPO_ROOT = Path(__file__).resolve().parents[4]
DOC_PATH = REPO_ROOT / "docs" / "sdk" / "sdks" / "agent-ui.mdx"

#: Framework/internal routes no doc page should ever need to list.
INTERNAL_PATHS = frozenset(
    {
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/{full_path}",  # Electron/browser SPA catch-all (path-converter stripped)
    }
)

#: Real surfaces documented on a different page, by design.
#:
#: - ``/v1/connections/*`` is the forwarded pre-authenticated connections API
#:   (#1292) — see docs/sdk/infrastructure/connectors.mdx.
#: - ``/v1/email/*`` is the email sidecar's own REST surface, a separate
#:   process this backend never imports — see docs/guides/email.mdx and
#:   docs/guides/email-integration.mdx.
DOCUMENTED_ELSEWHERE_PREFIXES = ("/v1/connections", "/v1/email")


@pytest.fixture(scope="module")
def app():
    return create_app(db_path=":memory:")


def _all_real_paths(app) -> set[str]:
    """Every path this app actually serves, sans path-converter syntax.

    Mirrors ``_all_api_routes`` in ``test_ui_request_guard.py`` — newer
    FastAPI defers each ``include_router`` behind a lazy wrapper that only
    materializes its children through ``effective_candidates()``.
    """
    found: set[str] = set()

    def visit(routes):
        for route in routes:
            materialised = False
            for name in ("effective_candidates", "effective_low_priority_routes"):
                method = getattr(route, name, None)
                if callable(method):
                    visit(method())
                    materialised = True
            if materialised:
                continue
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if isinstance(route, APIRoute) or hasattr(route, "original_route"):
                if path and methods:
                    normalised = re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)
                    # A router registering both "" and "/" (e.g. connectors)
                    # is one route with two spellings, not two routes to
                    # document separately.
                    if normalised.endswith("/") and normalised != "/":
                        normalised = normalised.rstrip("/")
                    found.add(normalised)
            elif hasattr(route, "routes"):
                visit(route.routes)

    visit(app.routes)
    return found


def _documented_paths() -> set[str]:
    """Every ``METHOD /path`` accordion title in the doc, path only."""
    text = DOC_PATH.read_text(encoding="utf-8")
    titles = re.findall(
        r'<Accordion title="(?:GET|POST|PUT|PATCH|DELETE) ([^"]+)"', text
    )
    return set(titles)


def _in_scope(path: str) -> bool:
    if path in INTERNAL_PATHS:
        return False
    return not path.startswith(DOCUMENTED_ELSEWHERE_PREFIXES)


def test_walk_finds_the_whole_surface(app):
    """Guard the guard: a walk that finds nothing would pass every test below."""
    assert len(_all_real_paths(app)) > 100


def test_every_real_route_is_documented(app):
    real = {p for p in _all_real_paths(app) if _in_scope(p)}
    documented = _documented_paths()
    undocumented = sorted(real - documented)
    assert not undocumented, (
        f"{len(undocumented)} route(s) exist but agent-ui.mdx never mentions "
        f"them: {undocumented}"
    )


def test_no_documented_route_is_fictional(app):
    real = _all_real_paths(app)
    documented = _documented_paths()
    fictional = sorted(documented - real)
    assert not fictional, (
        f"agent-ui.mdx documents {len(fictional)} route(s) that don't exist: "
        f"{fictional}"
    )
