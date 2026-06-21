# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the ``activate``/``deactivate`` orchestration in
``gaia.connectors.api`` — the one-click convenience layer that combines
grants + activations per issue #1005.

Activations apply to MCP-server connectors only — OAuth connectors have
no MCP tool surface to gate (see ``_require_mcp_server_for_activation``).
Tests use ``mcp-github`` (an MCP server) for the success paths and
``google`` (an OAuth provider) for the rejection paths.
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


@pytest.fixture
def mcp_test_spec(monkeypatch):
    """Register a stub ``mcp_server`` spec so ``activate``/``deactivate``
    pass the type guard without depending on the real catalog.

    Tests that want to exercise the rejection path use a different fake
    spec ``oauth-test`` registered in :func:`oauth_test_spec`.
    """
    from gaia.connectors.registry import ConnectorRegistry
    from gaia.connectors.spec import ConnectorSpec

    fresh = ConnectorRegistry()
    fresh.register(
        ConnectorSpec(
            id="mcp-test",
            display_name="MCP Test",
            icon="M",
            category="dev-tools",
            tier=1,
            type="mcp_server",
            description="Stub MCP server for api tests",
            mcp_command="true",
            mcp_args=(),
        )
    )
    fresh.register(
        ConnectorSpec(
            id="oauth-test",
            display_name="OAuth Test",
            icon="O",
            category="productivity",
            tier=1,
            type="oauth_pkce",
            description="Stub OAuth provider for api tests",
            default_scopes=("openid",),
            oauth_provider_ref="oauth-test",
        )
    )
    monkeypatch.setattr("gaia.connectors.registry.REGISTRY", fresh)
    return fresh


class _RecordingEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload):
        self.events.append((event_type, dict(payload)))


@pytest.fixture
def recording_emitter():
    """Capture events published through the connectors event bus (#1226)."""
    from gaia.connectors import events

    rec = _RecordingEmitter()
    events.set_emitter(rec)
    try:
        yield rec
    finally:
        events.reset_emitter()


class TestActivateWithExistingGrant:
    def test_activate_with_existing_grant_does_not_touch_grant(
        self, fake_home, mcp_test_spec
    ):
        grant_agent("mcp-test", "builtin:chat", ["use"])
        auto_granted = activate("mcp-test", "builtin:chat")
        assert auto_granted is False
        # Grant is preserved as-is.
        assert list_agent_grants("mcp-test") == {"builtin:chat": ["use"]}
        assert is_agent_active("mcp-test", "builtin:chat") is True

    def test_activate_ignores_scopes_for_grant_when_grant_exists(
        self, fake_home, mcp_test_spec
    ):
        # Existing grants are NOT overwritten — auto-grant only fires when
        # no grant is present at all.
        grant_agent("mcp-test", "builtin:chat", ["use"])
        activate("mcp-test", "builtin:chat", scopes_for_grant=["use:write"])
        assert list_agent_grants("mcp-test") == {"builtin:chat": ["use"]}


class TestActivateAutoGrant:
    def test_activate_without_grant_auto_grants_required_scopes(
        self, fake_home, mcp_test_spec
    ):
        auto_granted = activate(
            "mcp-test", "builtin:chat", scopes_for_grant=["use", "use:write"]
        )
        assert auto_granted is True
        assert list_agent_grants("mcp-test") == {"builtin:chat": ["use", "use:write"]}
        assert is_agent_active("mcp-test", "builtin:chat") is True

    def test_activate_without_grant_or_scopes_raises_configuration_error(
        self, fake_home, mcp_test_spec
    ):
        with pytest.raises(ConfigurationError) as exc:
            activate("mcp-test", "builtin:chat")
        msg = str(exc.value)
        assert "mcp-test" in msg
        assert "builtin:chat" in msg
        assert "scopes" in msg.lower()

    def test_auto_grant_scopes_are_a_copy_not_a_reference(
        self, fake_home, mcp_test_spec
    ):
        # Defensive: callers must not be able to mutate the stored grant
        # through the list they passed in.
        scopes = ["use"]
        activate("mcp-test", "builtin:chat", scopes_for_grant=scopes)
        scopes.append("use:write")
        assert list_agent_grants("mcp-test") == {"builtin:chat": ["use"]}


