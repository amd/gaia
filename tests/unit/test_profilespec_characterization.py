# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Characterization contract for the ChatAgent ProfileSpec refactor (#2323).

``ChatAgent._get_system_prompt()`` and ``ChatAgent._register_tools()`` currently
branch on ``prompt_profile`` via an ``if/elif`` chain. The refactor replaces that
chain with a declarative ``ProfileSpec`` table. This module pins the exact
byte-for-byte system prompt and the exact registered tool-name set for 15
fixture rows spanning every profile / doc-state / dynamic-tools / config-toggle
axis (see ``ROWS`` below), so the refactor can be verified 100%
behavior-preserving instead of "looks right".

Goldens live under ``tests/fixtures/profilespec_characterization/`` and were
CAPTURED by actually running this module's harness against current-main code —
never hand-typed. If a golden is wrong, this whole contract is corrupted; do
not "fix" a failing comparison by editing the JSON unless you have re-verified
the new golden is itself a real harness capture.

The two ``test_*_is_not_yet_declarative`` smoke tests are EXPECTED TO FAIL on
current main (the if/elif chain is still there) — that failure is the point.
They flip to green once the ProfileSpec table (Increment 2) lands. Do not
weaken them to a softer check to make them pass early.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

# ChatAgent ships as the standalone gaia-agent-chat wheel (#1102); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia.eval.tool_cost import (  # noqa: E402
    _build_skeleton_tool_loader,
    _ensure_optional_deps_stubbed,
    _isolated_registry,
)

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "profilespec_characterization"
)

# The only profile whose `_register_tools()` early-returns BEFORE reaching the
# VLM registration call (agent.py's "chat" branch, ~line 1184). VLM absence
# there is intentional current behavior, not a stripped-CI-image artifact —
# every other profile falls through to `init_vlm()` unconditionally.
_PROFILES_WITHOUT_VLM = frozenset({"chat"})


@contextlib.contextmanager
def chat_agent_build_context(
    profile: str,
    *,
    dynamic_tools: bool = False,
    model_id: str = "Gemma-4-E4B-it-GGUF",
    rag_indexed_files: Optional[List[str]] = None,
    library_documents: Optional[List[str]] = None,
    enable_filesystem: bool = False,
    enable_scratchpad: bool = False,
    enable_browser: bool = False,
    enable_sd_tools: bool = False,
    cwd: Optional[Path] = None,
):
    """Yield an unregistered ChatAgent skeleton with every nondeterminism axis pinned.

    Mirrors ``gaia.eval.tool_cost.build_doc_agent_skeleton``'s skeleton
    construction (bypassed ``Agent.__init__``, isolated tool registry,
    deterministic npx/Perplexity gating) but adds the per-row control the
    fixture matrix needs: rag/indexed-doc state, library_documents, model_id
    (tool-calling vs. not), the dynamic tool loader, and the ChatAgentConfig
    enable_* toggles.

    Deliberately does NOT reproduce ``build_doc_agent_skeleton``'s final
    ``agent._instance_tools = dict(tools_mod._TOOL_REGISTRY)`` overwrite: that
    line clobbers the ``_chat_exclude`` pop that ``_register_tools()`` applies
    for the file/data/full profiles (agent.py ~1846-1857), which would
    silently characterize the WRONG (un-excluded) tool set as the golden. Tool
    names must be read via ``agent._tools_registry`` (the property
    ``_register_tools()`` itself leaves populated — falls back to the global
    registry for the "chat" profile, which returns before calling
    ``_snapshot_tools()``), never via a raw dict copy of the global registry.
    """
    prev_cwd = os.getcwd()
    if cwd is not None:
        os.chdir(cwd)
    stubbed = _ensure_optional_deps_stubbed()
    try:
        from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

        cfg = ChatAgentConfig(
            rag_documents=[],
            streaming=False,
            silent_mode=True,
            prompt_profile=profile,
            enable_filesystem=enable_filesystem,
            enable_scratchpad=enable_scratchpad,
            enable_browser=enable_browser,
            enable_sd_tools=enable_sd_tools,
        )

        with _isolated_registry():
            stack = contextlib.ExitStack()
            stack.enter_context(
                patch("gaia.agents.base.agent.Agent.__init__", return_value=None)
            )
            # Axis: npx / Perplexity gating for the external-tools conditional.
            stack.enter_context(patch("shutil.which", return_value=None))
            scrubbed = {
                k: v for k, v in os.environ.items() if k != "PERPLEXITY_API_KEY"
            }
            stack.enter_context(patch.dict(os.environ, scrubbed, clear=True))
            # Axis: platform/OS block — fixed constants, never this host's.
            stack.enter_context(patch("platform.system", return_value="Linux"))
            stack.enter_context(
                patch("platform.version", return_value="#1 SMP FIXED-KERNEL-STRING")
            )
            stack.enter_context(patch("platform.machine", return_value="x86_64"))
            stack.enter_context(
                patch.object(Path, "home", return_value=Path("/fake/home"))
            )
            with stack:
                agent = ChatAgent.__new__(ChatAgent)
                agent.config = cfg
                agent._instance_tools = None
                agent.model_id = model_id
                agent._memory_store = MagicMock()  # non-None -> memory tools register
                agent.rag = MagicMock(indexed_files=set(rag_indexed_files or []))
                agent.library_documents = list(library_documents or [])
                agent.console = MagicMock()
                # Satisfy ChatAgent.__del__ so GC of the skeleton stays quiet.
                agent.observers = []
                agent._web_client = None
                agent._fs_index = None
                agent._scratchpad = None
                # Axis: MCP — the skeleton never runs the real __init__ that
                # sets this, and the /fake/home patch above provably has no
                # ~/.gaia/mcp_servers.json, so no dev box's real MCP config
                # can leak into a golden regardless of this assignment.
                agent._mcp_manager = None
                assert agent._mcp_manager is None
                agent.tool_loader = _build_skeleton_tool_loader(dynamic_tools)
                yield agent
    finally:
        for mod in stubbed:
            sys.modules.pop(mod, None)
        if cwd is not None:
            os.chdir(prev_cwd)


