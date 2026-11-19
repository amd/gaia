# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent-type registry pre-check decides which chats can be dispatched.

Every agent, sidecar or in-process, must resolve in the registry; one installed
after startup is found by a rescan instead of needing a server restart.
"""

from gaia.ui._chat_helpers import _SIDECAR_AGENT_TYPES, _agent_type_unknown


class _Registry:
    """Registry double: knows only the agent ids it is given."""

    def __init__(self, known=(), installed_later=()):
        self._known = set(known)
        self._installed_later = set(installed_later)
        self.refreshes = 0

    def get(self, agent_id):
        return object() if agent_id in self._known else None

    def refresh_installed(self):
        self.refreshes += 1
        self._known |= self._installed_later


def test_agent_installed_after_startup_is_found_without_restart():
    # `gaia init` from the setup wizard hub-installs the flagship after the
    # registry was built; the first chat must not need a server restart.
    registry = _Registry(known=(), installed_later={"gaia"})
    assert _agent_type_unknown("gaia", registry) is False
    assert registry.refreshes == 1


def test_known_agent_does_not_rescan():
    registry = _Registry(known={"gaia"})
    assert _agent_type_unknown("gaia", registry) is False
    assert registry.refreshes == 0


def test_chat_allowed_when_registered():
    assert _agent_type_unknown("chat", _Registry(known={"chat"})) is False


def test_chat_rejected_when_registry_lacks_it():
    # chat goes through the registry now, so a missing registration is a
    # loud unavailable-agent error instead of a hardcoded bypass.
    assert _agent_type_unknown("chat", _Registry(known=())) is True


def test_chat_is_not_a_sidecar_type():
    # The exemption is for out-of-process agents only; chat is in-process.
    assert "chat" not in _SIDECAR_AGENT_TYPES


def test_unknown_type_still_rejected():
    assert _agent_type_unknown("nonexistent-agent", _Registry(known=())) is True


def test_registry_agent_allowed_when_known():
    assert _agent_type_unknown("jira", _Registry(known={"jira"})) is False


def test_no_registry_preserves_permissive_behavior():
    # Registry not initialized -> guard never fired historically; keep that.
    assert _agent_type_unknown("anything", None) is False


def test_the_flagship_is_declared_sidecar_type():
    # Pins the constant so a rename/refactor can't silently drop the relay.
    assert "gaia" in _SIDECAR_AGENT_TYPES
