# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dedicated coverage for the conversation-facing skill library tools."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.skill_library_tools import (
    SKILL_LIBRARY_TOOL_NAMES,
    SkillLibraryToolsMixin,
)
from gaia.skills.errors import SkillValidationError
from gaia.skills.publish import publish_skill
from gaia.skills.signing import TrustStore

from .skills_helpers import (
    FakeHub,
    fake_hub,
    isolated_manager,
    make_key,
    write_audit_report,
    write_skill_dir,
)


def _instruction_skill(
    name: str,
    *,
    tier: str = "experimental",
    permissions: tuple[str, ...] = (),
) -> str:
    permission_lines = "".join(f"      - {permission}\n" for permission in permissions)
    permissions_yaml = f"    permissions:\n{permission_lines}" if permissions else ""
    return f"""---
name: {name}
description: Instructions for testing the {name} skill library entry.
version: 1.0.0
metadata:
  gaia:
    security_tier: {tier}
{permissions_yaml}---

# {name}

ZZ-{name.upper()}-BODY-ZZ
"""


def _code_skill(name: str, *, tier: str = "community") -> tuple[str, str]:
    markdown = f"""---
name: {name}
description: A code-bearing skill used to exercise the runtime audit gate.
version: 1.0.0
metadata:
  gaia:
    security_tier: {tier}
    tools:
      - name: ping
        description: Return a deterministic pong response.
        parameters: {{}}
---

# {name}

ZZ-{name.upper()}-BODY-ZZ
"""
    tools = """from gaia.agents.base.tools import tool


@tool
def ping() -> str:
    return "pong"
"""
    return markdown, tools


class _LibraryAgent(SkillLibraryToolsMixin):
    """Exercise real Agent skill methods without constructing an LLM client."""

    REQUIRED_CONNECTORS: list = []
    _active_skill_filter = None
    _instance_tools = None
    _loaded_skills: dict[str, Any] | None = None
    _granted_binaries = None

    skill_manager = Agent.skill_manager
    loaded_skills = Agent.loaded_skills
    granted_binaries = Agent.granted_binaries
    _tools_registry = Agent._tools_registry
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill
    get_skills_system_prompt = Agent.get_skills_system_prompt
    _note_skill_active = Agent._note_skill_active

    def __init__(self, manager) -> None:
        self._skill_manager = manager
        self._loaded_skills = {}
        self._instance_tools = None
        self._granted_binaries = None
        self.rebuilt = 0
        self.register_skill_library_tools()
        self._instance_tools = {
            name: _TOOL_REGISTRY[name] for name in SKILL_LIBRARY_TOOL_NAMES
        }

    @property
    def _always_on_skill_names(self) -> frozenset[str]:
        return frozenset()

    @property
    def active_skill_set(self) -> None:
        return None

    def rebuild_system_prompt(self) -> None:
        self.rebuilt += 1


@pytest.fixture(autouse=True)
def isolate_tool_and_hub_registries() -> Iterator[dict[str, Any]]:
    """Keep dynamic tools and catalog memory from leaking between tests."""
    from gaia.hub.catalog import clear_cache

    before = dict(_TOOL_REGISTRY)
    clear_cache()
    yield before
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(before)
    clear_cache()


@pytest.fixture
def agent(tmp_path: Path) -> _LibraryAgent:
    manager = isolated_manager(
        tmp_path,
        user_skills_root=tmp_path / "gaia-home" / "skills",
        claude_skill_dirs=[tmp_path / "claude" / "skills"],
    )
    return _LibraryAgent(manager)


def _call(agent: _LibraryAgent, name: str, *args, **kwargs):
    return agent._tools_registry[name]["function"](*args, **kwargs)


def _assert_error(result: dict, action: str) -> None:
    assert result["status"] == "error"
    assert result["action"] == action
    assert result["error"]


