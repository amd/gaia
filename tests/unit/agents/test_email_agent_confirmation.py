# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests that destructive tools are correctly registered for confirmation
gating, and that draft/reply/forward tools record proper threading
metadata.

The actual SSE confirmation flow is exercised in
``tests/integration/test_email_router_confirmation_flow.py``; here we
verify the agent-side contracts at unit level.
"""

from __future__ import annotations

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102).
pytest.importorskip("gaia_agent_email")
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402


class TestConfirmationGatingAtBaseLevel:
    """The EmailTriageAgent's confirmation set (its own
    ``CONFIRMATION_REQUIRED_TOOLS`` merged with the generic base set via
    ``confirmation_required_tools()`` — #1440) must list every email tool
    that has external side effects.
    """

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
    def test_destructive_tool_is_gated(self, tool_name):
        assert tool_name in EmailTriageAgent.confirmation_required_tools()

    @pytest.mark.parametrize(
        "tool_name",
        [
            "list_inbox",
            "get_message",
            "get_thread",
            "search_messages",
            "list_labels",
            "triage_inbox",
            # Reversible-via-undo organize tools — NOT confirmation-gated.
            "archive_message",
            "mark_read",
            "mark_unread",
            "add_star",
            "remove_star",
            "label_message",
            "move_to_label",
            # Reversible-within-window soft-delete — NOT confirmation-gated.
            # The user can ``restore_message`` instead.
            "trash_message",
            # Drafting is harmless; only sending requires confirmation.
            "draft_reply",
            "draft_forward",
            # restore_message is the undo path — never gated.
            "restore_message",
        ],
    )
    def test_safe_tool_is_NOT_gated(self, tool_name):
        assert tool_name not in EmailTriageAgent.confirmation_required_tools()
