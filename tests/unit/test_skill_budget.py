# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loaded-skill bodies scaled to the model's context window; their tools stay.

The pure parts (budget per window, ranking, fitting) are tested directly. The
render path drives the real ``Agent`` methods through a stub that never boots
the LLM stack, over real on-disk skills, as ``test_agent_lazy_skill_prompt.py``
does; the window is pinned by replacing ``resolve_skill_window``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.agents.base import skill_budget
from gaia.agents.base.agent import Agent
from gaia.agents.base.skill_budget import (
    SKILL_BUDGET_CEILING,
    SKILL_BUDGET_FLOOR,
    SUMMARIZED_THIS_TURN_HEADER,
    SUMMARY_HEADER,
    SkillBudget,
    fit_bodies,
    is_small_window,
    rank_skills,
    skill_body_budget,
    skill_budget_report,
)
from gaia.agents.base.tools import tool

from .skills_helpers import isolated_manager, write_skill_dir

# ── budget per window ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "window,budget",
    [
        (16_384, SKILL_BUDGET_FLOOR),  # 5% is 819, under the floor
        (32_768, 1_638),
        (65_536, 3_276),
        (131_072, SKILL_BUDGET_CEILING),
        (200_000, SKILL_BUDGET_CEILING),
        (None, SKILL_BUDGET_CEILING),  # cloud model, window not listed
    ],
)
def test_budget_scales_with_the_window(window, budget):
    assert skill_body_budget(window) == budget


@pytest.mark.parametrize(
    "window,small",
    [(16_384, True), (65_536, True), (131_072, False), (200_000, False), (None, False)],
)
def test_64k_and_below_is_a_small_window(window, small):
    assert is_small_window(window) is small


def test_a_cloud_model_uses_its_catalogue_window(monkeypatch):
    from gaia.agents.base import history
    from gaia.llm import lemonade_client

    monkeypatch.setattr(history, "_is_cloud", lambda _agent: True)
    monkeypatch.setattr(
        lemonade_client, "cloud_model_info", lambda _model: {"ctx_size": 262_144}
    )
    agent = SimpleNamespace(model_id="amd.big-model")
    assert skill_budget.resolve_skill_window(agent) == (262_144, "cloud catalogue")

    monkeypatch.setattr(lemonade_client, "cloud_model_info", lambda _model: None)
    window, source = skill_budget.resolve_skill_window(agent)
    assert window is None and "cloud" in source


def test_a_local_model_uses_the_history_window(monkeypatch):
    from gaia.agents.base import history

    monkeypatch.setattr(history, "_is_cloud", lambda _agent: False)
    monkeypatch.setattr(history, "context_window", lambda _agent: 65_536)
    assert skill_budget.resolve_skill_window(SimpleNamespace()) == (
        65_536,
        "model context window",
    )


def test_the_window_is_resolved_once_per_model(monkeypatch):
    calls = []

    def resolve(_agent):
        calls.append(1)
        return 65_536, "test"

    monkeypatch.setattr(skill_budget, "resolve_skill_window", resolve)
    agent = SimpleNamespace(model_id="m1")
    assert skill_budget.window_budget(agent) == (65_536, "test", 3_276)
    skill_budget.window_budget(agent)
    assert len(calls) == 1
    agent.model_id = "m2"
    skill_budget.window_budget(agent)
    assert len(calls) == 2
    agent.chat = SimpleNamespace(effective_model="m3")
    skill_budget.window_budget(agent)
    assert len(calls) == 3


# ── ranking and fitting ──────────────────────────────────────────────────


def test_explicit_loads_rank_first_then_relevance_then_recent_load():
    ranked = rank_skills(
        ["a", "b", "c", "d"],
        requested=["d"],
        scores={"a": 0.2, "b": 0.6, "c": 0.4},
        load_order=["a", "b", "c", "d"],
    )
    assert ranked == ["d", "b", "c", "a"]


def test_without_scores_the_most_recently_loaded_ranks_first():
    assert rank_skills(["a", "b", "c"], load_order=["a", "b", "c"]) == ["c", "b", "a"]


