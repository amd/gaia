# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic checks for the published Mintlify documentation navigation."""

import json
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "docs"
NAVIGATION = DOCS_ROOT / "docs.json"
# These historical drafts are deliberately reachable only by URL.
NON_NAVIGATION_PAGES = {
    "plans/github-agent",
    "plans/typescript-sdk",
    "spec/agent-hub-restructure",
}


def _page_entries(value, *, in_pages=False):
    """Yield every page path in a nested Mintlify navigation value."""
    if isinstance(value, dict):
        if "pages" in value:
            yield from _page_entries(value["pages"], in_pages=True)
            return
        for child in value.values():
            yield from _page_entries(child, in_pages=in_pages)
        return
    if isinstance(value, list):
        for item in value:
            yield from _page_entries(item, in_pages=in_pages)
        return
    if isinstance(value, str) and in_pages:
        yield value


def _published_pages():
    """Return tracked MDX pages, excluding deliberate non-navigation content."""
    pages = set()
    for path in DOCS_ROOT.rglob("*.mdx"):
        page = path.relative_to(DOCS_ROOT).with_suffix("").as_posix()
        if page in NON_NAVIGATION_PAGES:
            continue
        pages.add(page)
    return pages


def _nav_entries():
    if not NAVIGATION.is_file():
        pytest.fail(f"Missing Mintlify navigation file: {NAVIGATION}")
    data = json.loads(NAVIGATION.read_text(encoding="utf-8"))
    return list(_page_entries(data.get("navigation", {})))


def _nav_pages():
    return set(_nav_entries())


def test_every_published_doc_page_is_in_navigation():
    """A published MDX page must be reachable from docs.json."""
    missing = sorted(_published_pages() - _nav_pages())
    assert not missing, "Published MDX pages missing from docs.json:\n  " + "\n  ".join(
        missing
    )


def test_every_navigation_page_exists():
    """Every docs.json page entry must resolve to a tracked MDX page."""
    missing = sorted(
        page for page in _nav_pages() if not (DOCS_ROOT / f"{page}.mdx").is_file()
    )
    assert (
        not missing
    ), "docs.json navigation entries without an MDX page:\n  " + "\n  ".join(missing)


def test_navigation_has_no_duplicate_pages():
    """A page should not appear twice in the published navigation."""
    counts = Counter(_nav_entries())
    duplicates = sorted(page for page, count in counts.items() if count > 1)
    assert not duplicates, "Duplicate docs.json navigation entries:\n  " + "\n  ".join(
        duplicates
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
