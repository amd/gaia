# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Keep the published documentation tree and Mintlify navigation in sync."""

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"
NAVIGATION_FILE = DOCS_ROOT / "docs.json"

# Roadmap and release pages are intentionally kept outside the main navigation
# check: they are historical/planning material and may be published without a
# user-facing navigation entry.
NAVIGATION_EXCLUDED_DIRECTORIES = frozenset({"plans", "releases"})


def _navigation_pages(value, *, page_list=False):
    """Yield page paths from nested Mintlify navigation groups."""
    if isinstance(value, list):
        for item in value:
            yield from _navigation_pages(item, page_list=page_list)
    elif isinstance(value, dict):
        if "pages" in value:
            yield from _navigation_pages(value["pages"], page_list=True)
        for key in ("tabs", "groups"):
            if key in value:
                yield from _navigation_pages(value[key])
    elif page_list and isinstance(value, str):
        yield value


def _doc_pages():
    return {
        path.relative_to(DOCS_ROOT).with_suffix("").as_posix()
        for path in DOCS_ROOT.rglob("*.mdx")
    }


def test_mintlify_navigation_has_no_dangling_or_unpublished_pages():
    """Every published doc is navigable and every nav page exists on disk."""
    config = json.loads(NAVIGATION_FILE.read_text(encoding="utf-8"))
    nav_pages = set(_navigation_pages(config["navigation"]))
    doc_pages = _doc_pages()
    published_pages = {
        page
        for page in doc_pages
        if not set(Path(page).parts) & NAVIGATION_EXCLUDED_DIRECTORIES
    }

    dangling = sorted(nav_pages - doc_pages)
    orphaned = sorted(published_pages - nav_pages)

    assert not dangling, f"Navigation points to missing docs: {dangling}"
    assert not orphaned, f"Published docs missing from navigation: {orphaned}"


def test_navigation_page_paths_are_unique():
    """Duplicate entries make navigation maintenance and audits ambiguous."""
    config = json.loads(NAVIGATION_FILE.read_text(encoding="utf-8"))
    pages = list(_navigation_pages(config["navigation"]))

    assert len(pages) == len(set(pages)), "Duplicate page paths in docs/docs.json"