# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent wiring for the dynamic tool loader (#1449, Part 2 #1450).

Covers the ChatAgent-level glue without a Lemonade backend: loader construction
gating (profile + toggle + env), the three off-states reverting to the full
registry (``None`` filter), the selection-query builder, the LRU record hook,
env-override parsing (incl. loud failure on malformed values), the ``load_tools``
escape hatch + native-only menu, and that the Part-1 native known-gap warning is
gone now that Part 2 closes the gap.

ChatAgent is built via ``__new__`` with only the attributes each method needs —
``Agent.__init__`` (Lemonade) is never run.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Stub heavy optional deps only when genuinely absent (mirrors the budget test).
_stubbed: list[str] = []
for _mod in ("faiss", "sentence_transformers", "pdfplumber", "pypdf", "pypdfium2"):
    if _mod in sys.modules or importlib.util.find_spec(_mod) is not None:
        continue
    sys.modules[_mod] = MagicMock()
    _stubbed.append(_mod)

# ChatAgent ships as the standalone gaia-agent-chat wheel (#1102); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402

from gaia.agents.base.tool_loader import ToolLoader  # noqa: E402
from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402

for _mod in _stubbed:
    sys.modules.pop(_mod, None)


def _bare_agent(**attrs) -> ChatAgent:
    """A ChatAgent instance with only the attributes a method-under-test needs."""
    a = ChatAgent.__new__(ChatAgent)
    a.observers = []  # quiet __del__ during GC
    a.conversation_history = []
    a.tool_loader = None
    a._memory_store = object()
    a._dynamic_tools_validated = False
    a.model_id = None
    for k, v in attrs.items():
        setattr(a, k, v)
    return a


def _real_loader() -> ToolLoader:
    """A real loader over a tiny registry, with a deterministic embedder."""
    return ToolLoader(
        core_tools=frozenset({"c1"}),
        bundles=[],
        embed_fn=lambda t: np.zeros(768, dtype=np.float32),
        threshold=0.55,
        max_tools=14,
    )


# ── construction gating ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("profile", "dynamic", "expect_loader"),
    [
        ("doc", True, True),
        ("doc", False, False),
        ("full", True, False),
        ("chat", True, False),
    ],
)
def test_loader_built_only_for_doc_profile_with_toggle_on(
    monkeypatch, profile, dynamic, expect_loader
):
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    a = _bare_agent(
        config=ChatAgentConfig(prompt_profile=profile, dynamic_tools=dynamic)
    )
    loader = a._maybe_build_tool_loader()
    assert (loader is not None) == expect_loader


def test_env_toggle_overrides_config(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=False))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS", "1")
    assert a._maybe_build_tool_loader() is not None
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS", "0")
    assert a._maybe_build_tool_loader() is None


def test_env_override_helper_none_when_unset(monkeypatch):
    """``dynamic_tools_env_override`` returns ``None`` so callers fall back to
    the persisted/config value — the single source of truth the UI router and
    the agent resolver both read (#1798)."""
    from gaia_agent_chat.agent import dynamic_tools_env_override

    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    assert dynamic_tools_env_override() is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
    ],
)
def test_env_override_helper_parses_truthy_set(monkeypatch, raw, expected):
    """Same truthy set the resolver used to inline — pinned so the UI toggle
    and the agent never disagree on what counts as "on"."""
    from gaia_agent_chat.agent import dynamic_tools_env_override

    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS", raw)
    assert dynamic_tools_env_override() is expected


def test_env_threshold_and_max_overrides(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=True))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_TAU", "0.42")
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_MAX", "9")
    loader = a._maybe_build_tool_loader()
    assert loader._threshold == pytest.approx(0.42)
    assert loader._max_tools == 9


def test_malformed_env_threshold_fails_loudly(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=True))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_TAU", "not-a-float")
    with pytest.raises(ValueError, match="GAIA_DYNAMIC_TOOLS_TAU"):
        a._maybe_build_tool_loader()


def test_malformed_env_max_fails_loudly(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=True))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_MAX", "ten")
    with pytest.raises(ValueError, match="GAIA_DYNAMIC_TOOLS_MAX"):
        a._maybe_build_tool_loader()


# ── off-states → full registry (None filter) ──────────────────────────────


def test_off_state_toggle_off_returns_none():
    a = _bare_agent(tool_loader=None)  # loader not built
    assert a._dynamic_tools_active() is False
    assert a._select_tools_for_turn("anything") is None


