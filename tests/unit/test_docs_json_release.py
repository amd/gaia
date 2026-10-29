# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""docs/docs.json must describe the newest release it lists.

``main`` declares the next version in ``src/gaia/version.py`` as soon as a final
ships, but its notes join the Releases tab only in that release's PR. So the
release these checks follow is the newest ``releases/vX.Y.Z`` page listed, which
is never ahead of ``__version__``. ``publish.yml`` re-checks the tag against
the same files and hard-fails the release if they disagree, so run the checks at
PR time instead.
"""

import json
import sys
from pathlib import Path

import pytest

from gaia.version import LEMONADE_VERSION, __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_JSON = REPO_ROOT / "docs" / "docs.json"
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_versions as rv  # noqa: E402


@pytest.fixture(scope="module")
def docs_config() -> dict:
    return json.loads(DOCS_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def released() -> str:
    return rv.released_version(REPO_ROOT)


def _navbar_labels(docs: dict) -> list[str]:
    return [link.get("label", "") for link in docs.get("navbar", {}).get("links", [])]


def test_listed_release_is_not_ahead_of_the_declared_version(released):
    base = __version__.split("rc")[0]
    assert rv.version_key(released) <= rv.version_key(base), (
        f"docs.json lists releases/v{released}, but src/gaia/version.py declares "
        f"{__version__}."
    )


def test_navbar_label_carries_the_release_and_lemonade(docs_config, released):
    """One label must carry both; two labels each half-right still ship wrong."""
    labels = _navbar_labels(docs_config)
    assert any(
        f"v{released} " in label and LEMONADE_VERSION in label for label in labels
    ), (
        f"no single docs.json navbar label reads "
        f"'v{released} · Lemonade {LEMONADE_VERSION}' (labels: {labels}). "
        "Run: python util/release_versions.py --stamp"
    )


def test_navbar_does_not_name_an_unlisted_version(docs_config, released):
    if __version__ == released:
        pytest.skip("main is cutting the release it lists")
    labels = _navbar_labels(docs_config)
    assert not any(f"v{__version__} " in label for label in labels), (
        f"the navbar names v{__version__}, whose notes docs.json doesn't list yet "
        f"(newest listed: v{released})."
    )


def test_release_notes_file_exists_for_the_listed_release(released):
    notes = REPO_ROOT / "docs" / "releases" / f"v{released}.mdx"
    assert notes.is_file(), (
        f"{notes.relative_to(REPO_ROOT)} does not exist, but docs.json lists it "
        f"and publish.yml validates it."
    )
