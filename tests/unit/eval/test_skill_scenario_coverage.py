# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guards the mapping from shipped skills to eval scenarios.

GAIA ships its capabilities as skills rather than as agents, so a skill with no
scenario is a shipped capability nothing measures. This module makes that state
explicit instead of silent: every directory under ``hub/skills/`` is either
covered by scenarios or named in :data:`DEFERRED` with a reason, and adding a
skill without making that choice fails here.

The skill list is read from the tree (the same lane
``tests/unit/test_starter_skills.py`` iterates), never hardcoded, so a new skill
cannot slip past by not being on a list.

Coverage is claimed by filename convention: a scenario for skill ``file-ops``
lives at ``eval/scenarios/<category>/skill_file_ops_*.yaml``. The scenario schema
has no field for "this exercises skill X" and inventing one would change the
judge prompt, so the filename carries it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gaia.agents.tools.skill_library_tools import SkillLibraryToolsMixin
from gaia.eval.config import DEFAULT_AGENT_TYPE
from gaia.eval.runner import SCENARIOS_DIR, validate_scenario

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / "hub" / "skills"

#: Minimum scenarios per covered skill: one golden path, one failure/edge path.
MIN_SCENARIOS_PER_SKILL = 2

#: Skills with no scenarios yet, each with the reason it can wait.
#:
#: Phase 1 covers the four skills carrying parity risk from the retiring
#: chat/doc/file profiles. The rest are deferred on cost: the agent eval averages
#: ~4.2 min/scenario, so covering all sixteen at two scenarios each would add
#: over two hours to a suite already being split for length.
DEFERRED: dict[str, str] = {
    "check-in": "Scheduled-trigger skill; needs a clock fixture the runner lacks.",
    "coding": "Overlaps the code_index scenarios; needs its own corpus first.",
    "daily-brief": "Composes other skills; cover its parts before the composite.",
    "github-triage": "Requires a live GitHub connector; not available in the lane.",
    "image-gen": "Needs Stable Diffusion loaded; out of scope for the text lanes.",
    "inbox-triage": "Requires a mailbox connector; covered by the email eval lane.",
    "price-watch": "Requires live web fetch; non-deterministic ground truth.",
    "recommendations": "Subjective output; no stable rubric yet.",
    "research-report": "Requires live web search; non-deterministic ground truth.",
    "rss-digest": "Requires live feeds; non-deterministic ground truth.",
    "source-watch": "Requires live web fetch and stored state across runs.",
    "transcribe-meeting": "Requires audio input; belongs with the audio tests.",
}


def _skill_names() -> list[str]:
    """Every skill directory shipped in the hub lane, sorted."""
    return sorted(d.name for d in SKILLS_ROOT.iterdir() if (d / "SKILL.md").is_file())


def _scenario_prefix(skill: str) -> str:
    return f"skill_{skill.replace('-', '_')}_"


def _scenarios_for(skill: str) -> list[Path]:
    prefix = _scenario_prefix(skill)
    return sorted(SCENARIOS_DIR.rglob(f"{prefix}*.yaml"))


SKILL_NAMES = _skill_names()
COVERED = [s for s in SKILL_NAMES if _scenarios_for(s)]
SKILL_SCENARIOS = sorted(SCENARIOS_DIR.rglob("skill_*.yaml"))


def test_the_skill_lane_is_not_empty():
    """A globbed suite silently passes when the glob finds nothing."""
    assert SKILL_NAMES, f"No skills found under {SKILLS_ROOT}"


def test_every_skill_is_covered_or_explicitly_deferred():
    """A new skill must land on one side of the line, not in the gap."""
    undecided = sorted(set(SKILL_NAMES) - set(COVERED) - set(DEFERRED))
    assert not undecided, (
        f"Skills with neither eval scenarios nor a DEFERRED entry: {undecided}. "
        f"Add scenarios named skill_<name>_*.yaml under eval/scenarios/, or add "
        f"the skill to DEFERRED in {Path(__file__).name} with the reason it waits."
    )


