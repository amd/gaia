# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A shortlisted skill must say what it unlocks, not just its name.

Four of six benchmark models failed GitHub tasks on a private repository while
'github-triage' sat on the shortlist: its name alone never told them that the
skill is how an agent gets the gh CLI.
"""

from types import SimpleNamespace

from gaia.agents.base.skill_discovery import (
    SHORTLIST_DESCRIPTION_CHARS,
    DiscoveryResult,
    SkillDiscovery,
    _one_line,
)
from gaia.agents.base.skill_retriever import Decision

GH = "Triage GitHub work with the gh CLI — your unread notification inbox, or one repository's issue backlog."


def test_shortlist_note_keeps_its_instruction_and_adds_what_each_skill_does():
    note = DiscoveryResult(
        shortlist=("github-triage", "coding"),
        shortlist_descriptions=(_one_line(GH), ""),
    ).prompt_fragment()
    assert "are installed and may match this request" in note
    assert "call load_skill on it before answering" in note
    assert "- 'github-triage': Triage GitHub work with the gh CLI" in note
    assert "- 'coding'" not in note, "a skill with no description gets no empty line"


def test_a_shortlist_without_descriptions_renders_as_before():
    note = DiscoveryResult(shortlist=("github-triage",)).prompt_fragment()
    assert note.endswith("call load_skill on it before answering.")


def test_descriptions_keep_only_their_first_clause():
    assert _one_line(GH) == "Triage GitHub work with the gh CLI"
    assert (
        _one_line("Research a topic on the open web. Use when asked.")
        == "Research a topic on the open web"
    )
    assert len(_one_line("word " * 200)) <= SHORTLIST_DESCRIPTION_CHARS
    assert _one_line(None) == ""


def test_a_full_described_shortlist_stays_inside_the_per_turn_budget():
    """The same 120-token ceiling as test_per_turn_notes_stay_short, but with the
    real descriptions a three-skill shortlist carries — that test builds its
    shortlist without descriptions, so on its own it never sees this cost."""
    real = (
        GH,
        "Work on a codebase — read, search, edit and verify source files. Use when the user asks to fix a bug.",
        "Research a topic on the open web and write a cited Markdown report. Use when the user asks for a report.",
    )
    note = DiscoveryResult(
        shortlist=("github-triage", "coding", "research-report"),
        shortlist_descriptions=tuple(_one_line(d) for d in real),
    ).prompt_fragment()
    assert len(note) // 4 < 120, f"{len(note) // 4} tokens"


def test_run_fills_descriptions_in_shortlist_order():
    disc = SkillDiscovery.__new__(SkillDiscovery)
    disc._turn = 0
    disc._failures = {}
    disc._retriever = SimpleNamespace(size=2)
    disc.refresh = lambda: None
    disc._log = lambda decision, loaded: None
    disc._decide = lambda query, exclude: Decision(
        load=None, shortlist=("github-triage", "coding"), ranked=()
    )
    disc.candidates = lambda: {
        "github-triage": SimpleNamespace(description=GH + "\n Groups what arrived."),
        "coding": SimpleNamespace(description=""),
    }
    result = disc.run("triage my repo", loaded={}, load_fn=lambda name: None)
    assert result.shortlist == ("github-triage", "coding")
    assert result.shortlist_descriptions[0].startswith(
        "Triage GitHub work with the gh CLI"
    )
    assert result.shortlist_descriptions[1] == ""
