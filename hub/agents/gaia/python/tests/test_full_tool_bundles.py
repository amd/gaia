# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drift gate for the flagship's ``full``-profile tool bundles.

``FULL_CORE_TOOLS`` union all ``FULL_BUNDLES`` members must equal the live
flagship registry **exactly**, in both directions:

* a registry tool covered by neither shipped without anyone deciding whether
  every session should pay for it;
* a configured name absent from the registry is dead config (or a typo) that
  ``ToolLoader.validate_registry`` rejects at runtime, unless it is declared in
  ``FULL_OPTIONAL_TOOLS``.

This lives beside the flagship rather than in ``tests/unit/`` because the
``full`` registry is GaiaAgent's, not ChatAgent's: 11 of its tools come from the
skill-library and code-index mixins this package composes. The sibling guard for
the ``doc`` profile is ``tests/unit/test_chat_tool_bundles.py``.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
from gaia_agent.tool_bundles import (
    FULL_BUNDLES,
    FULL_CORE_TOOLS,
    FULL_OPTIONAL_TOOLS,
    PROFILE_TOOL_CONFIGS,
)

from gaia.agents.base.tool_loader import thin_tool_schema
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import GPT_OSS_20B_MODEL_NAME


@contextlib.contextmanager
def _isolated_registry():
    """@tool writes into a process-global dict shared with every other agent."""
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture(scope="module")
def flagship_registry():
    """The flagship's tool names with memory on and the loader active.

    Memory is forced off during construction (no embedder in CI) and the store
    is then faked so ``register_memory_tools`` runs — the same pinning
    ``test_gaia_agent.py`` uses, for the same reason: an environment-dependent
    count made the drift guard pass locally and fail in CI.
    """
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        # Opt-in (GAIA_DYNAMIC_TOOLS=1 / the UI toggle), and the bundles must
        # still cover the registry whenever someone turns it on.
        # On gpt-oss, the one model family that also registers set_reasoning_effort.
        agent = GaiaAgent(
            config=GaiaAgentConfig(
                silent_mode=True,
                dynamic_tools=True,
                model_id=GPT_OSS_20B_MODEL_NAME,
            )
        )
        assert agent.tool_loader is not None, (
            "the flagship builds no tool loader with dynamic_tools=True — 'full' "
            "must be in PROFILE_TOOL_CONFIGS, or search_tools never registers."
        )
        agent._memory_store = object()
        agent._register_tools()
        return set(agent._tools_registry)


def _bundle_members() -> set[str]:
    members: set[str] = set()
    for bundle in FULL_BUNDLES:
        members |= set(bundle.members)
    return members


def test_core_and_bundles_cover_the_flagship_registry_exactly(flagship_registry):
    covered = set(FULL_CORE_TOOLS) | _bundle_members()

    uncovered = sorted(flagship_registry - covered)
    dangling = sorted(covered - flagship_registry)

    assert not uncovered, (
        f"flagship tools covered by neither FULL_CORE_TOOLS nor a bundle: "
        f"{uncovered}. Add each to a bundle (or CORE) in "
        "hub/agents/gaia/python/gaia_agent/tool_bundles.py — every registered "
        "tool must be accounted for."
    )
    assert not dangling, (
        f"FULL_CORE_TOOLS/FULL_BUNDLES names absent from the flagship registry: "
        f"{dangling}. Remove them, fix the name, or — if the tool is absent by "
        "construction on some installs — declare it in FULL_OPTIONAL_TOOLS."
    )


@pytest.mark.parametrize(
    "message",
    ["run git status", "what is the working directory?", "do it in PowerShell"],
)
def test_asking_for_the_shell_stays_on_the_short_list(message):
    """The shell tool is core, so asking for it must not cost the one jump."""
    from gaia_agent.tool_requests import requested_tools

    registry = FULL_CORE_TOOLS | {"get_shell_state", "reset_shell_session"}
    assert set(requested_tools(message, registry)) <= FULL_CORE_TOOLS


def test_skill_request_tools_exist_and_cost_the_jump(flagship_registry):
    """A renamed skill tool would silently stop being requested."""
    from gaia_agent.tool_requests import SKILL_REQUEST_TOOLS

    assert set(SKILL_REQUEST_TOOLS) <= flagship_registry
    assert not set(SKILL_REQUEST_TOOLS) & FULL_CORE_TOOLS