def test_the_best_skill_is_full_even_over_budget():
    full, summarized, used = fit_bodies(
        ["big", "small"], {"big": 5000, "small": 10}, 1000
    )
    assert full == ["big"]
    assert summarized == ["small"]
    assert used == 5000


def test_a_later_skill_that_still_fits_stays_full():
    costs = {"a": 1000, "b": 3000, "c": 500}
    full, summarized, _ = fit_bodies(["a", "b", "c"], costs, 2000)
    assert full == ["a", "c"]
    assert summarized == ["b"]


# ── render path through the real Agent methods ───────────────────────────


def _skill_text(name: str, words: int) -> str:
    body = " ".join(["word"] * words)  # 5 chars a word: ~words * 1.25 tokens
    return (
        f"---\nname: {name}\ndescription: Does {name} things. Use for {name}.\n"
        f"---\n\n# {name}\n\nBODY-{name.upper()}\n{body}\n"
    )


class _StubAgent:
    """Drives the real skill-prompt methods without booting the LLM stack."""

    REQUIRED_CONNECTORS: list = []
    SKILL_DIRS: list = []
    SKILL_MANIFEST = None
    SKILL_CONTEXT_BUDGET = True
    _instance_tools = None
    _loaded_skills = None
    _skill_sets = None
    _requested_skill_set = None
    _active_skill_set = None
    _skill_set_loaded = None
    _active_skill_filter = None
    _active_tool_filter = None
    _sticky_skill_turns = None
    _SKILL_MANIFEST_FILENAME = Agent._SKILL_MANIFEST_FILENAME
    STICKY_SKILL_TURNS = Agent.STICKY_SKILL_TURNS

    skill_manager = Agent.skill_manager
    skill_sets = Agent.skill_sets
    _resolve_skill_manifest = Agent._resolve_skill_manifest
    _parse_skill_declarations = Agent._parse_skill_declarations
    loaded_skills = Agent.loaded_skills
    granted_binaries = Agent.granted_binaries
    _tools_registry = Agent._tools_registry
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill
    get_skills_system_prompt = Agent.get_skills_system_prompt
    _refresh_active_skill_filter = Agent._refresh_active_skill_filter
    _refresh_active_tool_filter = Agent._refresh_active_tool_filter
    _apply_tool_filter = Agent._apply_tool_filter
    _rendered_skill_bodies = Agent._rendered_skill_bodies
    _union_sticky_skills = Agent._union_sticky_skills
    _note_skill_active = Agent._note_skill_active
    _pin_skill_body = Agent._pin_skill_body
    _always_on_skill_names = Agent._always_on_skill_names
    _active_skill_names = Agent._active_skill_names
    _admit_skill_tools = Agent._admit_skill_tools

    def __init__(self, manager):
        self._skill_manager = manager
        self.scores = {}
        self.tools_this_turn = None
        self.rebuilt = 0

    def _select_skills_for_turn(self, _user_input):
        # Every loaded skill is relevant: the budget alone decides.
        return sorted(self.loaded_skills)

    def _skill_relevance(self):
        return self.scores

    def _select_tools_for_turn(self, _user_input):
        return self.tools_this_turn

    def _compose_system_prompt(self):
        return ""

    def rebuild_system_prompt(self):
        self.rebuilt += 1

    def _apply_skill_filter(self, new_filter):
        self._active_skill_filter = new_filter
        self.rebuild_system_prompt()


@pytest.fixture
def make_agent(tmp_path: Path, monkeypatch):
    root = tmp_path / "pkg" / "skills"
    # ~2,000 tokens each: one fits a 64K budget (3,276), three fit 200K (6,000).
    for name in ("alpha", "beta", "gamma"):
        write_skill_dir(root, name, _skill_text(name, 1_560))
    references = root / "beta" / "references"
    references.mkdir()
    (references / "api.md").write_text("# API\n", encoding="utf-8")
    (references / "examples.md").write_text("# Examples\n", encoding="utf-8")

    def make(window):
        monkeypatch.setattr(
            skill_budget, "resolve_skill_window", lambda _agent: (window, "test")
        )
        return _StubAgent(isolated_manager(tmp_path, agent_skill_dirs=[root]))

    return make