def test_off_state_memory_disabled_returns_none():
    a = _bare_agent(tool_loader=_real_loader(), _memory_store=None)
    assert a._dynamic_tools_active() is False
    assert a._select_tools_for_turn("anything") is None


def test_off_state_embedder_disabled_returns_none():
    loader = _real_loader()
    loader._session_disabled = True  # simulate embedder failure
    a = _bare_agent(tool_loader=loader)
    assert a._dynamic_tools_active() is False
    assert a._select_tools_for_turn("anything") is None


def test_active_state_delegates_to_loader_select():
    sentinel = ["c1", "read_file"]
    loader = MagicMock()
    loader.session_disabled = False
    loader.select.return_value = sentinel
    a = _bare_agent(tool_loader=loader)
    a._tools_registry_value = {}  # not used (loader mocked)
    with patch.object(
        ChatAgent, "_tools_registry", new_callable=lambda: property(lambda self: {})
    ):
        result = a._select_tools_for_turn("read the file")
    assert result is sentinel
    loader.select.assert_called_once()


# ── SKILL signal wiring (Part 3, #1451) ───────────────────────────────────


def _skill(name: str, tools: list[str]):
    """A minimal recalled Skill carrying ``tools_required``."""
    from gaia.agents.base.skill_synthesis import Skill

    return Skill(name=name, when_to_use="trigger", body="# body", tools_required=tools)


def test_recalled_skill_tools_flattens_and_dedupes_in_order():
    """tools_required flatten in recall rank then declaration order, deduped."""
    a = _bare_agent(
        _recalled_skills=[
            _skill("s1", ["read_file", "query_documents"]),
            _skill("s2", ["query_documents", "summarize"]),  # dup dropped
        ]
    )
    assert a._recalled_skill_tools() == ["read_file", "query_documents", "summarize"]


def test_recalled_skill_tools_empty_on_graceful_absence():
    """No recall (attr unset or empty list) → no SKILL signal at the agent layer."""
    assert _bare_agent()._recalled_skill_tools() == []  # attr never set
    assert _bare_agent(_recalled_skills=[])._recalled_skill_tools() == []


def test_select_passes_skill_tools_kwarg_to_loader():
    """_select_tools_for_turn hands loader.select the flattened skill_tools kwarg.

    Contract-shape assert at the ChatAgent→loader boundary: the kwarg is present,
    a list[str], and the flattened+deduped recipe — not merely "select was
    called".
    """
    loader = MagicMock()
    loader.session_disabled = False
    loader.select.return_value = ["c1"]
    a = _bare_agent(
        tool_loader=loader,
        _recalled_skills=[_skill("s1", ["read_file", "query_documents"])],
    )
    with patch.object(
        ChatAgent, "_tools_registry", new_callable=lambda: property(lambda self: {})
    ):
        a._select_tools_for_turn("read the report")
    _, kwargs = loader.select.call_args
    assert kwargs["skill_tools"] == ["read_file", "query_documents"]
    assert isinstance(kwargs["skill_tools"], list)


def test_select_skill_tools_empty_kwarg_on_graceful_absence():
    """With no recalled skills, select still gets skill_tools=[] (graceful absence)."""
    loader = MagicMock()
    loader.session_disabled = False
    loader.select.return_value = ["c1"]
    a = _bare_agent(tool_loader=loader)  # no _recalled_skills set
    with patch.object(
        ChatAgent, "_tools_registry", new_callable=lambda: property(lambda self: {})
    ):
        a._select_tools_for_turn("anything")
    _, kwargs = loader.select.call_args
    assert kwargs["skill_tools"] == []


# ── selection query builder ───────────────────────────────────────────────


def test_query_builder_turn1_is_just_current():
    a = _bare_agent(conversation_history=[])
    assert a._build_tool_selection_query("first message") == "first message"


def test_query_builder_prepends_previous_user_message():
    history = [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "answer"},
    ]
    a = _bare_agent(conversation_history=history)
    q = a._build_tool_selection_query("current question")
    assert q == "previous question\ncurrent question"


def test_query_builder_excludes_assistant_and_truncates():
    history = [{"role": "user", "content": "P" * 5000}]
    a = _bare_agent(conversation_history=history)
    q = a._build_tool_selection_query("C" * 100)
    assert len(q) == 4000  # trailing 4K chars
    assert q.endswith("C" * 100)  # current turn always fully included


# ── record hook ────────────────────────────────────────────────────────────


