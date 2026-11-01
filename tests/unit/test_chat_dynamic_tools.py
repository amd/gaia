# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent wiring for dynamic tool loading.

Covers the ChatAgent-level glue without a Lemonade backend: loader construction
gating (profile + toggle + env), the selection-query builder, the jump's wiring
(``_select_tools_for_turn``, the slot pin, the "loading more tools" notice), and
``search_tools`` with its native-only prompt line.

Most of ChatAgent is built via ``__new__`` with only the attributes each method
needs — ``Agent.__init__`` (Lemonade) is never run.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ChatAgent ships in the flagship gaia-agent-gaia wheel (#6099); skip the
# whole module when a framework-only env lacks it. Must run BEFORE the optional-
# dep stubbing below — otherwise a skip would leave MagicMock stand-ins for
# faiss/sentence_transformers in sys.modules and poison later modules in the run.
pytest.importorskip("gaia_agent")

# Stub heavy optional deps only when genuinely absent (mirrors the budget test).
_stubbed: list[str] = []
for _mod in ("faiss", "sentence_transformers", "pdfplumber", "pypdf", "pypdfium2"):
    if _mod in sys.modules or importlib.util.find_spec(_mod) is not None:
        continue
    sys.modules[_mod] = MagicMock()
    _stubbed.append(_mod)

from gaia_agent.chat_agent import (  # noqa: E402
    MAX_TOOL_SEARCH_CHARS,
    MAX_TOOL_SEARCHES,
    ChatAgent,
    ChatAgentConfig,
)

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.tool_loader import ToolLoader  # noqa: E402
from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402
from gaia.llm.lemonade_client import FULL_TOOLS_SLOT  # noqa: E402

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


# ── construction gating ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("profile", "dynamic", "expect_loader"),
    [
        ("doc", True, True),
        ("doc", False, False),
        # "full" gained a loader once FULL_CORE_TOOLS/FULL_BUNDLES covered its
        # whole registry; without that coverage its unbundled tools would have
        # become permanently invisible, which is why it was excluded before.
        ("full", True, True),
        ("full", False, False),
        # Everything else has no PROFILE_TOOL_CONFIGS entry and stays on the
        # full-registry legacy path even with the toggle on.
        ("chat", True, False),
        ("file", True, False),
        ("data", True, False),
        ("web", True, False),
    ],
)
def test_loader_built_only_for_configured_profiles_with_toggle_on(
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
    from gaia_agent.chat_agent import dynamic_tools_env_override

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
    from gaia_agent.chat_agent import dynamic_tools_env_override

    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS", raw)
    assert dynamic_tools_env_override() is expected


def test_a_built_loader_starts_from_profile_core_plus_workspace_core(monkeypatch):
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=True))
    a._workspace_core_tools = lambda: frozenset({"workspace_tool"})
    registry = {"workspace_tool": {}, "query_documents": {}, "not_core": {}}

    loader = a._maybe_build_tool_loader()

    assert loader.offered(registry) == ["workspace_tool", "query_documents"]


# ── the loader's off-states ───────────────────────────────────────────────


def _active_agent(**attrs) -> ChatAgent:
    """A bare agent whose list the loader decides."""
    registry = {name: {"description": f"{name} tool"} for name in ("c1", "d1", "d2")}
    loader = ToolLoader(
        core_tools=frozenset({"c1"}),
        bundles=[],
        embed_fn=lambda _t: np.zeros(4, dtype=np.float32),
    )
    a = _bare_agent(tool_loader=loader, _registry=registry, _tool_notice_due=False)
    a._hosted_native_tool_caller = lambda: False
    a._loaded_skill_tools = lambda: []
    a._recalled_skill_tools = lambda: []
    a._compose_system_prompt = lambda: "PROMPT"
    a._active_tool_filter = None
    a._system_prompt_cache = None
    for k, v in attrs.items():
        setattr(a, k, v)
    return a


@pytest.fixture
def registry_property():
    with patch.object(
        ChatAgent,
        "_tools_registry",
        new_callable=lambda: property(lambda self: self._registry),
    ):
        yield


