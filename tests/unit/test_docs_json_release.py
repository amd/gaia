# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""docs/docs.json must describe the version that ``src/gaia/version.py`` declares.

``publish.yml`` re-checks both of these against the tag and hard-fails the release
if they disagree, so a mismatch that merges here is only discovered when the tag
is already pushed. Run the same checks at PR time instead.
"""

import json
from pathlib import Path

import pytest

from gaia.version import LEMONADE_VERSION, __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_JSON = REPO_ROOT / "docs" / "docs.json"


@pytest.fixture(scope="module")
def docs_config() -> dict:
    return json.loads(DOCS_JSON.read_text(encoding="utf-8"))


def _navbar_labels(docs: dict) -> list[str]:
    return [link.get("label", "") for link in docs.get("navbar", {}).get("links", [])]


def _release_pages(docs: dict) -> list[str]:
    for tab in docs["navigation"]["tabs"]:
        if tab.get("tab") == "Releases":
            for group in tab.get("groups", []):
                if group.get("group") == "Release Notes":
                    return list(group.get("pages", []))
    raise AssertionError("docs.json has no Releases > 'Release Notes' group")


def test_navbar_label_matches_gaia_version(docs_config):
    labels = _navbar_labels(docs_config)
    assert any(f"v{__version__}" in label for label in labels), (
        f"docs.json navbar has no label containing 'v{__version__}' "
        f"(labels: {labels}). publish.yml fails the tag on this."
    )


def test_navbar_label_matches_lemonade_version(docs_config):
    labels = _navbar_labels(docs_config)
    assert any(LEMONADE_VERSION in label for label in labels), (
        f"docs.json navbar has no label containing Lemonade {LEMONADE_VERSION} "
        f"(labels: {labels})."
    )


def test_version_label_carries_both_versions(docs_config):
    """One label must carry both — two labels each half-right still ship wrong."""
    labels = _navbar_labels(docs_config)
    assert any(
        f"v{__version__}" in label and LEMONADE_VERSION in label for label in labels
    ), (
        f"no single docs.json navbar label reads "
        f"'v{__version__} · Lemonade {LEMONADE_VERSION}' (labels: {labels})"
    )


def test_releases_nav_lists_current_version(docs_config):
    page = f"releases/v{__version__}"
    pages = _release_pages(docs_config)
    assert page in pages, (
        f"docs.json Releases tab is missing '{page}'. "
        f"publish.yml fails the tag on this."
    )


def test_release_notes_file_exists_for_current_version():
    notes = REPO_ROOT / "docs" / "releases" / f"v{__version__}.mdx"
    assert notes.is_file(), (
        f"{notes.relative_to(REPO_ROOT)} does not exist, but docs.json links it "
        f"and publish.yml validates it."
    )


def _navigation_pages(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pages":
                for page in value:
                    if isinstance(page, str):
                        yield page
                    else:
                        yield from _navigation_pages(page)
            else:
                yield from _navigation_pages(value)
    elif isinstance(node, list):
        for value in node:
            yield from _navigation_pages(value)


def test_navigation_has_no_duplicate_pages(docs_config):
    from collections import Counter

    counts = Counter(_navigation_pages(docs_config["navigation"]))
    assert not {page: count for page, count in counts.items() if count > 1}


def test_navigation_pages_exist(docs_config):
    missing = [
        page
        for page in _navigation_pages(docs_config["navigation"])
        if not (REPO_ROOT / "docs" / f"{page}.mdx").is_file()
    ]
    assert not missing, f"Navigation references missing pages: {missing}"


def test_redirects_have_unique_sources_and_existing_destinations(docs_config):
    redirects = docs_config["redirects"]
    assert len({r["source"] for r in redirects}) == len(redirects)
    for redirect in redirects:
        destination = redirect["destination"].lstrip("/")
        assert (REPO_ROOT / "docs" / f"{destination}.mdx").is_file(), redirect


@pytest.mark.parametrize(
    "route",
    [
        "/roadmap",
        "/plans/agent-ui",
        "/plans/skill-format",
        "/playbooks/index",
        "/playbooks/custom-installer",
        "/playbooks/custom-installer/index",
        "/playbooks/chat-agent/part-1-getting-started",
        "/spec/component-status",
    ],
)
def test_retired_entry_points_redirect_to_maintained_docs(docs_config, route):
    assert route in {redirect["source"] for redirect in docs_config["redirects"]}
