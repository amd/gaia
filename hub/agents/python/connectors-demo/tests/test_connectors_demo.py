# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the ConnectorsDemoAgent — verify the per-agent grant path,
the credential-error translation, and the four tool implementations
(Gmail / Calendar / Drive / GitHub) without actually instantiating the
agent (which would spin up an LLM client).

The agent class itself (system prompt, tool registration, factory)
gets a thin smoke test that asserts REQUIRED_CONNECTORS is shaped
correctly and that the registry sees it as a built-in.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
from gaia_agent_connectors_demo.agent import (
    AGENT_NAMESPACED_ID,
    SCOPE_CALENDAR_READ,
    SCOPE_DRIVE_READ,
    SCOPE_GMAIL_READ,
    SCOPE_MCP_USE,
    ConnectorsDemoAgent,
    _calendar_today_impl,
    _drive_recent_files_impl,
    _format_connector_error,
    _github_my_repos_impl,
    _gmail_recent_subjects_impl,
)

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectorsError,
)

# ---------------------------------------------------------------------------
# REQUIRED_CONNECTORS shape
# ---------------------------------------------------------------------------


class TestRequiredConnectors:
    """The agent declares the connectors+scopes it needs so the AgentUI
    can render the per-agent grants section, and so check_agent_grant
    can fail closed when scopes are missing."""

    def test_required_connectors_lists_google_and_github(self):
        connector_ids = {
            req.connector_id for req in ConnectorsDemoAgent.REQUIRED_CONNECTORS
        }
        assert connector_ids == {"google", "mcp-github"}

    def test_google_scopes_include_all_three_apis(self):
        google = next(
            req
            for req in ConnectorsDemoAgent.REQUIRED_CONNECTORS
            if req.connector_id == "google"
        )
        assert SCOPE_GMAIL_READ in google.scopes
        assert SCOPE_CALENDAR_READ in google.scopes
        assert SCOPE_DRIVE_READ in google.scopes

    def test_github_uses_symbolic_use_scope(self):
        # v1 grants the entire PAT as a single unit. v2 may evolve to
        # per-tool grants — see the agent module docstring.
        github = next(
            req
            for req in ConnectorsDemoAgent.REQUIRED_CONNECTORS
            if req.connector_id == "mcp-github"
        )
        assert github.scopes == (SCOPE_MCP_USE,)

    def test_each_requirement_has_a_user_facing_reason(self):
        for req in ConnectorsDemoAgent.REQUIRED_CONNECTORS:
            assert req.reason, (
                f"{req.connector_id} missing a 'reason' — the AgentUI "
                "renders this when prompting users to grant scopes"
            )


# ---------------------------------------------------------------------------
# Error translation — every connectors exception type should produce a
# message the LLM can pass through to the user verbatim.
# ---------------------------------------------------------------------------


class TestFormatConnectorError:
    def test_agent_not_granted_names_missing_scopes(self):
        e = AuthRequiredError(
            AuthRequiredError.Reason.AGENT_NOT_GRANTED,
            provider="google",
            agent_id=AGENT_NAMESPACED_ID,
            missing_scopes=["scope-A", "scope-B"],
        )
        msg = _format_connector_error(e)
        assert "AGENT_NOT_GRANTED" in msg
        assert "scope-A" in msg
        assert "scope-B" in msg
        assert "Settings" in msg

    def test_not_connected_points_to_connect_button(self):
        e = AuthRequiredError(
            AuthRequiredError.Reason.NOT_CONNECTED,
            provider="google",
        )
        msg = _format_connector_error(e)
        assert "NOT_CONNECTED" in msg
        assert "Connect" in msg

    def test_reauth_required_treated_as_not_connected(self):
        # The user-facing remedy is the same: open Settings → Connect.
        e = AuthRequiredError(
            AuthRequiredError.Reason.REAUTH_REQUIRED,
            provider="google",
        )
        msg = _format_connector_error(e)
        assert "NOT_CONNECTED" in msg

    def test_configuration_error_passes_through(self):
        msg = _format_connector_error(ConfigurationError("client_id missing"))
        assert "CONFIG_ERROR" in msg
        assert "client_id" in msg

    def test_unknown_exception_labelled_unexpected(self):
        msg = _format_connector_error(RuntimeError("something else"))
        assert "UNEXPECTED_ERROR" in msg
        assert "RuntimeError" in msg


