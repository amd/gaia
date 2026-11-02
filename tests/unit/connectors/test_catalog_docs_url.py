# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression test for connector ``docs_url`` correctness (issue #1058)."""

from pathlib import Path
from urllib.parse import urlparse

import gaia.connectors.catalog  # noqa: F401  # populate REGISTRY
from gaia.connectors.registry import REGISTRY

_DOCS_CONNECTORS_DIR = Path(__file__).resolve().parents[3] / "docs" / "connectors"


def test_registry_is_populated():
    """Guard against false-negative if registry is unexpectedly empty.

    If pytest ordering or an autouse fixture clears ``REGISTRY`` before this
    test runs, the URL test below would pass vacuously. Fail loud instead.
    """
    specs = list(REGISTRY.all())
    assert len(specs) >= 2, (
        f"REGISTRY has {len(specs)} specs - expected at least 2 "
        "(google + mcp-github). Did test ordering or a fixture clear it?"
    )


def test_amd_gaia_docs_url_points_at_existing_mdx():
    """Every ``amd-gaia.ai`` connector ``docs_url`` must resolve to a real
    ``docs/connectors/<slug>.mdx`` file under the Documentation tab path.

    Verified live during plan reflection (2026-05-15):
    ``https://amd-gaia.ai/connectors/google`` -> 404
    ``https://amd-gaia.ai/docs/connectors/google`` -> 200
    """
    offenders = []
    for spec in REGISTRY.all():
        url = spec.docs_url
        if not url or "amd-gaia.ai" not in url:
            continue
        path = urlparse(url).path  # e.g. "/docs/connectors/google"
        if not path.startswith("/docs/connectors/"):
            offenders.append(
                f"{spec.id}: docs_url path {path!r} - "
                "expected /docs/connectors/<slug>"
            )
            continue
        slug = path[len("/docs/connectors/") :].rstrip("/")
        if not (_DOCS_CONNECTORS_DIR / f"{slug}.mdx").exists():
            offenders.append(
                f"{spec.id}: docs_url points at /docs/connectors/{slug}, "
                f"but docs/connectors/{slug}.mdx does not exist"
            )
    assert (
        not offenders
    ), "Connector docs_url offenders (see issue #1058):\n  " + "\n  ".join(offenders)


def test_every_catalog_connector_has_a_docs_page():
    """Every shipped connector needs a ``docs_url`` ("Learn more" link) that
    resolves to a page, so a new catalog entry can't ship undocumented (#937)."""
    missing = [spec.id for spec in REGISTRY.all() if not spec.docs_url]
    assert not missing, f"Connectors with no docs_url: {missing}"


def test_connector_pages_are_in_navigation():
    """A docs/connectors page absent from docs.json 404s on the site."""
    docs_json = Path(__file__).resolve().parents[3] / "docs" / "docs.json"
    nav = docs_json.read_text(encoding="utf-8")
    for mdx in _DOCS_CONNECTORS_DIR.glob("*.mdx"):
        assert f'"connectors/{mdx.stem}"' in nav, f"{mdx.name} missing from docs.json"