def _publish_installable_skill(
    tmp_path: Path,
    manager,
    *,
    name: str = "hub-skill",
    tier: str = "community",
    permissions: tuple[str, ...] = (),
    unsigned: bool = False,
) -> FakeHub:
    hub = fake_hub(tmp_path)
    if not unsigned:
        key = make_key(manager.user_root)
        trust = TrustStore.load(manager.user_root)
        trust.add(
            public_key_b64=base64.b64encode(key.public_bytes).decode("ascii"),
            publisher="acme",
            role="publisher",
        )
        trust.save()

    source = write_skill_dir(
        tmp_path / "sources",
        name,
        _instruction_skill(name, tier=tier, permissions=permissions),
    )
    publish_skill(
        source,
        token="test-token",
        hub_url=hub.BASE_URL,
        publisher="acme",
        unsigned=unsigned,
        audit_report=write_audit_report(tmp_path / "audit"),
        keys_root=manager.user_root,
        uploader=hub.accept_publish,
    )
    return hub


def _record_hub_requests(monkeypatch, hub: FakeHub) -> list[str]:
    requested: list[str] = []

    def fetch(url: str) -> bytes:
        requested.append(url)
        return hub.fetcher(url)

    monkeypatch.setenv("GAIA_HUB_URL", hub.BASE_URL)
    monkeypatch.setattr("gaia.skills.hub.fetch_bytes", fetch)
    return requested


def test_registers_exactly_the_seven_skill_library_tools(
    agent: _LibraryAgent,
    isolate_tool_and_hub_registries: dict[str, Any],
) -> None:
    added = tuple(
        name
        for name in agent._tools_registry
        if name not in isolate_tool_and_hub_registries
    )
    assert added == SKILL_LIBRARY_TOOL_NAMES


def test_list_skills_reports_installed_and_loaded_state(
    agent: _LibraryAgent,
) -> None:
    write_skill_dir(
        agent.skill_manager.user_root,
        "notes",
        _instruction_skill("notes"),
    )
    assert _call(agent, "load_skill", "notes")["status"] == "success"

    result = _call(agent, "list_skills")

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["loaded"] == ["notes"]
    assert result["skills"][0]["loaded"] is True
    assert result["skills"][0]["name"] == "notes"
    assert str(agent.skill_manager.user_root) in result["roots_searched"]


def test_list_skills_returns_the_skill_error_shape(
    agent: _LibraryAgent, monkeypatch
) -> None:
    def fail_reload():
        raise SkillValidationError("broken skill metadata; fix SKILL.md")

    monkeypatch.setattr(agent.skill_manager, "reload", fail_reload)

    result = _call(agent, "list_skills")

    _assert_error(result, "list_skills")
    assert result["error_type"] == "SkillValidationError"


def test_search_skill_hub_fetches_the_valid_index_request(
    agent: _LibraryAgent, tmp_path: Path, monkeypatch
) -> None:
    from gaia.hub import catalog

    hub = fake_hub(tmp_path)
    hub.put_manifest(
        "hub-skill",
        "1.2.0",
        filename="hub-skill-1.2.0.zip",
        sha256="0" * 64,
        description="A published skill for request-shape testing.",
        security_tier="community",
    )
    hub.rebuild_index()
    requested: list[str] = []

    def fetch(url: str) -> bytes:
        requested.append(url)
        return hub.fetcher(url)

    monkeypatch.setenv("GAIA_HUB_URL", hub.BASE_URL)
    monkeypatch.setattr(catalog, "fetch_bytes", fetch)
    monkeypatch.setattr(
        catalog, "default_cache_path", lambda: tmp_path / "catalog-cache.json"
    )

    result = _call(agent, "search_skill_hub", "published")

    assert result == {
        "status": "success",
        "query": "published",
        "count": 1,
        "results": [
            {
                "name": "hub-skill",
                "title": "hub-skill",
                "description": "A published skill for request-shape testing.",
                "version": "1.2.0",
                "security_tier": "community",
                "installed": False,
            }
        ],
    }
    assert requested == [f"{hub.BASE_URL}/index.json"]
    parsed = urlsplit(requested[0])
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "fake-hub.test",
        "/index.json",
    )
    assert not parsed.query and not parsed.fragment


def test_search_skill_hub_returns_the_transport_error_shape(
    agent: _LibraryAgent, tmp_path: Path, monkeypatch
) -> None:
    from gaia.hub import catalog

    def offline(_url: str) -> bytes:
        raise OSError("hub offline")

    monkeypatch.setenv("GAIA_HUB_URL", "https://unreachable-hub.test")
    monkeypatch.setattr(catalog, "fetch_bytes", offline)
    monkeypatch.setattr(
        catalog, "default_cache_path", lambda: tmp_path / "missing-cache.json"
    )

    result = _call(agent, "search_skill_hub", "anything")

    _assert_error(result, "search_skill_hub")
    assert result["error_type"] == "SkillHubError"


