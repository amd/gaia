# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-turn skill discovery: what it loads, what it refuses, and what it says.

The retriever's accuracy is covered in ``test_skill_retriever.py``. This file
covers the part a user actually feels — that a matched skill gets loaded, that a
skill which *cannot* load produces a refusal instead of a confident guess, and
that a broken skill is not re-proposed on every turn for the rest of the session.

``SkillDiscovery`` takes the loaded set and a load callable rather than an agent,
so none of this needs an LLM, an embedder, or Lemonade.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gaia.agents.base.skill_discovery import (
    GROUNDING_RULE,
    DiscoveryResult,
    SkillDiscovery,
)
from gaia.skills.errors import SkillError

DESCRIPTIONS = {
    "github-triage": (
        "Triage GitHub work with the gh CLI — your unread notification inbox, or "
        "one repository's issue backlog. Use when asked to triage issues."
    ),
    "data-explore": (
        "Load messy tabular data into SQL scratchpad tables and answer questions "
        "with real queries. Use when the user has a CSV or spreadsheet export."
    ),
    "price-watch": "Check product pages for a price drop and alert on a new low.",
    "source-watch": "Check a web page or feed for something worth reporting.",
}


#: Mirrors ``github-triage``'s real frontmatter: it declares a tool it does not
#: itself provide, which the loader treats as advisory.
REQUIRED_TOOLS = {"github-triage": ["run_shell_command"]}


def _skill(name: str, *, root: str = "user"):
    return SimpleNamespace(
        name=name,
        description=DESCRIPTIONS.get(name, ""),
        root=root,
        gaia=SimpleNamespace(tools_required=REQUIRED_TOOLS.get(name, [])),
    )


class _Manager:
    """Minimal stand-in for ``SkillManager`` — only ``discover()`` is used."""

    def __init__(self, skills):
        self._skills = dict(skills)

    def discover(self, *, force: bool = False):
        return dict(self._skills)


@pytest.fixture
def manager():
    return _Manager({name: _skill(name) for name in DESCRIPTIONS})


@pytest.fixture
def discovery(manager):
    return SkillDiscovery(manager)


@pytest.fixture
def discovery_with_tools(manager):
    """Discovery that can see the agent's registry — and finds it empty."""
    return SkillDiscovery(manager, tools_fn=dict)


class _Loader:
    """Records what was asked for, and can be told to fail."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[str] = []
        self.error = error

    def __call__(self, name: str):
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return _skill(name)


# ── the happy path ───────────────────────────────────────────────────────


def test_loads_the_skill_the_user_described_without_naming_it(discovery):
    """The defect this exists for, end to end at the discovery layer."""
    load = _Loader()
    result = discovery.run(
        "what's been going on in my github inbox the past few days?",
        loaded={},
        load_fn=load,
    )
    assert result.outcome == "loaded"
    assert result.loaded == "github-triage"
    assert load.calls == ["github-triage"]


def test_the_loaded_note_tells_the_model_to_say_which_skill_it_used(discovery):
    result = discovery.run("triage my github inbox", loaded={}, load_fn=_Loader())
    fragment = result.prompt_fragment()
    assert "github-triage" in fragment
    assert "one short line" in fragment


def test_an_already_loaded_skill_is_not_reloaded(discovery):
    """The loaded set is skill_loader's business; discovery must not fight it."""
    load = _Loader()
    result = discovery.run(
        "triage my github inbox",
        loaded={"github-triage": _skill("github-triage")},
        load_fn=load,
    )
    assert load.calls == []
    assert result.loaded is None


def test_an_unrelated_turn_loads_nothing_and_says_nothing(discovery):
    load = _Loader()
    result = discovery.run("what is 17 times 23?", loaded={}, load_fn=load)
    assert result.outcome == "none"
    assert load.calls == []
    assert result.prompt_fragment() == ""


# ── ambiguity ────────────────────────────────────────────────────────────


