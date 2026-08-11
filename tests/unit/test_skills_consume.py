# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Declarative skill consumption (#2467 scope D): ``gaia-agent.yaml`` → loaded skills.

The acceptance criterion is portability: an agent that GAIA does **not** ship must
resolve an installed hub skill by ``name@version`` through the same path a bundled
agent uses. So the end-to-end test here defines its agent class inside the test,
writes a ``gaia-agent.yaml`` beside it on a tmp path, and asserts the skill's tools
and instructions reach the agent — with no subclass hook, no registration call, and
no edit to the agent for skills to work.
"""

from __future__ import annotations

import textwrap

import pytest

from gaia.skills.consume import (
    SkillRequirement,
    find_agent_manifest,
    resolve_requirements,
)
from gaia.skills.errors import SkillNotFoundError, SkillValidationError
from gaia.skills.sets import SkillRef
from tests.unit.skills_helpers import isolated_manager, write_skill_dir

# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------


def test_the_agent_hot_path_constant_matches_the_canonical_one():
    """``Agent`` duplicates the filename to keep ``gaia.skills`` off ``__init__``.

    That import runs for every agent, so the cheap path check has to happen before
    it — at the cost of one duplicated literal, which this pins.
    """
    from gaia.agents.base.agent import Agent
    from gaia.skills.consume import AGENT_MANIFEST_FILENAME

    assert Agent._SKILL_MANIFEST_FILENAME == AGENT_MANIFEST_FILENAME


def test_finds_the_manifest_beside_the_module_and_one_level_up(tmp_path):
    package = tmp_path / "pkg" / "gaia_agent_demo"
    package.mkdir(parents=True)
    module = package / "agent.py"
    module.write_text("# agent\n", "utf-8")

    assert find_agent_manifest(module) is None

    # Custom-agent layout: manifest beside agent.py.
    beside = package / "gaia-agent.yaml"
    beside.write_text("id: demo\n", "utf-8")
    assert find_agent_manifest(module) == beside

    # Hub-package layout: manifest one level up. The nearer copy still wins.
    above = tmp_path / "pkg" / "gaia-agent.yaml"
    above.write_text("id: demo\n", "utf-8")
    assert find_agent_manifest(module) == beside
    beside.unlink()
    assert find_agent_manifest(module) == above


def test_search_does_not_walk_beyond_the_parent(tmp_path):
    """Walking further up would eventually claim an unrelated project's manifest."""
    (tmp_path / "gaia-agent.yaml").write_text("id: someone-else\n", "utf-8")
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    module = deep / "agent.py"
    module.write_text("# agent\n", "utf-8")
    assert find_agent_manifest(module) is None


def test_agent_manifest_parser_validates_the_skills_block(tmp_path):
    """A typo in skills: is a publish-time error, not a runtime surprise."""
    from gaia.hub.manifest import ManifestError, parse

    base = textwrap.dedent("""\
        id: web
        name: Web Research
        version: 0.1.0
        description: A web research agent.
        author: tester
        license: MIT
        language: python
        """)
    good = tmp_path / "good" / "gaia-agent.yaml"
    good.parent.mkdir()
    good.write_text(
        base + 'skills:\n  - name: web-research\n    version: ">=1.0.0"\n', "utf-8"
    )
    assert parse(good).skill_sets.always == (
        SkillRef(name="web-research", version=">=1.0.0", required=True),
    )

    bad = tmp_path / "bad" / "gaia-agent.yaml"
    bad.parent.mkdir()
    bad.write_text(base + "skills:\n  - version: '>=1.0.0'\n", "utf-8")
    with pytest.raises(ManifestError, match="missing a skill name"):
        parse(bad)