def test_deferred_entries_still_exist():
    """A renamed or deleted skill must not leave a stale excuse behind."""
    stale = sorted(set(DEFERRED) - set(SKILL_NAMES))
    assert not stale, f"DEFERRED names skills that no longer exist: {stale}"


def test_deferred_skills_are_not_also_covered():
    """Once a skill has scenarios, its deferral reason is wrong and must go."""
    both = sorted(set(DEFERRED) & set(COVERED))
    assert not both, (
        f"These skills have scenarios but are still listed as DEFERRED: {both}. "
        f"Remove them from DEFERRED."
    )


def test_deferred_reasons_are_substantive():
    """'Not yet' is not a reason; the next person needs to know what blocks it."""
    for skill, reason in sorted(DEFERRED.items()):
        assert len(reason.strip()) >= 20, f"{skill}: deferral reason is too thin"


def test_at_least_one_skill_is_covered():
    """Guards against a refactor that breaks the filename convention wholesale."""
    assert COVERED, (
        "No skill has scenarios. Either the filename convention "
        "(skill_<name>_*.yaml) changed or the scenarios were removed."
    )


@pytest.mark.parametrize("skill", COVERED)
def test_covered_skill_has_golden_and_edge_scenarios(skill: str):
    """One passing path proves nothing about what the skill claims to catch."""
    scenarios = _scenarios_for(skill)
    assert len(scenarios) >= MIN_SCENARIOS_PER_SKILL, (
        f"{skill}: found {len(scenarios)} scenario(s), expected at least "
        f"{MIN_SCENARIOS_PER_SKILL} (one golden path, one failure/edge path)"
    )


@pytest.mark.parametrize("path", SKILL_SCENARIOS, ids=lambda p: p.stem)
def test_skill_scenario_validates(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_scenario(path, data)


def test_the_eval_default_agent_is_the_flagship():
    """Every scenario here needs an agent that actually has ``load_skill``.

    Scenarios no longer pin ``agent_type`` -- the runner passes the one default
    for the whole run -- so this is the only place the requirement can be
    checked. ChatAgent (the chat/doc/file profiles) has no ``load_skill`` tool
    at all, so a default pointed back at it would make every skill scenario fail
    on turn 1 for a reason that has nothing to do with the skills.
    """
    assert DEFAULT_AGENT_TYPE == "gaia", (
        f"the eval default agent is {DEFAULT_AGENT_TYPE!r}, which is not the "
        f"flagship. Skill scenarios assert `load_skill` on turn 1 and only the "
        f"flagship provides it."
    )


def test_the_flagship_actually_provides_load_skill():
    """The other half of the assertion above, where the flagship is installed.

    ``gaia_agent`` is a hub package, absent from lanes that install only one
    agent, so this skips rather than failing there -- the name check above still
    runs everywhere.
    """
    agent_module = pytest.importorskip("gaia_agent.agent")

    assert SkillLibraryToolsMixin in agent_module.GaiaAgent.__mro__
    assert hasattr(agent_module.GaiaAgent, "load_skill")


@pytest.mark.parametrize("path", SKILL_SCENARIOS, ids=lambda p: p.stem)
def test_skill_scenario_asserts_the_skill_was_loaded(path: Path):
    """The anti-false-green guard.

    A scenario that would score the same with the skill uninstalled measures the
    underlying tools, not the skill. The runner exposes each turn's tool calls to
    the judge, so the first turn must assert ``load_skill`` was among them.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    first_turn = data["turns"][0]
    criteria = first_turn.get("success_criteria") or ""
    assert "load_skill" in criteria, (
        f"{path.name}: turn 1 success_criteria must require `load_skill`, "
        f"otherwise the scenario passes identically with the skill uninstalled"
    )


def test_scenario_filename_prefixes_are_unambiguous():
    """One skill's prefix must not swallow another's scenarios.

    ``skill_<name>_`` matching breaks down if one skill name is a prefix of
    another, which would silently credit one skill's coverage to the other.
    """
    collisions = [
        (a, b)
        for a in SKILL_NAMES
        for b in SKILL_NAMES
        if a != b and _scenario_prefix(b).startswith(_scenario_prefix(a))
    ]
    assert not collisions, f"Ambiguous skill-name prefixes: {collisions}"