def test_an_ambiguous_turn_is_shortlisted_never_guessed(discovery):
    load = _Loader()
    result = discovery.run("watch this for me", loaded={}, load_fn=load)
    assert result.outcome == "shortlist"
    assert load.calls == []
    assert set(result.shortlist) <= {"price-watch", "source-watch"}


def test_the_shortlist_note_hands_the_choice_to_the_model(discovery):
    result = discovery.run("watch this for me", loaded={}, load_fn=_Loader())
    assert "load_skill" in result.prompt_fragment()


# ── never fabricate ──────────────────────────────────────────────────────


def test_a_skill_that_will_not_load_produces_a_refusal_not_a_guess(discovery):
    """The actual defect: a confident answer with none of the required tools.

    A retrieval win that still lets the agent answer from memory is not a fix,
    so a failed load has to reach the model as an instruction to refuse.
    """
    load = _Loader(error=SkillError("gh is not installed on this machine"))
    result = discovery.run("triage my github inbox", loaded={}, load_fn=load)

    assert result.outcome == "failed"
    fragment = result.prompt_fragment()
    assert "gh is not installed on this machine" in fragment
    assert "cannot" in fragment
    assert "Do not answer from memory" in fragment


def test_a_load_failure_does_not_break_the_turn(discovery):
    """A broken skill degrades the answer honestly; it never raises at the user."""
    load = _Loader(error=SkillError("boom"))
    assert discovery.run("triage my github inbox", loaded={}, load_fn=load) is not None


def test_a_skill_that_explodes_on_import_does_not_take_down_the_turn(discovery):
    """Loading registers a skill's tools by importing its ``tools.py``.

    That import runs on a turn the user never asked for, so one broken
    third-party skill must not kill an unrelated question. Nothing is swallowed:
    the type and message still reach the model as a reason to refuse.
    """
    load = _Loader(error=ImportError("No module named 'pandas'"))
    result = discovery.run("triage my github inbox", loaded={}, load_fn=load)

    assert result.outcome == "failed"
    assert "ImportError" in result.prompt_fragment()
    assert "No module named 'pandas'" in result.prompt_fragment()


def test_unmet_required_tools_are_stated_up_front(discovery_with_tools):
    """``tools_required`` is advisory — a skill loads fine and dies mid-recipe.

    Told only when the tool call fails, the model improvises a substitute. Told
    at activation, it can say which parts it cannot do.
    """
    result = discovery_with_tools.run(
        "triage my github inbox", loaded={}, load_fn=_Loader()
    )
    assert result.loaded == "github-triage"
    assert result.unmet_tools == ("run_shell_command",)

    fragment = result.prompt_fragment()
    assert "run_shell_command" in fragment
    assert "NOT registered" in fragment


def test_met_required_tools_add_nothing_to_the_note(manager):
    discovery = SkillDiscovery(
        manager, tools_fn=lambda: {"run_shell_command": object()}
    )
    result = discovery.run("triage my github inbox", loaded={}, load_fn=_Loader())
    assert result.unmet_tools == ()
    assert "NOT registered" not in result.prompt_fragment()


def test_the_tools_check_is_skipped_when_no_registry_was_supplied(discovery):
    """An agent that did not wire ``tools_fn`` still works, it just says less."""
    result = discovery.run("triage my github inbox", loaded={}, load_fn=_Loader())
    assert result.loaded == "github-triage"
    assert result.unmet_tools == ()


def test_a_skill_that_keeps_failing_stops_being_proposed(discovery):
    """Without this a missing CLI is rediscovered and re-refused every single turn."""
    load = _Loader(error=SkillError("gh is not installed"))
    for _ in range(SkillDiscovery.MAX_FAILURES):
        assert discovery.run("triage my github inbox", loaded={}, load_fn=load).failed

    later = discovery.run("triage my github inbox", loaded={}, load_fn=load)
    assert later.outcome == "none"
    assert len(load.calls) == SkillDiscovery.MAX_FAILURES