def test_agent_manifest_without_skills_block_is_unchanged(tmp_path):
    from gaia.hub.manifest import parse

    manifest = tmp_path / "gaia-agent.yaml"
    manifest.write_text(
        textwrap.dedent("""\
            id: web
            name: Web Research
            version: 0.1.0
            description: A web research agent.
            author: tester
            license: MIT
            language: python
            """),
        "utf-8",
    )
    assert not parse(manifest).skill_sets


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _install(root, name, *, version, tools_required=(), tools=()):
    """Write an installed skill into a user root, as `gaia skill install` would."""
    lines = [
        "---",
        f"name: {name}",
        f"description: The {name} skill, installed for a resolution test.",
        f"version: {version}",
        "metadata:",
        "  gaia:",
    ]
    if tools:
        # The declaration must mirror the function signature exactly — the loader
        # refuses the skill otherwise, so the fixture has to be honest too.
        lines.append("    tools:")
        for tool in tools:
            lines += [
                f"      - name: {tool}",
                f"        description: Does {tool}.",
                "        parameters:",
                "          text:",
                "            type: string",
                "            required: true",
            ]
    if tools_required:
        lines.append("    tools_required:")
        lines += [f"      - {t}" for t in tools_required]
    lines += ["---", "", f"# {name}", "", "Follow these steps.", ""]
    body = "\n".join(lines)
    tools_py = None
    if tools:
        functions = "\n\n".join(
            f'@tool\ndef {t}(text: str) -> dict:\n    """Does {t}."""\n    return {{"t": text}}'
            for t in tools
        )
        tools_py = f"from gaia.agents.base.tools import tool\n\n\n{functions}\n"
    return write_skill_dir(root, name, body, tools=tools_py)


def test_resolves_an_installed_skill_by_version_range(tmp_path):
    root = tmp_path / "home" / "skills"
    _install(root, "web-research", version="1.4.0")
    manager = isolated_manager(tmp_path, user_skills_root=root)

    resolved = resolve_requirements(
        [SkillRequirement(name="web-research", version=">=1.0.0")], manager=manager
    )
    assert resolved.names == ["web-research"]
    assert resolved.skipped == {}


def test_required_skill_that_is_not_installed_raises_with_the_install_command(tmp_path):
    manager = isolated_manager(tmp_path, user_skills_root=tmp_path / "home" / "skills")
    with pytest.raises(SkillNotFoundError) as excinfo:
        resolve_requirements(
            [SkillRequirement(name="web-research", version="^1.0")], manager=manager
        )
    assert "gaia skill install web-research@^1.0" in str(excinfo.value)


def test_optional_skill_that_is_missing_is_recorded_not_raised(tmp_path):
    root = tmp_path / "home" / "skills"
    _install(root, "web-research", version="1.0.0")
    manager = isolated_manager(tmp_path, user_skills_root=root)

    resolved = resolve_requirements(
        [
            SkillRequirement(name="web-research", version="^1.0"),
            SkillRequirement(name="incident-review", version="^0.1", required=False),
        ],
        manager=manager,
    )
    assert resolved.names == ["web-research"]
    assert "not installed" in resolved.skipped["incident-review"]


def test_required_version_conflict_fails_loud(tmp_path):
    root = tmp_path / "home" / "skills"
    _install(root, "web-research", version="0.9.0")
    manager = isolated_manager(tmp_path, user_skills_root=root)

    with pytest.raises(SkillValidationError, match="Version conflict"):
        resolve_requirements(
            [SkillRequirement(name="web-research", version=">=1.0.0")], manager=manager
        )


def test_optional_version_conflict_is_skipped_with_a_reason(tmp_path):
    root = tmp_path / "home" / "skills"
    _install(root, "web-research", version="0.9.0")
    manager = isolated_manager(tmp_path, user_skills_root=root)

    resolved = resolve_requirements(
        [SkillRequirement(name="web-research", version=">=1.0.0", required=False)],
        manager=manager,
    )
    assert resolved.names == []
    assert "0.9.0" in resolved.skipped["web-research"]