@pytest.mark.usefixtures("registry_property")
class TestOffStates:
    def test_active_on_a_local_model_with_memory(self):
        assert _active_agent()._dynamic_tools_active() is True

    def test_no_loader_means_every_tool(self):
        a = _active_agent(tool_loader=None)
        assert a._dynamic_tools_active() is False
        assert a._select_tools_for_turn("q") is None

    def test_a_hosted_model_keeps_every_tool_in_full(self):
        a = _active_agent()
        a._hosted_native_tool_caller = lambda: True
        assert a._thin_tool_schemas() is False
        assert a._dynamic_tools_active() is False
        assert a._select_tools_for_turn("run_shell_command please") is None

    def test_a_hosted_model_is_never_pinned_to_the_full_list_slot(self):
        a = _active_agent()
        a._hosted_native_tool_caller = lambda: True
        a.tool_loader.jump("search")
        assert a._conversation_request_kwargs() == {}

    def test_memory_off_means_every_tool(self):
        a = _active_agent(_memory_store=None)
        assert a._dynamic_tools_active() is False
        assert a._select_tools_for_turn("q") is None

    def test_a_dead_embedder_offers_every_tool_and_keeps_them_thin(self):
        def dead(_text):
            raise ConnectionError("embedder down")

        loader = ToolLoader(frozenset({"c1"}), [], dead)
        a = _active_agent(tool_loader=loader)

        assert a._select_tools_for_turn("q") is None

        assert loader.session_disabled is True
        assert a._dynamic_tools_active() is False
        assert a._select_tools_for_turn("q") is None
        # Thinness must not change while a model is loaded.
        assert a._thin_tool_schemas() is True
        assert a._conversation_request_kwargs() == {}


# ── _select_tools_for_turn: what moves the session ────────────────────────


def _selecting_agent(registry_names, scores=None, **attrs):
    """A bare agent over a real loader that scores *scores* on every query."""
    scores = scores or {}
    names = list(registry_names)

    def embed(text):
        vec = np.zeros(len(names) + 1, dtype=np.float32)
        for i, name in enumerate(names):
            if text.startswith(f"{name}:"):
                vec[i] = 1.0
                return vec
        for name, score in scores.items():
            vec[names.index(name)] = score
        return vec

    registry = {name: {"description": f"{name} tool"} for name in names}
    return _active_agent(
        tool_loader=ToolLoader(frozenset(names[:1]), [], embed),
        _registry=registry,
        **attrs,
    )


@pytest.mark.usefixtures("registry_property")
class TestSelectToolsForTurn:
    NAMES = ["core_tool", "run_shell_command", "get_shell_state", "spare_tool"]

    def test_an_ordinary_message_stays_on_core(self):
        a = _selecting_agent(self.NAMES)
        assert a._select_tools_for_turn("hello there") == ["core_tool"]
        assert a._conversation_request_kwargs() == {}

    def test_a_tool_the_user_names_jumps(self):
        a = _selecting_agent(self.NAMES)
        assert a._select_tools_for_turn("call spare_tool now") == self.NAMES
        assert a._conversation_request_kwargs() == {"id_slot": FULL_TOOLS_SLOT}

    def test_asking_for_the_shell_jumps(self):
        a = _selecting_agent(self.NAMES)
        assert a._select_tools_for_turn("use your shell to run pwd") == self.NAMES

    def test_a_recalled_procedures_tool_jumps(self):
        a = _selecting_agent(self.NAMES)
        a._recalled_skill_tools = lambda: ["spare_tool"]
        assert a._select_tools_for_turn("do the usual") == self.NAMES

    def test_a_loaded_skills_tool_joins_without_a_jump(self):
        a = _selecting_agent(self.NAMES)
        a._loaded_skill_tools = lambda: ["spare_tool"]
        assert a._select_tools_for_turn("hello") == ["core_tool", "spare_tool"]
        assert a._conversation_request_kwargs() == {}

    def test_retrieval_jumps(self):
        a = _selecting_agent(self.NAMES, {"spare_tool": 0.9})
        assert a._select_tools_for_turn("anything") == self.NAMES

    def test_the_message_names_tools_by_the_current_turn_only(self):
        a = _selecting_agent(self.NAMES)
        a._select_tools_for_turn("hello")
        a.conversation_history = [{"role": "user", "content": "call spare_tool"}]
        assert a._select_tools_for_turn("thanks") == ["core_tool"]

    def test_a_reopened_chat_starts_on_the_full_list(self):
        a = _selecting_agent(
            self.NAMES,
            conversation_history=[{"role": "user", "content": "the csv"}],
        )
        assert a._select_tools_for_turn("thanks") == self.NAMES

    def test_the_query_is_the_current_message_alone(self):
        a = _selecting_agent(self.NAMES)
        a.tool_loader = MagicMock(session_disabled=False, turns=1)
        a._select_tools_for_turn("summarize it")
        assert a.tool_loader.select.call_args.args[0] == "summarize it"

    def test_config_drift_fails_on_first_use(self):
        a = _selecting_agent(self.NAMES)
        a.tool_loader = ToolLoader(frozenset({"renamed_tool"}), [], lambda _t: None)
        with pytest.raises(ValueError, match="renamed_tool"):
            a._select_tools_for_turn("hello")

    def test_config_is_validated_once(self):
        a = _selecting_agent(self.NAMES)
        a._select_tools_for_turn("hello")
        a.tool_loader.validate_registry = MagicMock()
        a._select_tools_for_turn("hello")
        a.tool_loader.validate_registry.assert_not_called()