def build_agent_for_row(profile: str, **kwargs) -> Tuple[str, List[str]]:
    """Build one fixture row's agent, register tools, and capture (prompt, tools)."""
    with chat_agent_build_context(profile, **kwargs) as agent:
        agent._register_tools()
        tools = sorted(agent._tools_registry.keys())
        prompt = agent._get_system_prompt()

    if profile in _PROFILES_WITHOUT_VLM:
        assert "analyze_image" not in tools, (
            f"profile={profile!r} is not supposed to reach VLM registration "
            "(the 'chat' branch returns early in _register_tools()) — got VLM "
            "tools unexpectedly; the branching logic changed."
        )
    else:
        assert "analyze_image" in tools, (
            f"profile={profile!r}: VLM tools missing from the captured tool "
            "set. init_vlm() is wrapped in a bare try/except in "
            "_register_tools() (agent.py ~1372-1380) — a stripped CI image or "
            "a missing VLM dependency would silently produce a VLM-less golden "
            "that diverges from a full dev-machine golden. Fix the "
            "environment; don't weaken this check."
        )
    return prompt, tools


# ── Fixture matrix (15 rows) — see class docstring / #2323 issue body ──────
ROWS: List[Tuple[str, Dict]] = [
    ("row01_chat_no_docs_baseline", dict(profile="chat")),
    (
        "row02_chat_no_docs_enable_filesystem",
        dict(profile="chat", enable_filesystem=True),
    ),
    (
        "row03_chat_no_docs_enable_scratchpad",
        dict(profile="chat", enable_scratchpad=True),
    ),
    (
        "row04_chat_no_docs_enable_browser",
        dict(profile="chat", enable_browser=True),
    ),
    ("row05_doc_no_docs_loader_off", dict(profile="doc")),
    (
        "row06_doc_library_only",
        dict(
            profile="doc",
            library_documents=[
                "/fake/library/handbook.pdf",
                "/fake/library/report.txt",
            ],
        ),
    ),
    (
        "row07_doc_indexed_n1",
        dict(profile="doc", rag_indexed_files=["/fake/indexed/report.pdf"]),
    ),
    (
        "row08_doc_indexed_n2",
        dict(
            profile="doc",
            rag_indexed_files=[
                "/fake/indexed/report.pdf",
                "/fake/indexed/handbook.pdf",
            ],
        ),
    ),
    (
        "row09_doc_loader_on_tool_calling_model",
        dict(profile="doc", dynamic_tools=True, model_id="Gemma-4-E4B-it-GGUF"),
    ),
    (
        "row10_doc_loader_on_non_tool_calling_model",
        # gemma4-it-e2b-FLM: MODELS["gemma-4-e2b"], tool_calling=False
        # (lemonade_client.py) — the FLM/NPU server 500s on a native `tools=`
        # payload, so is_tool_calling_model() is False for this id.
        dict(profile="doc", dynamic_tools=True, model_id="gemma4-it-e2b-FLM"),
    ),
    ("row11_file_no_docs_baseline", dict(profile="file")),
    (
        "row12_full_indexed_n1",
        dict(profile="full", rag_indexed_files=["/fake/indexed/report.pdf"]),
    ),
    (
        "row13_full_no_docs_enable_sd_tools",
        dict(profile="full", enable_sd_tools=True),
    ),
    ("row14_data_no_docs_baseline", dict(profile="data")),
    ("row15_web_no_docs_baseline", dict(profile="web")),
]

