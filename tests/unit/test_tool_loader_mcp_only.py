# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The ToolLoader's MCP-only mode (#1972).

A profile with no CORE or bundles of its own still gates MCP tools: every tool
that is not from an MCP server is core, so the agent's own tools are never held
back. That is the regression guard this file exists for: turning the loader on
for such a profile must not cost a single static tool.
"""

from __future__ import annotations

import numpy as np
import pytest

from gaia.agents.base.tool_loader import (
    JUMP_MARGIN,
    ToolBundle,
    ToolLoader,
    is_mcp_tool,
)

DIM = 64
STATIC = ["read_file", "search_web", "list_dir", "search_tools"]
MCP = ["mcp_files_move_file", "mcp_calendar_create_event"]


def _registry(static=STATIC, mcp=MCP) -> dict[str, dict]:
    reg = {name: {"description": f"does {name}"} for name in static}
    for name in mcp:
        reg[name] = {"description": f"[MCP:x] does {name}", "_mcp_server": "x"}
    return reg


def _embedder(scores: dict[str, dict[str, float]], names=STATIC + MCP):
    """One-hot tool axes; a query embeds to the scores given for it."""
    axis = {name: i for i, name in enumerate(names)}

    def embed(text: str) -> np.ndarray:
        vec = np.zeros(DIM, dtype=np.float32)
        name = text.split(":", 1)[0]
        if name in axis and text != name and text not in scores:
            vec[axis[name]] = 1.0
            return vec
        for tool, score in scores.get(text, {}).items():
            vec[axis[tool]] = score
        return vec

    return embed


def _loader(scores=None, **kwargs) -> ToolLoader:
    return ToolLoader((), (), _embedder(scores or {}), mcp_only=True, **kwargs)


def test_is_mcp_tool_reads_the_server_tag():
    assert is_mcp_tool({"_mcp_server": "files"})
    assert not is_mcp_tool({"description": "[MCP:files] looks like one"})
    assert not is_mcp_tool({"_mcp_server": None})


@pytest.mark.parametrize(
    "query, scores",
    [
        ("hello", {}),
        ("read it", {"read_file": 0.9}),
        # A static tool that out-scores everything changes nothing: it is listed.
        ("search", {"search_web": 0.99, "mcp_files_move_file": 0.1}),
    ],
)
def test_every_static_tool_is_listed_whatever_the_query(query, scores):
    loader = _loader({query: scores})
    offered = loader.select(query, _registry())
    assert offered == STATIC
    assert loader.jumped is False


def test_mcp_tools_wait_until_one_clearly_wins():
    query = "move the draft"
    loader = _loader({query: {"mcp_files_move_file": 0.8, "read_file": 0.1}})
    assert loader.select(query, _registry()) == STATIC + MCP
    assert loader.jumped is True


def test_an_mcp_tool_inside_the_margin_of_a_static_one_does_not_jump():
    query = "read or move"
    scores = {"mcp_files_move_file": 0.8, "read_file": 0.8 - JUMP_MARGIN + 0.001}
    loader = _loader({query: scores})
    assert loader.select(query, _registry()) == STATIC
    assert loader.jumped is False


def test_a_requested_mcp_tool_jumps_without_embedding():
    def broken(_text):
        raise AssertionError("must not embed")

    loader = ToolLoader((), (), broken, mcp_only=True)
    offered = loader.select("x", _registry(), requested_tools=["mcp_files_move_file"])
    assert offered == STATIC + MCP


def test_the_core_follows_the_registry_not_a_frozen_list():
    loader = _loader()
    later = _registry(static=STATIC + ["new_static_tool"])
    assert loader.offered(later) == STATIC + ["new_static_tool"]


def test_a_skill_can_admit_an_mcp_tool_without_a_jump():
    loader = _loader()
    offered = loader.admit_tools(["mcp_calendar_create_event"], _registry())
    assert offered == STATIC + ["mcp_calendar_create_event"]
    assert loader.jumped is False


def test_a_server_that_leaves_mid_session_drops_out_cleanly():
    query = "move the draft"
    loader = _loader({query: {"mcp_files_move_file": 0.8}})
    loader.select(query, _registry())
    gone = _registry(mcp=["mcp_calendar_create_event"])
    assert loader.offered(gone) == STATIC + ["mcp_calendar_create_event"]
    admitted = _loader()
    admitted.admit_tools(["mcp_files_move_file"], _registry())
    assert admitted.offered(gone) == STATIC


def test_no_mcp_tools_means_every_tool_is_listed():
    loader = _loader({"q": {}})
    assert loader.select("q", _registry(mcp=[])) == STATIC
    assert loader.jumped is False


def test_an_embedder_that_dies_hands_back_the_full_registry():
    def broken(_text):
        raise ConnectionError("embedder down")

    loader = ToolLoader((), (), broken, mcp_only=True)
    assert loader.select("move the draft", _registry()) is None
    assert loader.session_disabled is True


def test_validation_has_nothing_to_check():
    _loader().validate_registry(_registry())
    _loader().validate_registry({})


@pytest.mark.parametrize(
    "core, bundles",
    [(("read_file",), ()), ((), (ToolBundle("b", frozenset({"read_file"})),))],
)
def test_an_mcp_only_loader_refuses_a_core_or_bundles(core, bundles):
    with pytest.raises(ValueError, match="MCP-only"):
        ToolLoader(core, bundles, _embedder({}), mcp_only=True)


def test_as_mcp_only_keeps_the_embedder_and_its_cache():
    profile = ToolLoader(frozenset({"read_file"}), [], _embedder({}), threshold=0.5)
    profile.warm(_registry())
    twin = profile.as_mcp_only()
    assert twin.mcp_only is True and twin._threshold == 0.5
    assert twin._embed_cache is profile._embed_cache
    assert twin.offered(_registry()) == STATIC


def test_a_profile_loader_ignores_the_mcp_tag():
    """The doc and full profiles keep their CORE exactly, MCP-tagged or not."""
    registry = _registry()
    loader = ToolLoader(
        frozenset({"read_file", "mcp_files_move_file"}), [], _embedder({})
    )
    assert loader.mcp_only is False
    assert loader.offered(registry) == ["read_file", "mcp_files_move_file"]
