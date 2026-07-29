# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Coverage for #2603 — ``--grant-agent`` must derive scopes from the agent's own
``REQUIRED_CONNECTORS`` declaration instead of demanding the user type
``--scopes`` by hand.

Exercises the new shared resolver ``gaia.connectors.api.resolve_declared_scopes``
directly, its three new exception types in ``gaia.connectors.errors``
(``UnknownAgentError``, ``NoDeclaredScopesError``, ``ScopeNotAllowedError``),
and both call sites that must share it (the CLI's ``_handle_connect`` and the
router's ``_resolve_grant_scopes``) so they can never drift.

Hermetic: no ``gaia_agent_email`` import (``tests/unit/connectors/`` runs in a
CI job that installs only ``-e ".[api]"`` — the email wheel is not
importable there). Uses ``make_fake_agent_registry`` (``conftest.py``) plus
the real Google catalog spec (``gaia.connectors.catalog.google``) for the
``available_scopes`` ceiling checks.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from tests.unit.connectors.conftest import make_fake_agent_registry

UI_HEADER = {"x-gaia-ui": "1"}


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Isolated grants/mcp_servers dirs + a configured Google provider, per
    test — mirrors ``tests/unit/connectors/test_cli.py``'s ``fake_home``."""
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.activations.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_ID", "test.apps.example")
    monkeypatch.setenv("GAIA_GOOGLE_CLIENT_SECRET", "test-secret")

    from gaia.connectors.providers import _registry

    _registry.clear()
    yield
    _registry.clear()


@pytest.fixture(autouse=True)
def real_google_catalog():
    """Trigger registration of the real Google ``ConnectorSpec`` — its
    ``available_scopes`` already includes every scope the email agent
    declares (gmail.modify/send, calendar.readonly/events), so the ceiling
    check in ``resolve_declared_scopes`` needs no fake spec here."""
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import


def _run_cli(*argv) -> tuple[int, str, str]:
    """Local copy of test_cli.py's ``_run`` helper — kept separate so this
    file has no import-order dependency on test_cli.py."""
    import sys
    from io import StringIO

    from gaia.connectors import cli as connections_cli

    out = StringIO()
    err = StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        rc = connections_cli.main(list(argv))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue()


def _stub_registry_chain(monkeypatch, regs):
    """Monkeypatch the three-call sequence ``_handle_connect`` uses to build
    an ``AgentRegistry`` (``AgentRegistry() -> .discover() ->
    register_installed_sidecars()``) with a fake that returns *regs* from
    ``.list()``. Guards against #2408 (a two-call sequence that skips
    ``register_installed_sidecars`` makes binary-only sidecars invisible)."""

    class FakeAgentRegistry:
        def __init__(self, *_a, **_k):
            pass

        def discover(self):
            pass

        def list(self):
            return regs

    monkeypatch.setattr("gaia.agents.registry.AgentRegistry", FakeAgentRegistry)
    monkeypatch.setattr(
        "gaia.hub.installer.register_installed_sidecars", lambda registry: None
    )


# ---------------------------------------------------------------------------
# resolve_declared_scopes — direct unit coverage
# ---------------------------------------------------------------------------


class TestResolveDeclaredScopes:
    def test_resolve_declared_scopes_returns_sorted_dict(self):
        from gaia.connectors.api import resolve_declared_scopes

        registry = make_fake_agent_registry(
            "installed:email",
            "google",
            [
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
            ],
        )

        result = resolve_declared_scopes(registry, "google", ["installed:email"])

        assert result == {
            "installed:email": [
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
            ]
        }

    def test_empty_agent_ids_returns_empty_dict(self):
        from gaia.connectors.api import resolve_declared_scopes

        registry = make_fake_agent_registry(
            "installed:email", "google", ["https://www.googleapis.com/auth/gmail.send"]
        )

        assert resolve_declared_scopes(registry, "google", []) == {}

    def test_unknown_agent_raises_unknown_agent_error(self):
        from gaia.connectors.api import resolve_declared_scopes
        from gaia.connectors.errors import UnknownAgentError

        registry = make_fake_agent_registry(
            "installed:email", "google", ["https://www.googleapis.com/auth/gmail.send"]
        )

        with pytest.raises(UnknownAgentError) as exc:
            resolve_declared_scopes(registry, "google", ["installed:ghost"])

        assert exc.value.agent_ids == ["installed:ghost"]

    def test_agent_with_no_declaration_raises_no_declared_scopes_error(self):
        from gaia.connectors.api import resolve_declared_scopes
        from gaia.connectors.errors import NoDeclaredScopesError

        # The agent declares scopes for microsoft, not google.
        registry = make_fake_agent_registry(
            "installed:email",
            "microsoft",
            ["https://graph.microsoft.com/Mail.ReadWrite"],
        )

        with pytest.raises(NoDeclaredScopesError) as exc:
            resolve_declared_scopes(registry, "google", ["installed:email"])

        assert exc.value.agent_id == "installed:email"
        assert exc.value.connector_id == "google"
        # Never falls back to the provider's default_scopes or anything else
        # non-empty — the whole call raises, nothing is silently supplied.
        assert getattr(exc.value, "scopes", None) in (None, [], ())

    def test_declared_scope_outside_available_scopes_raises(self):
        from gaia.connectors.api import resolve_declared_scopes
        from gaia.connectors.errors import ScopeNotAllowedError

        bogus_scope = "https://www.googleapis.com/auth/fake.nonexistent.scope"
        registry = make_fake_agent_registry("installed:email", "google", [bogus_scope])

        with pytest.raises(ScopeNotAllowedError) as exc:
            resolve_declared_scopes(registry, "google", ["installed:email"])

        assert exc.value.agent_id == "installed:email"
        assert exc.value.connector_id == "google"
        assert bogus_scope in exc.value.scopes

    def test_mixed_allowed_and_disallowed_scopes_raises_not_filters(self):
        """A partially-bad declaration raises entirely — it never silently
        drops the disallowed scope and returns a truncated map."""
        from gaia.connectors.api import resolve_declared_scopes
        from gaia.connectors.errors import ScopeNotAllowedError

        bogus_scope = "https://www.googleapis.com/auth/fake.nonexistent.scope"
        registry = make_fake_agent_registry(
            "installed:email",
            "google",
            ["https://www.googleapis.com/auth/gmail.modify", bogus_scope],
        )

        with pytest.raises(ScopeNotAllowedError) as exc:
            resolve_declared_scopes(registry, "google", ["installed:email"])

        assert bogus_scope in exc.value.scopes


class TestAuthorizeAndGrantScopeUnion:
    def test_authorize_and_grant_scopes_differ(self):
        """The #2603 flagship math: the CLI authorizes the UNION of the
        agent's declared scopes and the provider's default_scopes, but
        grants exactly the declared set (no `openid` in the grant)."""
        from gaia.connectors.api import resolve_declared_scopes
        from gaia.connectors.providers import get as get_provider

        declared_scopes = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
        ]
        registry = make_fake_agent_registry(
            "installed:email", "google", declared_scopes
        )

        declared = resolve_declared_scopes(registry, "google", ["installed:email"])[
            "installed:email"
        ]
        provider = get_provider("google")

        # Fixture assumption this test documents: the provider's own
        # default_scopes includes `openid`, which the agent's declaration
        # does not.
        assert "openid" in provider.default_scopes

        union = sorted(set(declared) | set(provider.default_scopes))
        assert union != declared
        assert set(union) - set(declared) == set(provider.default_scopes) - set(
            declared
        )
        assert "openid" not in declared


