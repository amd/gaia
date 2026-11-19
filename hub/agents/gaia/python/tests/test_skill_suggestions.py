# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GaiaAgent's per-turn skill suggestions: settings, gates and the turn line.

Built via ``GaiaAgent.__new__`` like ``test_lazy_skill_activation.py``, so no
LLM stack or Lemonade is touched; selection itself is covered by
``tests/unit/test_skill_suggester.py``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pytest
from gaia_agent import agent as agent_module
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig, _apply_fast_mode

from gaia.agents.base.agent import Agent
from gaia.agents.base.model_features import COMPACT
from gaia.agents.base.skill_suggester import (
    DEFAULT_AUTOLOAD_MARGIN,
    DEFAULT_AUTOLOAD_THRESHOLD,
    DEFAULT_SUGGEST_THRESHOLD,
    DOC_PROMPT,
    SkillMatches,
    SkillSuggester,
)
from gaia.agents.registry import DEFAULT_EMBEDDING_MODEL, get_embedding_model_for_device
from gaia.agents.tools.shell_tools import skill_granted_binaries
from gaia.skills.binaries import BinaryGrants
from gaia.skills.manager import ROOT_AGENT_BUNDLED, ROOT_CLAUDE_PROJECT, ROOT_USER

#: The default context window, which the auto-load budget is a share of.
WINDOW = 32768