# ── the turn's filter, through the base agent ─────────────────────────────


@pytest.mark.usefixtures("registry_property")
def test_a_jump_turn_swaps_the_filter_and_the_prompt_together():
    a = _selecting_agent(["c1", "d1"], {"d1": 0.9})
    a._active_tool_filter = ["c1"]
    a._compose_system_prompt = lambda: f"PROMPT::{a._active_tool_filter}"
    a._on_task_start = lambda _q: None

    a._refresh_active_tool_filter("q")

    assert a._active_tool_filter == ["c1", "d1"]
    assert a._system_prompt_cache == "PROMPT::['c1', 'd1']"


# ── the tool-list notice ──────────────────────────────────────────────────


@pytest.mark.usefixtures("registry_property")
class TestToolListNotice:
    def _with_history(self):
        return _active_agent(
            _active_tool_filter=["c1"],
            conversation_history=[{"role": "user", "content": "hi"}],
        )

    def test_a_changed_list_mid_conversation_is_noted_once(self):
        a = self._with_history()
        a._apply_tool_filter(["c1", "d1"])
        assert a._tool_list_notice() is True
        assert a._tool_list_notice() is False

    def test_the_same_list_is_no_notice(self):
        a = self._with_history()
        a._apply_tool_filter(["c1"])
        assert a._tool_list_notice() is False

    def test_a_first_turn_has_nothing_to_re_read(self):
        a = self._with_history()
        a.conversation_history = []
        a._apply_tool_filter(["c1", "d1"])
        assert a._tool_list_notice() is False

    def test_the_first_list_of_a_reopened_chat_is_a_notice(self):
        a = self._with_history()
        a._active_tool_filter = None
        a._apply_tool_filter(["c1"])
        assert a._tool_list_notice() is True

    def test_the_first_list_of_a_new_chat_is_no_notice(self):
        a = self._with_history()
        a.conversation_history = []
        a._active_tool_filter = None
        a._apply_tool_filter(["c1"])
        assert a._tool_list_notice() is False

    def test_the_base_agent_never_gives_one(self):
        base = MagicMock(spec=Agent)
        assert Agent._tool_list_notice(base) is False


# ── skill tools on the list ───────────────────────────────────────────────


@pytest.mark.usefixtures("registry_property")
class TestSkillToolHooks:
    def test_admit_puts_them_on_the_list_and_owns_the_result(self):
        a = _active_agent()
        assert a._admit_skill_tools(["d2", "ghost"]) is True
        assert a._active_tool_filter == ["c1", "d2"]

    def test_admit_leaves_an_inactive_agent_to_its_caller(self):
        a = _active_agent(tool_loader=None)
        assert a._admit_skill_tools(["d2"]) is False

    def test_drop_takes_them_off(self):
        a = _active_agent()
        a._admit_skill_tools(["d1", "d2"])
        a._drop_skill_tools(["d1"])
        assert a._active_tool_filter == ["c1", "d2"]

    def test_drop_after_a_jump_keeps_the_full_list(self):
        a = _active_agent()
        a._admit_skill_tools(["d1"])
        a.tool_loader.jump("search")
        a._apply_tool_filter(a.tool_loader.offered(a._registry))
        a._drop_skill_tools(["d1"])
        assert a._active_tool_filter == ["c1", "d1", "d2"]

    def test_drop_without_a_loader_narrows_the_filter_it_has(self):
        a = _active_agent(tool_loader=None, _active_tool_filter=["c1", "d1", "d2"])
        a._drop_skill_tools(["d1"])
        assert a._active_tool_filter == ["c1", "d2"]

    def test_drop_without_a_filter_stays_without_one(self):
        a = _active_agent(tool_loader=None)
        a._drop_skill_tools(["d1"])
        assert a._active_tool_filter is None