# ---------------------------------------------------------------------------
# Exception-hierarchy guard — never alias the daemon-sidecar UnknownAgentError
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_unknown_agent_error_is_a_connectors_error_not_a_sidecar_error(self):
        from gaia.connectors.errors import (
            ConnectorsError,
        )
        from gaia.connectors.errors import (
            UnknownAgentError as ConnectorsUnknownAgentError,
        )
        from gaia.daemon.sidecars.errors import (
            UnknownAgentError as SidecarUnknownAgentError,
        )

        assert ConnectorsUnknownAgentError is not SidecarUnknownAgentError
        assert issubclass(ConnectorsUnknownAgentError, ConnectorsError)

    def test_no_declared_scopes_error_is_a_connectors_error(self):
        from gaia.connectors.errors import ConnectorsError, NoDeclaredScopesError

        assert issubclass(NoDeclaredScopesError, ConnectorsError)

    def test_scope_not_allowed_error_is_a_connectors_error(self):
        from gaia.connectors.errors import ConnectorsError, ScopeNotAllowedError

        assert issubclass(ScopeNotAllowedError, ConnectorsError)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


class TestCliErrorMapping:
    def test_cli_handle_maps_unknown_agent_error_to_exit_5_not_traceback(
        self, monkeypatch
    ):
        _stub_registry_chain(monkeypatch, regs=[])

        def _boom(*_a, **_k):
            from gaia.connectors.errors import UnknownAgentError

            raise UnknownAgentError(["installed:ghost"])

        monkeypatch.setattr("gaia.connectors.api.resolve_declared_scopes", _boom)

        rc, _out, err = _run_cli(
            "connectors", "connect", "google", "--grant-agent", "installed:ghost"
        )

        assert rc == 5
        assert "Connectors error" in err
        assert "installed:ghost" in err


