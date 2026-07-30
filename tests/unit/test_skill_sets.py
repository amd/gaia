# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Skill sets — manifest grammar + per-launch resolution (issue #2466).

Every test runs from a cold state: the manifest, the bundled skills root, and
the user/Claude-import roots all live under ``tmp_path``, so nothing here reads
the developer's real ``~/.gaia/skills`` or the repo's ``.claude/skills``. That is
the hidden-state rule from CLAUDE.md — a skill that only resolves because a
previous run left it lying around is not a passing test.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from gaia.hub.manifest import AgentManifest, ManifestError
from gaia.skills.errors import SkillSetError, SkillValidationError
from gaia.skills.sets import (
    SOURCE_DEFAULT,
    SOURCE_EXPLICIT,
    SOURCE_NONE,
    SOURCE_SELECTOR,
    SkillSets,
    parse_skill_sets,
)

from .skills_helpers import isolated_manager, write_skill_dir

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

_BASE_MANIFEST = {
    "id": "demo",
    "name": "Demo",
    "version": "0.1.0",
    "description": "A demo agent.",
    "author": "AMD",
    "license": "MIT",
    "language": "python",
}


def _manifest(**skill_blocks) -> AgentManifest:
    """Parse a minimal manifest with the given skill blocks layered on."""
    return AgentManifest.from_dict({**_BASE_MANIFEST, **skill_blocks})


def _two_set_manifest() -> AgentManifest:
    return _manifest(
        skill_sets={
            "personal": ["inbox-triage", "newsletter-digest"],
            "work": ["inbox-triage", "meeting-scheduling"],
        },
        default_skill_set="personal",
    )


def _skill_text(name: str) -> str:
    return (
        f"---\nname: {name}\n"
        f"description: Test skill {name}. Use when exercising skill sets.\n"
        f"---\n\n# {name.title()}\n\nBody of {name}.\n"
    )


@pytest.fixture
def bundled(tmp_path: Path) -> Path:
    """An agent-bundled skills root holding every skill the tests declare."""
    root = tmp_path / "pkg" / "skills"
    for name in (
        "always-on",
        "inbox-triage",
        "newsletter-digest",
        "meeting-scheduling",
    ):
        write_skill_dir(root, name, _skill_text(name))
    return root


# ----------------------------------------------------------------------
# Manifest parsing — the additive grammar
# ----------------------------------------------------------------------


def test_manifest_without_skill_blocks_is_empty_and_falsy():
    manifest = _manifest()
    assert not manifest.skill_sets
    assert manifest.skill_sets.always == ()
    assert manifest.skill_sets.set_names == []
    assert manifest.skill_sets.default_set is None


def test_parse_accepts_string_and_mapping_refs():
    sets = parse_skill_sets(
        {
            "skills": ["always-on"],
            "skill_sets": {
                "work": [
                    "inbox-triage",
                    {
                        "name": "meeting-scheduling",
                        "version": ">=0.1.0",
                        "required": False,
                    },
                ]
            },
            "default_skill_set": "work",
        }
    )
    assert [ref.name for ref in sets.always] == ["always-on"]
    work = sets.sets["work"]
    assert [ref.name for ref in work] == ["inbox-triage", "meeting-scheduling"]
    assert work[0].required is True and work[0].version is None
    assert work[1].required is False and work[1].version == ">=0.1.0"


def test_manifest_preserves_declaration_order_of_sets():
    manifest = _manifest(
        skill_sets={"work": ["inbox-triage"], "personal": ["newsletter-digest"]},
        default_skill_set="work",
    )
    assert manifest.skill_sets.set_names == ["work", "personal"]


def test_skills_for_prepends_the_always_on_list():
    sets = parse_skill_sets(
        {
            "skills": ["always-on"],
            "skill_sets": {"work": ["inbox-triage"]},
            "default_skill_set": "work",
        }
    )
    assert [r.name for r in sets.skills_for("work")] == ["always-on", "inbox-triage"]


