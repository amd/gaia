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

import logging
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
        # A range that parses as a string but names no version (#2864). Caught
        # here, not at load, so the verdict does not depend on whether the
        # pinned skill happens to be installed on this machine.
        (
            {
                "skill_sets": {"work": [{"name": "x", "version": ">=v2"}]},
                "default_skill_set": "work",
            },
            "not a version range GAIA can evaluate",
        ),
        (
            {"skills": [{"name": "x", "version": "1.2.x"}]},
            "not a version range GAIA can evaluate",
        ),
        (
            {"skill_sets": {"work": ["Inbox_Triage"]}, "default_skill_set": "work"},
            "not a valid",
        ),
        ({"skills": "inbox-triage"}, "must be a list"),
        # A bare `work:` key with nothing indented under it — the likeliest
        # authoring slip, and it must not validate into a set that loads nothing.
        (
            {"skill_sets": {"work": None}, "default_skill_set": "work"},
            "names no skills",
        ),
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
    _skill_set_loaded = None
    _SKILL_MANIFEST_FILENAME = Agent._SKILL_MANIFEST_FILENAME

    skill_manager = Agent.skill_manager
    skill_sets = Agent.skill_sets
    _resolve_skill_manifest = Agent._resolve_skill_manifest
    _parse_skill_declarations = Agent._parse_skill_declarations
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


def test_switching_sets_leaves_a_hand_loaded_skill_alone(tmp_path, bundled):
    """A skill the agent loaded itself is not a set's to unload."""
    agent = _agent(
        tmp_path,
        bundled,
        skill_sets={
            "personal": ["newsletter-digest"],
            "work": ["meeting-scheduling"],
        },
        default_skill_set="personal",
    )
    agent.load_skill_set()
    agent.load_skill("inbox-triage")  # not in any set — loaded imperatively

    agent.load_skill_set("work")

    assert "inbox-triage" in agent.loaded_skills
    assert "newsletter-digest" not in agent.loaded_skills
    assert "meeting-scheduling" in agent.loaded_skills


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


# ----------------------------------------------------------------------
# Version pins — a declared range is checked, never silently accepted (#2864)
#
# The resolver itself is unit-tested in ``test_skills_consume.py``. These drive
# the surface an agent author actually touches: a ``version:`` in the manifest,
# resolved through ``load_skill_set``. A pin that parses and is then dropped on
# the floor is the silent-fallback mode CLAUDE.md prohibits.
# ----------------------------------------------------------------------


def _versioned_skill_text(name: str, version: str) -> str:
    return (
        f"---\nname: {name}\nversion: {version}\n"
        f"description: Test skill {name}. Use when exercising version pins.\n"
        f"---\n\n# {name.title()}\n\nBody of {name}.\n"
    )


@pytest.fixture
def pinned(tmp_path: Path) -> Path:
    """A bundled root with one versioned skill and one that declares no version."""
    root = tmp_path / "pkg" / "skills"
    write_skill_dir(
        root, "inbox-triage", _versioned_skill_text("inbox-triage", "1.4.0")
    )
    write_skill_dir(root, "no-version", _skill_text("no-version"))
    return root


def _pinned_agent(tmp_path, pinned, entry) -> _StubAgent:
    return _agent(
        tmp_path, pinned, skill_sets={"work": [entry]}, default_skill_set="work"
    )


def test_agent_loads_a_skill_whose_version_satisfies_the_pin(tmp_path, pinned):
    agent = _pinned_agent(
        tmp_path, pinned, {"name": "inbox-triage", "version": ">=1.0.0"}
    )

    loaded = agent.load_skill_set()

    assert list(loaded) == ["inbox-triage"]
    assert loaded["inbox-triage"].version == "1.4.0"


def test_agent_with_no_pin_declared_accepts_whatever_is_installed(tmp_path, pinned):
    """The common case must stay byte-identical: no pin, no version gate."""
    for entry in ("inbox-triage", {"name": "inbox-triage"}):
        agent = _pinned_agent(tmp_path, pinned, entry)
        assert list(agent.load_skill_set()) == ["inbox-triage"]


def test_agent_required_pin_violation_fails_the_launch(tmp_path, pinned):
    agent = _pinned_agent(
        tmp_path, pinned, {"name": "inbox-triage", "version": ">=2.0.0"}
    )

    with pytest.raises(SkillValidationError) as excinfo:
        agent.load_skill_set()
    message = str(excinfo.value)

    # What failed: the pin and the version actually on disk, both named.
    assert ">=2.0.0" in message and "1.4.0" in message
    # What to do, and where to look next.
    assert "gaia skill install inbox-triage@>=2.0.0" in message
    assert "loosen the pin" in message
    # The agent is untouched — a refused launch never half-loads.
    assert agent.loaded_skills == {}
    assert agent.active_skill_set is None