# ── SKILL signal wiring (Part 3, #1451) ───────────────────────────────────


def _skill(name: str, tools: list[str]):
    """A minimal recalled DistilledProcedure carrying ``tools_required``."""
    from gaia.agents.base.skill_synthesis import DistilledProcedure

    return DistilledProcedure(
        name=name, when_to_use="trigger", body="# body", tools_required=tools
    )


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


@pytest.mark.usefixtures("registry_property")
def test_native_model_no_longer_warns_known_gap(caplog):
    a = _active_agent(model_id="Gemma-4-E4B-it-GGUF")
    with caplog.at_level(logging.WARNING):
        a._select_tools_for_turn("q1")
        a._select_tools_for_turn("q2")
    assert caplog.records == []


# ── _apply_tool_filter invariant (mid-loop recovery) ──────────────────────


def test_apply_tool_filter_swaps_filter_and_recomputes_prompt():
    """The base helper moves the filter and the cached prompt together."""
    a = ChatAgent.__new__(ChatAgent)
    a.observers = []  # quiet __del__ during GC
    a._active_tool_filter = None
    a._system_prompt_cache = "OLD"
    a._compose_system_prompt = lambda: f"PROMPT::{a._active_tool_filter}"
    a._apply_tool_filter(["search_tools", "search_file"])
    assert a._active_tool_filter == ["search_tools", "search_file"]
    assert a._system_prompt_cache == "PROMPT::['search_tools', 'search_file']"


# ── search_tools ──────────────────────────────────────────────────────────


class _SearchEmbedder:
    """One-hot docs, coordinate queries, as in ``test_tool_loader_selection.py``.

    ``scores`` keys on the doc's name, the text before its colon; a doc with no
    score embeds to zero. Every query embeds to the same vector.
    """

    def __init__(self, scores):
        self.names = list(scores)
        self.scores = scores
        self.queries: list[str] = []

    def __call__(self, text):
        vec = np.zeros(len(self.names) + 1, dtype=np.float32)
        if ":" in text:
            name = text.split(":", 1)[0]
            if name in self.scores:
                vec[self.names.index(name)] = 1.0
            return vec
        self.queries.append(text)
        return np.array([*self.scores.values(), 0.0], dtype=np.float32)


@pytest.fixture
def searching():
    """Builds a doc skeleton on its core list, whose search answers from *scores*."""

    def build(scores, skills=None):
        agent = build_doc_agent_skeleton(
            profile="doc", deterministic=True, dynamic_tools=True
        )
        embed = _SearchEmbedder(scores)
        agent.tool_loader = ToolLoader(
            core_tools=agent.tool_loader._core, bundles=[], embed_fn=embed
        )
        agent._hosted_native_tool_caller = lambda: False
        agent._tool_docs_sent = set()
        agent._tool_searches = 0
        agent._tool_notice_due = False
        agent._released_slots = set()
        agent._compose_system_prompt = lambda: "PROMPT"
        agent._active_tool_filter = None
        agent._system_prompt_cache = None
        agent._loaded_skills = {}
        agent._skill_manager = SimpleNamespace(discover=lambda: skills or {})
        agent._apply_tool_filter(agent.tool_loader.offered(agent._tools_registry))
        return agent, embed

    return build


def _search(agent, query="find something"):
    return agent._tools_registry["search_tools"]["function"](query)


def test_search_tools_registered_only_when_loader_active():
    on = build_doc_agent_skeleton(profile="doc", deterministic=True, dynamic_tools=True)
    off = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=False
    )
    assert "search_tools" in on._tools_registry
    assert "search_tools" not in off._tools_registry


def test_a_hit_on_the_list_leaves_the_list_alone(searching):
    agent, _ = searching({"query_documents": 0.9})
    before = list(agent._active_tool_filter)

    result = _search(agent)

    assert result["status"] == "success"
    assert result["tools"] == ["query_documents"]
    assert agent.tool_loader.jumped is False
    assert agent._active_tool_filter == before
    assert agent._tool_list_notice() is False