def _turn(agent, *names, query="the request"):
    for name in names:
        agent.load_skill(name)
    agent._refresh_active_skill_filter(query)
    return agent.get_skills_system_prompt()


def test_a_200k_window_shows_every_relevant_body(make_agent):
    agent = make_agent(200_000)
    prompt = _turn(agent, "alpha", "beta", "gamma")
    for name in ("ALPHA", "BETA", "GAMMA"):
        assert f"BODY-{name}" in prompt
    assert SUMMARY_HEADER not in prompt
    report = skill_budget_report(agent)
    assert report["budget"] == SKILL_BUDGET_CEILING
    assert report["summarized"] == []


def test_a_64k_window_shows_the_most_relevant_body_and_summarizes_the_rest(
    make_agent,
):
    agent = make_agent(65_536)
    agent.scores = {"alpha": 0.3, "beta": 0.7, "gamma": 0.5}
    for name in ("alpha", "beta", "gamma"):
        agent.load_skill(name)
    agent._skill_requests = []  # loaded on an earlier turn, not this one
    agent._refresh_active_skill_filter("about beta")
    prompt = agent.get_skills_system_prompt()

    assert "BODY-BETA" in prompt
    assert "BODY-ALPHA" not in prompt and "BODY-GAMMA" not in prompt
    assert SUMMARY_HEADER in prompt
    assert "- alpha: Does alpha things." in prompt
    assert "- gamma: Does gamma things." in prompt
    report = skill_budget_report(agent)
    assert report["window"] == 65_536
    assert report["budget"] == 3_276
    assert report["full"] == ["beta"]
    assert report["summarized"] == ["gamma", "alpha"]  # relevance order


def test_a_16k_window_still_shows_one_body_in_full(make_agent):
    agent = make_agent(16_384)
    prompt = _turn(agent, "alpha", "beta")
    assert prompt.count("--- SKILL:") == 1
    assert skill_budget_report(agent)["budget"] == SKILL_BUDGET_FLOOR


def test_a_summary_names_the_skills_reference_files(make_agent):
    agent = make_agent(65_536)
    agent.scores = {"alpha": 0.9, "beta": 0.1}
    agent.load_skill("alpha")
    agent.load_skill("beta")
    agent._skill_requests = []
    agent._refresh_active_skill_filter("about alpha")
    line = next(
        line
        for line in agent.get_skills_system_prompt().splitlines()
        if line.startswith("- beta:")
    )
    assert "references" in line
    assert "api.md, examples.md" in line
    assert "load_skill('<name>') again" in SUMMARY_HEADER


def test_this_turns_explicit_load_outranks_relevance(make_agent):
    agent = make_agent(65_536)
    agent.scores = {"alpha": 0.9, "beta": 0.1}
    prompt = _turn(agent, "alpha", "beta")  # beta was loaded last
    assert "BODY-BETA" in prompt and "BODY-ALPHA" not in prompt


def test_load_skill_again_brings_a_summarized_body_back_mid_turn(make_agent):
    agent = make_agent(65_536)
    agent.scores = {"alpha": 0.9, "beta": 0.1}
    agent.load_skill("alpha")
    agent.load_skill("beta")
    agent._skill_requests = []
    agent._refresh_active_skill_filter("about alpha")
    assert skill_budget_report(agent)["summarized"] == ["beta"]

    rebuilt = agent.rebuilt
    agent.load_skill("beta")  # the model follows the summary's hint
    prompt = agent.get_skills_system_prompt()

    assert agent.rebuilt == rebuilt + 1
    assert "BODY-BETA" in prompt and "BODY-ALPHA" not in prompt
    assert skill_budget_report(agent)["summarized"] == ["alpha"]


