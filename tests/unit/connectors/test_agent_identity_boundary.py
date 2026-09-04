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

import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.agents.base.agent import Agent
from gaia.connectors.context import _agent_context, current_agent_id
from gaia.connectors.errors import AuthRequiredError, ScopeNotAllowedError
from gaia.connectors.grants import grant_agent, list_agent_grants
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
        with pytest.raises(KeyError):
            grant_agent("gooogle", "installed:email", [_GMAIL_READ])
        assert list_agent_grants("gooogle") == {}

    def test_mcp_server_ceiling_is_the_implicit_use_scope(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
        grant_agent("mcp-tavily", _NSID, ["use"])
        with pytest.raises(ScopeNotAllowedError):
            grant_agent("mcp-tavily", _NSID, ["use", "admin"])


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