def test_a_hit_outside_the_list_jumps_to_every_tool(searching):
    agent, _ = searching({"analyze_data_file": 0.9})
    assert "analyze_data_file" not in agent._active_tool_filter

    result = _search(agent)

    assert result["tools"] == ["analyze_data_file"]
    assert agent.tool_loader.jumped is True
    assert agent._active_tool_filter == list(agent._tools_registry)
    assert agent._conversation_request_kwargs() == {"id_slot": FULL_TOOLS_SLOT}


def test_a_hit_below_the_top_two_still_jumps(searching):
    """Every tool returned must be callable, not just the two with docs."""
    agent, _ = searching(
        {
            "read_file": 0.9,
            "recall": 0.8,
            "remember": 0.7,
            "forget": 0.6,
            "analyze_data_file": 0.5,
        }
    )
    result = _search(agent)
    assert result["tools"][-1] == "analyze_data_file"
    assert agent.tool_loader.jumped is True


def test_a_search_after_the_jump_changes_nothing(searching):
    agent, _ = searching({"analyze_data_file": 0.9})
    _search(agent)
    agent._tool_list_notice()
    full = list(agent._active_tool_filter)

    _search(agent)

    assert agent._active_tool_filter == full
    assert agent._tool_list_notice() is False


def test_docs_are_for_the_top_two_only(searching):
    agent, _ = searching({"read_file": 0.9, "recall": 0.8, "remember": 0.7})

    result = _search(agent)

    assert result["tools"] == ["read_file", "recall", "remember"]
    assert result["docs"].count("[Tool docs: ") == 2
    assert "[Tool docs: read_file]" in result["docs"]
    assert "[Tool docs: recall]" in result["docs"]
    assert "[Tool docs: remember]" not in result["docs"]
    assert agent._tool_docs_sent == {"read_file", "recall"}


def test_the_docs_are_the_full_schema_not_the_thin_one(searching):
    agent, _ = searching({"read_file": 0.9})
    full = agent._tools_registry["read_file"]["description"].strip()
    assert full.splitlines()[0] in _search(agent)["docs"]


def test_matching_skills_come_back_with_their_descriptions(searching):
    skills = {
        "pdf-tricks": SimpleNamespace(description="Handle PDFs: forms and tables"),
        "unrelated": SimpleNamespace(description="Nothing to do with it"),
    }
    agent, _ = searching(
        {"read_file": 0.3, "pdf-tricks": 0.9, "unrelated": 0.0}, skills
    )

    result = _search(agent)

    assert result["skills"] == [
        {"name": "pdf-tricks", "description": "Handle PDFs: forms and tables"}
    ]
    assert "load_skill" in result["next_step"]


def test_a_skill_hit_alone_is_no_jump(searching):
    agent, _ = searching(
        {"pdf-tricks": 0.9}, {"pdf-tricks": SimpleNamespace(description="PDFs")}
    )

    result = _search(agent)

    assert result["status"] == "success"
    assert result["tools"] == []
    assert [s["name"] for s in result["skills"]] == ["pdf-tricks"]
    assert agent.tool_loader.jumped is False


def test_an_already_loaded_skill_is_not_offered_again(searching):
    agent, _ = searching(
        {"pdf-tricks": 0.9}, {"pdf-tricks": SimpleNamespace(description="PDFs")}
    )
    agent._loaded_skills = {"pdf-tricks": object()}

    result = _search(agent)

    assert result["skills"] == []
    assert result["tools"] == []
    assert "Nothing matches" in result["message"]


def test_search_tools_never_finds_itself(searching):
    agent, _ = searching({"search_tools": 0.99, "read_file": 0.5})
    assert _search(agent)["tools"] == ["read_file"]


def test_nothing_matching_says_so_and_stays_put(searching):
    agent, _ = searching({"read_file": 0.05})

    result = _search(agent)

    assert result == {
        "status": "success",
        "tools": [],
        "skills": [],
        "message": "Nothing matches. Tell the user this is not available.",
    }
    assert agent.tool_loader.jumped is False


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_query_is_refused_and_not_counted(searching, blank):
    agent, embed = searching({"read_file": 0.9})

    result = _search(agent, blank)

    assert result["status"] == "error"
    assert "query" in result["error"]
    assert agent._tool_searches == 0
    assert embed.queries == []