def test_an_agent_that_has_not_opted_in_is_unchanged(make_agent):
    agent = make_agent(16_384)
    agent.SKILL_CONTEXT_BUDGET = False
    prompt = _turn(agent, "alpha", "beta", "gamma")
    assert prompt.count("--- SKILL:") == 3
    assert skill_budget_report(agent) is None


class _AlwaysOnStub(_StubAgent):
    """A stub whose manifest pins ``voice`` on every turn."""

    @property
    def _always_on_skill_names(self):
        return frozenset({"voice"})


@pytest.mark.parametrize("window", [16_384, 65_536])
def test_an_always_on_skill_renders_in_full_on_a_small_window(
    tmp_path, monkeypatch, window
):
    root = tmp_path / "pkg" / "skills"
    # ~2,400 tokens: over the whole 16K budget, most of the 64K one.
    write_skill_dir(root, "voice", _skill_text("voice", 1_920))
    for name in ("alpha", "beta"):
        write_skill_dir(root, name, _skill_text(name, 1_560))
    monkeypatch.setattr(
        skill_budget, "resolve_skill_window", lambda _agent: (window, "test")
    )
    agent = _AlwaysOnStub(isolated_manager(tmp_path, agent_skill_dirs=[root]))
    agent.scores = {"alpha": 0.9, "beta": 0.1}
    for name in ("voice", "alpha", "beta"):
        agent.load_skill(name)
    agent._loaded_skills["voice"] = _ToolsOf(agent._loaded_skills["voice"], ["speak"])
    agent._skill_requests = []
    agent._refresh_active_skill_filter("about alpha")
    agent.tools_this_turn = ["read_file", "voice/speak"]
    agent._refresh_active_tool_filter("about alpha")
    prompt = agent.get_skills_system_prompt()

    assert "BODY-VOICE" in prompt
    assert "- voice:" not in prompt  # never summarized
    assert "BODY-ALPHA" in prompt  # the best other skill still shows in full
    report = skill_budget_report(agent)
    assert "voice" not in report["full"] + report["summarized"]
    assert report["summarized"] == ["beta"]
    assert report["used_tokens"] > report["budget"]
    assert agent._active_tool_filter == ["read_file", "voice/speak"]


# ── a loaded skill keeps its tools, summarized or not ───────────────────

_PROVIDED_TOOL = """from gaia.agents.base.tools import tool


@tool
def {func}(text: str) -> dict:
    \"\"\"Probe tool of {name}.\"\"\"
    return {{"status": "success"}}
"""


def _tool_skill_text(name: str, func: str) -> str:
    """A ~2,000-token skill (over a 16K budget) that provides tool *func*."""
    return _skill_text(name, 1_560).replace(
        "\n---\n",
        f"\nmetadata:\n  gaia:\n    tools:\n      - name: {func}\n"
        f"        description: Probe tool of {name}\n"
        "        parameters:\n          text: {type: string, required: true}\n"
        "        returns: {type: object}\n---\n",
        1,
    )


class _BudgetAgent(Agent):
    """A real agent on the skill budget that picks one tool per turn."""

    SKILL_CONTEXT_BUDGET = True

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        @tool
        def read_file(file_path: str) -> dict:
            """Probe reader."""
            return {"status": "success"}

    def _select_tools_for_turn(self, user_input):
        return ["read_file"]

    def _select_skills_for_turn(self, user_input):
        return sorted(self.loaded_skills)

    def _skill_relevance(self):
        return self.scores


@pytest.fixture
def budget_agent(tmp_path, monkeypatch):
    """``make(window, names)``: a real agent whose skills each provide a tool."""
    from unittest.mock import patch

    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()

    def make(window, names):
        root = tmp_path / f"skills-{window}-{len(names)}"
        for name in names:
            func = f"{name.replace('-', '_')}_run"
            write_skill_dir(
                root,
                name,
                _tool_skill_text(name, func),
                tools=_PROVIDED_TOOL.format(func=func, name=name),
            )
        monkeypatch.setattr(
            skill_budget, "resolve_skill_window", lambda _agent: (window, "test")
        )
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _BudgetAgent(silent_mode=True, skip_lemonade=True)
        agent.scores = {}
        agent._skill_manager = isolated_manager(tmp_path, agent_skill_dirs=[root])
        return agent

    try:
        yield make
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _sent_tools(agent):
    """Names in the ``tools=`` schemas built from this turn's filter."""
    schemas = agent._build_openai_tool_schemas(filter_to=agent._active_tool_filter)
    return [entry["function"]["name"] for entry in schemas]