def test_agent_optional_pin_violation_is_skipped_with_a_reason(
    tmp_path, pinned, caplog
):
    agent = _pinned_agent(
        tmp_path,
        pinned,
        {"name": "inbox-triage", "version": ">=2.0.0", "required": False},
    )

    with caplog.at_level(logging.INFO):
        assert agent.load_skill_set() == {}
    assert agent.active_skill_set == "work"

    # "Skipped" has to be visible, not invisible: the reason names the pin and
    # the version on disk, or the agent quietly runs without the capability.
    skipped = "\n".join(
        r.getMessage() for r in caplog.records if "inbox-triage" in r.getMessage()
    )
    assert ">=2.0.0" in skipped and "1.4.0" in skipped


def test_agent_pin_against_an_unversioned_skill_is_refused(tmp_path, pinned):
    """Unsatisfiable by unknowability: absence of a version is not a match."""
    agent = _pinned_agent(
        tmp_path, pinned, {"name": "no-version", "version": ">=1.0.0"}
    )

    with pytest.raises(SkillValidationError, match="unversioned"):
        agent.load_skill_set()
    assert agent.loaded_skills == {}


def test_agent_unreadable_pin_is_rejected_rather_than_widened(tmp_path, pinned):
    """'>=v2' parses as a manifest string but names no version — never treat as any."""
    agent = _pinned_agent(tmp_path, pinned, {"name": "inbox-triage", "version": ">=v2"})

    with pytest.raises(SkillValidationError, match="does not name a version number"):
        agent.load_skill_set()
    assert agent.loaded_skills == {}


def test_agent_without_a_manifest_loads_nothing(tmp_path, bundled):
    agent = _StubAgent(isolated_manager(tmp_path, agent_skill_dirs=[bundled]))
    assert agent.load_skill_set() == {}
    assert agent.loaded_skills == {}
    assert agent.get_skills_system_prompt() == ""


def test_explicit_request_on_a_set_less_agent_is_never_discarded(tmp_path, bundled):
    """The request must raise, not evaporate.

    An agent with no declarations is falsy, and an early return on that would
    drop the user's explicit ``--skill-set`` with no error and no log line — the
    exact silent fallback the spec says cannot happen.
    """
    agent = _StubAgent(
        isolated_manager(tmp_path, agent_skill_dirs=[bundled]), skill_set="work"
    )
    with pytest.raises(SkillSetError, match="declares no 'skill_sets:' block"):
        agent.load_skill_set()

    # And passed directly, not just via the constructor.
    plain = _StubAgent(isolated_manager(tmp_path, agent_skill_dirs=[bundled]))
    with pytest.raises(SkillSetError, match="declares no 'skill_sets:' block"):
        plain.load_skill_set("work")


def test_a_failed_switch_leaves_the_agent_exactly_as_it_was(tmp_path, bundled):
    """All-or-nothing: a half-switched agent lies about what it is carrying.

    Loading the new set before retiring the old one, and rolling back what this
    call added, keeps ``active_skill_set``, the prompt, and the internal
    tracking mutually consistent after a failure.
    """
    agent = _agent(
        tmp_path,
        bundled,
        skill_sets={
            "personal": ["inbox-triage", "newsletter-digest"],
            "work": ["meeting-scheduling", "not-bundled-anywhere"],
        },
        default_skill_set="personal",
    )
    agent.load_skill_set()
    before = sorted(agent.loaded_skills)
    assert before == ["inbox-triage", "newsletter-digest"]

    with pytest.raises(Exception, match="not-bundled-anywhere"):
        agent.load_skill_set("work")

    # Still the personal set, in full, and nothing from 'work' left behind.
    assert agent.active_skill_set == "personal"
    assert sorted(agent.loaded_skills) == before
    prompt = agent.get_skills_system_prompt()
    assert "meeting-scheduling" not in prompt
    assert "newsletter-digest" in prompt

    # And the failure did not corrupt tracking: a later successful switch still
    # retires exactly the personal set.
    agent.load_skill_set("personal")
    assert agent.active_skill_set == "personal"
    assert sorted(agent.loaded_skills) == before


def test_two_agents_keep_their_own_sets(tmp_path, bundled):
    """Skill-set state is per instance — a sibling must not see it."""
    blocks = dict(
        skill_sets={
            "personal": ["newsletter-digest"],
            "work": ["meeting-scheduling"],
        },
        default_skill_set="personal",
    )
    first = _agent(tmp_path, bundled, skill_set="personal", **blocks)
    second = _agent(tmp_path, bundled, skill_set="work", **blocks)
    first.load_skill_set()
    second.load_skill_set()

    assert first.active_skill_set == "personal"
    assert second.active_skill_set == "work"
    assert list(first.loaded_skills) == ["newsletter-digest"]
    assert list(second.loaded_skills) == ["meeting-scheduling"]
    assert "meeting-scheduling" not in first.get_skills_system_prompt()
    assert "newsletter-digest" not in second.get_skills_system_prompt()


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