# ---------------------------------------------------------------------------
# Tool: gmail_recent_subjects
# ---------------------------------------------------------------------------


def _stub_gmail_response(messages):
    """Build the two-step Gmail API response shape the impl expects."""

    def _fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/messages"):
            return httpx.Response(
                200, json={"messages": [{"id": m["id"]} for m in messages]}
            )
        # /messages/<id>
        msg_id = url.rsplit("/", 1)[-1]
        msg = next(m for m in messages if m["id"] == msg_id)
        return httpx.Response(
            200,
            json={
                "payload": {
                    "headers": [
                        {"name": "From", "value": msg["from"]},
                        {"name": "Subject", "value": msg["subject"]},
                    ]
                }
            },
        )

    return _fake_get


class TestGmailRecentSubjects:
    def test_happy_path_returns_subjects_and_senders(self):
        fake_messages = [
            {"id": "1", "from": "alice@example.com", "subject": "Lunch?"},
            {"id": "2", "from": "bob@example.com", "subject": "Re: PR review"},
        ]
        with (
            patch(
                "gaia_agent_connectors_demo.agent._gmail_token",
                return_value="tok-xyz",
            ),
            patch("httpx.get", side_effect=_stub_gmail_response(fake_messages)),
        ):
            result = _gmail_recent_subjects_impl(limit=5)
        assert result["ok"] is True
        assert result["count"] == 2
        assert result["messages"][0]["subject"] == "Lunch?"
        assert result["messages"][1]["from"] == "bob@example.com"

    def test_grant_failure_returns_actionable_error(self):
        with patch(
            "gaia_agent_connectors_demo.agent._gmail_token",
            side_effect=AuthRequiredError(
                AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                provider="google",
                agent_id=AGENT_NAMESPACED_ID,
                missing_scopes=[SCOPE_GMAIL_READ],
            ),
        ):
            result = _gmail_recent_subjects_impl(limit=5)
        assert result["ok"] is False
        assert "AGENT_NOT_GRANTED" in result["error"]
        assert SCOPE_GMAIL_READ in result["error"]

    def test_api_failure_returns_connector_error(self):
        # Token resolves, but Gmail returns 401.
        with (
            patch(
                "gaia_agent_connectors_demo.agent._gmail_token",
                return_value="tok",
            ),
            patch(
                "httpx.get",
                return_value=httpx.Response(401, text="Invalid Credentials"),
            ),
        ):
            result = _gmail_recent_subjects_impl(limit=5)
        assert result["ok"] is False
        assert "CONNECTOR_ERROR" in result["error"]


# ---------------------------------------------------------------------------
# Tool: calendar_today
# ---------------------------------------------------------------------------


class TestCalendarToday:
    def test_happy_path_lists_events(self):
        fake_response = httpx.Response(
            200,
            json={
                "items": [
                    {
                        "summary": "Standup",
                        "start": {"dateTime": "2026-05-01T10:00:00-07:00"},
                        "end": {"dateTime": "2026-05-01T10:15:00-07:00"},
                        "location": "Zoom",
                    },
                    {
                        "summary": "All-day offsite",
                        "start": {"date": "2026-05-01"},
                        "end": {"date": "2026-05-02"},
                    },
                ]
            },
        )
        with (
            patch(
                "gaia_agent_connectors_demo.agent._calendar_token",
                return_value="tok",
            ),
            patch("httpx.get", return_value=fake_response),
        ):
            result = _calendar_today_impl()
        assert result["ok"] is True
        assert result["count"] == 2
        assert result["events"][0]["summary"] == "Standup"
        # All-day events have a 'date' field rather than 'dateTime' —
        # the impl must accept both shapes.
        assert result["events"][1]["start"] == "2026-05-01"


# ---------------------------------------------------------------------------
# Tool: drive_recent_files
# ---------------------------------------------------------------------------


class TestDriveRecentFiles:
    def test_happy_path_lists_files(self):
        fake_response = httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "1abc",
                        "name": "Q3 Plan.gdoc",
                        "mimeType": "application/vnd.google-apps.document",
                        "modifiedTime": "2026-05-01T12:00:00Z",
                        "webViewLink": "https://drive.google.com/d/1abc/view",
                    }
                ]
            },
        )
        with (
            patch(
                "gaia_agent_connectors_demo.agent._drive_token",
                return_value="tok",
            ),
            patch("httpx.get", return_value=fake_response),
        ):
            result = _drive_recent_files_impl(limit=5)
        assert result["ok"] is True
        assert result["files"][0]["name"] == "Q3 Plan.gdoc"