@pytest.mark.parametrize(
    "blocks, expected",
    [
        ({"skill_sets": ["work"]}, "must be a mapping"),
        ({"skill_sets": {"work": []}, "default_skill_set": "work"}, "is empty"),
        ({"skill_sets": {"work": ["inbox-triage"]}}, "default_skill_set"),
        (
            {"skill_sets": {"work": ["inbox-triage"]}, "default_skill_set": "personal"},
            "does not name a declared skill set",
        ),
        (
            {"skill_sets": {"Work": ["inbox-triage"]}, "default_skill_set": "Work"},
            "invalid set name",
        ),
        (
            {
                "skill_sets": {"work": ["inbox-triage", "inbox-triage"]},
                "default_skill_set": "work",
            },
            "twice",
        ),
        (
            {
                "skills": ["inbox-triage"],
                "skill_sets": {"work": ["inbox-triage"]},
                "default_skill_set": "work",
            },
            "already in the always-on",
        ),
        (
            {
                "skill_sets": {"work": [{"name": "x", "requird": False}]},
                "default_skill_set": "work",
            },
            "unrecognized key",
        ),
        (
            {
                "skill_sets": {"work": [{"name": "x", "required": "yes"}]},
                "default_skill_set": "work",
            },
            "must be true or false",
        ),
        (
            {
                "skill_sets": {"work": [{"name": "x", "version": 1}]},
                "default_skill_set": "work",
            },
            "must be a non-empty string",
        ),
        (
            {
                "skill_sets": {"work": [{"version": "1.0.0"}]},
                "default_skill_set": "work",
            },
            "missing a skill name",
        ),
        (
            {"skill_sets": {"work": ["Inbox_Triage"]}, "default_skill_set": "work"},
            "not a valid",
        ),
        ({"skills": "inbox-triage"}, "must be a list"),
    ],
)
def test_malformed_declarations_fail_loudly(blocks, expected):
    with pytest.raises(SkillValidationError, match=expected):
        parse_skill_sets(blocks)
    # The same failure must reach a manifest author as a ManifestError.
    with pytest.raises(ManifestError, match=expected):
        _manifest(**blocks)


def test_manifest_error_names_the_file():
    path = "/tmp/does-not-matter/gaia-agent.yaml"
    with pytest.raises(ManifestError, match=path):
        AgentManifest.from_dict(
            {**_BASE_MANIFEST, "skill_sets": {"work": []}, "default_skill_set": "work"},
            source=path,
        )


# ----------------------------------------------------------------------
# Resolution order
# ----------------------------------------------------------------------


def test_resolution_order_explicit_then_selector_then_default():
    sets = _two_set_manifest().skill_sets

    default = sets.resolve()
    assert (default.name, default.source) == ("personal", SOURCE_DEFAULT)

    selector = sets.resolve(selected="work")
    assert (selector.name, selector.source) == ("work", SOURCE_SELECTOR)

    explicit = sets.resolve(requested="work", selected="personal")
    assert (explicit.name, explicit.source) == ("work", SOURCE_EXPLICIT)


def test_resolution_returns_only_the_selected_sets_skills():
    sets = _two_set_manifest().skill_sets
    assert [r.name for r in sets.resolve(requested="work").skills] == [
        "inbox-triage",
        "meeting-scheduling",
    ]
    assert [r.name for r in sets.resolve(requested="personal").skills] == [
        "inbox-triage",
        "newsletter-digest",
    ]


def test_blank_request_is_treated_as_unset():
    sets = _two_set_manifest().skill_sets
    assert sets.resolve(requested="  ", selected="work").source == SOURCE_SELECTOR


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"requested": "buisness"}, "Skill set 'buisness' is not declared"),
        ({"selected": "buisness"}, "selector returned skill set 'buisness'"),
    ],
)
def test_unknown_set_fails_loudly_listing_valid_sets(kwargs, expected):
    sets = _two_set_manifest().skill_sets
    with pytest.raises(SkillSetError) as excinfo:
        sets.resolve(**kwargs)
    message = str(excinfo.value)
    assert expected in message
    assert "Valid sets: personal, work" in message


def test_requesting_a_set_on_an_agent_with_none_fails_loudly():
    with pytest.raises(SkillSetError, match="declares no 'skill_sets:' block"):
        _manifest(skills=["always-on"]).skill_sets.resolve(requested="work")


def test_no_sets_declared_resolves_to_the_always_on_list():
    resolution = _manifest(skills=["always-on"]).skill_sets.resolve()
    assert resolution.name is None
    assert resolution.source == SOURCE_NONE
    assert [r.name for r in resolution.skills] == ["always-on"]


