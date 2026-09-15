# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression test for #3752: the flagship can't reliably select edit tools.

Reproduces the bug against the real ``FULL_CORE_TOOLS`` / ``FULL_BUNDLES``
config and the real :class:`ToolLoader` — only the embedder is faked, giving
exact control over which tools "semantically" outscore ``write_file`` /
``edit_file`` for a change-to-code request. This mirrors the issue's own
measurement: on 5 of 7 coding prompts, memory/skill/RAG tools filled the
per-turn budget and neither edit tool was present.

The fake embedder proves the CORE/bundle *assembly*, not the real embedding
model — a live Lemonade backend would be needed to prove real prompts score
this way, which is what the issue's own 7-prompt measurement already showed
and what the required ``gaia eval agent --category tool_selection`` run
verifies end to end. This test's job is narrower: given scores shaped like
the reported failure, does the current CORE/bundle config let ``write_file``
and ``edit_file`` through? Before the fix: no. After: yes, unconditionally,
because they are cap- and score-exempt CORE members.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia.agents.base.tool_loader import ToolLoader
from gaia_agent_chat.tool_bundles import FULL_BUNDLES, FULL_CORE_TOOLS

DIM = 768

# 15 non-core, non-file_edit-bundle tools that outscore write_file/edit_file
# on a "fix this bug" style query -- the exact failure mode #3752 measured:
# memory/skill/RAG/web/code-index tools win the per-turn budget over editing.
_COMPETITORS = [
    "search_indexed_chunks",
    "summarize_document",
    "dump_document",
    "evaluate_retrieval",
    "index_document",
    "index_directory",
    "list_indexed_documents",
    "rag_status",
    "add_watch_directory",
    "search_web",
    "fetch_page",
    "download_file",
    "index_codebase",
    "search_code_index",
    "list_skills",
]

_QUERY = "fix the lowercase z bug in parse_config and add a test"


def _make_embed_fn(tools: list[str], scores: dict[str, float], query: str):
    """One-hot tool axes; the query embeds to the given per-tool cosine score."""
    axis = {name: i for i, name in enumerate(tools)}

    def embed(text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        for name in tools:
            if text == f"{name}: does {name}":
                v[axis[name]] = 1.0
                return v
        if text == query:
            for name, score in scores.items():
                v[axis[name]] = score
            return v
        raise AssertionError(f"unexpected text embedded: {text!r}")

    return embed


def _registry(tools: list[str]) -> dict[str, dict]:
    return {name: {"description": f"does {name}"} for name in tools}


def _tools_and_scores() -> tuple[list[str], dict[str, float]]:
    tools = sorted(FULL_CORE_TOOLS | {"write_file", "edit_file"}) + _COMPETITORS
    scores = {"read_file": 0.05, "write_file": 0.10, "edit_file": 0.09}
    scores.update(
        {name: round(0.90 - 0.05 * i, 2) for i, name in enumerate(_COMPETITORS)}
    )
    return tools, scores


def _select(query: str) -> list[str]:
    """Select tools for a coding request against the real full-profile config.

    ``read_file``/``write_file``/``edit_file`` score low (below the 0.20
    threshold) -- unmatched and, pre-fix, not even bundle-pulled since
    ``read_file`` doesn't match either. The 15 competitors score 0.20-0.90,
    filling every dynamic slot the current (pre-fix) 11-tool CORE leaves under
    the real 26-tool cap.
    """
    tools, scores = _tools_and_scores()
    embed = _make_embed_fn(tools, scores, query)
    loader = ToolLoader(
        FULL_CORE_TOOLS, FULL_BUNDLES, embed, threshold=0.20, max_tools=26
    )
    result = loader.select(query, _registry(tools))
    assert result is not None
    return result


def test_edit_tools_are_selected_for_a_coding_request():
    """The actual defect: write_file/edit_file must survive real-world scoring.

    Fails against unpatched ``FULL_CORE_TOOLS`` (write_file/edit_file are only
    in the ``file_edit`` bundle, unmatched here and never bundle-pulled since
    read_file also scores below threshold) -- passes once they are promoted
    into CORE, where score and cap are irrelevant.
    """
    loaded = _select(_QUERY)
    assert "write_file" in loaded, (
        "write_file missing from selection on a coding request -- the exact "
        "failure #3752 measured (2 of 7 prompts got an edit tool)"
    )
    assert "edit_file" in loaded, "edit_file missing from selection"


def test_edit_tools_are_now_core_cap_and_score_exempt():
    """Pin the chosen fix shape: promotion into FULL_CORE_TOOLS, not ranking."""
    assert "write_file" in FULL_CORE_TOOLS
    assert "edit_file" in FULL_CORE_TOOLS


@pytest.mark.parametrize(
    "query",
    [
        "rename doIt to processRequest across the codebase",
        "make the sort in analyze.py O(n log n) instead of O(n^2)",
        "the test suite is failing, find out why and fix it",
    ],
)
def test_edit_tools_selected_across_held_out_coding_prompts(query):
    """Held-out prompts beyond the issue's own seven, same competitor shaping."""
    loaded = _select(query)
    assert "write_file" in loaded and "edit_file" in loaded