_ROW_IDS = [row_id for row_id, _ in ROWS]


def _load_golden(row_id: str) -> Dict:
    path = FIXTURE_DIR / f"{row_id}.json"
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


@pytest.mark.parametrize("row_id,kwargs", ROWS, ids=_ROW_IDS)
def test_profile_prompt_and_tools_match_golden(row_id, kwargs, tmp_path):
    """The composed prompt + registered tool set must match the captured golden.

    This is the behavior-preservation contract for the ProfileSpec refactor:
    each increment must keep every row's (prompt, tools) pair byte-for-byte /
    set-for-set identical to what current-main produces today.
    """
    build_kwargs = dict(kwargs)
    if build_kwargs.get("enable_sd_tools"):
        # init_sd() mkdir's a *relative* ".gaia/cache/sd/images" — redirect
        # into a throwaway dir so this test never litters the real worktree.
        build_kwargs["cwd"] = tmp_path

    prompt, tools = build_agent_for_row(**build_kwargs)
    golden = _load_golden(row_id)

    assert prompt == golden["prompt"], (
        f"{row_id}: system prompt drifted from the captured golden. If this "
        "drift is an INTENTIONAL part of the ProfileSpec refactor, it is not "
        "behavior-preserving and needs sign-off, not a golden update."
    )
    assert tools == golden["tools"], (
        f"{row_id}: registered tool set drifted from the captured golden. If "
        "this drift is INTENTIONAL, it is not behavior-preserving and needs "
        "sign-off, not a golden update."
    )


# ── Structural smoke tests — MUST currently fail (#2323 Increment 2 target) ─


def test_get_system_prompt_is_not_yet_declarative():
    """Pins the CURRENT if/elif profile-branching state of `_get_system_prompt`.

    Expected to FAIL on current main — the ProfileSpec table hasn't landed
    yet. It flips to passing once Increment 2 replaces the chain; at that
    point this test documents the refactor succeeded (or becomes a permanent
    "don't regress back to if/elif" guard — reviewer's call which).
    """
    from gaia_agent_chat.agent import ChatAgent

    source = inspect.getsource(ChatAgent._get_system_prompt)
    assert "if profile ==" not in source, (
        "ChatAgent._get_system_prompt still branches on `if profile ==` — "
        "expected on pre-refactor main. This is not a bug to fix in this "
        "commit; it documents the state Increment 2 must change."
    )


def test_register_tools_is_not_yet_declarative():
    """Pins the CURRENT if/elif profile-branching state of `_register_tools`.

    See `test_get_system_prompt_is_not_yet_declarative` — same contract, the
    tool-registration chain instead of the prompt-assembly chain.
    """
    from gaia_agent_chat.agent import ChatAgent

    source = inspect.getsource(ChatAgent._register_tools)
    assert "if profile ==" not in source, (
        "ChatAgent._register_tools still branches on `if profile ==` — "
        "expected on pre-refactor main. This is not a bug to fix in this "
        "commit; it documents the state Increment 2 must change."
    )