@pytest.mark.parametrize("window", [16_384, 65_536])
def test_a_summarized_skill_keeps_the_tools_it_provides(budget_agent, window):
    agent = budget_agent(window, ["alpha", "beta"])
    agent.scores = {"alpha": 0.9, "beta": 0.1}
    agent.load_skill("alpha")
    agent.load_skill("beta")
    agent._skill_requests = []
    agent._refresh_active_skill_filter("about alpha")
    agent._refresh_active_tool_filter("about alpha")

    report = skill_budget_report(agent)
    assert report["full"] == ["alpha"] and report["summarized"] == ["beta"]
    assert "BODY-BETA" not in agent.get_skills_system_prompt()
    for listed in (agent._active_tool_filter, _sent_tools(agent)):
        assert "alpha/alpha_run" in listed
        assert "beta/beta_run" in listed  # summarized, still callable


@pytest.mark.parametrize("window", [16_384, 65_536])
def test_a_mid_turn_load_that_summarizes_another_keeps_its_tools(budget_agent, window):
    agent = budget_agent(window, ["alpha", "beta"])
    agent.scores = {"alpha": 0.9, "beta": 0.1}
    agent.load_skill("alpha")
    agent.load_skill("beta")
    agent._skill_requests = []
    agent._refresh_active_skill_filter("about alpha")
    agent._refresh_active_tool_filter("about alpha")

    agent.load_skill("beta")  # beta now full, alpha summarized

    assert skill_budget_report(agent)["summarized"] == ["alpha"]
    assert {"alpha/alpha_run", "beta/beta_run"} <= set(agent._active_tool_filter)
    agent._refresh_active_tool_filter("next turn")
    assert {"alpha/alpha_run", "beta/beta_run"} <= set(agent._active_tool_filter)


def test_many_skills_on_a_16k_window_shrink_text_but_keep_every_tool(budget_agent):
    names = [f"skill-{i}" for i in range(8)]
    agent = budget_agent(16_384, names)
    for name in names:
        agent.load_skill(name)
    agent._skill_requests = []
    for query in ("first", "second", "third"):
        agent._refresh_active_skill_filter(query)
        agent._refresh_active_tool_filter(query)

        report = skill_budget_report(agent)
        assert len(report["full"]) == 1
        assert len(report["summarized"]) == len(names) - 1
        prompt = agent.get_skills_system_prompt()
        assert prompt.count("--- SKILL:") == 1
        expected = {f"{n}/{n.replace('-', '_')}_run" for n in names}
        assert expected <= set(agent._active_tool_filter)
        assert expected <= set(_sent_tools(agent))


class _TurnMessages(list):
    """A turn's message list that records the notes appended mid-turn."""

    def __init__(self, *args):
        super().__init__(*args)
        self.notes = []

    def append_transient(self, message):
        self.notes.append(message["content"])


@pytest.mark.parametrize("window", [16_384, 65_536])
def test_a_skill_summarized_mid_turn_is_not_called_inactive(budget_agent, window):
    agent = budget_agent(window, ["alpha", "beta"])
    agent.scores = {"alpha": 0.9}
    agent.load_skill("alpha")
    agent._refresh_active_skill_filter("about alpha")
    agent._refresh_active_tool_filter("about alpha")
    messages = _TurnMessages([{"role": "user", "content": "about alpha"}])
    agent._turn_session_context(messages)  # pins the turn's skills section

    agent.load_skill("beta")  # over budget with alpha: alpha is cut to a line
    agent._turn_session_context(messages)

    assert skill_budget_report(agent)["summarized"] == ["alpha"]
    (note,) = messages.notes
    assert "BODY-BETA" in note
    assert SUMMARIZED_THIS_TURN_HEADER in note
    assert "- alpha: Does alpha things." in note
    assert "no longer active" not in note
    assert "alpha/alpha_run" in agent._active_tool_filter


