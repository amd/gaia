# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship changelog's Unreleased section names recent user-visible fixes."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = REPO_ROOT / "hub/agents/gaia/npm/CHANGELOG.md"


@pytest.fixture(scope="module")
def unreleased():
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index("## [Unreleased]")
    end = text.find("\n## [", start + 1)
    assert end != -1, "the Unreleased section must end at the next version"
    return text[start:end]


@pytest.mark.parametrize(
    "headline",
    [
        "MCP servers added by hand stay on for GAIA after an upgrade",
        "a plain one-sentence request to remember one fact",
        "A file an answer only cites is not a claimed save",
        "Made-up data you ask for is not flagged as unsourced",
    ],
)
def test_unreleased_names_recent_changes(unreleased, headline):
    assert headline in unreleased


def test_unreleased_drops_stale_small_model_wording(unreleased):
    assert "a memory save that never happened is named" not in unreleased