def test_an_unversioned_skill_cannot_satisfy_a_pin(tmp_path):
    """Otherwise a local edit would silently shadow a pinned hub install."""
    root = tmp_path / "home" / "skills"
    write_skill_dir(
        root,
        "web-research",
        "---\nname: web-research\ndescription: No version declared here at all.\n---\nBody\n",
    )
    manager = isolated_manager(tmp_path, user_skills_root=root)

    with pytest.raises(SkillValidationError, match="unversioned"):
        resolve_requirements(
            [SkillRequirement(name="web-research", version=">=1.0.0")], manager=manager
        )
    # ...but an unpinned requirement still accepts it.
    assert resolve_requirements(
        [SkillRequirement(name="web-research", version="*")], manager=manager
    ).names == ["web-research"]


def test_requirements_from_refs_adapts_a_skill_set_expansion(tmp_path):
    """The seam #2466's ``skill_sets:`` uses to get version resolution.

    Its ``SkillRef`` parses a ``version`` but explicitly does not act on one until
    this phase. Duck-typed so neither module imports the other, and so whichever
    lands second is a wire-up rather than a rewrite.
    """
    from dataclasses import dataclass as dc

    from gaia.skills.consume import requirements_from_refs

    @dc(frozen=True)
    class ForeignSkillRef:
        """Stands in for gaia.skills.sets.SkillRef, field-for-field."""

        name: str
        version: str = None
        required: bool = True

    root = tmp_path / "home" / "skills"
    _install(root, "inbox-triage", version="1.4.0")
    _install(root, "meeting-scheduling", version="0.9.0")
    manager = isolated_manager(tmp_path, user_skills_root=root)

    requirements = requirements_from_refs(
        [
            ForeignSkillRef("inbox-triage", ">=1.0.0"),
            # A ref with no version must mean "any", not "no version".
            ForeignSkillRef("meeting-scheduling", None, False),
        ],
        origin="skill_sets:triage",
    )
    assert [(r.name, r.version, r.required) for r in requirements] == [
        ("inbox-triage", ">=1.0.0", True),
        ("meeting-scheduling", "*", False),
    ]
    assert sorted(resolve_requirements(requirements, manager=manager).names) == [
        "inbox-triage",
        "meeting-scheduling",
    ]

    # And the version a set declared is actually enforced through this path.
    with pytest.raises(SkillValidationError, match="Version conflict"):
        resolve_requirements(
            requirements_from_refs(
                [ForeignSkillRef("meeting-scheduling", ">=2.0.0")],
                origin="skill_sets:triage",
            ),
            manager=manager,
        )


def test_requirements_from_refs_rejects_an_object_with_no_name():
    from gaia.skills.consume import requirements_from_refs

    class Nameless:
        version = ">=1.0.0"

    with pytest.raises(SkillValidationError, match="non-empty"):
        requirements_from_refs([Nameless()], origin="skill_sets:x")


def test_dependency_order_puts_a_tool_provider_first(tmp_path):
    """A skill consuming another's tool must load after it, whatever the order."""
    root = tmp_path / "home" / "skills"
    _install(root, "searcher", version="1.0.0", tools=["search_web"])
    _install(root, "reporter", version="1.0.0", tools_required=["searcher/search_web"])
    manager = isolated_manager(tmp_path, user_skills_root=root)

    resolved = resolve_requirements(
        [
            SkillRequirement(name="reporter", version="*"),
            SkillRequirement(name="searcher", version="*"),
        ],
        manager=manager,
    )
    assert resolved.names == ["searcher", "reporter"]


def test_declaration_order_is_preserved_when_there_are_no_dependencies(tmp_path):
    root = tmp_path / "home" / "skills"
    _install(root, "alpha", version="1.0.0")
    _install(root, "beta", version="1.0.0")
    manager = isolated_manager(tmp_path, user_skills_root=root)

    resolved = resolve_requirements(
        [SkillRequirement(name="beta"), SkillRequirement(name="alpha")], manager=manager
    )
    assert resolved.names == ["beta", "alpha"]