def test_core_is_the_list_every_session_starts_on():
    """A change here moves the first-turn cost of every session."""
    assert FULL_CORE_TOOLS == {
        "analyze_data_file",
        "browse_directory",
        "edit_file",
        "fetch_page",
        "find_files",
        "forget",
        "forget_everything",
        "index_document",
        "list_files",
        "load_skill",
        "query_documents",
        "query_specific_file",
        "read_file",
        "read_tool_output",
        "recall",
        "remember",
        "request_user_input",
        "run_python",
        "run_shell_command",
        "search_file",
        "search_file_content",
        "search_past_conversations",
        "search_tools",
        "search_web",
        "set_loop_state",
        "sleep",
        "summarize_document",
        "update_memory",
        "write_file",
    }


def test_search_tools_is_registered_and_core(flagship_registry):
    """Without search_tools a retrieval miss is unrecoverable."""
    assert "search_tools" in FULL_CORE_TOOLS
    assert "search_tools" in flagship_registry


def test_load_skill_is_registered_and_core(flagship_registry):
    """The skill catalogue must be able to call the tool its prompt advertises."""
    assert "load_skill" in FULL_CORE_TOOLS
    assert "load_skill" in flagship_registry


def test_file_edit_tools_are_core(flagship_registry):
    """Retrieval cannot rank editing (#3752) — it must be always-on."""
    assert {"write_file", "edit_file"} <= set(FULL_CORE_TOOLS)
    assert {"write_file", "edit_file"} <= flagship_registry


def test_run_python_is_core(flagship_registry):
    """Without it the model guesses a number or leaves a script in the repo."""
    assert "run_python" in FULL_CORE_TOOLS
    assert "run_python" in flagship_registry


def test_skill_catalogue_renders_for_the_flagship():
    """A skill the model cannot see is a skill it never loads (#3764)."""
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        mp.delenv("GAIA_SKILL_DISCOVERY", raising=False)
        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True, dynamic_tools=True))
        prompt = agent.system_prompt
    assert "==== SKILLS ====" in prompt
    assert "- github-triage: " in prompt


def test_optional_tools_are_present_on_a_full_install(flagship_registry):
    """FULL_OPTIONAL_TOOLS is a runtime tolerance, never a coverage exemption.

    Every name in it must still exist on the flagship, so a rename fails here
    instead of quietly disabling the validation that would have caught it.
    """
    missing = sorted(FULL_OPTIONAL_TOOLS - flagship_registry)
    assert not missing, (
        f"declared optional but absent even on a full install: {missing}. "
        "Either the tool was renamed/removed, or it never belonged in "
        "FULL_OPTIONAL_TOOLS."
    )


def test_core_is_subset_of_bundle_union():
    """Every CORE tool is in a bundle too, except the CORE-only search_tools."""
    assert set(FULL_CORE_TOOLS) - _bundle_members() == {
        "search_tools",
        "read_tool_output",
    }


def test_bundles_have_unique_names():
    names = [b.name for b in FULL_BUNDLES]
    assert len(names) == len(set(names)), f"duplicate bundle names: {names}"


def test_full_profile_is_wired_to_the_full_config():
    cfg = PROFILE_TOOL_CONFIGS["full"]
    assert cfg.core is FULL_CORE_TOOLS
    assert cfg.bundles is FULL_BUNDLES
    assert cfg.optional is FULL_OPTIONAL_TOOLS


def test_trimming_actually_saves_most_of_the_tool_payload():
    """The point of the whole change, pinned so a regression is visible.

    Measured on the real renderer with tiktoken (cl100k) as a tokenizer-agnostic
    proxy — Gemma's own counts differ, but the ratio is what this guards. Uses
    the offline skeleton: constructing a live GaiaAgent preloads a model.
    """
    tiktoken = pytest.importorskip("tiktoken")
    from gaia.eval.tool_cost import build_full_agent_skeleton

    enc = tiktoken.get_encoding("cl100k_base")
    agent = build_full_agent_skeleton()
    registry = agent._tools_registry

    def thin_cost(names):
        schemas = agent._build_openai_tool_schemas(filter_to=names)
        return len(enc.encode(json.dumps([thin_tool_schema(s) for s in schemas])))

    everything = len(enc.encode(json.dumps(agent._build_openai_tool_schemas())))
    core = thin_cost(agent.tool_loader.offered(registry))
    full = thin_cost(list(registry))

    assert core < everything * 0.20, (
        f"the list a session starts on is {core} tokens against {everything} for "
        "every tool in full — the always-on set has grown too expensive."
    )
    assert full < everything * 0.50, (
        f"the full thin list is {full} tokens against {everything} in full — a "
        "jump is meant to stay cheap."
    )
