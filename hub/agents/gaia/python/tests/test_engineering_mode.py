# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Developer host gates and real permission UI behavior, without model inference."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
from gaia_agent.engineering_tools import (
    ENGINEERING_SKILL,
    ENGINEERING_TOOL_NAMES,
    EngineeringToolsMixin,
)
from gaia_agent.stdio import build_parser

from gaia.agents.base.console import OutputHandler
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.skills.manager import SkillManager

_REGISTERED_TEST_TOOLS = {}


@pytest.fixture
def registered():
    saved = dict(_TOOL_REGISTRY)
    service = Mock()
    console = Mock(spec=OutputHandler)
    console.auto_approve_confirmations_enabled.return_value = False
    console.call_is_granted.return_value = False
    console.confirm_tool_execution.return_value = True
    host = EngineeringToolsMixin()
    host.config = SimpleNamespace(developer_mode=True)
    host.console = console
    host._engineering = service
    _REGISTERED_TEST_TOOLS.update(host.register_engineering_tools())
    try:
        yield host, service, console
    finally:
        _REGISTERED_TEST_TOOLS.clear()
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def call(name, **kwargs):
    return _REGISTERED_TEST_TOOLS[name]["function"](**kwargs)


def test_environment_and_stdio_are_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("GAIA_DEVELOPER_MODE", raising=False)
    assert not GaiaAgentConfig().developer_mode
    assert not build_parser().parse_args(["--dev"]).developer_mode
    assert build_parser().parse_args(["--developer-mode"]).developer_mode
    monkeypatch.setenv("GAIA_DEVELOPER_MODE", "1")
    assert GaiaAgentConfig().developer_mode
    assert build_parser().parse_args([]).developer_mode
    assert not GaiaAgentConfig(developer_mode=False).developer_mode


def test_normal_mode_cannot_discover_or_load_with_custom_manager():
    agent = object.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(developer_mode=False)
    assert ENGINEERING_SKILL not in agent.skill_manager.discover()
    custom = SkillManager(agent_skill_dirs=GaiaAgent.SKILL_DIRS)
    assert ENGINEERING_SKILL in custom.discover()
    with pytest.raises(PermissionError, match="developer-mode"):
        agent.load_skill(ENGINEERING_SKILL, manager=custom)


def test_disabled_host_cannot_access_service(registered):
    host, service, _ = registered
    host.config.developer_mode = False
    with pytest.raises(PermissionError):
        call(
            "share_engineering_context",
            backend="codex",
            summary="failure",
            context="trace",
        )
    service.share.assert_not_called()


@pytest.mark.parametrize("decision", [True, False])
def test_snapshot_sharing_requires_exact_human_decision(registered, decision):
    _, service, console = registered
    console.confirm_tool_execution.return_value = decision
    result = call(
        "share_engineering_context",
        backend="claude",
        summary="bad answer",
        context="selected trace",
    )
    name, shown = console.confirm_tool_execution.call_args.args
    assert name == "share_engineering_context"
    assert shown["backend"] == "claude"
    assert shown["summary"] == "bad answer"
    assert shown["context"] == "selected trace"
    if decision:
        service.share.assert_called_once_with(
            backend="claude", summary="bad answer", context="selected trace"
        )
    else:
        assert result["status"] == "denied"
        service.share.assert_not_called()


@pytest.mark.parametrize("bypass, granted", [(True, False), (False, True)])
def test_unattended_or_prior_grant_never_shares(registered, bypass, granted):
    _, service, console = registered
    console.auto_approve_confirmations_enabled.return_value = bypass
    console.call_is_granted.return_value = granted
    assert (
        call(
            "share_engineering_context", backend="codex", summary="bug", context="trace"
        )["status"]
        == "denied"
    )
    service.share.assert_not_called()
    console.confirm_tool_execution.assert_not_called()


def test_engineering_tools_cannot_create_always_grants():
    from gaia.ui.sse_handler import SSEOutputHandler

    console = SSEOutputHandler()
    for name in ENGINEERING_TOOL_NAMES:
        assert console.grant_call_for_session(name, {"job_id": "same-job"}) is None
    assert console.session_grants() == set()


def test_feedback_and_code_scope_each_require_new_confirmation(registered):
    _, service, console = registered
    service.status.return_value = {"backend": "codex"}
    console.confirm_tool_execution.return_value = False
    assert (
        call("append_engineering_context", job_id="job", context="feedback")["status"]
        == "denied"
    )
    assert call("approve_engineering_code", job_id="job")["status"] == "denied"
    service.append.assert_not_called()
    service.approve_code.assert_not_called()
    assert console.confirm_tool_execution.call_count == 2
    call("revoke_engineering_context", job_id="job")
    service.revoke.assert_called_once_with("job")


