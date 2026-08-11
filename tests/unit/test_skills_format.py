# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the ``SKILL.md`` parser, writer, and validator (issue #888).

Covers the acceptance criteria that live at the format layer: round-trip
identity, the bare agentskills.io skill, the ignored standard keys, and every
loud-failure path (name↔directory mismatch, bad SemVer, over-long description,
malformed permissions).
"""

from __future__ import annotations

import pytest

from gaia.skills import (
    DEFAULT_SECURITY_TIER,
    MAX_DESCRIPTION_LENGTH,
    Skill,
    SkillValidationError,
    parse_skill,
    parse_skill_file,
    parse_skill_metadata,
)
from tests.unit.skills_helpers import FIXTURES, copy_fixture, write_skill_dir

BARE = """---
name: bare-standard
description: Walk through a production incident postmortem. Use when the user mentions an outage.
---

# Incident Review

1. Establish the timeline.
"""

FULL = """---
name: web-search
description: Search the web via the Brave Search API.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: verified
    permissions:
      - network:read:*.brave.com
    requirements:
      python: ">=3.10"
      dependencies:
        - requests>=2.31
      env_vars:
        - BRAVE_API_KEY
      hardware: {npu: optional}
    tools:
      - name: search_web
        description: Search the web for current information
        parameters:
          query: {type: string, required: true}
          max_results: {type: integer, required: false}
        returns: {type: object}
        atomic: true
    tools_required:
      - remember
  hermes:
    category: research
---

# Web Search