def test_hand_built_sets_without_a_default_fail_loudly():
    # parse_skill_sets rejects this shape; a programmatic caller must too.
    with pytest.raises(SkillSetError, match="No skill set could be resolved"):
        SkillSets(sets={"work": ()}).resolve()


# ----------------------------------------------------------------------
# Agent integration — only the selected set registers
# ----------------------------------------------------------------------


def _write_manifest(tmp_path: Path, **skill_blocks) -> Path:
    path = tmp_path / "gaia-agent.yaml"
    path.write_text(
        yaml.safe_dump({**_BASE_MANIFEST, **skill_blocks}, sort_keys=False),
        encoding="utf-8",
    )
    return path


class _StubAgent:
    """Drives the real skill-set methods without booting the LLM stack."""

    from gaia.agents.base.agent import Agent

    REQUIRED_CONNECTORS: list = []
    SKILL_DIRS: list = []
    SKILL_MANIFEST = None
    _instance_tools = None
    _loaded_skills = None
    _skill_sets = None
    _requested_skill_set = None
    _active_skill_set = None

    skill_manager = Agent.skill_manager
    skill_sets = Agent.skill_sets
    active_skill_set = Agent.active_skill_set
    loaded_skills = Agent.loaded_skills
    _tools_registry = Agent._tools_registry
    _format_tools_for_prompt = Agent._format_tools_for_prompt
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill
    select_skill_set = Agent.select_skill_set
    resolve_skill_set = Agent.resolve_skill_set
    load_skill_set = Agent.load_skill_set
    get_skills_system_prompt = Agent.get_skills_system_prompt

    def __init__(self, manager, *, manifest=None, skill_set=None):
        self._skill_manager = manager
        self.SKILL_MANIFEST = str(manifest) if manifest else None
        self._requested_skill_set = skill_set
        self.rebuilt = 0

    def rebuild_system_prompt(self):
        self.rebuilt += 1


def _agent(tmp_path, bundled, *, skill_set=None, **skill_blocks) -> _StubAgent:
    return _StubAgent(
        isolated_manager(tmp_path, agent_skill_dirs=[bundled]),
        manifest=_write_manifest(tmp_path, **skill_blocks),
        skill_set=skill_set,
    )


def test_agent_loads_only_the_default_set(tmp_path, bundled):
    agent = _agent(
        tmp_path,
        bundled,
        skill_sets={
            "personal": ["inbox-triage", "newsletter-digest"],
            "work": ["inbox-triage", "meeting-scheduling"],
        },
        default_skill_set="personal",
    )

    loaded = agent.load_skill_set()

    assert sorted(loaded) == ["inbox-triage", "newsletter-digest"]
    assert sorted(agent.loaded_skills) == ["inbox-triage", "newsletter-digest"]
    assert "meeting-scheduling" not in agent.loaded_skills
    assert agent.active_skill_set == "personal"


def test_agent_explicit_skill_set_beats_the_selector_hook(tmp_path, bundled):
    agent = _agent(
        tmp_path,
        bundled,
        skill_set="work",
        skill_sets={
            "personal": ["newsletter-digest"],
            "work": ["meeting-scheduling"],
        },
        default_skill_set="personal",
    )
    agent.select_skill_set = lambda: "personal"

    agent.load_skill_set()

    assert agent.active_skill_set == "work"
    assert list(agent.loaded_skills) == ["meeting-scheduling"]


def test_agent_selector_hook_beats_the_default(tmp_path, bundled):
    agent = _agent(
        tmp_path,
        bundled,
        skill_sets={
            "personal": ["newsletter-digest"],
            "work": ["meeting-scheduling"],
        },
        default_skill_set="personal",
    )
    agent.select_skill_set = lambda: "work"

    agent.load_skill_set()

    assert agent.active_skill_set == "work"
    assert list(agent.loaded_skills) == ["meeting-scheduling"]


def test_agent_always_on_skills_load_for_every_set(tmp_path, bundled):
    blocks = dict(
        skills=["always-on"],
        skill_sets={
            "personal": ["newsletter-digest"],
            "work": ["meeting-scheduling"],
        },
        default_skill_set="personal",
    )
    for requested, extra in (
        ("personal", "newsletter-digest"),
        ("work", "meeting-scheduling"),
    ):
        agent = _agent(tmp_path, bundled, skill_set=requested, **blocks)
        agent.load_skill_set()
        assert sorted(agent.loaded_skills) == sorted(["always-on", extra])