def test_an_unloaded_skill_is_still_called_inactive(budget_agent):
    agent = budget_agent(200_000, ["alpha", "beta"])
    for name in ("alpha", "beta"):
        agent.load_skill(name)
    agent._refresh_active_skill_filter("both")
    messages = _TurnMessages([{"role": "user", "content": "both"}])
    agent._turn_session_context(messages)

    agent.unload_skill("alpha")
    agent._turn_session_context(messages)

    (note,) = messages.notes
    assert "Skills no longer active: alpha." in note
    assert SUMMARIZED_THIS_TURN_HEADER not in note


class _ToolsOf:
    """A real loaded skill that also reports provided tools."""

    def __init__(self, skill, tools):
        self._skill = skill
        self.tool_names = list(tools)

    def __getattr__(self, attr):
        return getattr(self._skill, attr)

    def namespaced_tool_name(self, tool):
        return f"{self._skill.name}/{tool}"


# ── diagnostics ──────────────────────────────────────────────────────────


def test_the_turn_record_carries_the_skill_budget(tmp_path):
    from gaia.agents.base.turn_metrics import TurnRecorder

    plan = SkillBudget(
        window=65_536,
        window_source="test",
        budget=3_276,
        small_window=True,
        full=["beta"],
        summarized=["alpha"],
    )
    recorder = TurnRecorder(
        query="q",
        agent_name="A",
        model_id="m",
        system_prompt="s",
        tool_schemas=None,
        skill_budget=plan.as_dict(),
        path=tmp_path / "turns.jsonl",
    )
    record = recorder.finish(answer="a", steps=1)
    assert record["prompt"]["skill_budget"]["summarized"] == ["alpha"]
    assert record["prompt"]["skill_budget"]["budget"] == 3_276


def test_the_skill_loader_keeps_the_scores_the_budget_ranks_by():
    import numpy as np

    from gaia.agents.base.skill_loader import SkillLoader

    axis = {"alpha: does alpha": 0, "beta: does beta": 1}

    def embed(text):
        v = np.zeros(2, dtype=np.float32)
        if text in axis:
            v[axis[text]] = 1.0
        else:
            v[:] = (0.8, 0.6)  # the query: closer to alpha
        return v

    skills = {
        name: SimpleNamespace(name=name, description=f"does {name}")
        for name in ("alpha", "beta")
    }
    loader = SkillLoader(embed)
    loader.select("q", skills)
    assert loader.last_scores == pytest.approx({"alpha": 0.8, "beta": 0.6})
    loader.select("q", skills, always_on=frozenset(skills))
    assert loader.last_scores == {}


def test_a_mid_turn_load_keeps_the_other_summaries(make_agent):
    agent = make_agent(65_536)
    agent.scores = {"alpha": 0.9, "beta": 0.5, "gamma": 0.1}
    for name in ("alpha", "beta", "gamma"):
        agent.load_skill(name)
    agent._skill_requests = []
    agent._refresh_active_skill_filter("about alpha")
    assert skill_budget_report(agent)["summarized"] == ["beta", "gamma"]

    agent.load_skill("gamma")
    prompt = agent.get_skills_system_prompt()

    assert skill_budget_report(agent)["full"] == ["gamma"]
    assert skill_budget_report(agent)["summarized"] == ["alpha", "beta"]
    assert "(instructions hidden" not in prompt  # still summaries, not a menu


def test_cjk_skill_body_is_charged_per_character():
    assert skill_budget.body_tokens(SimpleNamespace(body="a" * 400)) == 100
    assert skill_budget.body_tokens(SimpleNamespace(body="技能" * 200)) == 400
