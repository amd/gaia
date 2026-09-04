# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The per-agent connector grant control (#915), end to end.

Three regressions, one boundary. Each was independently enough to make the
control do nothing:

1. **Identity never reached the tool body.** ``Agent._call_tool_bounded``
   runs every tool in a ``threading.Thread``, and a new thread starts with an
   empty context — so the agent id ``process_query`` binds was ``None`` inside
   every tool, and both credential paths read ``None`` as "no agent, skip the
   check".
2. **The ledger the check reads had no ceiling.** ``grants.grant_agent`` wrote
   whatever scopes it was handed, for whatever connector id, so a check that
   started working could be answered with scopes the connector never
   advertised. Same ceiling ``flow._reject_scopes_outside_catalog`` applies to
   the OAuth authorize path (#3247), pushed down so the CLI and the Agent UI
   route cannot drift.
3. **Tavily asked for nothing.** ``web/tavily.py`` named neither an agent nor
   any scopes, and the dispatcher skips the check when scopes are missing — so
   the API key stayed ungated even with (1) fixed.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.agents.base.agent import Agent
from gaia.connectors.context import _agent_context, current_agent_id
from gaia.connectors.errors import (
    AuthRequiredError,
    ScopeNotAllowedError,
    UnknownConnectorError,
)
from gaia.connectors.grants import (
    grant_agent,
    list_agent_grants,
    revoke_agent_grant,
    revoke_all_grants_for,
)
from gaia.connectors.registry import REGISTRY

_NSID = "builtin:grant-boundary-probe"
_GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"


# ---------------------------------------------------------------------------
# 1. Identity survives the tool-execution thread
# ---------------------------------------------------------------------------


class _ProbeAgent(Agent):
    """Minimal stand-in: real ``process_query`` and real ``_execute_tool``,
    with the LLM turn replaced by a single tool call."""

    AGENT_ID = _NSID

    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        pass


def _make_probe_agent(tool_body):
    agent = _ProbeAgent.__new__(_ProbeAgent)
    # ``_tools_registry`` is a read-only property over ``_instance_tools``.
    agent._instance_tools = {
        "probe": {"function": tool_body, "description": "identity probe"}
    }
    agent._policy_refusal = lambda name, args: None
    agent._tool_requires_confirmation = lambda name, args: False
    agent._on_tool_invoked = lambda name: None
    agent._fold_tool_usage = lambda name, result: None
    agent._resolve_tool_timeout = lambda name: 10.0
    agent._finish_turn_record = lambda answer, steps: None
    agent._turn_recorder = None
    return agent


def test_tool_body_sees_the_identity_process_query_bound():
    """The C1 regression. Before the fix this read ``None``: the tool ran in a
    bare ``threading.Thread``, which starts with an empty context."""
    seen = {}

    def probe():
        seen["agent_id"] = current_agent_id()
        seen["thread"] = threading.current_thread().name
        return {"status": "ok"}

    agent = _make_probe_agent(probe)
    agent._process_query_impl = lambda *a, **kw: agent._execute_tool("probe", {})

    result = agent.process_query("go")

    assert result == {"status": "ok"}
    # Guard the guard: if the tool ever stops running in a worker thread this
    # test would pass for the wrong reason.
    assert seen["thread"] == "tool:probe"
    assert seen["agent_id"] == _NSID


def test_identity_is_unbound_again_after_the_turn():
    """The context must not leak past ``process_query`` — a later CLI call
    would otherwise be checked against a stale agent."""
    agent = _make_probe_agent(lambda: {"status": "ok"})
    agent._process_query_impl = lambda *a, **kw: agent._execute_tool("probe", {})
    agent.process_query("go")
    assert current_agent_id() is None


# ---------------------------------------------------------------------------
# 2. The ledger has a ceiling
# ---------------------------------------------------------------------------


class TestGrantScopeCeiling:
    def test_scope_outside_available_scopes_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        outside = "https://mail.google.com/"
        assert outside not in REGISTRY.get("google").available_scopes

        with pytest.raises(ScopeNotAllowedError) as exc:
            grant_agent("google", "installed:email", [outside])

        assert exc.value.scopes == [outside]
        assert exc.value.connector_id == "google"
        # Nothing was persisted — a rejected grant must not half-write.
        assert list_agent_grants("google") == {}

    def test_scope_inside_available_scopes_is_written(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        grant_agent("google", "installed:email", [_GMAIL_READ])
        assert list_agent_grants("google") == {"installed:email": [_GMAIL_READ]}

    def test_unknown_connector_id_is_rejected(self, tmp_path, monkeypatch):
        """A typo-d id used to return success and persist a phantom key."""
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        with pytest.raises(UnknownConnectorError):
            grant_agent("gooogle", "installed:email", [_GMAIL_READ])
        assert list_agent_grants("gooogle") == {}

    def test_mcp_server_ceiling_is_the_implicit_use_scope(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        grant_agent("mcp-tavily", _NSID, ["use"])
        with pytest.raises(ScopeNotAllowedError):
            grant_agent("mcp-tavily", _NSID, ["use", "admin"])


class TestRevokeIsNeverCatalogGated:
    """Only WIDENING is gated. Taking authority away has to work even for a
    connector the catalog no longer publishes — otherwise dropping a connector
    from the catalog strands grants that can never be cleared, which is a
    security regression pointing the other way.
    """

    @pytest.fixture
    def stranded_grant(self, tmp_path, monkeypatch):
        """A ledger row for a connector that is no longer in the catalog."""
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        grant_agent("mcp-tavily", _NSID, ["use"])
        grant_agent("mcp-github", "builtin:chat", ["use"])

        from gaia.connectors.registry import REGISTRY as _REAL
        from gaia.connectors.registry import ConnectorRegistry

        # Drop mcp-tavily from the catalog, keeping everything else.
        shrunk = ConnectorRegistry()
        for spec in _REAL.all():
            if spec.id != "mcp-tavily":
                shrunk.register(spec)
        monkeypatch.setattr("gaia.connectors.registry.REGISTRY", shrunk)

        with pytest.raises(UnknownConnectorError):
            grant_agent("mcp-tavily", _NSID, ["use"])  # widening IS refused

    def test_revoke_agent_grant_still_clears_it(self, stranded_grant):
        revoke_agent_grant("mcp-tavily", _NSID)
        assert list_agent_grants("mcp-tavily") == {}

    def test_revoke_all_grants_for_still_clears_it(self, stranded_grant):
        assert revoke_all_grants_for("mcp-tavily") == [_NSID]
        assert list_agent_grants("mcp-tavily") == {}
        # And an unrelated connector is untouched.
        assert list_agent_grants("mcp-github") == {"builtin:chat": ["use"]}


class TestUnknownConnectorIsStructured:
    """A bare ``KeyError`` from the registry reaches HTTP as a 500 and a CLI as
    a traceback. Grant writes raise a typed error every surface can translate.
    """

    def test_grant_agent_raises_the_typed_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        with pytest.raises(UnknownConnectorError) as exc:
            grant_agent("gooogle", "builtin:chat", [_GMAIL_READ])
        assert exc.value.connector_id == "gooogle"
        assert "google" in exc.value.known_ids
        # Actionable: names the id, the catalog, and the command to inspect it.
        assert "gaia connectors list" in str(exc.value)


class TestPutGrantRouteTranslation:
    """The Agent UI route's half of the ceiling. ``test_router_connectors.py``
    covers the same two cases through the real HTTP stack; those need a
    ``TestClient``, which cannot start on Windows (review C41), so the handler
    is called directly here to keep the contract verifiable on both platforms.
    """

    @staticmethod
    def _put(connector_id, agent_id, scopes):
        import asyncio

        from gaia.ui.routers.connectors import GrantRequest, put_grant

        return asyncio.run(
            put_grant(connector_id, agent_id, GrantRequest(scopes=scopes))
        )

    @pytest.mark.allow_network
    def test_out_of_ceiling_scope_is_400_scope_not_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._put("google", "installed:email", ["https://mail.google.com/"])

        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "scope_not_allowed"
        assert exc.value.detail["scopes"] == ["https://mail.google.com/"]
        assert list_agent_grants("google") == {}

    @pytest.mark.allow_network
    def test_unknown_connector_is_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._put("gooogle", "builtin:chat", [_GMAIL_READ])
        assert exc.value.status_code == 404
        # Structured, not a bare string — a raw KeyError here would be a 500.
        assert exc.value.detail == {
            "error": "unknown_connector",
            "connector_id": "gooogle",
        }


class TestEveryCredentialDoorIsGuarded:
    """Both entry points into the credential layer, checked the same way.

    ``handler.get_credential`` covers the MCP/dispatcher path;
    ``api._authorize_access`` covers the OAuth token path
    (``get_access_token`` / ``_with_expiry`` and their sync wrappers all
    funnel through it). Fixing one and leaving the other is how a control
    stays bypassable: an agent that simply names no scopes would satisfy
    ``check_agent_grant`` vacuously and get a full-capability token.
    """

    def test_token_path_refuses_a_scope_less_request(self, tmp_path, monkeypatch):
        from gaia.connectors.api import _authorize_access
        from gaia.connectors.errors import ConfigurationError

        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        with pytest.raises(ConfigurationError) as exc:
            _authorize_access(
                provider="google",
                scopes=[],
                agent_id="builtin:chat",
                account_email="default",
            )
        assert "named no scopes" in str(exc.value)

    def test_token_path_still_checks_a_named_scope(self, tmp_path, monkeypatch):
        from gaia.connectors.api import _authorize_access

        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        with pytest.raises(AuthRequiredError) as exc:
            _authorize_access(
                provider="google",
                scopes=[_GMAIL_READ],
                agent_id="builtin:chat",
                account_email="default",
            )
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED

    def test_token_path_outside_a_turn_keeps_the_cli_escape_hatch(
        self, tmp_path, monkeypatch
    ):
        """No agent id and no agent turn: the documented ungated path. It must
        fail on the missing CONNECTION, never on the grant check."""
        from gaia.connectors.api import _authorize_access

        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "gaia.connectors.api.get_provider",
            lambda p: SimpleNamespace(client_id_hash="h", tenant=None),
        )
        monkeypatch.setattr(
            "gaia.connectors.api.load_connection", lambda *a, **kw: None
        )
        with pytest.raises(AuthRequiredError) as exc:
            _authorize_access(
                provider="google",
                scopes=[],
                agent_id=None,
                account_email="default",
            )
        assert exc.value.reason is AuthRequiredError.Reason.NOT_CONNECTED

    def test_token_path_fails_closed_with_no_identity_in_a_turn(
        self, tmp_path, monkeypatch
    ):
        from gaia.connectors.api import _authorize_access

        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        with _agent_context(None):
            with pytest.raises(AuthRequiredError) as exc:
                _authorize_access(
                    provider="google",
                    scopes=[_GMAIL_READ],
                    agent_id=None,
                    account_email="default",
                )
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        assert exc.value.agent_id is None

    def test_identity_missing_error_does_not_tell_you_to_grant_nothing(self):
        """ "Open Settings and grant it" is unfollowable when there is no agent
        to grant TO — that branch names the dropped context instead."""
        from gaia.connectors.formatting import format_connector_error

        msg = format_connector_error(
            AuthRequiredError(
                AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                provider="google",
                agent_id=None,
                missing_scopes=[_GMAIL_READ],
            )
        )
        assert "AGENT_IDENTITY_MISSING" in msg
        assert "Per-agent grants" not in msg
        assert "agent_id=" in msg  # names the actual remedy

        # A real agent still gets the grant-me instruction.
        granted_msg = format_connector_error(
            AuthRequiredError(
                AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                provider="google",
                agent_id="builtin:chat",
                missing_scopes=[_GMAIL_READ],
            )
        )
        assert "Per-agent grants" in granted_msg


# ---------------------------------------------------------------------------
# 3. Tavily's credential fetch is grant-checked
# ---------------------------------------------------------------------------


class TestTavilyKeyIsGated:
    """``_load_api_key`` must hand the dispatcher an identity AND scopes —
    the dispatcher skips the grant check when either is missing (I14)."""

    @pytest.fixture
    def configured_tavily(self, monkeypatch, tmp_path):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "gaia.connectors.mcp_server.is_mcp_server_configured", lambda cid: True
        )

        async def _fake_get_credential(spec, *, required_scopes=None, account_id=None):
            return {"env": {"TAVILY_API_KEY": "tvly-secret"}}

        from gaia.connectors import handler as handler_mod

        class _FakeHandler:
            get_credential = staticmethod(_fake_get_credential)

            async def configure(self, spec, config):
                return {}

            async def disconnect(self, spec, *, account_id=None):
                return None

            async def health_check(self, spec):
                return {"ok": True}

        monkeypatch.setitem(handler_mod._HANDLER_REGISTRY, "mcp_server", _FakeHandler())

    # asyncio's ProactorEventLoop self-pipe needs socket.socketpair(), which
    # tests/unit/conftest.py's network guard blocks on Windows (review C41).
    @pytest.mark.allow_network
    def test_ungranted_agent_is_refused(self, configured_tavily):
        from gaia.web.tavily import _load_api_key

        with _agent_context(_NSID):
            with pytest.raises(AuthRequiredError) as exc:
                _load_api_key()
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
        assert exc.value.provider == "mcp-tavily"

    @pytest.mark.allow_network
    def test_granted_agent_gets_the_key(self, configured_tavily):
        from gaia.web.tavily import _load_api_key

        grant_agent("mcp-tavily", _NSID, ["use"])
        with _agent_context(_NSID):
            assert _load_api_key() == "tvly-secret"

    @pytest.mark.allow_network
    def test_cli_caller_outside_an_agent_turn_is_unaffected(self, configured_tavily):
        """``gaia knowledge`` has no agent identity and stays ungated."""
        from gaia.web.tavily import _load_api_key

        assert _load_api_key() == "tvly-secret"
