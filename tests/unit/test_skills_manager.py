# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for skill discovery, precedence, permissions, tool registration, and
``Agent.load_skill`` (issue #888).

Every test runs from a cold state: roots live under ``tmp_path`` and the real
``~/.gaia/skills`` / ``~/.claude/skills`` are never read or written.
"""

from __future__ import annotations

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.skills import (
    Permission,
    SkillManager,
    SkillNotFoundError,
    SkillPermissionError,
    SkillValidationError,
    connector_requirements,
    refuse_unbridged_permissions,
    register_skill_tools,
    unregister_skill_tools,
    user_skills_dir,
)
from gaia.skills.manager import (
    ROOT_AGENT_BUNDLED,
    ROOT_CLAUDE_IMPORT,
    ROOT_USER,
)
from tests.unit.skills_helpers import copy_fixture, isolated_manager, write_skill_dir


@pytest.fixture(autouse=True)
def _clean_tool_registry():
    """Restore ``_TOOL_REGISTRY`` after any test that registers skill tools."""
    before = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(before)


@pytest.fixture
def roots(tmp_path):
    """Three empty, isolated discovery roots."""
    agent = tmp_path / "agent-pkg" / "skills"
    user = tmp_path / "gaia-home" / "skills"
    claude = tmp_path / "claude" / "skills"
    for path in (agent, user, claude):
        path.mkdir(parents=True)
    return {"agent": agent, "user": user, "claude": claude}


def make_manager(roots):
    return SkillManager(
        agent_skill_dirs=[roots["agent"]],
        user_skills_root=roots["user"],
        claude_skill_dirs=[roots["claude"]],
    )


# ----------------------------------------------------------------------
# Discovery + roots
# ----------------------------------------------------------------------


def test_cold_state_discovers_nothing(roots):
    manager = make_manager(roots)
    assert manager.list_skills() == []
    assert manager.discovery_errors == {}


def test_root_order_is_agent_then_user_then_claude(roots):
    labels = [r.label for r in make_manager(roots).roots]
    assert labels == [ROOT_AGENT_BUNDLED, ROOT_USER, ROOT_CLAUDE_IMPORT]


def test_only_three_roots_in_v1(tmp_path):
    """Project-local ./.gaia/skills and the registry-lock root are deferred."""
    manager = isolated_manager(tmp_path)
    paths = [str(r.path) for r in manager.roots]
    assert not any(p.endswith("/.gaia/skills") and "gaia-home" not in p for p in paths)
    assert len(manager.roots) == 2  # user + one claude root when no agent dirs


def test_user_root_honors_gaia_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "custom"))
    assert user_skills_dir() == tmp_path / "custom" / "skills"


def test_discovers_skills_from_every_root(roots):
    copy_fixture("bare-standard", roots["user"])
    copy_fixture("web-search", roots["agent"])
    copy_fixture("incident-review", roots["claude"], as_name="incident-review")

    manager = make_manager(roots)
    found = {s.name: s.root for s in manager.list_skills()}
    assert found == {
        "bare-standard": ROOT_USER,
        "web-search": ROOT_AGENT_BUNDLED,
        "incident-review": ROOT_CLAUDE_IMPORT,
    }


def test_claude_import_is_read_only(roots):
    copy_fixture("incident-review", roots["claude"])
    skill = make_manager(roots).get("incident-review")
    assert skill.read_only is True
    assert skill.root == ROOT_CLAUDE_IMPORT
    # A bare Claude Code skill imports as instruction-only with safe defaults.
    assert skill.is_instruction_only
    assert skill.security_tier == "experimental"


def test_claude_skill_with_allowed_tools_is_not_granted_permissions(roots):
    copy_fixture("incident-review", roots["claude"])
    skill = make_manager(roots).load("incident-review")
    assert skill.extra_fields["allowed-tools"] == "Read, Write, Bash"
    assert skill.gaia.permissions == []


# ----------------------------------------------------------------------
# Precedence — acceptance criterion #7
# ----------------------------------------------------------------------


def test_lower_precedence_root_never_overrides(roots):
    """Same name in all three roots: the agent-bundled copy wins."""
    for root, description in (
        ("agent", "AGENT copy"),
        ("user", "USER copy"),
        ("claude", "CLAUDE copy"),
    ):
        write_skill_dir(
            roots[root],
            "shared",
            f"---\nname: shared\ndescription: {description}\n---\n\n# {description}\n",
        )

    manager = make_manager(roots)
    winner = manager.get("shared")
    assert winner.root == ROOT_AGENT_BUNDLED
    assert winner.description == "AGENT copy"

    shadowed = {s.root for s in manager.shadowed("shared")}
    assert shadowed == {ROOT_USER, ROOT_CLAUDE_IMPORT}
    assert manager.load("shared").body.strip() == "# AGENT copy"


def test_user_root_beats_claude_import(roots):
    for root, description in (("user", "USER copy"), ("claude", "CLAUDE copy")):
        write_skill_dir(
            roots[root],
            "shared",
            f"---\nname: shared\ndescription: {description}\n---\n\nbody\n",
        )
    manager = make_manager(roots)
    assert manager.get("shared").root == ROOT_USER
    assert [s.root for s in manager.shadowed("shared")] == [ROOT_CLAUDE_IMPORT]


def test_shadowed_skills_are_listed_once(roots):
    for root in ("agent", "user"):
        write_skill_dir(
            roots[root], "shared", "---\nname: shared\ndescription: d\n---\n\nbody\n"
        )
    manager = make_manager(roots)
    assert [s.name for s in manager.list_skills()] == ["shared"]


# ----------------------------------------------------------------------
# Errors + caching
# ----------------------------------------------------------------------


def test_missing_skill_raises_with_actionable_message(roots):
    copy_fixture("bare-standard", roots["user"])
    manager = make_manager(roots)
    with pytest.raises(SkillNotFoundError) as excinfo:
        manager.load("no-such-skill")
    message = str(excinfo.value)
    assert "gaia skill create no-such-skill" in message
    assert "bare-standard" in message  # lists what IS available


def test_invalid_skill_is_reported_not_silently_dropped(roots):
    copy_fixture("bare-standard", roots["user"])
    write_skill_dir(roots["user"], "broken", "not a skill file\n")

    manager = make_manager(roots)
    assert [s.name for s in manager.list_skills()] == ["bare-standard"]
    errors = manager.discovery_errors
    assert len(errors) == 1
    assert "broken" in next(iter(errors))


def test_reload_picks_up_a_new_skill(roots):
    manager = make_manager(roots)
    assert manager.list_skills() == []
    copy_fixture("bare-standard", roots["user"])
    assert manager.list_skills() == []  # cached
    assert [s.name for s in manager.reload().values()] == ["bare-standard"]


def test_resource_path_resolves_bundled_files(roots):
    skill_dir = copy_fixture("web-search", roots["user"])
    (skill_dir / "reference").mkdir()
    (skill_dir / "reference" / "syntax.md").write_text("# Syntax\n", encoding="utf-8")

    manager = make_manager(roots)
    assert manager.resource_path("web-search", "reference/syntax.md").is_file()


def test_resource_path_rejects_traversal(roots):
    copy_fixture("web-search", roots["user"])
    manager = make_manager(roots)
    with pytest.raises(SkillValidationError, match="escapes skill"):
        manager.resource_path("web-search", "../../etc/passwd")


def test_resource_path_rejects_missing_file(roots):
    copy_fixture("web-search", roots["user"])
    with pytest.raises(SkillValidationError, match="no resource"):
        make_manager(roots).resource_path("web-search", "nope.md")


# ----------------------------------------------------------------------
# Permissions — acceptance criterion #5
# ----------------------------------------------------------------------


def test_local_capability_permission_is_refused_with_an_actionable_error():
    permissions = [Permission.parse("filesystem:write", skill_name="x")]
    with pytest.raises(SkillPermissionError) as excinfo:
        refuse_unbridged_permissions(permissions, skill_name="x")
    message = str(excinfo.value)
    assert "deferred to a later phase" in message
    assert "filesystem" in message
    assert "1019" in message  # where to look next


@pytest.mark.parametrize(
    "permission",
    [
        "filesystem:read",
        "shell:execute",
        "database:write",
        "desktop:control",
        "env:read",
    ],
)
def test_every_local_capability_domain_is_refused(permission):
    parsed = [Permission.parse(permission, skill_name="x")]
    with pytest.raises(SkillPermissionError):
        refuse_unbridged_permissions(parsed, skill_name="x")


@pytest.mark.parametrize(
    "permission",
    ["network:read", "network:write:*.brave.com", "mcp:connect:mcp-tavily"],
)
def test_connector_bridged_permissions_are_allowed(permission):
    parsed = [Permission.parse(permission, skill_name="x")]
    refuse_unbridged_permissions(parsed, skill_name="x")  # must not raise


def test_network_permission_resolves_to_a_connector_requirement():
    parsed = [Permission.parse("network:read:*.brave.com", skill_name="web-search")]
    (requirement,) = connector_requirements(parsed, skill_name="web-search")
    assert requirement.connector_id == "network"
    assert requirement.scopes == ("read:*.brave.com",)
    assert "web-search" in requirement.reason


def test_mcp_permission_resolves_to_the_named_connector():
    parsed = [Permission.parse("mcp:connect:mcp-tavily", skill_name="research")]
    (requirement,) = connector_requirements(parsed, skill_name="research")
    assert requirement.connector_id == "mcp-tavily"


def test_unscoped_mcp_connect_fails_loudly():
    parsed = [Permission.parse("mcp:connect", skill_name="research")]
    with pytest.raises(SkillValidationError, match="without naming a connector"):
        connector_requirements(parsed, skill_name="research")


def test_mcp_connect_to_an_unknown_connector_fails_loudly():
    parsed = [Permission.parse("mcp:connect:not-a-connector", skill_name="research")]
    with pytest.raises(SkillValidationError, match="no connector with id"):
        connector_requirements(parsed, skill_name="research")


def test_none_level_produces_no_requirement():
    parsed = [Permission.parse("network:none", skill_name="x")]
    assert connector_requirements(parsed, skill_name="x") == []


@pytest.mark.parametrize("permission", ["filesystem:none", "shell:none", "env:none"])
def test_explicit_local_capability_denial_is_not_refused(permission):
    """``<domain>:none`` asks for *less* than the default — refusing it would
    reject a skill for being explicit about what it does not need."""
    parsed = [Permission.parse(permission, skill_name="x")]
    refuse_unbridged_permissions(parsed, skill_name="x")  # must not raise
    assert connector_requirements(parsed, skill_name="x") == []


def test_a_none_denial_alongside_a_real_grant_still_refuses():
    parsed = [
        Permission.parse("filesystem:none", skill_name="x"),
        Permission.parse("shell:execute", skill_name="x"),
    ]
    with pytest.raises(SkillPermissionError, match="shell:execute"):
        refuse_unbridged_permissions(parsed, skill_name="x")


def test_permission_round_trips_through_str():
    assert str(Permission.parse("network:read:*.brave.com", skill_name="x")) == (
        "network:read:*.brave.com"
    )


# ----------------------------------------------------------------------
# Tool registration — acceptance criteria #3 and #4
# ----------------------------------------------------------------------


def test_tools_register_under_the_skill_namespace(roots):
    copy_fixture("web-search", roots["user"])
    skill = make_manager(roots).load("web-search")

    registered = register_skill_tools(skill)
    assert set(registered) == {"web-search/search_web"}
    assert "web-search/search_web" in _TOOL_REGISTRY
    # The unqualified name must NOT leak into the global registry.
    assert "search_web" not in registered

    entry = _TOOL_REGISTRY["web-search/search_web"]
    assert entry["name"] == "web-search/search_web"
    assert entry["skill"] == "web-search"
    assert entry["parameters"]["query"]["required"] is True
    assert entry["function"](query="amd", max_results=2)["query"] == "amd"


def test_a_skill_tool_may_shadow_an_existing_registry_name(roots):
    """The ``<skill>/<tool>`` namespace exists so a skill can provide a tool the
    framework already has — loading must not clobber or drop either one."""
    original = {"name": "search_web", "description": "the framework's", "parameters": {}}
    _TOOL_REGISTRY["search_web"] = original

    copy_fixture("web-search", roots["user"])
    skill = make_manager(roots).load("web-search")
    registered = register_skill_tools(skill)

    assert set(registered) == {"web-search/search_web"}
    # The framework's tool survives untouched…
    assert _TOOL_REGISTRY["search_web"] is original
    # …and the skill's is reachable under its namespace.
    assert _TOOL_REGISTRY["web-search/search_web"]["skill"] == "web-search"


def test_unregister_removes_only_that_skills_tools(roots):
    copy_fixture("web-search", roots["user"])
    skill = make_manager(roots).load("web-search")
    register_skill_tools(skill)
    assert unregister_skill_tools("web-search") == ["web-search/search_web"]
    assert "web-search/search_web" not in _TOOL_REGISTRY


def test_instruction_only_skill_registers_no_tools(roots):
    copy_fixture("bare-standard", roots["user"])
    skill = make_manager(roots).load("bare-standard")
    assert register_skill_tools(skill) == {}


def test_tools_mismatch_fails_loudly_with_no_partial_load(roots):
    """The headline acceptance criterion: a declared tool tools.py never
    registers rejects the whole skill and leaves the registry untouched."""
    copy_fixture("tool-mismatch", roots["user"])
    skill = make_manager(roots).load("tool-mismatch")
    before = dict(_TOOL_REGISTRY)

    with pytest.raises(SkillValidationError) as excinfo:
        register_skill_tools(skill)

    message = str(excinfo.value)
    assert "missing_tool" in message
    assert "Nothing was loaded" in message
    # No partial load: neither the missing tool nor its valid sibling landed.
    assert _TOOL_REGISTRY == before
    assert "tool-mismatch/present_tool" not in _TOOL_REGISTRY
    assert "present_tool" not in _TOOL_REGISTRY


def test_undeclared_tool_in_module_fails_loudly(roots):
    write_skill_dir(
        roots["user"],
        "extra-tool",
        "---\nname: extra-tool\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - name: declared\n        parameters:\n          text: {type: string, required: true}\n"
        "---\n\nbody\n",
        tools=(
            "from gaia.agents.base.tools import tool\n\n\n"
            "@tool\ndef declared(text: str) -> dict:\n"
            '    """Declared."""\n    return {}\n\n\n'
            "@tool\ndef sneaky(text: str) -> dict:\n"
            '    """Undeclared."""\n    return {}\n'
        ),
    )
    skill = make_manager(roots).load("extra-tool")
    before = dict(_TOOL_REGISTRY)
    with pytest.raises(SkillValidationError, match="does not declare"):
        register_skill_tools(skill)
    assert _TOOL_REGISTRY == before


def test_signature_parameter_mismatch_fails_loudly(roots):
    write_skill_dir(
        roots["user"],
        "bad-params",
        "---\nname: bad-params\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - name: echo\n        parameters:\n          message: {type: string, required: true}\n"
        "---\n\nbody\n",
        tools=(
            "from gaia.agents.base.tools import tool\n\n\n"
            "@tool\ndef echo(text: str) -> dict:\n"
            '    """Echo."""\n    return {}\n'
        ),
    )
    skill = make_manager(roots).load("bad-params")
    with pytest.raises(SkillValidationError, match="parameters do not match"):
        register_skill_tools(skill)


def test_signature_requiredness_mismatch_fails_loudly(roots):
    write_skill_dir(
        roots["user"],
        "bad-required",
        "---\nname: bad-required\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - name: echo\n        parameters:\n          text: {type: string, required: false}\n"
        "---\n\nbody\n",
        tools=(
            "from gaia.agents.base.tools import tool\n\n\n"
            "@tool\ndef echo(text: str) -> dict:\n"
            '    """Echo."""\n    return {}\n'
        ),
    )
    skill = make_manager(roots).load("bad-required")
    with pytest.raises(SkillValidationError, match="declared optional"):
        register_skill_tools(skill)


def test_signature_type_mismatch_fails_loudly(roots):
    write_skill_dir(
        roots["user"],
        "bad-type",
        "---\nname: bad-type\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - name: echo\n        parameters:\n          text: {type: integer, required: true}\n"
        "---\n\nbody\n",
        tools=(
            "from gaia.agents.base.tools import tool\n\n\n"
            "@tool\ndef echo(text: str) -> dict:\n"
            '    """Echo."""\n    return {}\n'
        ),
    )
    skill = make_manager(roots).load("bad-type")
    with pytest.raises(SkillValidationError, match="annotates it as 'string'"):
        register_skill_tools(skill)


def test_declared_tools_without_a_tools_module_fails_loudly(roots):
    write_skill_dir(
        roots["user"],
        "no-module",
        "---\nname: no-module\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - name: echo\n        parameters: {}\n---\n\nbody\n",
    )
    skill = make_manager(roots).load("no-module")
    with pytest.raises(SkillValidationError, match="has no tools.py"):
        register_skill_tools(skill)


def test_a_raising_tools_module_fails_loudly_without_partial_load(roots):
    write_skill_dir(
        roots["user"],
        "boom",
        "---\nname: boom\ndescription: d\nmetadata:\n  gaia:\n    tools:\n"
        "      - name: echo\n        parameters: {}\n---\n\nbody\n",
        tools=(
            "from gaia.agents.base.tools import tool\n\n\n"
            "@tool\ndef echo() -> dict:\n"
            '    """Echo."""\n    return {}\n\n\n'
            "raise RuntimeError('exploding module')\n"
        ),
    )
    skill = make_manager(roots).load("boom")
    before = dict(_TOOL_REGISTRY)
    with pytest.raises(SkillValidationError, match="exploding module"):
        register_skill_tools(skill)
    assert _TOOL_REGISTRY == before


# ----------------------------------------------------------------------
# Agent.load_skill
# ----------------------------------------------------------------------


class _StubAgent:
    """Exercises ``Agent.load_skill`` without booting the LLM stack.

    Binds the real unbound methods so the test covers the shipped code, not a
    reimplementation of it.
    """

    from gaia.agents.base.agent import Agent

    REQUIRED_CONNECTORS: list = []
    SKILL_DIRS: list = []
    _instance_tools = None
    _skill_manager = None
    _loaded_skills = None

    skill_manager = Agent.skill_manager
    loaded_skills = Agent.loaded_skills
    _tools_registry = Agent._tools_registry
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill
    get_skills_system_prompt = Agent.get_skills_system_prompt

    def __init__(self):
        self.rebuilt = 0

    def rebuild_system_prompt(self):
        self.rebuilt += 1


def test_agent_load_skill_registers_namespaced_tools_and_injects_body(roots):
    copy_fixture("web-search", roots["user"])
    agent = _StubAgent()

    skill = agent.load_skill("web-search", manager=make_manager(roots))

    assert skill.name == "web-search"
    assert "web-search/search_web" in _TOOL_REGISTRY
    assert agent.loaded_skills["web-search"] is skill
    assert agent.rebuilt == 1

    prompt = agent.get_skills_system_prompt()
    assert "==== LOADED SKILLS ====" in prompt
    assert "--- SKILL: web-search ---" in prompt
    assert "A self-contained Brave Search wrapper" in prompt


def test_agent_load_skill_bridges_permissions_to_connector_requirements(roots):
    copy_fixture("web-search", roots["user"])
    agent = _StubAgent()
    agent.load_skill("web-search", manager=make_manager(roots))

    ids = [r.connector_id for r in agent.REQUIRED_CONNECTORS]
    assert ids == ["network"]
    # The ClassVar must not be mutated — that would leak into sibling agents.
    assert _StubAgent.REQUIRED_CONNECTORS == []


def test_agent_load_skill_refuses_local_capability_permissions(roots):
    copy_fixture("local-capability", roots["user"])
    agent = _StubAgent()

    with pytest.raises(SkillPermissionError, match="deferred to a later phase"):
        agent.load_skill("local-capability", manager=make_manager(roots))

    assert agent.loaded_skills == {}
    assert agent.get_skills_system_prompt() == ""
    assert agent.rebuilt == 0


def test_agent_load_skill_logs_unavailable_tools_required(roots, caplog):
    """A recipe skill whose registry tools are inactive here loads, but says so —
    scoping, not a defect (the tool universe is assembled dynamically)."""
    import logging

    copy_fixture("triage-support-ticket", roots["user"])
    agent = _StubAgent()
    agent._instance_tools = {}

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        agent.load_skill("triage-support-ticket", manager=make_manager(roots))

    assert "triage-support-ticket" in agent.loaded_skills
    assert "query_documents" in caplog.text


def test_agent_load_skill_instruction_only(roots):
    copy_fixture("bare-standard", roots["user"])
    agent = _StubAgent()
    skill = agent.load_skill("bare-standard", manager=make_manager(roots))
    assert skill.is_instruction_only
    assert "Establish the timeline" in agent.get_skills_system_prompt()


def test_agent_load_skill_is_idempotent(roots):
    copy_fixture("bare-standard", roots["user"])
    agent = _StubAgent()
    manager = make_manager(roots)
    first = agent.load_skill("bare-standard", manager=manager)
    second = agent.load_skill("bare-standard", manager=manager)
    assert first is second
    assert agent.rebuilt == 1


def test_agent_load_skill_leaves_nothing_behind_on_tool_mismatch(roots):
    copy_fixture("tool-mismatch", roots["user"])
    agent = _StubAgent()
    before = dict(_TOOL_REGISTRY)

    with pytest.raises(SkillValidationError):
        agent.load_skill("tool-mismatch", manager=make_manager(roots))

    assert agent.loaded_skills == {}
    assert _TOOL_REGISTRY == before


def test_agent_unload_skill_removes_tools_and_body(roots):
    copy_fixture("web-search", roots["user"])
    agent = _StubAgent()
    agent.load_skill("web-search", manager=make_manager(roots))

    assert agent.unload_skill("web-search") is True
    assert "web-search/search_web" not in _TOOL_REGISTRY
    assert agent.get_skills_system_prompt() == ""
    assert agent.unload_skill("web-search") is False


def test_agent_load_skill_updates_an_instance_tool_snapshot(roots):
    copy_fixture("web-search", roots["user"])
    agent = _StubAgent()
    agent._instance_tools = {}
    agent.load_skill("web-search", manager=make_manager(roots))
    assert "web-search/search_web" in agent._instance_tools


def test_agent_skill_manager_uses_bundled_dirs(tmp_path):
    class Bundled(_StubAgent):
        SKILL_DIRS = [str(tmp_path / "bundled")]

    manager = Bundled().skill_manager
    assert manager.roots[0].label == ROOT_AGENT_BUNDLED
    assert manager.roots[0].path == tmp_path / "bundled"


def test_agent_without_skills_adds_nothing_to_the_prompt():
    """Existing agents' prompts stay byte-identical until a skill is loaded."""
    assert _StubAgent().get_skills_system_prompt() == ""


# ----------------------------------------------------------------------
# Hot reload
# ----------------------------------------------------------------------


def test_hot_reload_invalidates_the_cache_on_change(roots):
    manager = make_manager(roots)
    assert manager.list_skills() == []

    watched = manager.start_watching()
    try:
        assert watched == 3
        assert manager.is_watching
        copy_fixture("bare-standard", roots["user"])
        _wait_for(lambda: [s.name for s in manager.list_skills()] == ["bare-standard"])
    finally:
        manager.stop_watching()
    assert not manager.is_watching


def _wait_for(predicate, timeout: float = 10.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("Condition not met before the timeout")
