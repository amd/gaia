# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for Stage D of issue #1428: _agent_unavailable_message helper.

Validates that requesting an unknown agent_type surfaces a user-friendly
error message rather than silently falling back to chat.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.install_hints import CHAT_WHEEL_AGENT_IDS
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


class TestRetiredChatIdsGetTheInstallHint:
    """`chat`/`doc`/`file` are hidden, not deleted, so every historical
    session still carries one. On a box without the wheel, the generic
    message points at a selector they are no longer in — a dead end. Name
    the package and the command instead.
    """

    @staticmethod
    def _without_the_wheel():
        return patch(
            "gaia.ui._chat_helpers.importlib.util.find_spec", return_value=None
        )

    @pytest.mark.parametrize("agent_id", sorted(CHAT_WHEEL_AGENT_IDS))
    def test_names_the_package_and_the_install_command(self, agent_id):
        registry = MagicMock()
        registry.get_load_error.return_value = None
        with self._without_the_wheel():
            msg = _agent_unavailable_message(agent_id, registry)

        assert "gaia-agent-chat" in msg
        assert "pip install" in msg
        assert agent_id in msg

    @pytest.mark.parametrize("agent_id", sorted(CHAT_WHEEL_AGENT_IDS))
    def test_does_not_send_the_user_to_a_selector_it_is_absent_from(self, agent_id):
        with self._without_the_wheel():
            msg = _agent_unavailable_message(agent_id, None)
        assert "selector" not in msg.lower()

    def test_installed_wheel_falls_through_to_the_recorded_load_error(self):
        """The wheel being present means the id failed for some OTHER reason;
        that reason says more than "not installed"."""
        registry = MagicMock()
        registry.get_load_error.return_value = "SyntaxError: invalid syntax"
        with patch(
            "gaia.ui._chat_helpers.importlib.util.find_spec", return_value=MagicMock()
        ):
            msg = _agent_unavailable_message("chat", registry)

        assert "SyntaxError" in msg
        assert "pip install" not in msg

    def test_an_unrelated_agent_still_gets_the_generic_message(self):
        with self._without_the_wheel():
            msg = _agent_unavailable_message("some-third-party-agent", None)
        assert "gaia-agent-chat" not in msg
        assert "selector" in msg.lower()
