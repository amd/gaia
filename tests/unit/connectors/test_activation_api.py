# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the ``activate``/``deactivate`` orchestration in
``gaia.connectors.api`` — the one-click convenience layer that combines
grants + activations per issue #1005.
"""

from __future__ import annotations

import pytest

from gaia.connectors.activations import is_agent_active
from gaia.connectors.api import activate, deactivate
from gaia.connectors.errors import ConfigurationError
from gaia.connectors.grants import grant_agent, list_agent_grants


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.activations.Path.home", lambda: tmp_path)
    return tmp_path


class TestActivateWithExistingGrant:
    def test_activate_with_existing_grant_does_not_touch_grant(self, fake_home):
        grant_agent("github", "builtin:chat", ["repo:read"])
        auto_granted = activate("github", "builtin:chat")
        assert auto_granted is False
        # Grant is preserved as-is.
        assert list_agent_grants("github") == {"builtin:chat": ["repo:read"]}
        assert is_agent_active("github", "builtin:chat") is True

    def test_activate_ignores_scopes_for_grant_when_grant_exists(self, fake_home):
        # Existing grants are NOT overwritten — auto-grant only fires when
        # no grant is present at all.
        grant_agent("github", "builtin:chat", ["repo:read"])
        activate("github", "builtin:chat", scopes_for_grant=["repo:write"])
        assert list_agent_grants("github") == {"builtin:chat": ["repo:read"]}


class TestActivateAutoGrant:
    def test_activate_without_grant_auto_grants_required_scopes(self, fake_home):
        auto_granted = activate(
            "github", "builtin:chat", scopes_for_grant=["repo:read", "repo:write"]
        )
        assert auto_granted is True
        assert list_agent_grants("github") == {
            "builtin:chat": ["repo:read", "repo:write"]
        }
        assert is_agent_active("github", "builtin:chat") is True

    def test_activate_without_grant_or_scopes_raises_configuration_error(
        self, fake_home
    ):
        with pytest.raises(ConfigurationError) as exc:
            activate("github", "builtin:chat")
        msg = str(exc.value)
        assert "github" in msg
        assert "builtin:chat" in msg
        assert "scopes" in msg.lower()

    def test_auto_grant_scopes_are_a_copy_not_a_reference(self, fake_home):
        # Defensive: callers must not be able to mutate the stored grant
        # through the list they passed in.
        scopes = ["repo:read"]
        activate("github", "builtin:chat", scopes_for_grant=scopes)
        scopes.append("repo:write")
        assert list_agent_grants("github") == {"builtin:chat": ["repo:read"]}


class TestDeactivatePreservesGrant:
    def test_deactivate_preserves_grant(self, fake_home):
        grant_agent("github", "builtin:chat", ["repo:read"])
        activate("github", "builtin:chat")
        deactivate("github", "builtin:chat")
        # Grant survives — re-activate must be one click, no re-consent.
        assert list_agent_grants("github") == {"builtin:chat": ["repo:read"]}
        assert is_agent_active("github", "builtin:chat") is False

    def test_deactivate_without_prior_activation_is_idempotent(self, fake_home):
        grant_agent("github", "builtin:chat", ["repo:read"])
        # Has grant but never activated — deactivate must not raise.
        deactivate("github", "builtin:chat")
        assert is_agent_active("github", "builtin:chat") is False
        assert list_agent_grants("github") == {"builtin:chat": ["repo:read"]}


class TestActivateIsIdempotent:
    def test_double_activate_no_double_grant(self, fake_home):
        activate("github", "builtin:chat", scopes_for_grant=["repo:read"])
        # Second activate must NOT re-grant (existing grant blocks the
        # auto-grant path).
        auto_granted = activate(
            "github", "builtin:chat", scopes_for_grant=["repo:write"]
        )
        assert auto_granted is False
        assert list_agent_grants("github") == {"builtin:chat": ["repo:read"]}
        assert is_agent_active("github", "builtin:chat") is True
