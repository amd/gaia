# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Construction + tool-registry tests for ``EmailTriageAgent``.

These tests exercise the agent class WITHOUT making any network calls or
LLM requests — they construct the agent against an injected
``FakeGmailBackend`` and assert the expected tool surface, the registry
declarations, and AC3 enforcement at the unit level.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make tests.fixtures.email importable.
# parents[0]=agents, [1]=unit, [2]=tests, [3]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.outlook_scopes import (  # noqa: E402
    OUTLOOK_CALENDAR_SCOPES,
    OUTLOOK_MAIL_SCOPES,
)
from gaia_agent_email.scopes import AGENT_NAMESPACED_ID, ALL_SCOPES  # noqa: E402

from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeCalendarBackend,
    FakeGmailBackend,
)


@pytest.fixture
def fake_gmail():
    fixture_path = _REPO_ROOT / "tests" / "fixtures" / "email" / "_stub_inbox.mbox"
    assert fixture_path.exists(), fixture_path
    return FakeGmailBackend(fixture_path)


@pytest.fixture
def fake_calendar():
    return FakeCalendarBackend()


@pytest.fixture
def agent(fake_gmail, fake_calendar, tmp_path):
    """Construct an ``EmailTriageAgent`` against fake backends.

    ``patch`` the LLM-side ``AgentSDK`` so we don't connect to Lemonade.
    """
    cfg = EmailAgentConfig(
        gmail_backend=fake_gmail,
        calendar_backend=fake_calendar,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
        debug=False,
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        a = EmailTriageAgent(config=cfg)
        # Expose the mock so tests can introspect.
        a._mock_chat = mock_sdk.return_value
    yield a
    a.close_db()


class TestConstruction:
    def test_can_construct_with_fake_backends(self, agent):
        assert agent is not None
        assert agent.AGENT_ID == "email"
        assert agent.AGENT_NAME == "Email Triage"

    def test_namespaced_id_constant(self):
        assert AGENT_NAMESPACED_ID == "installed:email"

    def test_required_connectors_well_formed(self):
        # Two providers are declared: Google (Gmail #962 + Calendar) and
        # Microsoft (Outlook.com mailbox #1275 + calendar #1276). They coexist —
        # the active mail/calendar backend is chosen by ``config.mail_provider``
        # / ``config.calendar_provider``.
        reqs = {c.connector_id: c for c in EmailTriageAgent.REQUIRED_CONNECTORS}
        assert set(reqs) == {"google", "microsoft"}

        google = reqs["google"]
        # Tuple form (frozen dataclass normalizes).
        assert google.scopes == ALL_SCOPES
        assert google.reason  # non-empty

        microsoft = reqs["microsoft"]
        # Mail (#1275) + calendar (#1276) scopes, mirroring how the Google
        # requirement bundles Gmail + Calendar in ALL_SCOPES.
        assert microsoft.scopes == OUTLOOK_MAIL_SCOPES + OUTLOOK_CALENDAR_SCOPES
        assert microsoft.reason  # non-empty

    def test_response_mode_is_conversational(self, agent):
        assert agent.response_mode == "conversational"

    def test_injected_single_fake_builds_backends_map(self, agent, fake_gmail):
        # Phase 2 (#1603 D2): the agent binds a provider→backend map. An
        # injected single fake (no provider) tags as "google" to preserve the
        # shipped Gmail fixtures, and ``self._gmail`` stays the primary backend.
        assert agent._backends == {"google": fake_gmail}
        assert agent._gmail is fake_gmail

    def test_primary_backend_is_first_in_map(self, agent):
        # self._gmail must be the first value in self._backends so existing
        # single-backend tool closures keep working unchanged.
        assert agent._gmail is next(iter(agent._backends.values()))

    def test_system_prompt_pre_scan_canary(self, agent):
        """Canary against silent prompt drift.

        ``pre_scan_inbox`` is the tool the LLM must call when the user
        asks for a triage view. The structured render-card contract is
        now handled by the backend SSE hook (``SSEOutputHandler``
        intercepts ``pre_scan_inbox`` results and injects the
        ``email_pre_scan`` fenced block deterministically — see
        sse_handler.py and issue #1000 for the planned multi-model fix
        that replaces the hook), so the prompt only needs to name the
        tool. Assert that name is present so a future prompt edit
        doesn't silently drop the routing instruction.
        """
        prompt = agent._get_system_prompt()
        assert "pre_scan_inbox" in prompt, (
            "system prompt must mention ``pre_scan_inbox`` so the LLM "
            "calls it on triage requests; the frontend card mount path "
            "depends on this tool firing"
        )


class TestToolRegistry:
    """The agent must register all tools from its tool mixins."""

    EXPECTED_TOOLS = {
        # Read
        "list_inbox",
        "get_message",
        "get_thread",
        "search_messages",
        "list_labels",
        "triage_inbox",
        "pre_scan_inbox",
        "profile_inbox",
        # Follow-up tracking (#1606) — read-only detection
        "check_followups",
        # Organize
        "archive_message",
        "mark_read",
        "mark_unread",
        "add_star",
        "remove_star",
        "label_message",
        "move_to_label",
        # Batch organize
        "mark_read_batch",
        "mark_unread_batch",
        "add_star_batch",
        "remove_star_batch",
        "archive_message_batch",
        "undo_archive_batch",
        "label_message_batch",
        "move_to_label_batch",
        # Reply / send / forward
        "draft_reply",
        "draft_forward",
        "send_draft",
        "send_now",
        "forward_message",
        # Scheduled send + snooze (#1609)
        "schedule_send",
        "snooze_message",
        "cancel_scheduled_job",
        "list_scheduled_jobs",
        # Delete
        "trash_message",
        "restore_message",
        "permanent_delete",
        # Phishing quarantine (#1271)
        "quarantine_phishing_message",
        "unquarantine_message",
        # Calendar
        "list_calendar_events",
        "accept_invite",
        "decline_invite",
        "create_event_from_email",
        "detect_meeting_request",
        "detect_calendar_conflicts",
        # Summarize (#1267, #1268)
        "summarize_message",
        "summarize_thread",
        # Session preferences (in-memory; wiped on agent restart)
        "set_priority_sender",
        "set_low_priority_sender",
        "set_category_default",
        "clear_session_preferences",
        # Inbox profiling from memory (#1289)
        "profile_inbox",
        # Voice/style profile from Sent mail (#1607)
        "build_voice_profile",
        "clear_voice_profile",
        # Briefing / task extraction agent-loop tools (#2110)
        "get_briefing",
        "list_tasks",
        "extract_action_items",
    }

    def test_every_expected_tool_is_registered(self, agent):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        registered = set(_TOOL_REGISTRY.keys())
        missing = self.EXPECTED_TOOLS - registered
        assert not missing, f"missing tools: {missing}"

    def test_no_unexpected_tool_set(self, agent):
        # Sanity — the tool set should be exactly the expected set
        # (allow for atomic subset variations later by checking subset
        # rather than equality).
        from gaia.agents.base.tools import _TOOL_REGISTRY

        registered = set(_TOOL_REGISTRY.keys())
        # Every registered tool should match an expected one — guard
        # against accidentally registering a tool that bypasses our
        # confirmation logic.
        unexpected = registered - self.EXPECTED_TOOLS
        assert not unexpected, f"unexpected tools registered: {unexpected}"


class TestConfirmationGating:
    """Destructive tools must be in ``TOOLS_REQUIRING_CONFIRMATION``."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "send_draft",
            "send_now",
            "schedule_send",
            "forward_message",
            "permanent_delete",
            "accept_invite",
            "decline_invite",
            "create_event_from_email",
        ],
    )
    def test_destructive_tool_is_confirmation_gated(self, tool_name):
        assert tool_name in EmailTriageAgent.confirmation_required_tools()


class TestAC3LocalLLMOnly:
    """Unit-level proof that we never construct against a cloud LLM."""

    def test_constructed_agent_has_no_cloud_llm_flags(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """We never pass ``use_claude=True`` / ``use_chatgpt=True`` to the
        parent ``Agent``. We can prove that by inspecting the kwargs the
        ``AgentSDK`` constructor was called with.
        """
        cfg = EmailAgentConfig(
            gmail_backend=fake_gmail,
            calendar_backend=fake_calendar,
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
        )
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            EmailTriageAgent(config=cfg)
            call_kwargs = mock_sdk.call_args.kwargs
            assert "use_claude" not in call_kwargs or call_kwargs["use_claude"] is False
            assert (
                "use_chatgpt" not in call_kwargs or call_kwargs["use_chatgpt"] is False
            )

    def test_remote_base_url_rejected_at_construction(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        from gaia_agent_email.config import ConfigurationError

        cfg = EmailAgentConfig(
            base_url="https://api.openai.com/v1",
            gmail_backend=fake_gmail,
            calendar_backend=fake_calendar,
            db_path=str(tmp_path / "state.db"),
        )
        with pytest.raises(ConfigurationError) as exc:
            EmailTriageAgent(config=cfg)
        assert "AC3" in str(exc.value)


class TestRegistryIntegration:
    def test_connectors_demo_required_connections_are_proper_objects(self):
        """#962 follow-up: the connectors_demo entry must use
        ``ConnectorRequirement`` objects, not bare strings.
        """
        from gaia.agents.registry import AgentRegistry
        from gaia.connectors.providers.base import ConnectorRequirement

        reg = AgentRegistry()
        reg._register_builtin_agents()
        demo = reg.get("connectors-demo")
        if demo is None:
            pytest.skip("connectors-demo not loaded")
        for r in demo.required_connections:
            assert isinstance(
                r, ConnectorRequirement
            ), f"connectors_demo declared bare-string requirement: {r!r}"