def test_circular_skill_dependency_raises(tmp_path):
    root = tmp_path / "home" / "skills"
    _install(
        root, "left", version="1.0.0", tools=["l_tool"], tools_required=["right/r_tool"]
    )
    _install(
        root, "right", version="1.0.0", tools=["r_tool"], tools_required=["left/l_tool"]
    )
    manager = isolated_manager(tmp_path, user_skills_root=root)

    with pytest.raises(SkillValidationError, match="Circular skill dependency"):
        resolve_requirements(
            [SkillRequirement(name="left"), SkillRequirement(name="right")],
            manager=manager,
        )


# ---------------------------------------------------------------------------
# Any-agent consumption — a NON-BUNDLED agent
# ---------------------------------------------------------------------------


@pytest.fixture
def custom_agent_harness(tmp_path, monkeypatch):
    """A user-authored agent GAIA does not ship, laid out like ~/.gaia/agents/<id>/.

    Written to disk and imported by path, so ``inspect.getfile`` on the class
    returns a real module file — which is what manifest auto-detection walks from.
    Without that, the test would prove nothing about portability.
    """
    agent_dir = tmp_path / "custom-agents" / "my-harness"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.py").write_text(
        textwrap.dedent('''\
            """A user-authored agent that ships no skills of its own."""

            from gaia.agents.base.agent import Agent


            class MyHarnessAgent(Agent):
                """Declares its skills in gaia-agent.yaml; composes none in code."""

                def _register_tools(self):
                    pass
            '''),
        "utf-8",
    )

    def load(manifest: str | None):
        import importlib.util
        import sys

        if manifest is not None:
            (agent_dir / "gaia-agent.yaml").write_text(manifest, "utf-8")
        name = f"custom_harness_{abs(hash(manifest or '')) % 10**8}"
        spec = importlib.util.spec_from_file_location(name, agent_dir / "agent.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module.MyHarnessAgent

    return load


def _stub_llm(monkeypatch):
    """Keep ``Agent.__init__`` off the network — this test is about skills."""
    import gaia.agents.base.agent as agent_module

    class FakeSDK:
        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(agent_module, "AgentSDK", FakeSDK)


def test_a_non_bundled_agent_resolves_an_installed_skill_by_name_at_version(
    tmp_path, monkeypatch, custom_agent_harness
):
    """The portability criterion: no per-agent code change, no bundled skills dir."""
    _stub_llm(monkeypatch)
    root = tmp_path / "home" / "skills"
    _install(root, "web-research", version="1.4.0", tools=["search_web"])
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent_class = custom_agent_harness(textwrap.dedent("""\
            id: my-harness
            name: My Harness
            version: 0.1.0
            language: python

            skills:
              - name: web-research
                version: ">=1.0.0"
                required: true
            """))
    # The agent bundles nothing and never calls load_skill.
    assert agent_class.SKILL_DIRS == []

    agent = agent_class(silent_mode=True)
    try:
        assert sorted(agent.loaded_skills) == ["web-research"]
        loaded = agent.loaded_skills["web-research"]
        assert loaded.version == "1.4.0"
        # The skill's tool is namespaced into the agent...
        assert "web-research/search_web" in agent._tools_registry
        # ...and its instructions reach the system prompt.
        assert "SKILL: web-research" in agent.get_skills_system_prompt()
    finally:
        agent.unload_skill("web-research")


def test_a_non_bundled_agent_fails_loudly_when_a_required_skill_is_missing(
    tmp_path, monkeypatch, custom_agent_harness
):
    _stub_llm(monkeypatch)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent_class = custom_agent_harness(textwrap.dedent("""\
            id: my-harness
            name: My Harness
            version: 0.1.0
            language: python

            skills:
              - name: never-installed
                version: ">=1.0.0"
            """))
    with pytest.raises(SkillNotFoundError, match="gaia skill install never-installed"):
        agent_class(silent_mode=True)


def test_an_agent_with_no_manifest_loads_no_skills(
    tmp_path, monkeypatch, custom_agent_harness
):
    """Every existing agent must be untouched by the autoload hook."""
    _stub_llm(monkeypatch)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent = custom_agent_harness(None)(silent_mode=True)
    assert agent.loaded_skills == {}


def test_manifest_lookup_does_not_reach_the_skills_parser_without_a_manifest(
    tmp_path, monkeypatch, custom_agent_harness
):
    """The autoload hook runs in every Agent.__init__, so it must stay cheap.

    Importing the skills package drags in the connector base module. An agent
    with no manifest — which is every agent today — must not pay for it, so the
    path check happens before the import.
    """
    import gaia.skills.sets as sets_module

    _stub_llm(monkeypatch)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    def explode(*_args, **_kwargs):
        raise AssertionError(
            "the skills parser was reached for an agent with no manifest"
        )

    monkeypatch.setattr(sets_module, "parse_skill_sets", explode)
    assert custom_agent_harness(None)(silent_mode=True).loaded_skills == {}


def test_an_unreadable_manifest_raises_rather_than_assuming_no_skills(
    tmp_path, monkeypatch, custom_agent_harness
):
    """An agent whose own manifest cannot be read is broken, not skill-free."""
    _stub_llm(monkeypatch)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent_class = custom_agent_harness("id: demo\nskills: [\n")
    with pytest.raises(SkillValidationError, match="Could not read the agent manifest"):
        agent_class(silent_mode=True)


def test_an_agent_whose_manifest_declares_no_skills_loads_none(
    tmp_path, monkeypatch, custom_agent_harness
):
    _stub_llm(monkeypatch)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent_class = custom_agent_harness(
        "id: my-harness\nname: My Harness\nversion: 0.1.0\nlanguage: python\n"
    )
    assert agent_class(silent_mode=True).loaded_skills == {}


def test_autoload_can_be_disabled(tmp_path, monkeypatch, custom_agent_harness):
    _stub_llm(monkeypatch)
    root = tmp_path / "home" / "skills"
    _install(root, "web-research", version="1.4.0")
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent_class = custom_agent_harness(
        "id: my-harness\nname: My Harness\nversion: 0.1.0\nlanguage: python\n"
        'skills:\n  - name: web-research\n    version: ">=1.0.0"\n'
    )
    agent_class.AUTOLOAD_DECLARED_SKILLS = False
    try:
        agent = agent_class(silent_mode=True)
        assert agent.loaded_skills == {}
        # ...and calling it explicitly still works.
        assert sorted(agent.load_declared_skills()) == ["web-research"]
        agent.unload_skill("web-research")
    finally:
        agent_class.AUTOLOAD_DECLARED_SKILLS = True


def test_an_optional_declared_skill_that_is_missing_does_not_block_construction(
    tmp_path, monkeypatch, custom_agent_harness
):
    _stub_llm(monkeypatch)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent_class = custom_agent_harness(textwrap.dedent("""\
            id: my-harness
            name: My Harness
            version: 0.1.0
            language: python

            skills:
              - name: nice-to-have
                version: ">=1.0.0"
                required: false
            """))
    assert agent_class(silent_mode=True).loaded_skills == {}


def test_a_bad_skill_manifest_pointer_raises(
    tmp_path, monkeypatch, custom_agent_harness
):
    _stub_llm(monkeypatch)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))

    agent_class = custom_agent_harness(None)
    agent_class.SKILL_MANIFEST = "no-such-manifest.yaml"
    try:
        with pytest.raises(SkillValidationError, match="SKILL_MANIFEST"):
            agent_class(silent_mode=True)
    finally:
        agent_class.SKILL_MANIFEST = None