def test_install_skill_uses_the_expected_hub_objects(
    agent: _LibraryAgent, tmp_path: Path, monkeypatch
) -> None:
    hub = _publish_installable_skill(tmp_path, agent.skill_manager)
    requested = _record_hub_requests(monkeypatch, hub)

    result = _call(agent, "install_skill", "hub-skill", "1.0.0")

    assert result["status"] == "success"
    assert result["name"] == "hub-skill"
    assert result["version"] == "1.0.0"
    assert result["security_tier"] == "community"
    assert agent.skill_manager.load("hub-skill").name == "hub-skill"
    assert requested == [
        f"{hub.BASE_URL}/skills/hub-skill/manifest.json",
        f"{hub.BASE_URL}/skills/hub-skill/1.0.0/hub-skill-1.0.0.zip",
        f"{hub.BASE_URL}/skills/hub-skill/1.0.0/SKILL.md",
    ]
    assert all(
        urlsplit(url).scheme == "https"
        and urlsplit(url).netloc == "fake-hub.test"
        and not urlsplit(url).query
        and not urlsplit(url).fragment
        for url in requested
    )


def test_install_skill_refuses_an_unsigned_skill(
    agent: _LibraryAgent, tmp_path: Path, monkeypatch
) -> None:
    hub = _publish_installable_skill(
        tmp_path,
        agent.skill_manager,
        name="unsigned-skill",
        tier="experimental",
        unsigned=True,
    )
    _record_hub_requests(monkeypatch, hub)

    result = _call(agent, "install_skill", "unsigned-skill")

    _assert_error(result, "install_skill")
    assert result["error_type"] == "SkillInstallError"
    assert "--allow-experimental" in result["error"]
    assert not (agent.skill_manager.user_root / "unsigned-skill").exists()


def test_install_skill_refuses_a_dangerous_grant_without_a_human(
    agent: _LibraryAgent, tmp_path: Path, monkeypatch
) -> None:
    hub = _publish_installable_skill(
        tmp_path,
        agent.skill_manager,
        name="dangerous-skill",
        permissions=("network:write:*.example.com",),
    )
    _record_hub_requests(monkeypatch, hub)

    result = _call(agent, "install_skill", "dangerous-skill")

    _assert_error(result, "install_skill")
    assert result["error_type"] == "SkillInstallError"
    assert "network:write" in result["error"]
    assert not (agent.skill_manager.user_root / "dangerous-skill").exists()


@pytest.mark.parametrize("tool_name", ["install_skill", "remove_skill", "load_skill"])
def test_disk_touching_tools_reject_non_bare_names(
    agent: _LibraryAgent, tool_name: str
) -> None:
    result = _call(agent, tool_name, "../outside")

    _assert_error(result, tool_name)
    assert "no slashes" in result["error"]


def test_remove_skill_deletes_a_hub_install_and_unloads_it(
    agent: _LibraryAgent, tmp_path: Path, monkeypatch
) -> None:
    hub = _publish_installable_skill(tmp_path, agent.skill_manager)
    _record_hub_requests(monkeypatch, hub)
    assert _call(agent, "install_skill", "hub-skill")["status"] == "success"
    assert _call(agent, "load_skill", "hub-skill")["status"] == "success"

    result = _call(agent, "remove_skill", "hub-skill")

    assert result["status"] == "success"
    assert result["unloaded_from_session"] is True
    assert "hub-skill" not in agent.loaded_skills
    assert not (agent.skill_manager.user_root / "hub-skill").exists()


def test_remove_skill_returns_the_missing_skill_error_shape(
    agent: _LibraryAgent,
) -> None:
    result = _call(agent, "remove_skill", "not-installed")

    _assert_error(result, "remove_skill")
    assert result["error_type"] == "SkillNotFoundError"


