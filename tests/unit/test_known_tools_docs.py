# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The two hand-written KNOWN_TOOLS tables list every registered mixin."""

import re
from pathlib import Path

import pytest

from gaia.agents.registry import KNOWN_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLES = ("CLAUDE.md", "docs/sdk/mixins/tool-mixins.mdx")


def _documented(text: str) -> dict[str, str]:
    """Map each table row's tool name to the rest of that row."""
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\| `([a-z_]+)` \| (.+)$", text, re.MULTILINE)
    }


@pytest.mark.parametrize("doc", TABLES)
def test_table_matches_known_tools(doc):
    rows = _documented((REPO_ROOT / doc).read_text(encoding="utf-8"))
    missing = sorted(set(KNOWN_TOOLS) - set(rows))
    assert not missing, f"{doc} has no KNOWN_TOOLS row for: {missing}"
    for name, (module, cls) in KNOWN_TOOLS.items():
        assert (
            module in rows[name] and cls in rows[name]
        ), f"{doc} row for {name!r} does not name {module}.{cls}"
