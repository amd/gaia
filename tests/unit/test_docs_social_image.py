# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The docs' link-preview image must resolve on the live site and be small enough to fetch.

``og:image`` / ``twitter:image`` in ``docs/docs.json`` are what chat apps and social
sites show when someone shares a docs link. Mintlify serves the docs tab under
``/docs/``, so a URL without that prefix 404s, and an image of several MB is
dropped by preview services that cap the fetch (the card was once 10 MB).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

DOCS = Path(__file__).resolve().parents[2] / "docs"
SITE_PREFIX = "https://amd-gaia.ai/docs/"
MAX_BYTES = 1_000_000
MIN_SIDE = 600


def _image_urls() -> dict[str, str]:
    meta = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))["metadata"]
    urls = {k: v for k, v in meta.items() if k in ("og:image", "twitter:image")}
    assert urls, "docs.json declares no og:image / twitter:image"
    return urls


@pytest.mark.parametrize("key, url", sorted(_image_urls().items()))
def test_social_image_resolves_to_a_small_docs_asset(key, url):
    assert url.startswith(
        SITE_PREFIX
    ), f"{key} {url!r} must start with {SITE_PREFIX!r}; the docs site 404s without /docs/"
    asset = DOCS / url[len(SITE_PREFIX) :]
    assert asset.is_file(), f"{key} points at {asset}, which does not exist"
    size = asset.stat().st_size
    assert size < MAX_BYTES, f"{asset.name} is {size:,} bytes; keep it under 1 MB"
    with Image.open(asset) as im:
        assert (
            min(im.size) >= MIN_SIDE
        ), f"{asset.name} is {im.size}; too small to preview"