def test_load_skill_allows_code_from_the_gated_user_root(
    agent: _LibraryAgent,
) -> None:
    markdown, tools = _code_skill("cleared-code")
    directory = write_skill_dir(
        agent.skill_manager.user_root,
        "cleared-code",
        markdown,
        tools,
    )

    result = _call(agent, "load_skill", "cleared-code")

    assert result["status"] == "success"
    assert result["directory"] == str(directory)
    assert result["registered_tools"] == ["cleared-code/ping"]
    assert agent.rebuilt == 1
    assert "cleared-code/ping" in agent._tools_registry
    assert "ZZ-CLEARED-CODE-BODY-ZZ" in agent.get_skills_system_prompt()


def test_load_skill_refuses_ungated_code_with_an_actionable_error(
    agent: _LibraryAgent, tmp_path: Path
) -> None:
    markdown, tools = _code_skill("ungated-code")
    write_skill_dir(
        tmp_path / "claude" / "skills",
        "ungated-code",
        markdown,
        tools,
    )

    result = _call(agent, "load_skill", "ungated-code")

    _assert_error(result, "load_skill")
    assert result["error_type"] == "SkillPermissionError"
    assert "gaia skill import" in result["error"]
    assert "signature-, tier-, or audit-checked" in result["error"]
    assert "ungated-code" not in agent.loaded_skills
    assert "ungated-code/ping" not in agent._tools_registry


def test_load_skill_does_not_trust_a_tier_claim_from_an_ungated_root(
    agent: _LibraryAgent, tmp_path: Path
) -> None:
    markdown, tools = _code_skill("claimed-verified", tier="verified")
    write_skill_dir(
        tmp_path / "claude" / "skills",
        "claimed-verified",
        markdown,
        tools,
    )

    result = _call(agent, "load_skill", "claimed-verified")

    _assert_error(result, "load_skill")
    assert result["error_type"] == "SkillPermissionError"
    assert "claimed-verified" not in agent.loaded_skills
    assert "claimed-verified/ping" not in agent._tools_registry


def test_load_skill_allows_instruction_only_imports(
    agent: _LibraryAgent, tmp_path: Path
) -> None:
    write_skill_dir(
        tmp_path / "claude" / "skills",
        "imported-notes",
        _instruction_skill("imported-notes"),
    )

    result = _call(agent, "load_skill", "imported-notes")

    assert result["status"] == "success"
    assert result["registered_tools"] == []
    assert "imported-notes" in agent.loaded_skills


def test_load_skill_returns_the_missing_skill_error_shape(
    agent: _LibraryAgent,
) -> None:
    result = _call(agent, "load_skill", "not-installed")

    _assert_error(result, "load_skill")
    assert result["error_type"] == "SkillNotFoundError"


def test_unload_skill_restores_prompt_and_tool_state(
    agent: _LibraryAgent,
) -> None:
    markdown, tools = _code_skill("temporary-code")
    write_skill_dir(
        agent.skill_manager.user_root,
        "temporary-code",
        markdown,
        tools,
    )
    assert _call(agent, "load_skill", "temporary-code")["status"] == "success"
    assert agent.rebuilt == 1
    assert "temporary-code/ping" in agent._tools_registry
    assert "ZZ-TEMPORARY-CODE-BODY-ZZ" in agent.get_skills_system_prompt()

    result = _call(agent, "unload_skill", "temporary-code")

    assert result["status"] == "success"
    assert result["loaded_skills"] == []
    assert agent.rebuilt == 2
    assert "temporary-code/ping" not in agent._tools_registry
    assert "ZZ-TEMPORARY-CODE-BODY-ZZ" not in agent.get_skills_system_prompt()


def test_unload_skill_returns_the_not_loaded_error_shape(
    agent: _LibraryAgent,
) -> None:
    result = _call(agent, "unload_skill", "not-loaded")

    _assert_error(result, "unload_skill")
    assert "Currently loaded: (none)" in result["error"]


def test_skill_status_reports_loaded_and_available_skills(
    agent: _LibraryAgent,
) -> None:
    for name in ("loaded-skill", "available-skill"):
        write_skill_dir(
            agent.skill_manager.user_root,
            name,
            _instruction_skill(name),
        )
    assert _call(agent, "load_skill", "loaded-skill")["status"] == "success"

    result = _call(agent, "skill_status")

    assert result["status"] == "success"
    assert result["loaded_count"] == 1
    assert result["loaded"][0]["name"] == "loaded-skill"
    assert result["loaded"][0]["active_this_turn"] is True
    assert result["active_skill_set"] == ""
    assert result["installed_not_loaded"] == ["available-skill"]