Search first, then fetch the best result.
"""


# ----------------------------------------------------------------------
# Round-trip identity — acceptance criterion #1
# ----------------------------------------------------------------------


@pytest.mark.parametrize("text", [BARE, FULL], ids=["bare", "full"])
def test_round_trip_is_identity(text):
    """parse → write → parse yields an identical Skill."""
    first = parse_skill(text)
    second = parse_skill(first.to_markdown())
    assert second == first


@pytest.mark.parametrize("text", [BARE, FULL], ids=["bare", "full"])
def test_round_trip_is_byte_stable_after_one_pass(text):
    """A second write produces byte-identical output — no drift on re-save."""
    once = parse_skill(text).to_markdown()
    twice = parse_skill(once).to_markdown()
    assert twice == once


@pytest.mark.parametrize(
    "fixture",
    ["bare-standard", "web-search", "triage-support-ticket", "local-capability"],
)
def test_every_fixture_round_trips(fixture):
    skill = parse_skill_file(FIXTURES / fixture)
    assert parse_skill(skill.to_markdown()) == skill


def test_round_trip_preserves_foreign_metadata_namespaces():
    skill = parse_skill(FULL)
    assert skill.other_metadata == {"hermes": {"category": "research"}}
    assert parse_skill(skill.to_markdown()).other_metadata == skill.other_metadata


def test_round_trip_ignores_but_preserves_standard_extras():
    """`compatibility` / `allowed-tools` parse, are ignored, and survive a write."""
    text = BARE.replace(
        "---\n\n# Incident",
        'allowed-tools: Read, Write\ncompatibility: ">=1.0"\n---\n\n# Incident',
    )
    skill = parse_skill(text)
    assert skill.extra_fields["allowed-tools"] == "Read, Write"
    # Ignored means: not a permission mechanism.
    assert skill.gaia.permissions == []
    assert parse_skill(skill.to_markdown()) == skill


def test_write_and_reparse_from_disk(tmp_path):
    skill = parse_skill(FULL)
    path = skill.write(tmp_path / "web-search" / "SKILL.md")
    assert parse_skill_file(path) == skill


def test_write_without_destination_fails_loudly():
    skill = parse_skill(BARE)
    with pytest.raises(ValueError, match="needs a destination"):
        skill.write()


# ----------------------------------------------------------------------
# Bare agentskills.io skill — acceptance criterion #2
# ----------------------------------------------------------------------


def test_bare_standard_skill_loads_instruction_only():
    skill = parse_skill(BARE)
    assert skill.is_instruction_only
    assert skill.security_tier == DEFAULT_SECURITY_TIER == "experimental"
    assert skill.gaia.tools == []
    assert skill.gaia.permissions == []
    assert skill.gaia.tools_required == []
    assert skill.version is None
    assert skill.body.startswith("# Incident Review")


def test_bare_skill_does_not_gain_a_gaia_block_on_write():
    """A standard skill stays standard — GAIA never stamps defaults into it."""
    written = parse_skill(BARE).to_markdown()
    assert "metadata" not in written
    assert "security_tier" not in written


def test_gaia_fields_parse_into_typed_metadata():
    skill = parse_skill(FULL)
    assert skill.security_tier == "verified"
    assert skill.gaia.permissions == ["network:read:*.brave.com"]
    assert skill.gaia.requirements.python == ">=3.10"
    assert skill.gaia.requirements.env_vars == ["BRAVE_API_KEY"]
    assert skill.gaia.requirements.hardware == {"npu": "optional"}
    assert skill.gaia.tools_required == ["remember"]

    (declared,) = skill.gaia.tools
    assert declared.name == "search_web"
    assert declared.atomic is True
    assert declared.parameters["query"] == {"type": "string", "required": True}
    assert skill.tool_names == ["search_web"]
    assert skill.namespaced_tool_name("search_web") == "web-search/search_web"


def test_metadata_only_parse_drops_the_body(tmp_path):
    """Progressive disclosure level 1 keeps metadata resident, not instructions."""
    copy_fixture("web-search", tmp_path)
    skill = parse_skill_metadata(tmp_path / "web-search")
    assert skill.name == "web-search"
    assert skill.description
    assert skill.body == ""
    assert skill.tool_names == ["search_web"]


# ----------------------------------------------------------------------
# Loud failures
# ----------------------------------------------------------------------


def test_missing_frontmatter_fails_loudly():
    with pytest.raises(SkillValidationError, match="no YAML frontmatter"):
        parse_skill("# Just markdown\n")


def test_invalid_yaml_fails_loudly():
    with pytest.raises(SkillValidationError, match="invalid"):
        parse_skill("---\nname: x\n  bad: [indent\n---\n\nbody\n")


def test_missing_name_fails_loudly():
    with pytest.raises(SkillValidationError, match="'name' is missing"):
        parse_skill("---\ndescription: something\n---\n\nbody\n")


def test_missing_description_fails_loudly():
    with pytest.raises(SkillValidationError, match="'description' is missing"):
        parse_skill("---\nname: thing\n---\n\nbody\n")


@pytest.mark.parametrize(
    "name",
    ["Web-Search", "web_search", "-web", "web-", "web--search", "web search", "wéb"],
)
def test_invalid_names_fail_loudly(name):
    with pytest.raises(SkillValidationError, match="not a valid skill name"):
        parse_skill(f"---\nname: {name}\ndescription: d\n---\n\nbody\n")


def test_over_long_name_fails_loudly():
    name = "a" * 65
    with pytest.raises(SkillValidationError, match="the limit is 64"):
        parse_skill(f"---\nname: {name}\ndescription: d\n---\n\nbody\n")


def test_over_long_description_fails_loudly():
    description = "x" * (MAX_DESCRIPTION_LENGTH + 1)
    with pytest.raises(SkillValidationError, match="the limit is 1024"):
        parse_skill(f"---\nname: ok\ndescription: {description}\n---\n\nbody\n")


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0.0", "latest", "01.0.0"])
def test_bad_semver_fails_loudly(version):
    with pytest.raises(SkillValidationError, match="not valid SemVer"):
        parse_skill(
            f'---\nname: ok\ndescription: d\nversion: "{version}"\n---\n\nbody\n'
        )


@pytest.mark.parametrize("version", ["0.0.0", "1.0.0", "1.2.3-rc.1", "1.2.3+build.5"])
def test_valid_semver_accepted(version):
    skill = parse_skill(
        f'---\nname: ok\ndescription: d\nversion: "{version}"\n---\n\nbody\n'
    )
    assert skill.version == version


def test_name_directory_mismatch_fails_loudly(tmp_path):
    write_skill_dir(tmp_path, "not-the-name", BARE)
    with pytest.raises(SkillValidationError, match="but the directory is named"):
        parse_skill_file(tmp_path / "not-the-name")


def test_name_directory_match_accepted(tmp_path):
    write_skill_dir(tmp_path, "bare-standard", BARE)
    assert parse_skill_file(tmp_path / "bare-standard").name == "bare-standard"


def test_missing_skill_file_fails_loudly(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SkillValidationError, match="No SKILL.md"):
        parse_skill_file(tmp_path / "empty")


@pytest.mark.parametrize(
    "tier_line,expected",
    [
        ("security_tier: nuclear", "not one of"),
        ("permissions: network:read", "must be a list"),
        ("tools: 3", "must be a list"),
        ("tools_required: query_documents", "must be a list"),
        ("requirements: []", "must be a mapping"),
    ],
)
def test_malformed_gaia_block_fails_loudly(tier_line, expected):
    text = f"---\nname: ok\ndescription: d\nmetadata:\n  gaia:\n    {tier_line}\n---\n\nbody\n"
    with pytest.raises(SkillValidationError, match=expected):
        parse_skill(text)


@pytest.mark.parametrize(
    "permission,expected",
    [
        ("networkread", "missing its level"),
        ("teleport:read", "unknown domain"),
        ("network:teleport", "does not define"),
        ("shell:read", "does not define"),
    ],
)
def test_malformed_permission_fails_loudly(permission, expected):
    text = (
        "---\nname: ok\ndescription: d\nmetadata:\n  gaia:\n    permissions:\n"
        f"      - {permission}\n---\n\nbody\n"
    )
    with pytest.raises(SkillValidationError, match=expected):
        parse_skill(text)


def test_duplicate_tool_names_fail_loudly():
    text = (
        "---\nname: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - name: dup\n        parameters: {}\n"
        "      - name: dup\n        parameters: {}\n---\n\nbody\n"
    )
    with pytest.raises(SkillValidationError, match="more than once"):
        parse_skill(text)


def test_tool_entry_without_name_fails_loudly():
    text = (
        "---\nname: ok\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - description: nameless\n---\n\nbody\n"
    )
    with pytest.raises(SkillValidationError, match="missing its 'name'"):
        parse_skill(text)


def test_numeric_version_fails_loudly_with_a_quoting_hint():
    with pytest.raises(SkillValidationError, match="Quote it"):
        parse_skill("---\nname: ok\ndescription: d\nversion: 1.0\n---\n\nbody\n")


def test_error_messages_name_what_to_do():
    """Fail-loudly rule: every message points at a fix and a doc."""
    with pytest.raises(SkillValidationError) as excinfo:
        parse_skill("---\nname: Bad_Name\ndescription: d\n---\n\nbody\n")
    message = str(excinfo.value)
    assert "web-research" in message  # what a good name looks like
    assert "skill-format" in message  # where to look next


def test_crlf_frontmatter_parses():
    skill = parse_skill(BARE.replace("\n", "\r\n"))
    assert skill.name == "bare-standard"


def test_skill_equality_ignores_provenance(tmp_path):
    """Two copies in different roots are the same Skill — that is what makes
    round-trip identity meaningful across directories."""
    a = parse_skill(BARE)
    b = parse_skill(BARE)
    b.path = tmp_path / "elsewhere" / "SKILL.md"
    b.root = "user"
    b.read_only = True
    assert a == b


def test_skill_dataclass_is_constructible_directly():
    """Downstream issues build Skills in code — keep the constructor usable."""
    skill = Skill(name="hand-made", description="Built in code.")
    assert skill.is_instruction_only
    assert skill.security_tier == "experimental"
    assert parse_skill(skill.to_markdown()) == skill
