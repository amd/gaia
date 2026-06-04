# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for Stage D of issue #1428: _agent_unavailable_message helper.

Validates that requesting an unknown agent_type surfaces a user-friendly
error message rather than silently falling back to chat.
"""

from unittest.mock import MagicMock

from gaia.ui._chat_helpers import _agent_unavailable_message


class TestAgentUnavailableMessage:
    """_agent_unavailable_message returns a helpful string, never empty."""

    def test_basic_message_mentions_agent_name(self):
        msg = _agent_unavailable_message("my-bot", None)
        assert "my-bot" in msg

    def test_message_does_not_claim_success(self):
        msg = _agent_unavailable_message("missing-agent", None)
        assert "created" not in msg.lower()
        assert "✅" not in msg

    def test_message_suggests_action(self):
        msg = _agent_unavailable_message("broken", None)
        # Must give the user something to do
        assert any(
            word in msg.lower()
            for word in ["try", "re-create", "recreate", "selector", "install"]
        )

    def test_includes_load_error_reason_when_available(self):
        registry = MagicMock()
        registry.get_load_error.return_value = "SyntaxError: invalid syntax"
        msg = _agent_unavailable_message("broken-bot", registry)
        assert "SyntaxError" in msg or "invalid syntax" in msg

    def test_no_reason_appended_when_no_load_error(self):
        registry = MagicMock()
        registry.get_load_error.return_value = None
        msg = _agent_unavailable_message("unknown-bot", registry)
        assert "SyntaxError" not in msg

    def test_handles_none_registry_gracefully(self):
        msg = _agent_unavailable_message("orphan", None)
        assert isinstance(msg, str)
        assert len(msg) > 10
