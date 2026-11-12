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

Which scenarios cover a skill is decided by ``util/skill_eval_scenarios.py``
(a ``skill_<name>_*.yaml`` filename, or a ``gaia_skills_tasks`` scenario whose
first turn is "Load the <name> skill."), the same helper ``skill_audit.yml`` uses
to tell a PR which scenarios to dispatch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from gaia.agents.tools.skill_library_tools import SkillLibraryToolsMixin
from gaia.eval.config import DEFAULT_AGENT_TYPE
from gaia.eval.runner import SCENARIOS_DIR, validate_scenario

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = REPO_ROOT / "hub" / "skills"
if str(REPO_ROOT / "util") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "util"))

import skill_eval_scenarios as ses  # noqa: E402  (path set above)

#: Minimum scenarios per covered skill: one golden path, one failure/edge path.
MIN_SCENARIOS_PER_SKILL = 2

#: Skills with no scenarios yet, each with the reason it can wait.
DEFERRED: dict[str, str] = {
    "analyze-recording": "Requires audio input; belongs with the audio tests.",
    "calculators": (
        "The card does the math in the client; a skill scenario needs a judge "
        "that reads the card payload rather than the short reply."
    ),
    "calendar": (
        "Requires a Google or Outlook calendar connection; the eval mailbox "
        "fixture has no calendar, and every change needs a person to approve."
    ),
    "guides": (
        "The answer is a card the client draws; a skill scenario needs a judge "
        "that reads the card payload rather than the short reply."
    ),
    "send-email": (
        "Every send needs a person to approve the exact message; the email eval "
        "lane's fixture mailbox covers its tools once a scenario drives them."
    ),
}


def _skill_names() -> list[str]:
    """Every skill directory shipped in the hub lane, sorted."""
    return sorted(d.name for d in SKILLS_ROOT.iterdir() if (d / "SKILL.md").is_file())


_scenario_prefix = ses.filename_prefix


TASK_SKILLS = ses.task_skills(SCENARIOS_DIR / "gaia_skills_tasks")


def _scenarios_for(skill: str) -> list[Path]:
    return ses.scenarios_for(skill, SCENARIOS_DIR, TASK_SKILLS)


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


def test_task_scenarios_load_only_shipped_skills():
    """A "Load the X skill." turn naming a renamed skill would credit nothing."""
    unknown = sorted(
        f"{p.name}: {s}" for p, s in TASK_SKILLS.items() if s and s not in SKILL_NAMES
    )
    assert not unknown, f"Task scenarios load skills not under hub/skills/: {unknown}"


def test_audit_hint_names_a_changed_skills_scenarios_and_the_dispatch_input():
    summary = ses.markdown(["price-watch"])

    assert "`task_pricewatch_new_low`" in summary
    assert "eval_flagship.yml" in summary
    assert "task_pricewatch_new_low" in summary.split("```")[1]


def test_audit_hint_ignores_directories_outside_the_hub_lane(capsys):
    assert ses.main(["tests/fixtures/skills/evil", "src/gaia/skills"]) == 0
    assert capsys.readouterr().out == ""
