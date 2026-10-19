# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Markdown in all its spellings can be added to the document library."""

import re
from pathlib import Path

from gaia.ui.utils import ALLOWED_EXTENSIONS, TEXT_EXTENSIONS

MARKDOWN = {".md", ".mdx", ".markdown", ".rst"}
WEBUI_LIST = (
    Path(__file__).resolve().parents[4]
    / "src/gaia/apps/webui/src/components/UnsupportedFeature.tsx"
)


def test_markdown_spellings_are_accepted_and_previewable():
    assert MARKDOWN <= ALLOWED_EXTENSIONS
    assert MARKDOWN <= TEXT_EXTENSIONS


def test_the_agent_ui_accepts_the_same_extensions_as_the_server():
    source = WEBUI_LIST.read_text(encoding="utf-8")
    block = re.search(r"SUPPORTED_EXTENSIONS = new Set\(\[(.*?)\]\)", source, re.S)
    assert block, f"SUPPORTED_EXTENSIONS not found in {WEBUI_LIST}"
    webui = set(re.findall(r"'(\.[a-z0-9]+)'", block.group(1)))
    assert webui == set(ALLOWED_EXTENSIONS)