@pytest.fixture(autouse=True)
def _no_autoload_env(monkeypatch):
    for name in (
        "GAIA_SKILL_AUTOLOAD",
        "GAIA_SKILL_AUTOLOAD_TAU",
        "GAIA_SKILL_AUTOLOAD_MARGIN",
        "GAIA_SKILL_DISCOVERY",
        "GAIA_MEMORY_DISABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def _never(_text):
    raise AssertionError("the embedder should not be called")


def _agent(monkeypatch, *, catalog=True, **config):
    monkeypatch.delenv("GAIA_SKILL_SUGGEST", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(**config)
    agent._skill_catalog_enabled = catalog
    agent._embed_text = _never
    agent._embed_texts_batch = _never
    agent.skill_loader = None
    return agent


def test_on_by_default_with_the_calibrated_threshold():
    config = GaiaAgentConfig()
    assert config.skill_suggestions is True
    assert config.skill_suggestion_threshold == DEFAULT_SUGGEST_THRESHOLD


def test_built_when_on(monkeypatch):
    agent = _agent(monkeypatch, skill_suggestion_threshold=0.4)
    suggester = agent._maybe_build_skill_suggester()
    assert isinstance(suggester, SkillSuggester)
    assert suggester.threshold == pytest.approx(0.4)


def test_off_without_the_catalogue(monkeypatch):
    agent = _agent(monkeypatch, catalog=False)
    monkeypatch.setenv("GAIA_SKILL_SUGGEST", "1")
    assert agent._maybe_build_skill_suggester() is None


def test_config_can_turn_it_off(monkeypatch):
    agent = _agent(monkeypatch, skill_suggestions=False)
    assert agent._maybe_build_skill_suggester() is None


@pytest.mark.parametrize("raw,built", [("0", False), ("1", True)])
def test_env_wins_over_config(monkeypatch, raw, built):
    agent = _agent(monkeypatch, skill_suggestions=not built)
    monkeypatch.setenv("GAIA_SKILL_SUGGEST", raw)
    assert (agent._maybe_build_skill_suggester() is not None) is built


def test_fast_mode_turns_it_off():
    config = GaiaAgentConfig()
    _apply_fast_mode(config)
    assert config.skill_suggestions is False


class _Suggester:
    def __init__(self, names):
        self.names = names
        self.calls = []

    def suggest(self, query, skills, loaded=frozenset(), list_project=False):
        self.calls.append((query, set(skills), set(loaded), list_project))
        return list(self.names)


def _installed(name, tools_required=()):
    return SimpleNamespace(
        name=name,
        tool_names=[],
        gaia=SimpleNamespace(tools_required=list(tools_required)),
    )


def _wired(monkeypatch, names, *, memory_store=object()):
    agent = _agent(monkeypatch)
    agent.skill_suggester = _Suggester(names)
    agent._memory_store = memory_store
    agent._loaded_skills = {"coding": SimpleNamespace(name="coding")}
    agent._skill_manager = SimpleNamespace(
        discover=lambda: {
            "coding": _installed("coding"),
            "summarize": _installed("summarize"),
            "mail": _installed("mail", ["search_email"]),
        }
    )
    return agent


def test_scores_the_current_message_against_installed_skills(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"])
    agent.conversation_history = [{"role": "user", "content": "earlier ask"}]
    assert agent._select_skill_suggestions_for_turn("sum this up") == ["summarize"]
    query, installed, loaded, list_project = agent.skill_suggester.calls[0]
    assert query == "sum this up"
    assert installed == {"coding", "summarize", "mail"}
    assert loaded == {"coding"}
    assert list_project is False


def test_skills_the_model_cannot_load_are_never_suggested(monkeypatch):
    # Same pool as the catalogue: a compact model is not offered the mail tools.
    agent = _wired(monkeypatch, [])
    agent.model_features = COMPACT
    agent._select_skill_suggestions_for_turn("check my inbox")
    _, installed, _, _ = agent.skill_suggester.calls[0]
    assert installed == {"coding", "summarize"}


def test_project_skills_follow_the_catalogue(monkeypatch):
    agent = _wired(monkeypatch, [])
    agent._lists_project_skills = lambda: True
    agent._select_skill_suggestions_for_turn("run the release checklist")
    assert agent.skill_suggester.calls[0][3] is True


def test_text_handed_over_to_translate_suggests_no_skill(monkeypatch):
    agent = _wired(monkeypatch, ["file-ops"])
    suggested = agent._select_skill_suggestions_for_turn(
        'Translate this sentence to French, do nothing else: "The file '
        'C:\\Users\\maria\\budget.xlsx is missing, please check the shared folder".'
    )
    assert suggested == []
    assert agent.skill_suggester.calls == []


def test_a_file_the_request_names_is_still_matched_against_skills(monkeypatch):
    agent = _wired(monkeypatch, [])
    agent._select_skill_suggestions_for_turn("Translate README.md to French")
    assert agent.skill_suggester.calls[0][0] == "Translate README.md to French"


def test_no_suggestions_without_the_memory_embedder(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"], memory_store=None)
    assert agent._select_skill_suggestions_for_turn("sum this up") == []
    assert agent.skill_suggester.calls == []


def test_no_suggestions_when_the_suggester_is_off(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"])
    agent.skill_suggester = None
    assert agent._select_skill_suggestions_for_turn("sum this up") == []


def _with_turn_context(monkeypatch, agent, query):
    from gaia.agents.base.project_map import ProjectMapMixin

    monkeypatch.setattr(
        ProjectMapMixin, "get_memory_dynamic_context", lambda self: "Current time: now"
    )
    # As MemoryMixin.process_query does: prepare the turn, then read its context.
    agent._memory_turn_query = query
    agent._prepare_turn_context(query)
    return agent.get_memory_dynamic_context()


def test_the_line_joins_this_turns_context(monkeypatch):
    agent = _wired(monkeypatch, ["summarize", "data-explore"])
    context = _with_turn_context(monkeypatch, agent, "sum up this spreadsheet")
    assert context.splitlines() == [
        "Current time: now",
        "Call load_skill('summarize') before you start: it covers this kind of "
        "request. Also consider: data-explore.",
    ]
    assert agent.skill_suggester.calls[0][0] == "sum up this spreadsheet"


def test_no_match_leaves_the_turn_context_alone(monkeypatch):
    agent = _wired(monkeypatch, [])
    assert _with_turn_context(monkeypatch, agent, "thanks") == "Current time: now"


def test_the_system_prompt_never_carries_a_suggestion():
    """A per-turn line in the system prompt would break the prompt cache."""
    fragments = [
        name
        for name in dir(GaiaAgent)
        if name.startswith("get_") and name.endswith("_system_prompt")
    ]
    assert not [name for name in fragments if "suggest" in name]
    assert not [n for n in Agent.VOLATILE_PROMPT_FRAGMENTS if "suggest" in n]


def test_a_real_suggester_reaches_the_turn_context(monkeypatch):
    agent = _agent(monkeypatch)
    skills = {
        name: SimpleNamespace(
            name=name,
            description=f"does {name}",
            root="agent",
            tool_names=[],
            gaia=SimpleNamespace(pairs_with=[], tools_required=[]),
        )
        for name in ("image-gen", "coding")
    }
    axis = {f"{DOC_PROMPT}does image-gen": 0, f"{DOC_PROMPT}does coding": 1}

    def embed(text):
        v = np.zeros(4, dtype=np.float32)
        v[axis.get(text, 0)] = 1.0
        return v

    agent.skill_suggester = SkillSuggester(embed)
    agent._memory_store = object()
    agent._loaded_skills = {}
    agent._skill_manager = SimpleNamespace(discover=lambda: skills)
    context = _with_turn_context(monkeypatch, agent, "draw a lighthouse")
    assert "Call load_skill('image-gen') before you start" in context


# ── auto-load: settings ─────────────────────────────────────────────────────


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def _inactive(caplog):
    return [m for m in _warnings(caplog) if "auto-load was asked for" in m]


def test_autoload_is_opt_in_with_the_calibrated_threshold(monkeypatch, caplog):
    config = GaiaAgentConfig()
    assert config.skill_autoload is False
    assert config.skill_autoload_threshold == DEFAULT_AUTOLOAD_THRESHOLD
    assert config.skill_autoload_margin == DEFAULT_AUTOLOAD_MARGIN
    with caplog.at_level(logging.WARNING):
        suggester = _agent(monkeypatch)._maybe_build_skill_suggester()
    assert suggester is not None
    assert suggester.autoload_threshold is None
    assert _inactive(caplog) == []


def test_config_turns_autoload_on_and_carries_its_threshold_and_margin(monkeypatch):
    agent = _agent(monkeypatch, skill_autoload=True)
    suggester = agent._maybe_build_skill_suggester()
    assert suggester.autoload_threshold == pytest.approx(DEFAULT_AUTOLOAD_THRESHOLD)
    assert suggester.autoload_margin == pytest.approx(DEFAULT_AUTOLOAD_MARGIN)
    assert suggester.threshold == pytest.approx(DEFAULT_SUGGEST_THRESHOLD)
    agent = _agent(
        monkeypatch,
        skill_autoload=True,
        skill_autoload_threshold=0.6,
        skill_autoload_margin=0.01,
    )
    suggester = agent._maybe_build_skill_suggester()
    assert suggester.autoload_threshold == pytest.approx(0.6)
    assert suggester.autoload_margin == pytest.approx(0.01)


def test_env_sets_the_autoload_threshold_and_margin_over_config(monkeypatch):
    agent = _agent(
        monkeypatch,
        skill_autoload=True,
        skill_autoload_threshold=0.6,
        skill_autoload_margin=0.01,
    )
    monkeypatch.setenv("GAIA_SKILL_AUTOLOAD_TAU", "0.45")
    monkeypatch.setenv("GAIA_SKILL_AUTOLOAD_MARGIN", "0.02")
    suggester = agent._maybe_build_skill_suggester()
    assert suggester.autoload_threshold == pytest.approx(0.45)
    assert suggester.autoload_margin == pytest.approx(0.02)


@pytest.mark.parametrize(
    "name", ["GAIA_SKILL_AUTOLOAD_TAU", "GAIA_SKILL_AUTOLOAD_MARGIN"]
)
def test_a_malformed_autoload_number_fails_at_build(monkeypatch, name):
    agent = _agent(monkeypatch, skill_autoload=True)
    monkeypatch.setenv(name, "lots")
    with pytest.raises(ValueError, match=name):
        agent._maybe_build_skill_suggester()


def test_the_threshold_env_alone_does_not_turn_autoload_on(monkeypatch):
    agent = _agent(monkeypatch)
    monkeypatch.setenv("GAIA_SKILL_AUTOLOAD_TAU", "0.45")
    assert agent._maybe_build_skill_suggester().autoload_threshold is None


@pytest.mark.parametrize("raw,on", [("0", False), ("1", True)])
def test_autoload_env_wins_over_config(monkeypatch, raw, on):
    agent = _agent(monkeypatch, skill_autoload=not on)
    monkeypatch.setenv("GAIA_SKILL_AUTOLOAD", raw)
    suggester = agent._maybe_build_skill_suggester()
    assert (suggester.autoload_threshold is not None) is on


# ── auto-load: asked for but inactive ───────────────────────────────────────


@pytest.mark.parametrize(
    "embedder",
    [
        get_embedding_model_for_device("npu"),
        "nomic-embed-text-v2-moe-GGUF",
        DEFAULT_EMBEDDING_MODEL.removeprefix("user."),
        DEFAULT_EMBEDDING_MODEL.upper(),
    ],
)
@pytest.mark.parametrize("env", [None, "1"])
def test_autoload_off_for_an_uncalibrated_embedder(monkeypatch, caplog, embedder, env):
    """The threshold is embedder-specific, so even an explicit opt-in cannot
    auto-load on scores it was never measured against; suggestions stay."""
    agent = _agent(monkeypatch, skill_autoload=True)
    if env is not None:
        monkeypatch.setenv("GAIA_SKILL_AUTOLOAD", env)
    agent._embedding_model = embedder
    with caplog.at_level(logging.WARNING):
        suggester = agent._maybe_build_skill_suggester()
    assert suggester is not None
    assert suggester.autoload_threshold is None
    (warning,) = _inactive(caplog)
    assert "calibrated for" in warning and embedder in warning


@pytest.mark.parametrize("how", ["env", "config"])
def test_autoload_without_the_catalogue_warns_once(monkeypatch, caplog, how):
    agent = _agent(monkeypatch, catalog=False, skill_autoload=how == "config")
    if how == "env":
        monkeypatch.setenv("GAIA_SKILL_AUTOLOAD", "1")
    with caplog.at_level(logging.WARNING):
        assert agent._maybe_build_skill_suggester() is None
        assert agent._maybe_build_skill_suggester() is None
    (warning,) = _inactive(caplog)
    assert "catalogue is off" in warning


def test_autoload_without_suggestions_warns_once(monkeypatch, caplog):
    agent = _agent(monkeypatch, skill_suggestions=False, skill_autoload=True)
    with caplog.at_level(logging.WARNING):
        assert agent._maybe_build_skill_suggester() is None
    (warning,) = _inactive(caplog)
    assert "suggestions are off" in warning


def test_autoload_in_fast_mode_warns_once(monkeypatch, caplog):
    agent = _agent(monkeypatch, skill_autoload=True, fast=True)
    _apply_fast_mode(agent.config)
    agent._skill_catalog_enabled = agent._resolve_skill_catalog_enabled()
    with caplog.at_level(logging.WARNING):
        assert agent._maybe_build_skill_suggester() is None
    (warning,) = _inactive(caplog)
    assert "fast mode" in warning


def test_fast_mode_keeps_autoload_off_even_with_suggestions_forced_on(
    monkeypatch, caplog
):
    agent = _agent(monkeypatch, skill_autoload=True, fast=True)
    _apply_fast_mode(agent.config)
    monkeypatch.setenv("GAIA_SKILL_SUGGEST", "1")
    with caplog.at_level(logging.WARNING):
        suggester = agent._maybe_build_skill_suggester()
    assert suggester is not None and suggester.autoload_threshold is None
    assert len(_inactive(caplog)) == 1


def test_autoload_with_memory_switched_off_warns_at_build(monkeypatch, caplog):
    agent = _agent(monkeypatch, skill_autoload=True)
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with caplog.at_level(logging.WARNING):
        agent.skill_suggester = agent._maybe_build_skill_suggester()
        agent._memory_store = None
        assert agent._select_skill_matches_for_turn("sum this up") == SkillMatches()
    (warning,) = _inactive(caplog)
    assert "GAIA_MEMORY_DISABLED" in warning


def test_autoload_without_a_memory_store_warns_once_across_turns(monkeypatch, caplog):
    agent = _loading(monkeypatch, ["summarize"])
    agent._memory_store = None
    with caplog.at_level(logging.WARNING):
        _with_turn_context(monkeypatch, agent, "sum this up")
        _with_turn_context(monkeypatch, agent, "and this")
    (warning,) = _inactive(caplog)
    assert "memory store is unavailable" in warning
    assert agent.loads == []


def test_suggestions_alone_never_warn_about_autoload(monkeypatch, caplog):
    agent = _wired(monkeypatch, ["summarize"], memory_store=None)
    with caplog.at_level(logging.WARNING):
        assert agent._select_skill_suggestions_for_turn("sum this up") == []
    assert _inactive(caplog) == []


# ── auto-load: the turn ─────────────────────────────────────────────────────


class _AutoSuggester:
    """A suggester with auto-load on: *auto* to load, *names* to suggest."""

    autoload_threshold = DEFAULT_AUTOLOAD_THRESHOLD

    def __init__(self, names, auto):
        self.names = list(names)
        self.auto = list(auto)
        self.calls = []

    def select(self, query, skills, loaded=frozenset(), list_project=False):
        self.calls.append((query, set(skills), set(loaded), list_project))
        return SkillMatches(
            auto_load=[n for n in self.auto if n not in loaded],
            suggest=[n for n in self.names if n not in loaded],
        )


def _full_skill(
    name,
    body="x" * 400,
    permissions=(),
    tools=(),
    root=ROOT_AGENT_BUNDLED,
    directory=None,
):
    from gaia.skills.permissions import parse_permissions

    return SimpleNamespace(
        name=name,
        body=body,
        root=root,
        directory=directory,
        tool_names=[],
        gaia=SimpleNamespace(tools=list(tools), tools_required=[]),
        parsed_permissions=lambda: parse_permissions(
            list(permissions), skill_name=name
        ),
    )


def _loading(monkeypatch, auto, *, names=(), library=None, fail=None, window=WINDOW):
    """An agent whose suggester auto-loads *auto* and whose load_skill works."""
    agent = _agent(monkeypatch, skill_autoload=True)
    agent.skill_suggester = _AutoSuggester(names, auto)
    agent._memory_store = object()
    agent._loaded_skills = {}
    agent._granted_binaries = BinaryGrants()
    agent.console = SimpleNamespace(full_access=False)
    agent.window = window
    monkeypatch.setattr(agent_module, "context_window", lambda host: host.window)
    library = library or {}
    for name in [*auto, *names]:
        library.setdefault(name, _full_skill(name))
    agent._skill_manager = SimpleNamespace(
        discover=lambda: dict(library), load=lambda name: library[name]
    )
    agent.loads = []
    agent.announced = []

    def load_skill(name):
        if fail is not None and name == fail:
            from gaia.skills.errors import SkillPermissionError

            raise SkillPermissionError(f"{name} needs a connector")
        agent.loads.append(name)
        agent._loaded_skills[name] = library[name]
        for permission in library[name].parsed_permissions():
            agent._granted_binaries.grant(permission.scope, skill_name=name)
        return library[name]

    def base_unload(self, name):
        """What Agent.unload_skill does to the state these tests look at."""
        if self._loaded_skills.pop(name, None) is None:
            return False
        self._granted_binaries.revoke_skill(name)
        return True

    monkeypatch.setattr(Agent, "unload_skill", base_unload)
    agent.load_skill = load_skill
    agent._announce_skill = lambda name, how: agent.announced.append((name, how))
    return agent


def _suggests(context, name):
    """The last line names *name*: as the instruction, or after an auto-load
    as available."""
    line = context.splitlines()[-1]
    return line.startswith(("Call load_skill(", "Also available: ")) and name in line


def test_a_high_confidence_match_is_loaded_and_announced(monkeypatch):
    agent = _loading(monkeypatch, ["report-from-sources"], names=["office-docs"])
    context = _with_turn_context(monkeypatch, agent, "build a deck from my docs")
    assert agent.loads == ["report-from-sources"]
    assert agent.announced == [("report-from-sources", "auto")]
    assert context.splitlines() == [
        "Current time: now",
        "Loaded report-from-sources because it matches this request — its "
        "instructions are in the system prompt.",
        "Also available: office-docs. Call load_skill for one only if this "
        "request needs it.",
    ]
    # Pinned, so this turn's body selection cannot hide what it was loaded for.
    assert "report-from-sources" in agent._sticky_skill_turns
    query, installed, loaded, list_project = agent.skill_suggester.calls[0]
    assert query == "build a deck from my docs"
    assert installed == {"report-from-sources", "office-docs"}
    assert loaded == set()
    assert list_project is False


def test_with_autoload_off_a_strong_match_is_only_suggested(monkeypatch):
    agent = _wired(monkeypatch, ["summarize"])
    agent.load_skill = lambda name: pytest.fail("nothing may load")
    context = _with_turn_context(monkeypatch, agent, "sum this up")
    assert _suggests(context, "summarize")
    assert "Loaded" not in context


def test_reading_the_context_outside_a_turn_never_loads(monkeypatch):
    """The eval memory harness reads the context to inspect it: a match that
    would load in a turn is reported as a suggestion, and nothing changes."""
    agent = _loading(monkeypatch, ["summarize"], names=["pdf"])
    from gaia.agents.base.project_map import ProjectMapMixin

    monkeypatch.setattr(
        ProjectMapMixin, "get_memory_dynamic_context", lambda self: "Current time: now"
    )
    agent._memory_turn_query = "sum this up"
    context = agent.get_memory_dynamic_context()
    assert agent.loads == [] and agent._loaded_skills == {}
    assert context.splitlines()[-1] == (
        "Call load_skill('summarize') before you start: it covers this kind of "
        "request. Also consider: pdf."
    )


def test_a_prepared_turn_is_reported_once(monkeypatch):
    agent = _loading(monkeypatch, ["summarize"])
    first = _with_turn_context(monkeypatch, agent, "sum this up")
    assert "Loaded summarize" in first
    # A second read is an inspection: the skill is loaded, so nothing to say.
    assert agent.get_memory_dynamic_context() == "Current time: now"
    assert agent.loads == ["summarize"]


def test_a_loaded_skill_is_not_reloaded_or_re_suggested(monkeypatch):
    agent = _loading(monkeypatch, ["report-from-sources"])
    _with_turn_context(monkeypatch, agent, "build a deck from my docs")
    context = _with_turn_context(monkeypatch, agent, "now a second deck")
    assert agent.loads == ["report-from-sources"]
    assert context == "Current time: now"
    assert agent.skill_suggester.calls[1][2] == {"report-from-sources"}


def test_an_unloaded_auto_load_is_only_suggested_after(monkeypatch):
    agent = _loading(monkeypatch, ["summarize"])
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.unload_skill("summarize") is True
    context = _with_turn_context(monkeypatch, agent, "sum this up too")
    assert agent.loads == ["summarize"]
    assert _suggests(context, "summarize")
    assert "Loaded summarize" not in context


def test_a_skill_the_model_loaded_and_unloaded_is_never_auto_loaded(
    monkeypatch, caplog
):
    """Every unload counts, not only the unload of an auto-loaded skill."""
    agent = _loading(monkeypatch, ["summarize"])
    agent.load_skill("summarize")  # the model's own call, before any auto-load
    agent.loads.clear()
    assert agent.unload_skill("summarize") is True
    assert agent._unloaded_skills == {"summarize"}
    with caplog.at_level(logging.INFO):
        context = _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == []
    assert _suggests(context, "summarize")
    assert "it was unloaded earlier this session" in caplog.text


def test_unloading_a_skill_that_is_not_loaded_records_nothing(monkeypatch):
    agent = _loading(monkeypatch, ["summarize"])
    assert agent.unload_skill("summarize") is False
    assert not getattr(agent, "_unloaded_skills", None)
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == ["summarize"]


def test_a_rebuilt_agent_keeps_what_was_auto_loaded_and_unloaded(monkeypatch):
    from gaia_agent.session_registry import _carry_session_state

    old = _loading(monkeypatch, ["summarize", "pdf"])
    _with_turn_context(monkeypatch, old, "sum up this pdf")
    old.unload_skill("pdf")
    old._autoload_granted = {"summarize": ["pytest"]}

    new = _loading(monkeypatch, ["summarize", "pdf"])
    _carry_session_state(old, new, "session-1")
    assert new.loads == ["summarize"]  # the loaded skill came across
    assert new._autoloaded_skills == {"summarize", "pdf"}
    assert new._unloaded_skills == {"pdf"}
    assert new._autoload_granted == {"summarize": ["pytest"]}
    assert new._unloaded_skills is not old._unloaded_skills

    new.loads.clear()
    context = _with_turn_context(monkeypatch, new, "sum up this pdf")
    assert new.loads == []
    assert _suggests(context, "pdf")


def test_a_rebuild_from_an_agent_without_the_records_carries_none(monkeypatch):
    from gaia_agent.session_registry import _carry_session_state

    new = _loading(monkeypatch, ["summarize"])
    _carry_session_state(SimpleNamespace(loaded_skills={}), new, "session-1")
    assert getattr(new, "_unloaded_skills", None) is None
    assert getattr(new, "_autoload_granted", None) is None


def test_a_failed_auto_load_falls_back_to_the_suggestion(monkeypatch, caplog):
    agent = _loading(monkeypatch, ["inbox-triage"], fail="inbox-triage")
    with caplog.at_level(logging.WARNING):
        context = _with_turn_context(monkeypatch, agent, "check my mail")
    assert _suggests(context, "inbox-triage")
    assert "could not auto-load 'inbox-triage'" in caplog.text


def test_no_embedder_means_no_auto_load(monkeypatch):
    agent = _loading(monkeypatch, ["summarize"])
    agent._memory_store = None
    assert _with_turn_context(monkeypatch, agent, "sum this up") == (
        "Current time: now"
    )
    assert agent.loads == []
    assert agent.skill_suggester.calls == []


def test_text_handed_over_to_translate_auto_loads_no_skill(monkeypatch):
    agent = _loading(monkeypatch, ["file-ops"])
    context = _with_turn_context(
        monkeypatch,
        agent,
        'Translate this sentence to French, do nothing else: "The file '
        'C:\\Users\\maria\\budget.xlsx is missing, please check the shared folder".',
    )
    assert context == "Current time: now"
    assert agent.loads == []
    assert agent.skill_suggester.calls == []


def _plain(name, pairs=()):
    return SimpleNamespace(
        name=name,
        description=f"does {name}",
        root=ROOT_AGENT_BUNDLED,
        tool_names=[],
        gaia=SimpleNamespace(pairs_with=list(pairs), tools_required=[]),
    )


def _scoring(names, scores):
    """An embedder giving each skill in *names* its score for any request."""
    axis = {f"{DOC_PROMPT}does {name}": i for i, name in enumerate(names)}

    def embed(text):
        v = np.zeros(len(names) + 1, dtype=np.float32)
        if text in axis:
            v[axis[text]] = 1.0
        else:
            for name, score in scores.items():
                v[names.index(name)] = score
        return v

    return embed


def test_a_real_suggester_loads_the_best_match_and_names_its_companions(monkeypatch):
    names = ["slide-decks", "report-from-sources", "office-docs"]
    skills = {
        "slide-decks": _plain("slide-decks", pairs=names[1:]),
        "report-from-sources": _plain("report-from-sources", pairs=["office-docs"]),
        "office-docs": _plain("office-docs"),
    }
    agent = _loading(monkeypatch, [], names=names)
    agent.skill_suggester = SkillSuggester(
        _scoring(
            names,
            {"slide-decks": 0.62, "report-from-sources": 0.60, "office-docs": 0.595},
        ),
        autoload_threshold=DEFAULT_AUTOLOAD_THRESHOLD,
    )
    agent._skill_manager = SimpleNamespace(
        discover=lambda: skills, load=lambda name: _full_skill(name)
    )
    context = _with_turn_context(monkeypatch, agent, "turn my notes into slides")
    assert agent.loads == ["slide-decks"]
    assert context.splitlines()[-2:] == [
        "Loaded slide-decks because it matches this request — its instructions "
        "are in the system prompt.",
        "Also available: report-from-sources, office-docs. Call load_skill for "
        "one only if this request needs it.",
    ]


def test_a_companion_that_leads_the_rest_itself_loads_with_the_best_match(
    monkeypatch,
):
    names = ["alpha", "beta", "gamma"]
    skills = {
        "alpha": _plain("alpha", pairs=["beta"]),
        "beta": _plain("beta"),
        "gamma": _plain("gamma"),
    }
    agent = _loading(monkeypatch, [], names=names)
    agent.skill_suggester = SkillSuggester(
        _scoring(names, {"alpha": 0.62, "beta": 0.60, "gamma": 0.41}),
        autoload_threshold=DEFAULT_AUTOLOAD_THRESHOLD,
    )
    agent._skill_manager = SimpleNamespace(
        discover=lambda: skills, load=lambda name: _full_skill(name)
    )
    context = _with_turn_context(monkeypatch, agent, "do both things")
    assert agent.loads == ["alpha", "beta"]
    assert "Loaded alpha and beta because they match" in context
    assert "gamma" not in context


# ── auto-load: trust ────────────────────────────────────────────────────────


def _locked(tmp_path, name, **entry):
    """A user-root skill folder whose ``skill-lock.json`` holds *entry*."""
    from gaia.skills.lock import LockEntry, SkillLock

    root = tmp_path / "skills"
    (root / name).mkdir(parents=True, exist_ok=True)
    if entry:
        lock = SkillLock.load(root)
        lock.record(LockEntry(name=name, version="1.0.0", **entry))
        lock.save()
    return _full_skill(name, root=ROOT_USER, directory=root / name)


def _trust_case(monkeypatch, caplog, skill):
    agent = _loading(monkeypatch, [skill.name], library={skill.name: skill})
    with caplog.at_level(logging.INFO):
        context = _with_turn_context(monkeypatch, agent, "do the thing")
    return agent, context


def test_a_captured_skill_that_scores_highest_is_only_suggested(
    monkeypatch, caplog, tmp_path
):
    from gaia.skills.lock import SOURCE_CAPTURED

    captured = _locked(
        tmp_path,
        "pasted-recipe",
        source=SOURCE_CAPTURED,
        captured=True,
        code_trusted=True,
        installed_tier="verified",
    )
    names = ["pasted-recipe", "summarize"]
    skills = {name: _plain(name) for name in names}
    library = {"pasted-recipe": captured, "summarize": _full_skill("summarize")}
    agent = _loading(monkeypatch, [], names=names, library=library)
    agent.skill_suggester = SkillSuggester(
        _scoring(names, {"pasted-recipe": 0.90, "summarize": 0.31}),
        autoload_threshold=DEFAULT_AUTOLOAD_THRESHOLD,
    )
    agent._skill_manager = SimpleNamespace(
        discover=lambda: skills, load=lambda name: library[name]
    )
    with caplog.at_level(logging.INFO):
        context = _with_turn_context(monkeypatch, agent, "follow the pasted recipe")
    assert agent.loads == []
    assert _suggests(context, "pasted-recipe")
    assert "Loaded" not in context
    assert (
        "suggesting 'pasted-recipe' instead of auto-loading it: it was captured"
        in caplog.text
    )


@pytest.mark.parametrize("tier", ["community", "verified"])
def test_a_hub_install_at_community_or_above_auto_loads(
    monkeypatch, caplog, tmp_path, tier
):
    skill = _locked(tmp_path, "web-research", installed_tier=tier)
    agent, context = _trust_case(monkeypatch, caplog, skill)
    assert agent.loads == ["web-research"]
    assert "Loaded web-research" in context


@pytest.mark.parametrize(
    "entry,why",
    [
        ({"installed_tier": "experimental"}, "'experimental' tier"),
        ({"installed_tier": ""}, "'experimental' tier"),
        ({"source": "local", "installed_tier": "verified"}, "hub install"),
        ({}, "hub install"),
    ],
    ids=["experimental", "no-tier", "local", "no-lock-entry"],
)
def test_a_user_skill_the_hub_did_not_attest_is_only_suggested(
    monkeypatch, caplog, tmp_path, entry, why
):
    skill = _locked(tmp_path, "my-notes", **entry)
    agent, context = _trust_case(monkeypatch, caplog, skill)
    assert agent.loads == []
    assert _suggests(context, "my-notes")
    assert "suggesting 'my-notes' instead of auto-loading it" in caplog.text
    assert why in caplog.text


@pytest.mark.parametrize(
    "tools,why",
    [(["cut"], "ships tools"), ([], "from a .claude folder")],
    ids=["with-tools", "instructions-only"],
)
def test_a_claude_skill_is_suggested_not_loaded_and_the_log_says_why(
    monkeypatch, caplog, tools, why
):
    skill = _full_skill("release", tools=tools, root=ROOT_CLAUDE_PROJECT)
    agent, context = _trust_case(monkeypatch, caplog, skill)
    assert agent.loads == []
    assert _suggests(context, "release")
    assert "suggesting 'release' instead of auto-loading it" in caplog.text
    assert why in caplog.text


def test_an_unreadable_lock_leaves_the_skill_a_suggestion(
    monkeypatch, caplog, tmp_path
):
    skill = _locked(tmp_path, "web-research", installed_tier="community")
    (tmp_path / "skills" / "skill-lock.json").write_text("{not json", encoding="utf-8")
    agent, context = _trust_case(monkeypatch, caplog, skill)
    assert agent.loads == []
    assert _suggests(context, "web-research")
    assert "not auto-loading 'web-research'" in caplog.text


# ── auto-load: skills that grant a program ──────────────────────────────────


def _granting(monkeypatch, *, full_access, skill=None):
    skill = skill or _full_skill("test-runner", permissions=["shell:execute:pytest"])
    agent = _loading(monkeypatch, [skill.name], library={skill.name: skill})
    agent.console.full_access = full_access
    return agent


def test_in_ask_mode_a_skill_that_grants_a_cli_is_suggested_not_loaded(
    monkeypatch, caplog
):
    agent = _granting(monkeypatch, full_access=False)
    with caplog.at_level(logging.INFO):
        context = _with_turn_context(monkeypatch, agent, "run the tests")
    assert agent.loads == []
    assert _suggests(context, "test-runner")
    assert "it would grant pytest without a prompt" in caplog.text
    assert skill_granted_binaries(agent) == frozenset()


def test_under_full_access_a_skill_that_grants_a_cli_loads_with_a_warning(
    monkeypatch, caplog
):
    agent = _granting(monkeypatch, full_access=True)
    with caplog.at_level(logging.WARNING):
        context = _with_turn_context(monkeypatch, agent, "run the tests")
    assert agent.loads == ["test-runner"]
    assert "Loaded test-runner" in context
    assert agent._autoload_granted == {"test-runner": ["pytest"]}
    assert skill_granted_binaries(agent) == {"pytest"}
    (warning,) = [m for m in _warnings(caplog) if "full access" in m]
    assert "'test-runner'" in warning and "pre-approves pytest" in warning


def test_pre_approved_tools_without_full_access_do_not_load_a_granting_skill(
    monkeypatch,
):
    """``auto_approve_gated_tools`` answers prompts; it does not widen the shell."""
    agent = _granting(monkeypatch, full_access=False)
    agent.console.auto_approve_gated_tools = True
    agent.console.auto_approve_confirmations_enabled = lambda: True
    context = _with_turn_context(monkeypatch, agent, "run the tests")
    assert agent.loads == []
    assert _suggests(context, "test-runner")


def test_an_untrusted_skill_with_a_grant_never_loads_even_under_full_access(
    monkeypatch, caplog, tmp_path
):
    from gaia.skills.lock import SOURCE_CAPTURED

    skill = _locked(tmp_path, "pasted-runner", source=SOURCE_CAPTURED, captured=True)
    skill.parsed_permissions = _full_skill(
        "pasted-runner", permissions=["shell:execute:pytest"]
    ).parsed_permissions
    agent = _granting(monkeypatch, full_access=True, skill=skill)
    with caplog.at_level(logging.INFO):
        context = _with_turn_context(monkeypatch, agent, "run the tests")
    assert agent.loads == []
    assert _suggests(context, "pasted-runner")
    assert "it was captured" in caplog.text
    assert skill_granted_binaries(agent) == frozenset()


def test_full_access_is_read_each_turn(monkeypatch):
    agent = _granting(monkeypatch, full_access=False)
    _with_turn_context(monkeypatch, agent, "run the tests")
    assert agent.loads == []
    agent.console.full_access = True
    _with_turn_context(monkeypatch, agent, "run the tests")
    assert agent.loads == ["test-runner"]


def test_switching_full_access_off_withdraws_the_grant_at_the_next_turn(
    monkeypatch, caplog
):
    agent = _granting(monkeypatch, full_access=True)
    _with_turn_context(monkeypatch, agent, "run the tests")
    assert skill_granted_binaries(agent) == {"pytest"}

    agent.console.full_access = False
    with caplog.at_level(logging.WARNING):
        context = _with_turn_context(monkeypatch, agent, "run the tests again")
    assert "test-runner" not in agent._loaded_skills
    assert skill_granted_binaries(agent) == frozenset()
    assert agent._autoload_granted == {}
    assert "unloading 'test-runner': full access is off" in caplog.text
    # Back to a suggestion: the model can still ask for it.
    assert agent.loads == ["test-runner"]
    assert _suggests(context, "test-runner")


def test_a_grant_the_model_asked_for_survives_full_access_going_off(monkeypatch):
    """Unloaded and loaded again by the model: that load is the model's ask."""
    agent = _granting(monkeypatch, full_access=True)
    _with_turn_context(monkeypatch, agent, "run the tests")
    agent.unload_skill("test-runner")
    agent.load_skill("test-runner")
    agent.console.full_access = False
    _with_turn_context(monkeypatch, agent, "run the tests again")
    assert "test-runner" in agent._loaded_skills
    assert skill_granted_binaries(agent) == {"pytest"}


def test_a_skill_without_a_grant_stays_loaded_when_full_access_goes_off(monkeypatch):
    agent = _loading(monkeypatch, ["summarize"])
    agent.console.full_access = True
    _with_turn_context(monkeypatch, agent, "sum this up")
    agent.console.full_access = False
    _with_turn_context(monkeypatch, agent, "and this")
    assert "summarize" in agent._loaded_skills
    assert not getattr(agent, "_autoload_granted", None)


# ── auto-load: the size cap ─────────────────────────────────────────────────


def _budget(window=WINDOW):
    return int(window * GaiaAgent.SKILL_AUTOLOAD_CONTEXT_SHARE)


def _sized(tokens):
    return "x" * (tokens * 4)


def test_the_default_window_gives_about_six_thousand_tokens():
    assert 5500 <= _budget(32768) <= 6500


@pytest.mark.parametrize("window", [8192, 32768, 131072])
def test_the_body_budget_is_a_share_of_the_context_window(monkeypatch, window):
    budget = _budget(window)
    library = {
        "fits": _full_skill("fits", body=_sized(budget)),
        "over": _full_skill("over", body=_sized(budget + 1)),
    }
    agent = _loading(monkeypatch, ["over", "fits"], library=library, window=window)
    context = _with_turn_context(monkeypatch, agent, "do both")
    assert agent.loads == ["fits"]
    assert _suggests(context, "over")


def test_a_skill_skipped_for_size_is_a_warning_with_the_numbers(monkeypatch, caplog):
    agent = _loading(monkeypatch, ["summarize"], window=10000)
    agent._loaded_skills["coding"] = _full_skill("coding", body=_sized(1750))
    agent.skill_manager.load("summarize").body = _sized(100)
    with caplog.at_level(logging.WARNING):
        context = _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == []
    assert _suggests(context, "summarize")
    (warning,) = [m for m in _warnings(caplog) if "over the budget" in m]
    assert "'summarize'" in warning
    for number in ("~100-token body", "~1750 tokens", "budget of 1800", "10000-token"):
        assert number in warning, warning


def test_bodies_loaded_this_turn_count_against_the_budget(monkeypatch):
    budget = _budget()
    library = {
        "big": _full_skill("big", body=_sized(budget - 100)),
        "small": _full_skill("small", body=_sized(200)),
    }
    agent = _loading(monkeypatch, ["big", "small"], library=library)
    context = _with_turn_context(monkeypatch, agent, "do both")
    assert agent.loads == ["big"]
    assert "Loaded big because it matches" in context
    assert _suggests(context, "small")


def test_a_window_change_between_turns_is_honoured(monkeypatch):
    library = {"big": _full_skill("big", body=_sized(3000))}
    agent = _loading(monkeypatch, ["big"], library=library, window=8192)
    _with_turn_context(monkeypatch, agent, "do it")
    assert agent.loads == []
    agent.window = 32768
    _with_turn_context(monkeypatch, agent, "do it")
    assert agent.loads == ["big"]
    agent._loaded_skills.clear()
    agent._autoloaded_skills.clear()
    agent.window = 8192
    _with_turn_context(monkeypatch, agent, "do it")
    assert agent.loads == ["big"]


class _BodySelector:
    """A skill-body selector that renders only *active* this turn."""

    session_disabled = False

    def __init__(self, active):
        self.active = active

    def select(self, query, loaded_skills, always_on=frozenset()):
        return None if self.active is None else sorted(self.active)


def _with_a_long_session(monkeypatch, active):
    """An agent already holding a budget's worth of an older skill's body."""
    agent = _loading(monkeypatch, ["summarize"])
    agent._loaded_skills["coding"] = _full_skill("coding", body=_sized(_budget()))
    agent.skill_loader = _BodySelector(active)
    agent._skill_sets = SimpleNamespace(always=[])
    return agent


def test_collapsed_bodies_do_not_count_against_the_budget(monkeypatch):
    agent = _with_a_long_session(monkeypatch, active=[])
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == ["summarize"]


def test_a_body_this_turn_selects_counts(monkeypatch):
    agent = _with_a_long_session(monkeypatch, active=["coding"])
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == []


def test_a_pinned_body_counts(monkeypatch):
    agent = _with_a_long_session(monkeypatch, active=[])
    agent._sticky_skill_turns = {"coding": 2}
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == []
    assert agent._sticky_skill_turns == {"coding": 2}  # counting spends no pin


def test_an_always_on_body_counts(monkeypatch):
    agent = _with_a_long_session(monkeypatch, active=[])
    agent._skill_sets = SimpleNamespace(always=[SimpleNamespace(name="coding")])
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == []


@pytest.mark.parametrize("why", ["selector-off", "selector-down", "no-selection"])
def test_every_loaded_body_counts_when_none_is_collapsed(monkeypatch, why):
    agent = _with_a_long_session(
        monkeypatch, active=None if why != "selector-off" else []
    )
    if why == "selector-off":
        agent.skill_loader = None
    elif why == "selector-down":
        agent.skill_loader.session_disabled = True
    _with_turn_context(monkeypatch, agent, "sum this up")
    assert agent.loads == []