def test_on_tool_invoked_forwards_to_loader():
    loader = MagicMock()
    a = _bare_agent(tool_loader=loader)
    a._on_tool_invoked("read_file")
    loader.record_tool_use.assert_called_once_with("read_file")


def test_on_tool_invoked_noop_when_no_loader():
    a = _bare_agent(tool_loader=None)
    a._on_tool_invoked("read_file")  # must not raise


def test_native_model_no_longer_warns_known_gap(caplog):
    """Part 2 (#1450) closed the native gap via load_tools — the warning is gone."""
    loader = MagicMock()
    loader.session_disabled = False
    loader.select.return_value = ["c1"]
    a = _bare_agent(tool_loader=loader, model_id="Gemma-4-E4B-it-GGUF")  # native
    with patch.object(
        ChatAgent, "_tools_registry", new_callable=lambda: property(lambda self: {})
    ):
        with caplog.at_level(logging.WARNING):
            a._select_tools_for_turn("q1")
            a._select_tools_for_turn("q2")
    assert not any(
        "known gap" in r.getMessage() or "no escape hatch" in r.getMessage()
        for r in caplog.records
    )


# ── _apply_tool_filter invariant (Part 2 mid-loop recovery) ────────────────


def test_apply_tool_filter_swaps_filter_and_recomputes_prompt():
    """The base helper moves the filter and the cached prompt together."""
    a = ChatAgent.__new__(ChatAgent)
    a.observers = []  # quiet __del__ during GC
    a._active_tool_filter = None
    a._system_prompt_cache = "OLD"
    a._compose_system_prompt = lambda: f"PROMPT::{a._active_tool_filter}"
    a._apply_tool_filter(["load_tools", "search_file"])
    assert a._active_tool_filter == ["load_tools", "search_file"]
    assert a._system_prompt_cache == "PROMPT::['load_tools', 'search_file']"


# ── load_tools registration + handler (Part 2, #1450) ──────────────────────


def test_load_tools_registered_only_when_loader_active():
    on = build_doc_agent_skeleton(profile="doc", deterministic=True, dynamic_tools=True)
    off = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=False
    )
    assert "load_tools" in on._tools_registry
    assert "load_tools" not in off._tools_registry


def test_load_tools_handler_admits_bundle_and_applies_filter():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    applied: dict = {}
    agent._apply_tool_filter = lambda f: applied.__setitem__("filter", f)
    load_tools = agent._tools_registry["load_tools"]["function"]

    result = load_tools("file_search")
    assert result["status"] == "success"
    assert result["bundle"] == "file_search"
    # The bundle's tools are now in the loaded set, and that set was applied as
    # the active filter so the next model step sees them.
    assert "search_file" in result["loaded_tools"]
    assert applied["filter"] == result["loaded_tools"]


def test_load_tools_handler_resolves_bare_tool_name():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    agent._apply_tool_filter = lambda f: None
    load_tools = agent._tools_registry["load_tools"]["function"]
    result = load_tools("search_file")  # bare tool name → its bundle
    assert result["status"] == "success"
    assert "search_file" in result["loaded_tools"]


def test_load_tools_handler_unknown_bundle_returns_actionable_error():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    agent._apply_tool_filter = lambda f: None
    load_tools = agent._tools_registry["load_tools"]["function"]
    result = load_tools("does_not_exist")
    assert result["status"] == "error"
    assert "Unknown bundle 'does_not_exist'" in result["error"]
    assert "file_search" in result["error"]  # lists valid bundle names


# ── native-only escape-hatch menu ──────────────────────────────────────────


def test_native_doc_prompt_includes_load_tools_menu():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    agent.rag = None  # no-docs branch keeps _get_system_prompt light
    prompt = agent._get_system_prompt()
    assert "LOADABLE TOOL BUNDLES" in prompt
    assert "load_tools(bundle)" in prompt
    assert "- file_search:" in prompt  # a real bundle line from the menu


def test_non_native_doc_prompt_omits_load_tools_menu():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    agent.rag = None
    agent.model_id = None  # non-tool-calling → free recovery, no menu
    prompt = agent._get_system_prompt()
    assert "LOADABLE TOOL BUNDLES" not in prompt


def test_loader_off_doc_prompt_omits_load_tools_menu():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=False
    )
    agent.rag = None
    prompt = agent._get_system_prompt()
    assert "LOADABLE TOOL BUNDLES" not in prompt
