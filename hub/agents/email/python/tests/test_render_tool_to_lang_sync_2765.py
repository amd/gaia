# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``_RENDER_TOOL_TO_LANG`` cross-package drift guard (#2765).

The tool→card-key map exists in TWO places — ``gaia.ui.sse_handler.
SSEOutputHandler._RENDER_TOOL_TO_LANG`` (Agent UI / REST-facing) and
``gaia_agent_email.sse_translation._RENDER_TOOL_TO_LANG`` (the sidecar's
dependency-light canonical translator, which duplicates rather than imports
the first to avoid pulling in the ``gaia.ui`` import chain) — with a
source comment on both sides saying "keep both in sync". A comment is a
hope, not a guarantee: this test converts it into a failing assertion the
day one side is updated without the other, which is exactly the drift mode
that would silently break card rendering on one surface while leaving the
other correct.

Kept in its own file (not ``test_sse_translation.py``, which is explicitly
"dependency-light: no ... gaia.ui needed") so that file's stated scope
stays true — this one alone carries the cross-package import.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")
pytest.importorskip("gaia.ui.sse_handler")

from gaia_agent_email.sse_translation import (  # noqa: E402
    _RENDER_TOOL_TO_LANG as _SIDECAR_MAP,
)

from gaia.ui.sse_handler import SSEOutputHandler  # noqa: E402


class TestRenderToolToLangMapsStayInSync:
    def test_render_tool_to_lang_maps_differ_only_by_the_pre_scan_card(self):
        """The two maps are deliberately no longer identical: the TUI drops
        the pre-scan card so the triage reply is its single inbox view, while
        the Agent UI — a different surface, not retested here — keeps it.
        Pinned as an explicit, single-key difference so any OTHER drift still
        fails."""
        assert set(SSEOutputHandler._RENDER_TOOL_TO_LANG) - set(_SIDECAR_MAP) == {
            "pre_scan_inbox"
        }
        assert not set(_SIDECAR_MAP) - set(SSEOutputHandler._RENDER_TOOL_TO_LANG)
        for tool, lang in _SIDECAR_MAP.items():
            assert SSEOutputHandler._RENDER_TOOL_TO_LANG[tool] == lang

    def test_get_thread_is_registered_as_a_table_card_on_both_sides(self):
        """#2765: get_thread must render a card on EITHER surface reaching
        it -- the sidecar (TUI/REST) map and the Agent UI map both need the
        entry, not just one."""
        assert _SIDECAR_MAP["get_thread"] == "table"
        assert SSEOutputHandler._RENDER_TOOL_TO_LANG["get_thread"] == "table"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
