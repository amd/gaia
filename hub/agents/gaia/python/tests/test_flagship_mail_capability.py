# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mail is the flagship's own capability: its tools need no other agent installed."""

from __future__ import annotations

import contextlib
import json

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.registry import KNOWN_TOOLS
from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME
from gaia.skills.manager import SkillManager

MAILBOX_READ_TOOLS = {
    "check_mailbox_access",
    "list_connected_mailboxes",
    "list_inbox",
    "search_email",
    "read_email",
    "get_thread",
    "list_mail_folders",
    "list_waiting_on_you",
}

SEND_EMAIL_TOOLS = {
    "list_drafts",
    "create_draft",
    "update_draft",
    "discard_draft",
    "send_draft",
    "send_now",
    "reply_to_message",
    "forward_message",
}


@contextlib.contextmanager
def _full_tier_flagship():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GAIA_MEMORY_DISABLED", "1")
            mp.setenv("GAIA_DYNAMIC_TOOLS", "0")
            mp.delenv("GAIA_FAST", raising=False)
            mp.delenv("GAIA_FEATURE_TIER", raising=False)
            yield GaiaAgent(
                config=GaiaAgentConfig(
                    silent_mode=True, model_id=LARGE_DEFAULT_MODEL_NAME
                )
            )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_the_email_mixin_is_still_composable_by_name():
    assert KNOWN_TOOLS["email"] == (
        "gaia.agents.tools.email_tools",
        "EmailToolsMixin",
    )


def test_a_full_tier_flagship_registers_its_mailbox_reading_tools():
    with _full_tier_flagship() as agent:
        missing = MAILBOX_READ_TOOLS - set(agent._tools_registry)
        assert not missing, sorted(missing)


def test_a_full_tier_flagship_gets_the_send_email_tools_when_the_skill_loads(
    tmp_path,
):
    with _full_tier_flagship() as agent:
        assert agent.BUNDLES_SEND_EMAIL_SKILL
        manager = SkillManager(
            agent_skill_dirs=agent.SKILL_DIRS,
            user_skills_root=tmp_path / "user-skills",
            include_claude_roots=False,
        )
        agent.load_skill("send-email", manager=manager)
        try:
            loaded = {
                name.split("/", 1)[1]
                for name in agent._tools_registry
                if name.startswith("send-email/")
            }
            assert loaded == SEND_EMAIL_TOOLS
        finally:
            assert agent.unload_skill("send-email")


def test_a_mailbox_that_is_not_connected_is_reported_per_provider(monkeypatch):
    """With nothing connected, the tool names each provider and how to connect it."""
    import gaia.connectors.api as connectors_api

    monkeypatch.setattr(connectors_api, "get_connection", lambda provider: None)
    with _full_tier_flagship() as agent:
        result = agent._tools_registry["check_mailbox_access"]["function"]()
    payload = json.loads(result)
    assert payload["success"] is False
    assert "google: not connected" in payload["error"]
    assert "microsoft: not connected" in payload["error"]