def test_real_host_keeps_skill_resident_and_tools_out_of_normal_mode(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.delenv("GAIA_DEVELOPER_MODE", raising=False)
    monkeypatch.setattr(GaiaAgent, "_start_engineering_setup", lambda self: None)
    saved = dict(_TOOL_REGISTRY)
    try:
        developer = GaiaAgent(
            config=GaiaAgentConfig(silent_mode=True, developer_mode=True)
        )
        assert ENGINEERING_SKILL in developer.loaded_skills
        assert ENGINEERING_SKILL in developer._always_on_skill_names
        assert set(ENGINEERING_TOOL_NAMES) <= developer._tools_registry.keys()
        assert not set(ENGINEERING_TOOL_NAMES) & _TOOL_REGISTRY.keys()
        normal = GaiaAgent(
            config=GaiaAgentConfig(silent_mode=True, developer_mode=False)
        )
        assert ENGINEERING_SKILL not in normal.loaded_skills
        assert ENGINEERING_SKILL not in normal._always_on_skill_names
        assert not set(ENGINEERING_TOOL_NAMES) & normal._tools_registry.keys()
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_dynamic_tool_selection_keeps_handoff_tools_reachable(monkeypatch):
    from gaia_agent_chat.agent import ChatAgent

    agent = object.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(developer_mode=True)
    monkeypatch.setattr(
        ChatAgent, "_select_tools_for_turn", lambda self, query: ["search_web"]
    )
    assert set(ENGINEERING_TOOL_NAMES) <= set(agent._select_tools_for_turn("continue"))
    agent.config.developer_mode = False
    assert agent._select_tools_for_turn("continue") == ["search_web"]


def test_setup_failure_is_visible_without_breaking_daily_host(registered, monkeypatch):
    import threading

    host, service, _ = registered
    finished = threading.Event()

    def fail():
        finished.set()
        raise RuntimeError("cache is not writable")

    service.setup.side_effect = fail

    # Execute only the background body synchronously to inspect its error record.
    class InlineThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("gaia_agent.engineering_tools.threading.Thread", InlineThread)
    host._start_engineering_setup()
    assert finished.is_set()
    assert call("engineering_status")["error"] == "cache is not writable"


def test_code_scope_approval_binds_the_displayed_revision(registered):
    _, service, console = registered
    service.status.return_value = {
        "id": "job",
        "revision": 7,
        "diagnosis": "fix harness",
    }
    call("approve_engineering_code", job_id="job")
    shown = console.confirm_tool_execution.call_args.args[1]
    assert shown["job"]["revision"] == 7
    service.approve_code.assert_called_once_with("job", expected_revision=7)


def test_overlapping_chat_construction_never_sees_developer_tools(monkeypatch):
    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.setattr(GaiaAgent, "_start_engineering_setup", lambda self: None)
    original = ChatAgent._register_tools
    observed = []

    def overlap(self):
        if getattr(self.config, "developer_mode", False):
            # Deterministic scheduling point: developer tools have registered,
            # but the developer's own snapshot has not happened yet.
            ordinary = ChatAgent(config=ChatAgentConfig(silent_mode=True))
            observed.append(set(ordinary._tools_registry))
        return original(self)

    saved = dict(_TOOL_REGISTRY)
    try:
        monkeypatch.setattr(ChatAgent, "_register_tools", overlap)
        developer = GaiaAgent(
            config=GaiaAgentConfig(silent_mode=True, developer_mode=True)
        )
        assert observed
        assert not set(ENGINEERING_TOOL_NAMES) & observed[0]
        assert set(ENGINEERING_TOOL_NAMES) <= developer._tools_registry.keys()
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_explicit_tool_registry_never_touches_global():
    from gaia.agents.base.tools import tool

    local = {}
    global_before = dict(_TOOL_REGISTRY)

    @tool(atomic=True, registry=local)
    def isolated_developer_test_tool(value: str) -> str:
        """Echo the value in an isolated registry."""
        return value

    assert local["isolated_developer_test_tool"]["function"]("x") == "x"
    assert (
        local["isolated_developer_test_tool"]["parameters"]["value"]["type"] == "string"
    )
    assert _TOOL_REGISTRY == global_before
