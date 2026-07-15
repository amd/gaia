# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent-type registry pre-check must not reject sidecar-backed agents.

The email agent ships as an out-of-process sidecar: a binary-only Hub install
has no importable wheel, so ``registry.get("email")`` is ``None`` even when the
agent is installed and healthy. The chat guard must exempt sidecar types (like
``chat``) or the email chat is unreachable on exactly the consumer machines the
Hub install targets — dev boxes mask this because they pip-install the wheel.
"""

from gaia.ui._chat_helpers import _SIDECAR_AGENT_TYPES, _agent_type_unknown


class _Registry:
    """Registry double: knows only the agent ids it is given."""

    def __init__(self, known=()):
        self._known = set(known)

    def get(self, agent_id):
        return object() if agent_id in self._known else None


def test_email_allowed_without_registry_entry():
    # The consumer-machine state: binary hub install, no importable wheel.
    assert _agent_type_unknown("email", _Registry(known=())) is False


def test_chat_always_allowed():
    assert _agent_type_unknown("chat", _Registry(known=())) is False


def test_unknown_type_still_rejected():
    assert _agent_type_unknown("nonexistent-agent", _Registry(known=())) is True


def test_registry_agent_allowed_when_known():
    assert _agent_type_unknown("jira", _Registry(known={"jira"})) is False


def test_no_registry_preserves_permissive_behavior():
    # Registry not initialized -> guard never fired historically; keep that.
    assert _agent_type_unknown("anything", None) is False


def test_email_is_declared_sidecar_type():
    # Pins the constant so a rename/refactor can't silently drop the exemption.
    assert "email" in _SIDECAR_AGENT_TYPES