class TestSharedResolverCallSites:
    def test_shared_resolver_call_through_cli(self, monkeypatch):
        """The CLI must call the SHARED resolver, not reimplement its own
        scope-derivation logic (#2603 AC5)."""
        spy = Mock(return_value={"installed:email": ["s1"]})
        monkeypatch.setattr("gaia.connectors.api.resolve_declared_scopes", spy)
        _stub_registry_chain(monkeypatch, regs=[])

        async def _fake_start(connector_id, *, scopes, grant_agents=None):
            return {"flow_id": "F1", "authorization_url": "https://auth.example"}

        async def _fake_complete(flow_id):
            return {"account_email": "alice@example.com"}

        monkeypatch.setattr("gaia.connectors.api.start_authorization", _fake_start)
        monkeypatch.setattr(
            "gaia.connectors.api.complete_authorization", _fake_complete
        )

        rc, _out, err = _run_cli(
            "connectors", "connect", "google", "--grant-agent", "installed:email"
        )
        assert rc == 0, err

        spy.assert_called_once()
        call = spy.call_args
        args, kwargs = call.args, call.kwargs
        # Tolerant of positional-or-keyword: pull connector_id/agent_ids out
        # of whichever form the implementer used.
        all_values = list(args) + list(kwargs.values())
        assert "google" in all_values or kwargs.get("connector_id") == "google"
        assert ["installed:email"] in all_values or kwargs.get("agent_ids") == [
            "installed:email"
        ]

    def test_shared_resolver_call_through_router(self, monkeypatch, ui_api_client):
        """The router must call the SAME shared resolver (#2603 AC5)."""
        spy = Mock(
            return_value={
                "installed:email": ["https://www.googleapis.com/auth/gmail.modify"]
            }
        )
        monkeypatch.setattr("gaia.ui.routers.connectors.resolve_declared_scopes", spy)
        ui_api_client.app.state.agent_registry = make_fake_agent_registry(
            "installed:email",
            "google",
            ["https://www.googleapis.com/auth/gmail.modify"],
        )

        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            "gaia.connectors.start_authorization",
            AsyncMock(
                return_value={"flow_id": "f1", "authorization_url": "https://auth"}
            ),
        )

        resp = ui_api_client.post(
            "/api/connectors/google/authorize",
            json={"scopes": ["openid"], "grant_agents": ["installed:email"]},
            headers=UI_HEADER,
        )
        assert resp.status_code == 200, resp.text

        spy.assert_called_once()
        call = spy.call_args
        all_values = list(call.args) + list(call.kwargs.values())
        assert "google" in all_values or call.kwargs.get("connector_id") == "google"
        assert ["installed:email"] in all_values or call.kwargs.get("agent_ids") == [
            "installed:email"
        ]


# ---------------------------------------------------------------------------
# #2408 guard — real discovery, no fake registry, no wheel
# ---------------------------------------------------------------------------


class Test2408GuardRealDiscoveryNoWheel:
    """Mirrors ``test_router_connectors.py::TestSidecarRegistrationEndToEnd``
    but drives the CLI instead of the router. A test that plants
    ``installed:email`` directly into a fake registry passes identically
    whether or not the sidecar is actually wired into discovery, and proves
    nothing about #2408 — so this test uses the REAL ``AgentRegistry`` +
    real ``register_installed_sidecars`` + real Google catalog spec, with
    only ``gaia.hub.installer.list_installed`` monkeypatched to report
    ``email`` as installed.
    """

    @staticmethod
    def _fake_installed_email():
        from gaia.hub.installer import ARTIFACT_KIND_BINARY, InstalledAgent

        return {
            "email": InstalledAgent(
                id="email",
                version="0.1.0",
                language="python",
                installed_at="2026-01-01T00:00:00Z",
                artifact_kind=ARTIFACT_KIND_BINARY,
            )
        }

    def test_2408_guard_real_discovery_no_wheel(self, monkeypatch):
        from gaia.daemon.sidecars.spec import builtin_specs

        monkeypatch.setattr(
            "gaia.hub.installer.list_installed",
            lambda *a, **kw: self._fake_installed_email(),
        )

        captured = {}

        async def _fake_start(connector_id, *, scopes, grant_agents=None):
            captured["scopes"] = list(scopes)
            captured["grant_agents"] = grant_agents
            return {"flow_id": "F1", "authorization_url": "https://auth.example"}

        async def _fake_complete(flow_id):
            return {"account_email": "alice@example.com"}

        monkeypatch.setattr("gaia.connectors.api.start_authorization", _fake_start)
        monkeypatch.setattr(
            "gaia.connectors.api.complete_authorization", _fake_complete
        )

        rc, _out, err = _run_cli(
            "connectors", "connect", "google", "--grant-agent", "installed:email"
        )
        assert rc == 0, err

        email_spec = builtin_specs()["email"]
        expected_google_scopes = sorted(
            {
                s
                for cr in email_spec.required_connections
                if cr.connector_id == "google"
                for s in cr.scopes
            }
        )
        assert (
            sorted(captured["grant_agents"]["installed:email"])
            == expected_google_scopes
        )
