# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression test for #3752: the flagship can't reliably select edit tools.

Reproduces the bug against the real ``FULL_CORE_TOOLS`` / ``FULL_BUNDLES``
config and the real :class:`ToolLoader` — only the embedder is faked, giving
exact control over how ``write_file`` / ``edit_file`` score against a
change-to-code request. Two distinct pre-fix failure modes are covered, both
observed in the issue's own 7-prompt measurement:

1. the edit tools score below the match threshold — never even candidates,
   regardless of the cap;
2. the edit tools score above threshold but lower than enough competing
   tools to exhaust the per-turn cap — matched, then skipped/evicted.

The fake embedder proves the CORE/bundle *assembly*, not the real embedding
model — a live Lemonade backend would be needed to prove real prompts score
this way, which is what the issue's own measurement already showed and what
the required ``gaia eval agent --category tool_selection`` run verifies end
to end. This suite's job is narrower: given scores shaped like each reported
failure mode, does the current CORE/bundle config let ``write_file`` and
``edit_file`` through? Before the fix: no, in either mode. After: yes,
unconditionally, because they are cap- and score-exempt CORE members.
"""

from __future__ import annotations

import numpy as np

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
    """One-hot tool axes; *query* embeds to the given per-tool cosine score."""
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


def _select(scores: dict[str, float]) -> list[str]:
    tools = sorted(FULL_CORE_TOOLS | {"write_file", "edit_file"}) + _COMPETITORS
    embed = _make_embed_fn(tools, scores, _QUERY)
    loader = ToolLoader(
        FULL_CORE_TOOLS, FULL_BUNDLES, embed, threshold=0.20, max_tools=26
    )
    result = loader.select(_QUERY, _registry(tools))
    assert result is not None
    return result


def test_edit_tools_are_selected_when_unmatched_by_score():
    """Failure mode 1: below threshold, never even a semantic candidate.

    ``read_file``/``write_file``/``edit_file`` all score below the 0.20
    threshold, so ``file_edit`` is never bundle-pulled either. The 15
    competitors score 0.20-0.90, filling every dynamic slot the current
    (pre-fix) 11-tool CORE leaves under the real 26-tool cap.

    Fails against unpatched ``FULL_CORE_TOOLS`` — passes once write_file/
    edit_file are promoted into CORE, where score is irrelevant.
    """
    scores = {"read_file": 0.05, "write_file": 0.10, "edit_file": 0.09}
    scores.update(
        {name: round(0.90 - 0.05 * i, 2) for i, name in enumerate(_COMPETITORS)}
    )
    loaded = _select(scores)
    assert "write_file" in loaded, (
        "write_file missing from selection on a coding request -- the exact "
        "failure #3752 measured (2 of 7 prompts got an edit tool)"
    )
    assert "edit_file" in loaded, "edit_file missing from selection"


def test_edit_tools_are_selected_when_outranked_at_cap():
    """Failure mode 2: matched, but ranked below the tools that fill the cap.

    ``write_file``/``edit_file`` score 0.21/0.22 -- above threshold, so they
    ARE semantic candidates -- but every one of the 15 competitors scores
    higher (0.25-0.95), so they take all the dynamic slots and the edit tools
    are skipped at cap.

    Fails against unpatched ``FULL_CORE_TOOLS`` (matched but out-ranked) --
    passes once promoted into CORE, where the cap doesn't apply.
    """
    scores = {"read_file": 0.05, "write_file": 0.22, "edit_file": 0.21}
    scores.update(
        {name: round(0.95 - 0.05 * i, 2) for i, name in enumerate(_COMPETITORS)}
    )
    loaded = _select(scores)
    assert "write_file" in loaded, "write_file lost the cap to lower-priority tools"
    assert "edit_file" in loaded, "edit_file lost the cap to lower-priority tools"


def test_edit_tools_are_core():
    """Not a behavioural test -- pins the config shape the two tests above rely on."""
    assert "write_file" in FULL_CORE_TOOLS
    assert "edit_file" in FULL_CORE_TOOLS