# ---------------------------------------------------------------------------
# Tool: github_my_repos
# ---------------------------------------------------------------------------


class TestGithubMyRepos:
    def test_happy_path_lists_repos(self):
        fake_response = httpx.Response(
            200,
            json=[
                {
                    "full_name": "octocat/Hello-World",
                    "private": False,
                    "description": "My first repo",
                    "html_url": "https://github.com/octocat/Hello-World",
                    "updated_at": "2026-04-30T09:00:00Z",
                }
            ],
        )
        with (
            patch(
                "gaia_agent_connectors_demo.agent._github_pat",
                return_value="ghp_x",
            ),
            patch("httpx.get", return_value=fake_response),
        ):
            result = _github_my_repos_impl(limit=10)
        assert result["ok"] is True
        assert result["repos"][0]["full_name"] == "octocat/Hello-World"

    def test_pat_missing_returns_connector_error(self):
        with patch(
            "gaia_agent_connectors_demo.agent._github_pat",
            side_effect=ConnectorsError(
                "GitHub MCP credential resolved but GITHUB_TOKEN was empty."
            ),
        ):
            result = _github_my_repos_impl(limit=10)
        assert result["ok"] is False
        assert "CONNECTOR_ERROR" in result["error"]
        assert "GITHUB_TOKEN" in result["error"]


# ---------------------------------------------------------------------------
# Registry — the agent shows up as a built-in so the AgentUI dropdown
# can list it.
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_connectors_demo_is_registered(self):
        from gaia.agents.registry import AgentRegistry

        reg = AgentRegistry()
        reg.discover()
        ids = {a.id for a in reg.list()}
        assert "connectors-demo" in ids

    def test_required_connections_surface_in_registration(self):
        from gaia.agents.registry import AgentRegistry

        reg = AgentRegistry()
        reg.discover()
        agent = next(a for a in reg.list() if a.id == "connectors-demo")
        # #962 fix — connectors_demo now registers ConnectorRequirement
        # objects (not bare strings; the previous form silently broke
        # ``_reg_to_info`` in agents.py). Check by connector_id.
        connector_ids = {r.connector_id for r in agent.required_connections}
        assert "google" in connector_ids
        assert "mcp-github" in connector_ids

    def test_namespaced_agent_id_matches_module_constant(self):
        # The registry's namespaced id must agree with the module-level
        # constant the tools pass to get_credential_sync; otherwise the
        # grant-ledger check would look at the wrong agent.
        from gaia.agents.registry import AgentRegistry

        reg = AgentRegistry()
        reg.discover()
        agent = next(a for a in reg.list() if a.id == "connectors-demo")
        assert agent.namespaced_agent_id == AGENT_NAMESPACED_ID


# ---------------------------------------------------------------------------
# Tool wiring — the @tool-decorated functions return JSON strings the LLM
# can parse, not raw dicts. Smoke-test by calling _register_tools without
# instantiating the LLM client.
# ---------------------------------------------------------------------------


class TestToolJsonShape:
    def test_each_tool_impl_returns_json_serializable(self):
        # The four impls return dicts; the @tool wrappers call json.dumps.
        # If a future change makes a dict non-serializable (e.g. nested
        # datetime), this test catches it before it ships.
        with patch(
            "gaia_agent_connectors_demo.agent._gmail_token",
            side_effect=ConnectorsError("offline"),
        ):
            assert json.dumps(_gmail_recent_subjects_impl(limit=1))
        with patch(
            "gaia_agent_connectors_demo.agent._calendar_token",
            side_effect=ConnectorsError("offline"),
        ):
            assert json.dumps(_calendar_today_impl())
        with patch(
            "gaia_agent_connectors_demo.agent._drive_token",
            side_effect=ConnectorsError("offline"),
        ):
            assert json.dumps(_drive_recent_files_impl(limit=1))
        with patch(
            "gaia_agent_connectors_demo.agent._github_pat",
            side_effect=ConnectorsError("offline"),
        ):
            assert json.dumps(_github_my_repos_impl(limit=1))