class TestDeactivatePreservesGrant:
    def test_deactivate_preserves_grant(self, fake_home, mcp_test_spec):
        grant_agent("mcp-test", "builtin:chat", ["use"])
        activate("mcp-test", "builtin:chat")
        deactivate("mcp-test", "builtin:chat")
        # Grant survives — re-activate must be one click, no re-consent.
        assert list_agent_grants("mcp-test") == {"builtin:chat": ["use"]}
        assert is_agent_active("mcp-test", "builtin:chat") is False

    def test_deactivate_without_prior_activation_is_idempotent(
        self, fake_home, mcp_test_spec
    ):
        grant_agent("mcp-test", "builtin:chat", ["use"])
        # Has grant but never activated — deactivate must not raise.
        deactivate("mcp-test", "builtin:chat")
        assert is_agent_active("mcp-test", "builtin:chat") is False
        assert list_agent_grants("mcp-test") == {"builtin:chat": ["use"]}


class TestActivateIsIdempotent:
    def test_double_activate_no_double_grant(self, fake_home, mcp_test_spec):
        activate("mcp-test", "builtin:chat", scopes_for_grant=["use"])
        # Second activate must NOT re-grant (existing grant blocks the
        # auto-grant path).
        auto_granted = activate(
            "mcp-test", "builtin:chat", scopes_for_grant=["use:write"]
        )
        assert auto_granted is False
        assert list_agent_grants("mcp-test") == {"builtin:chat": ["use"]}
        assert is_agent_active("mcp-test", "builtin:chat") is True


class TestRejectNonMcpServer:
    """#1005 follow-up — activations gate MCP tool visibility only.

    Both ``activate`` and ``deactivate`` must reject OAuth connectors so
    the CLI / SDK / HTTP surfaces all enforce the same invariant. The
    HTTP router has its own pre-check (``_require_mcp_server``) for early
    rejection + cleaner error semantics, but this is the canonical guard
    every caller flows through.
    """

    def test_activate_oauth_connector_raises_configuration_error(
        self, fake_home, mcp_test_spec
    ):
        with pytest.raises(ConfigurationError) as exc:
            activate("oauth-test", "builtin:chat", scopes_for_grant=["openid"])
        msg = str(exc.value)
        assert "MCP-server" in msg
        assert "oauth-test" in msg
        # Nothing was written — both ledgers stay empty.
        assert list_agent_grants("oauth-test") == {}
        assert is_agent_active("oauth-test", "builtin:chat") is False

    def test_deactivate_oauth_connector_raises_configuration_error(
        self, fake_home, mcp_test_spec
    ):
        with pytest.raises(ConfigurationError) as exc:
            deactivate("oauth-test", "builtin:chat")
        assert "MCP-server" in str(exc.value)

    def test_activate_unknown_connector_raises_configuration_error(
        self, fake_home, mcp_test_spec
    ):
        with pytest.raises(ConfigurationError) as exc:
            activate("does-not-exist", "builtin:chat", scopes_for_grant=["use"])
        assert "does-not-exist" in str(exc.value)

    def test_deactivate_unknown_connector_raises_configuration_error(
        self, fake_home, mcp_test_spec
    ):
        with pytest.raises(ConfigurationError) as exc:
            deactivate("does-not-exist", "builtin:chat")
        assert "does-not-exist" in str(exc.value)