def test_agent_unknown_skill_set_fails_loudly_and_loads_nothing(tmp_path, bundled):
    agent = _agent(
        tmp_path,
        bundled,
        skill_set="buisness",
        skill_sets={"personal": ["newsletter-digest"], "work": ["meeting-scheduling"]},
        default_skill_set="personal",
    )

    with pytest.raises(SkillSetError, match="Valid sets: personal, work"):
        agent.load_skill_set()

    assert agent.loaded_skills == {}
    assert agent.active_skill_set is None


def test_agent_switching_sets_unloads_the_previous_one(tmp_path, bundled):
    agent = _agent(
        tmp_path,
        bundled,
        skill_sets={
            "personal": ["inbox-triage", "newsletter-digest"],
            "work": ["inbox-triage", "meeting-scheduling"],
        },
        default_skill_set="personal",
    )
    agent.load_skill_set()

    agent.load_skill_set("work")

    assert sorted(agent.loaded_skills) == ["inbox-triage", "meeting-scheduling"]
    assert "newsletter-digest" not in agent.get_skills_system_prompt()
    assert "meeting-scheduling" in agent.get_skills_system_prompt()


def test_agent_missing_required_skill_fails_loudly(tmp_path, bundled):
    agent = _agent(
        tmp_path,
        bundled,
        skill_sets={"work": ["not-bundled-anywhere"]},
        default_skill_set="work",
    )
    with pytest.raises(Exception, match="not-bundled-anywhere"):
        agent.load_skill_set()


def test_agent_missing_optional_skill_is_skipped(tmp_path, bundled):
    agent = _agent(
        tmp_path,
        bundled,
        skill_sets={
            "work": [
                "meeting-scheduling",
                {"name": "not-bundled-anywhere", "required": False},
            ]
        },
        default_skill_set="work",
    )

    loaded = agent.load_skill_set()

    assert list(loaded) == ["meeting-scheduling"]
    assert agent.active_skill_set == "work"


def test_agent_without_a_manifest_loads_nothing(tmp_path, bundled):
    agent = _StubAgent(isolated_manager(tmp_path, agent_skill_dirs=[bundled]))
    assert agent.load_skill_set() == {}
    assert agent.loaded_skills == {}
    assert agent.get_skills_system_prompt() == ""


# ----------------------------------------------------------------------
# The real Agent.__init__ path
# ----------------------------------------------------------------------


def test_agent_init_loads_the_resolved_set(tmp_path, bundled):
    """A real ``Agent`` subclass resolves and loads its set during __init__."""
    from gaia.agents.base.agent import Agent

    manifest = _write_manifest(
        tmp_path,
        skill_sets={
            "personal": ["newsletter-digest"],
            "work": ["meeting-scheduling"],
        },
        default_skill_set="personal",
    )
    manager = isolated_manager(tmp_path, agent_skill_dirs=[bundled])

    class _Harness(Agent):
        SKILL_MANIFEST = str(manifest)

        def __init__(self, **kwargs):
            self._skill_manager = manager
            super().__init__(skip_lemonade=True, silent_mode=True, **kwargs)

        def _register_tools(self):
            pass

    with patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()):
        agent = _Harness()
        assert agent.active_skill_set == "personal"
        assert list(agent.loaded_skills) == ["newsletter-digest"]
        assert "Body of newsletter-digest" in agent.system_prompt

        override = _Harness(skill_set="work")
        assert override.active_skill_set == "work"
        assert list(override.loaded_skills) == ["meeting-scheduling"]

        with pytest.raises(SkillSetError, match="Valid sets: personal, work"):
            _Harness(skill_set="buisness")


def test_agent_init_without_a_manifest_is_unchanged(tmp_path):
    """The default path must not read any skills root at all."""
    from gaia.agents.base.agent import Agent

    class _Plain(Agent):
        def _register_tools(self):
            pass

    with patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()):
        agent = _Plain(skip_lemonade=True, silent_mode=True)

    assert agent.loaded_skills == {}
    assert agent.active_skill_set is None
    assert agent.get_skills_system_prompt() == ""
    assert agent._skill_manager is None