def test_the_grounding_rule_is_about_sourcing_not_skills():
    """It has to apply on the turns where NO skill matched — that is where the
    fabrication happened."""
    assert "tool call in THIS turn" in GROUNDING_RULE
    assert "cannot" in GROUNDING_RULE
    assert "skill" not in GROUNDING_RULE.lower()


def test_the_grounding_rule_is_small_enough_to_carry_every_turn():
    """Budget claim, asserted — a latency effort is actively cutting this prompt.

    Measured 96 tokens (384 chars at GAIA's 4-chars-per-token estimate) against
    a ~17,000-token composed prompt: 0.6%. The ceiling is 120 so a reword has
    room, but doubling it has to be a decision someone makes on purpose.
    """
    assert len(GROUNDING_RULE) // 4 < 120


@pytest.mark.parametrize(
    "result",
    [
        DiscoveryResult(loaded="github-triage"),
        DiscoveryResult(shortlist=("price-watch", "source-watch")),
        DiscoveryResult(failed=("github-triage", "gh is not installed")),
    ],
    ids=["loaded", "shortlist", "failed"],
)
def test_per_turn_notes_stay_short(result):
    """Measured 49 / 59 / 81 tokens. These ride only on the turns they apply to,
    but a note that grows into a paragraph would be paid on every match."""
    assert len(result.prompt_fragment()) // 4 < 120


# ── corpus scope ─────────────────────────────────────────────────────────


def test_claude_imported_skills_are_never_proposed():
    """``.claude/skills`` is another host's marketplace — skills for working ON a
    repo, not answers to a user's question. Indexing them made "what is the
    contract for this API endpoint?" match a presentation-authoring skill."""
    manager = _Manager(
        {
            "github-triage": _skill("github-triage", root="claude-import"),
            "data-explore": _skill("data-explore"),
        }
    )
    discovery = SkillDiscovery(manager)
    assert "github-triage" not in discovery.candidates()

    load = _Loader()
    result = discovery.run("triage my github inbox", loaded={}, load_fn=load)
    assert result.loaded is None and load.calls == []


def test_the_index_rebuilds_when_a_skill_is_installed_mid_session(manager):
    discovery = SkillDiscovery(manager)
    discovery.refresh()
    before = discovery._retriever.size

    manager._skills["rss-digest"] = SimpleNamespace(
        name="rss-digest",
        description="Read an RSS or Atom feed and summarize the newest entries.",
        root="user",
    )
    discovery.refresh()

    assert discovery._retriever.size == before + 1
    assert (
        discovery.run(
            "what has this atom feed published lately?", loaded={}, load_fn=_Loader()
        ).loaded
        == "rss-digest"
    )


def test_an_empty_library_is_a_no_op():
    discovery = SkillDiscovery(_Manager({}))
    result = discovery.run("triage my github inbox", loaded={}, load_fn=_Loader())
    assert result.outcome == "none"


# ── threshold override ───────────────────────────────────────────────────


def test_threshold_override_is_restored_after_use(manager):
    """It patches a module global, so a raise must not leave it patched."""
    import gaia.agents.base.skill_retriever as module

    original = module.MIN_SCORE
    SkillDiscovery(manager, threshold=0.99).run(
        "triage my github inbox", loaded={}, load_fn=_Loader()
    )
    assert module.MIN_SCORE == original


def test_a_high_threshold_suppresses_auto_load(manager):
    result = SkillDiscovery(manager, threshold=1.01).run(
        "triage my github inbox", loaded={}, load_fn=_Loader()
    )
    assert result.loaded is None


# ── result rendering ─────────────────────────────────────────────────────


def test_an_empty_result_renders_nothing():
    assert DiscoveryResult().prompt_fragment() == ""
    assert DiscoveryResult().outcome == "none"
