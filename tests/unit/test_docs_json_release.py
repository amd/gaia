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