def test_a_long_query_is_cut_before_it_is_embedded(searching):
    agent, embed = searching({"read_file": 0.9})
    _search(agent, "word   " * 1000)
    assert {len(q) for q in embed.queries} == {MAX_TOOL_SEARCH_CHARS}


def test_searches_are_capped_each_turn(searching):
    agent, embed = searching({"read_file": 0.9})

    results = [_search(agent) for _ in range(MAX_TOOL_SEARCHES + 1)]

    assert MAX_TOOL_SEARCHES == 3
    assert [r["status"] for r in results] == ["success"] * 3 + ["error"]
    assert "Searched 3 times this turn" in results[-1]["error"]
    assert len(embed.queries) == 3


def test_the_cap_and_sent_docs_reset_each_turn(searching):
    agent, _ = searching({"read_file": 0.9})
    agent._begin_turn_provenance = lambda: None
    for _ in range(MAX_TOOL_SEARCHES):
        _search(agent)

    with patch.object(Agent, "_on_task_start"):
        agent._on_task_start("next turn")

    assert agent._tool_searches == 0
    assert agent._tool_docs_sent == set()
    assert _search(agent)["status"] == "success"


def test_a_dead_embedder_reports_an_error_and_keeps_the_list(searching):
    agent, _ = searching({"analyze_data_file": 0.9})
    before = list(agent._active_tool_filter)

    def dead(_text):
        raise ConnectionError("embedder down")

    agent.tool_loader._embed_fn = dead
    agent.tool_loader._embed_cache.clear()

    result = _search(agent)

    assert result["status"] == "error"
    assert "embedder down" in result["error"]
    assert "Use a tool from your list" in result["error"]
    assert agent.tool_loader.jumped is False
    assert agent.tool_loader.session_disabled is False
    assert agent._active_tool_filter == before


# ── native-only prompt line ───────────────────────────────────────────────


def test_native_doc_prompt_explains_search_tools():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    agent.rag = None  # no-docs branch keeps _get_system_prompt light
    prompt = agent._get_system_prompt()
    assert "**TOOLS:**" in prompt
    assert "search_tools" in prompt
    assert "load_tools" not in prompt


def test_non_native_doc_prompt_omits_the_search_tools_line():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=True
    )
    agent.rag = None
    agent.model_id = None  # non-tool-calling: the text tool list is its docs
    prompt = agent._get_system_prompt()
    assert "**TOOLS:**" not in prompt


def test_loader_off_doc_prompt_omits_the_search_tools_line():
    agent = build_doc_agent_skeleton(
        profile="doc", deterministic=True, dynamic_tools=False
    )
    agent.rag = None
    prompt = agent._get_system_prompt()
    assert "**TOOLS:**" not in prompt
    assert "search_tools" not in prompt


# ── turn setup ordering ───────────────────────────────────────────────────


class _TurnSetupDone(Exception):
    """Stops the turn once the setup steps under test have run."""


def test_skill_body_filter_runs_before_the_turn_tool_filter(monkeypatch):
    """The tool filter admits the ``tools_required`` of the skills active this
    turn, so the body filter that decides which skills are active has to run
    first. The other order hands the model a recipe naming tools the same
    prompt does not contain.
    """
    order: list[str] = []
    agent = _bare_agent(_turn_seq=0)

    monkeypatch.setattr(
        ChatAgent, "_begin_turn_provenance", lambda self: None, raising=True
    )
    monkeypatch.setattr(ChatAgent, "_on_task_start", lambda self, q: None, raising=True)
    monkeypatch.setattr(
        ChatAgent,
        "_refresh_active_skill_filter",
        lambda self, q: order.append("bodies"),
        raising=True,
    )
    monkeypatch.setattr(
        ChatAgent,
        "_refresh_active_tool_filter",
        lambda self, q: order.append("tools"),
        raising=True,
    )

    def _stop(self, user_input):
        raise _TurnSetupDone

    monkeypatch.setattr(ChatAgent, "_begin_turn_record", _stop, raising=True)

    with pytest.raises(_TurnSetupDone):
        agent._process_query_impl("read me an rss feed")

    assert order == ["bodies", "tools"]