class TestActivateColdStartLoadsCatalog:
    """Regression: ``api.activate`` must populate ``REGISTRY`` from the
    catalog on its own — callers cannot be required to ``import
    gaia.connectors.catalog`` first.

    Before the fix, a bare ``gaia connectors activations activate
    mcp-github …`` from a fresh shell raised
    ``ConfigurationError: Unknown connector 'mcp-github'`` because the CLI
    handler for ``activations`` did not load the catalog (other handlers
    like ``list``/``configure``/``test``/``disconnect`` do). The type
    guard then read an empty registry and rejected every connector id.

    Spawned in a subprocess to guarantee a pristine import cache — any
    in-process check is contaminated by prior tests that have already
    imported the catalog.
    """

    def _spawn(self, code: str) -> tuple[int, str, str]:
        import os
        import subprocess
        import sys

        env = os.environ.copy()
        # Isolate ledger writes — otherwise the subprocess would mutate
        # the developer's real ~/.gaia/ files.
        env["HOME"] = env.get("PYTEST_CURRENT_HOME", env["HOME"])
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def test_activate_works_without_explicit_catalog_import(self, tmp_path):
        # Subprocess: bare activate() call against a real catalog id with
        # no prior ``import gaia.connectors.catalog``. Use ``mcp-github``
        # because it's a real ``mcp_server`` entry that ships in the
        # built-in catalog. Isolate ledger writes via HOME override.
        code = (
            f"import os; os.environ['HOME'] = {str(tmp_path)!r}\n"
            "from gaia.connectors.api import activate\n"
            # If activate() does not load the catalog itself, this raises
            # ConfigurationError('Unknown connector mcp-github') and the
            # process exits non-zero.
            "activate('mcp-github', 'builtin:chat', "
            "scopes_for_grant=['use'])\n"
            "print('OK')\n"
        )
        rc, out, err = self._spawn(code)
        assert rc == 0, f"activate failed cold: stderr={err!r}"
        assert "OK" in out

    def test_deactivate_works_without_explicit_catalog_import(self, tmp_path):
        code = (
            f"import os; os.environ['HOME'] = {str(tmp_path)!r}\n"
            "from gaia.connectors.api import deactivate\n"
            "deactivate('mcp-github', 'builtin:chat')\n"
            "print('OK')\n"
        )
        rc, out, err = self._spawn(code)
        assert rc == 0, f"deactivate failed cold: stderr={err!r}"
        assert "OK" in out


class TestActivationEmitsSseEvent:
    """#1226 — CLI/SDK callers flow through ``api.activate``/``deactivate``,
    which must emit ``connector.activation.changed`` with the same payload the
    HTTP PUT/DELETE handlers used to emit inline. This is the mockable
    ``CLI → SSE`` path: the CLI calls these functions directly.
    """

    _EVENT = "connector.activation.changed"

    def test_activate_emits_changed_event_active_true(
        self, fake_home, mcp_test_spec, recording_emitter
    ):
        activate("mcp-test", "builtin:chat", scopes_for_grant=["use"])
        assert (
            self._EVENT,
            {"connector_id": "mcp-test", "agent_id": "builtin:chat", "active": True},
        ) in recording_emitter.events

    def test_deactivate_emits_changed_event_active_false(
        self, fake_home, mcp_test_spec, recording_emitter
    ):
        grant_agent("mcp-test", "builtin:chat", ["use"])
        activate("mcp-test", "builtin:chat")
        recording_emitter.events.clear()
        deactivate("mcp-test", "builtin:chat")
        assert (
            self._EVENT,
            {"connector_id": "mcp-test", "agent_id": "builtin:chat", "active": False},
        ) in recording_emitter.events

    def test_failed_activate_emits_nothing(
        self, fake_home, mcp_test_spec, recording_emitter
    ):
        # No grant + no scopes raises before any write — and before any emit.
        with pytest.raises(ConfigurationError):
            activate("mcp-test", "builtin:chat")
        assert recording_emitter.events == []

    def test_rejected_oauth_activate_emits_nothing(
        self, fake_home, mcp_test_spec, recording_emitter
    ):
        with pytest.raises(ConfigurationError):
            activate("oauth-test", "builtin:chat", scopes_for_grant=["openid"])
        assert recording_emitter.events == []
