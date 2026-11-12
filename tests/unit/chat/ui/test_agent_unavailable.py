# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for Stage D of issue #1428: _agent_unavailable_message helper.

Validates that requesting an unknown agent_type surfaces a user-friendly
error message rather than silently falling back to chat.
"""

from unittest.mock import MagicMock

import pytest

from gaia.agents.install_hints import LEGACY_FLAGSHIP_AGENT_IDS
from gaia.llm.surface_hints import AGENT_UI_SETUP_HINT
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


class TestTheFlagshipPointsAtSetup:
    """The flagship has no selector to pick from and nothing to re-create, and
    ChatAgent's old ids (carried by every historical session) resolve to it.
    Its message must send the user to Settings > Setup, not to a command.
    """

    @pytest.mark.parametrize("agent_id", ["gaia", *sorted(LEGACY_FLAGSHIP_AGENT_IDS)])
    def test_a_missing_flagship_says_so_and_names_setup(self, agent_id):
        registry = MagicMock()
        registry.get_load_error.return_value = None
        msg = _agent_unavailable_message(agent_id, registry)

        assert msg == f"GAIA's assistant isn't installed. {AGENT_UI_SETUP_HINT}"

    @pytest.mark.parametrize("agent_id", ["gaia", *sorted(LEGACY_FLAGSHIP_AGENT_IDS)])
    def test_names_no_selector_and_no_command(self, agent_id):
        msg = _agent_unavailable_message(agent_id, None).lower()
        for dead_end in ("selector", "re-creat", "pip install", "`gaia "):
            assert dead_end not in msg

    def test_a_recorded_load_error_says_why(self):
        registry = MagicMock()
        registry.get_load_error.return_value = "SyntaxError: invalid syntax"
        msg = _agent_unavailable_message("gaia", registry)

        assert "GAIA's assistant failed to load: SyntaxError" in msg
        assert AGENT_UI_SETUP_HINT in msg

    def test_an_unrelated_agent_still_gets_the_generic_message(self):
        msg = _agent_unavailable_message("some-third-party-agent", None)
        assert "GAIA's assistant" not in msg
        assert "selector" in msg.lower()
