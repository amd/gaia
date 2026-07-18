#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for JiraAgent (issue #1991).

Covers the HTTP tool implementations at the mocked Atlassian boundary:
- ``_get_jira_credentials`` URL normalization and caching
- ``_discover_jira_config`` endpoint fan-out and response parsing
- ``_execute_jira_search_async`` request shape and result mapping
- ``_execute_jira_create_async`` payload building, project auto-discovery,
  and the Atlassian 400 error-rewriting branch
- ``_execute_jira_update_async`` field-diff payload building
- System-prompt JQL guidance (config-driven vs. fallback)

Per the #1655 rule, every mocked HTTP interaction asserts the OUTGOING
request shape (method, URL path, auth header, params, JSON payload) —
never merely that a mock was called. The JQL template helper is covered
separately in tests/unit/agents/test_jql_templates.py and not duplicated
here.

No network, no Atlassian, no LLM — the aiohttp boundary is replaced with
a recording fake session.
"""

import asyncio
import base64
import os
import sys
from types import SimpleNamespace

import aiohttp
import pytest

# Add package directory to path (hub-package test convention)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia_agent_jira import agent as agent_module  # noqa: E402
from gaia_agent_jira.agent import JiraAgent  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402

SITE = "https://example.atlassian.net"
EMAIL = "user@example.com"
TOKEN = "api-token-123"
EXPECTED_AUTH = "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()

SEARCH_URL = f"{SITE}/rest/api/3/search/jql"
ISSUE_URL = f"{SITE}/rest/api/3/issue"
PROJECT_URL = f"{SITE}/rest/api/3/project"
DEFAULT_SEARCH_FIELDS = "key,summary,status,priority,issuetype,assignee"


# ---------------------------------------------------------------------------
# Fake aiohttp boundary
# ---------------------------------------------------------------------------


class FakeResponse:
    """Async-context-manager response double for aiohttp."""

    def __init__(self, status=200, json_data=None, json_exc=None):
        self.status = status
        self._json_data = json_data
        self._json_exc = json_exc

    async def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data

    def raise_for_status(self):
        if self.status >= 400:
            # real_url is required by ClientResponseError.__str__
            request_info = SimpleNamespace(real_url="https://fake.test/")
            raise aiohttp.ClientResponseError(
                request_info=request_info,
                history=(),
                status=self.status,
                message=f"HTTP {self.status}",
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records every outgoing request; serves responses from a route table.

    Routes are keyed by ``(METHOD, full_url)``. An un-routed request is a
    test failure — the agent must never make a call the test didn't expect.
    """

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = []

    def _handle(self, method, url, **kwargs):
        self.requests.append(
            SimpleNamespace(
                method=method,
                url=url,
                headers=kwargs.get("headers"),
                params=kwargs.get("params"),
                json=kwargs.get("json"),
            )
        )
        key = (method, url)
        if key not in self.routes:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.routes[key]

    def get(self, url, **kwargs):
        return self._handle("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._handle("PUT", url, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def jira_env(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_SITE_URL", SITE)
    monkeypatch.setenv("ATLASSIAN_API_KEY", TOKEN)
    monkeypatch.setenv("ATLASSIAN_USER_EMAIL", EMAIL)


@pytest.fixture
def make_agent():
    """Build a JiraAgent without a Lemonade server; clean up the tool registry."""
    _TOOL_REGISTRY.clear()

    def _make(**kwargs):
        kwargs.setdefault("silent_mode", True)
        kwargs.setdefault("skip_lemonade", True)
        return JiraAgent(**kwargs)

    yield _make
    _TOOL_REGISTRY.clear()


@pytest.fixture
def agent(jira_env, make_agent):
    return make_agent()


@pytest.fixture
def http(monkeypatch):
    """Replace the aiohttp module used by the agent with a recording fake."""

    def _install(routes=None):
        session = FakeSession(routes)
        monkeypatch.setattr(
            agent_module,
            "aiohttp",
            SimpleNamespace(ClientSession=lambda: session),
        )
        return session

    return _install


def run(coro):
    return asyncio.run(coro)


def assert_auth_headers(request):
    assert request.headers["Authorization"] == EXPECTED_AUTH
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept"] == "application/json"


# ---------------------------------------------------------------------------
# _get_jira_credentials — URL normalization
# ---------------------------------------------------------------------------


class TestGetJiraCredentials:
    def test_missing_env_raises_actionable_error(self, monkeypatch, make_agent):
        for var in (
            "ATLASSIAN_SITE_URL",
            "ATLASSIAN_API_KEY",
            "ATLASSIAN_USER_EMAIL",
        ):
            monkeypatch.delenv(var, raising=False)
        agent = make_agent()
        with pytest.raises(ValueError) as excinfo:
            agent._get_jira_credentials()
        msg = str(excinfo.value)
        assert "ATLASSIAN_SITE_URL" in msg
        assert "ATLASSIAN_API_KEY" in msg
        assert "ATLASSIAN_USER_EMAIL" in msg

    def test_partial_env_raises(self, monkeypatch, make_agent):
        monkeypatch.setenv("ATLASSIAN_SITE_URL", SITE)
        monkeypatch.setenv("ATLASSIAN_API_KEY", TOKEN)
        monkeypatch.delenv("ATLASSIAN_USER_EMAIL", raising=False)
        with pytest.raises(ValueError):
            make_agent()._get_jira_credentials()

    def test_trailing_slash_stripped(self, monkeypatch, jira_env, make_agent):
        monkeypatch.setenv("ATLASSIAN_SITE_URL", f"{SITE}/")
        site_url, api_key, user_email = make_agent()._get_jira_credentials()
        assert site_url == SITE
        assert api_key == TOKEN
        assert user_email == EMAIL

    def test_bare_host_gets_https_prefix(self, monkeypatch, jira_env, make_agent):
        monkeypatch.setenv("ATLASSIAN_SITE_URL", "example.atlassian.net")
        site_url, _, _ = make_agent()._get_jira_credentials()
        assert site_url == "https://example.atlassian.net"

    def test_explicit_http_scheme_preserved(self, monkeypatch, jira_env, make_agent):
        monkeypatch.setenv("ATLASSIAN_SITE_URL", "http://jira.internal:8080/")
        site_url, _, _ = make_agent()._get_jira_credentials()
        assert site_url == "http://jira.internal:8080"

    def test_credentials_cached_after_first_read(
        self, monkeypatch, jira_env, make_agent
    ):
        agent = make_agent()
        first = agent._get_jira_credentials()
        monkeypatch.setenv("ATLASSIAN_SITE_URL", "https://other.atlassian.net")
        assert agent._get_jira_credentials() == first


# ---------------------------------------------------------------------------
# _discover_jira_config
# ---------------------------------------------------------------------------


class TestDiscoverJiraConfig:
    def test_discovery_request_shape_and_parsing(self, agent, http):
        session = http(
            {
                ("GET", PROJECT_URL): FakeResponse(
                    json_data=[
                        {"key": "ENG", "name": "Engineering", "id": "10000"},
                        {"key": "OPS", "name": "Operations", "id": "10001"},
                    ]
                ),
                ("GET", f"{SITE}/rest/api/3/issuetype"): FakeResponse(
                    json_data=[
                        {"name": "Task"},
                        {"name": "Sub-task", "subtask": True},
                        {"name": "Bug", "subtask": False},
                    ]
                ),
                ("GET", f"{SITE}/rest/api/3/status"): FakeResponse(
                    json_data=[{"name": "To Do"}, {"name": "Done"}]
                ),
                ("GET", f"{SITE}/rest/api/3/priority"): FakeResponse(
                    json_data=[{"name": "High"}, {"name": "Low"}]
                ),
            }
        )

        config = run(agent._discover_jira_config())

        # Outgoing request shape: all four discovery endpoints, GET, authed
        assert [(r.method, r.url) for r in session.requests] == [
            ("GET", PROJECT_URL),
            ("GET", f"{SITE}/rest/api/3/issuetype"),
            ("GET", f"{SITE}/rest/api/3/status"),
            ("GET", f"{SITE}/rest/api/3/priority"),
        ]
        for request in session.requests:
            assert_auth_headers(request)

        # Parsing: only key/name kept, subtask types filtered out
        assert config["projects"] == [
            {"key": "ENG", "name": "Engineering"},
            {"key": "OPS", "name": "Operations"},
        ]
        assert config["issue_types"] == ["Task", "Bug"]
        assert config["statuses"] == ["To Do", "Done"]
        assert config["priorities"] == ["High", "Low"]
        assert agent._jira_config == config

    def test_non_200_endpoint_leaves_section_empty(self, agent, http):
        http(
            {
                ("GET", PROJECT_URL): FakeResponse(
                    json_data=[{"key": "ENG", "name": "Engineering"}]
                ),
                ("GET", f"{SITE}/rest/api/3/issuetype"): FakeResponse(status=500),
                ("GET", f"{SITE}/rest/api/3/status"): FakeResponse(
                    json_data=[{"name": "Done"}]
                ),
                ("GET", f"{SITE}/rest/api/3/priority"): FakeResponse(status=403),
            }
        )

        config = run(agent._discover_jira_config())

        assert config["projects"] == [{"key": "ENG", "name": "Engineering"}]
        assert config["issue_types"] == []
        assert config["statuses"] == ["Done"]
        assert config["priorities"] == []

    def test_cached_config_short_circuits_http(self, jira_env, make_agent, monkeypatch):
        cached = {
            "projects": [{"key": "ENG", "name": "Engineering"}],
            "issue_types": ["Bug"],
            "statuses": ["Done"],
            "priorities": ["High"],
        }
        agent = make_agent(jira_config=cached)

        def _fail():
            raise AssertionError("HTTP session must not be created for cached config")

        monkeypatch.setattr(
            agent_module, "aiohttp", SimpleNamespace(ClientSession=_fail)
        )
        assert run(agent._discover_jira_config()) == cached

    def test_initialize_returns_empty_config_on_failure(self, monkeypatch, make_agent):
        for var in (
            "ATLASSIAN_SITE_URL",
            "ATLASSIAN_API_KEY",
            "ATLASSIAN_USER_EMAIL",
        ):
            monkeypatch.delenv(var, raising=False)
        agent = make_agent()
        config = agent.initialize()
        assert config == {
            "projects": [],
            "issue_types": [],
            "statuses": [],
            "priorities": [],
        }


# ---------------------------------------------------------------------------
# _execute_jira_search_async
# ---------------------------------------------------------------------------


class TestJiraSearch:
    def test_search_request_shape_defaults(self, agent, http):
        session = http(
            {("GET", SEARCH_URL): FakeResponse(json_data={"issues": [], "total": 0})}
        )
        jql = "assignee = currentUser()"

        run(agent._execute_jira_search_async(jql))

        assert len(session.requests) == 1
        request = session.requests[0]
        assert request.method == "GET"
        assert request.url == SEARCH_URL
        assert_auth_headers(request)
        # No maxResults when not requested; default field list always sent
        assert request.params == {"jql": jql, "fields": DEFAULT_SEARCH_FIELDS}

    def test_search_request_shape_with_max_results_and_fields(self, agent, http):
        session = http(
            {("GET", SEARCH_URL): FakeResponse(json_data={"issues": [], "total": 0})}
        )

        run(
            agent._execute_jira_search_async(
                "project = ENG", max_results=25, fields="key,summary"
            )
        )

        assert session.requests[0].params == {
            "jql": "project = ENG",
            "maxResults": 25,
            "fields": "key,summary",
        }

    def test_search_result_parsing(self, agent, http):
        http(
            {
                ("GET", SEARCH_URL): FakeResponse(
                    json_data={
                        "total": 7,
                        "issues": [
                            {
                                "key": "ENG-1",
                                "fields": {
                                    "summary": "Fix login",
                                    "status": {"name": "In Progress"},
                                    "priority": {"name": "High"},
                                    "issuetype": {"name": "Bug"},
                                    "assignee": {"displayName": "Ada Lovelace"},
                                },
                            },
                            {
                                "key": "ENG-2",
                                "fields": {
                                    "summary": "Bare issue",
                                    "status": None,
                                    "priority": None,
                                    "issuetype": None,
                                    "assignee": None,
                                },
                            },
                        ],
                    }
                )
            }
        )

        result = run(agent._execute_jira_search_async("project = ENG"))

        assert result["total"] == 7
        assert result["jql"] == "project = ENG"
        assert result["issues"][0] == {
            "key": "ENG-1",
            "summary": "Fix login",
            "status": "In Progress",
            "priority": "High",
            "type": "Bug",
            "assignee": "Ada Lovelace",
        }
        # Null nested fields must map to None / "Unassigned", not crash
        assert result["issues"][1] == {
            "key": "ENG-2",
            "summary": "Bare issue",
            "status": None,
            "priority": None,
            "type": None,
            "assignee": "Unassigned",
        }

    def test_search_total_falls_back_to_issue_count(self, agent, http):
        http(
            {
                ("GET", SEARCH_URL): FakeResponse(
                    json_data={
                        "issues": [{"key": "ENG-1", "fields": {"summary": "One"}}]
                    }
                )
            }
        )
        result = run(agent._execute_jira_search_async("project = ENG"))
        assert result["total"] == 1

    def test_search_http_error_raises(self, agent, http):
        http({("GET", SEARCH_URL): FakeResponse(status=401)})
        with pytest.raises(aiohttp.ClientResponseError):
            run(agent._execute_jira_search_async("project = ENG"))

    def test_sync_wrapper_converts_failure_to_error_dict(self, agent, http):
        http({("GET", SEARCH_URL): FakeResponse(status=401)})
        result = agent._execute_jira_search("project = ENG")
        assert result["status"] == "error"
        assert result["error"].startswith("Async execution failed")


# ---------------------------------------------------------------------------
# _execute_jira_create_async
# ---------------------------------------------------------------------------


class TestJiraCreate:
    def test_create_request_shape_with_explicit_project(self, agent, http):
        session = http(
            {
                ("POST", ISSUE_URL): FakeResponse(
                    json_data={"key": "ENG-42", "id": "10042"}
                )
            }
        )

        result = run(
            agent._execute_jira_create_async(
                summary="Fix login",
                description="",
                issue_type="Bug",
                priority=None,
                project="ENG",
            )
        )

        # Exactly one call — no project auto-discovery when project is given
        assert len(session.requests) == 1
        request = session.requests[0]
        assert request.method == "POST"
        assert request.url == ISSUE_URL
        assert_auth_headers(request)
        assert request.json == {
            "fields": {
                "project": {"key": "ENG"},
                "summary": "Fix login",
                "description": "Created via GAIA",  # empty description defaulted
                "issuetype": {"name": "Bug"},
            }
        }

        assert result == {
            "status": "success",
            "created": True,
            "key": "ENG-42",
            "id": "10042",
            "url": f"{SITE}/browse/ENG-42",
            "metadata": {
                "project": "ENG",
                "issue_type": "Bug",
                "priority": None,
                "summary": "Fix login",
            },
        }

    def test_create_payload_includes_priority_and_description(self, agent, http):
        session = http(
            {("POST", ISSUE_URL): FakeResponse(json_data={"key": "ENG-1", "id": "1"})}
        )

        run(
            agent._execute_jira_create_async(
                summary="Add SSO",
                description="Support SAML login",
                issue_type="Story",
                priority="High",
                project="ENG",
            )
        )

        assert session.requests[0].json == {
            "fields": {
                "project": {"key": "ENG"},
                "summary": "Add SSO",
                "description": "Support SAML login",
                "issuetype": {"name": "Story"},
                "priority": {"name": "High"},
            }
        }

    def test_create_auto_discovers_first_project(self, agent, http):
        session = http(
            {
                ("GET", PROJECT_URL): FakeResponse(
                    json_data=[
                        {"key": "OPS", "name": "Operations"},
                        {"key": "ENG", "name": "Engineering"},
                    ]
                ),
                ("POST", ISSUE_URL): FakeResponse(
                    json_data={"key": "OPS-7", "id": "7"}
                ),
            }
        )

        result = run(
            agent._execute_jira_create_async(summary="New task", project=None)
        )

        assert [(r.method, r.url) for r in session.requests] == [
            ("GET", PROJECT_URL),
            ("POST", ISSUE_URL),
        ]
        assert_auth_headers(session.requests[0])
        assert session.requests[1].json["fields"]["project"] == {"key": "OPS"}
        assert result["key"] == "OPS-7"
        assert result["metadata"]["project"] == "OPS"

    def test_create_no_projects_available_errors_without_post(self, agent, http):
        session = http({("GET", PROJECT_URL): FakeResponse(json_data=[])})

        result = run(agent._execute_jira_create_async(summary="Orphan"))

        assert result == {"status": "error", "error": "No projects available"}
        assert [(r.method, r.url) for r in session.requests] == [("GET", PROJECT_URL)]

    def test_create_400_rewrites_atlassian_error_messages(self, agent, http):
        http(
            {
                ("POST", ISSUE_URL): FakeResponse(
                    status=400,
                    json_data={
                        "errorMessages": [
                            "Specify a valid project ID",
                            "Field 'issuetype' is invalid",
                        ]
                    },
                )
            }
        )

        result = run(
            agent._execute_jira_create_async(
                summary="Bad", issue_type="Wibble", project="ENG"
            )
        )

        assert result["status"] == "error"
        assert (
            "Bad Request: Specify a valid project ID; Field 'issuetype' is invalid"
            in result["error"]
        )
        # Rewritten error must include recovery guidance for the LLM
        assert "jira_search" in result["error"]

    def test_create_400_without_error_messages_names_issue_type(self, agent, http):
        http(
            {
                ("POST", ISSUE_URL): FakeResponse(
                    status=400, json_data={"errorMessages": []}
                )
            }
        )

        result = run(
            agent._execute_jira_create_async(
                summary="Bad", issue_type="Wibble", project="ENG"
            )
        )

        assert result["status"] == "error"
        assert "Invalid issue type 'Wibble'" in result["error"]

    def test_create_400_with_unparseable_body_uses_fallback_error(self, agent, http):
        http(
            {
                ("POST", ISSUE_URL): FakeResponse(
                    status=400, json_exc=ValueError("not json")
                )
            }
        )

        result = run(
            agent._execute_jira_create_async(
                summary="Bad", issue_type="Wibble", project="ENG"
            )
        )

        assert result["status"] == "error"
        assert "'Wibble' may not be valid" in result["error"]

    def test_create_non_400_http_error_raises(self, agent, http):
        http({("POST", ISSUE_URL): FakeResponse(status=500)})
        with pytest.raises(aiohttp.ClientResponseError):
            run(agent._execute_jira_create_async(summary="Boom", project="ENG"))


# ---------------------------------------------------------------------------
# _execute_jira_update_async — field-diff payload building
# ---------------------------------------------------------------------------


class TestJiraUpdate:
    def test_update_without_issue_key_errors_without_http(self, agent, http):
        session = http()
        result = run(agent._execute_jira_update_async(issue_key=""))
        assert result == {"status": "error", "error": "Issue key is required"}
        assert session.requests == []

    def test_update_without_fields_errors_without_http(self, agent, http):
        session = http()
        result = run(agent._execute_jira_update_async(issue_key="ENG-7"))
        assert result == {"status": "error", "error": "No fields to update"}
        assert session.requests == []

    def test_update_payload_contains_only_provided_fields(self, agent, http):
        session = http(
            {("PUT", f"{ISSUE_URL}/ENG-7"): FakeResponse(json_data={})}
        )

        result = run(
            agent._execute_jira_update_async(
                issue_key="ENG-7", summary="New title", priority="High"
            )
        )

        assert len(session.requests) == 1
        request = session.requests[0]
        assert request.method == "PUT"
        assert request.url == f"{ISSUE_URL}/ENG-7"
        assert_auth_headers(request)
        # Field diff: only the provided fields, priority wrapped as {"name": ...}
        assert request.json == {
            "fields": {"summary": "New title", "priority": {"name": "High"}}
        }

        assert result["status"] == "success"
        assert result["updated"] is True
        assert result["key"] == "ENG-7"
        assert result["url"] == f"{SITE}/browse/ENG-7"
        assert result["metadata"]["updated_fields"] == ["summary", "priority"]

    def test_update_payload_with_all_fields(self, agent, http):
        session = http(
            {("PUT", f"{ISSUE_URL}/OPS-3"): FakeResponse(json_data={})}
        )

        run(
            agent._execute_jira_update_async(
                issue_key="OPS-3",
                summary="S",
                description="D",
                priority="Low",
                status="Done",
            )
        )

        assert session.requests[0].json == {
            "fields": {
                "summary": "S",
                "description": "D",
                "priority": {"name": "Low"},
                "status": {"name": "Done"},
            }
        }

    def test_update_http_error_raises(self, agent, http):
        http({("PUT", f"{ISSUE_URL}/ENG-404"): FakeResponse(status=404)})
        with pytest.raises(aiohttp.ClientResponseError):
            run(agent._execute_jira_update_async(issue_key="ENG-404", summary="X"))

    def test_sync_wrapper_converts_failure_to_error_dict(self, agent, http):
        http({("PUT", f"{ISSUE_URL}/ENG-404"): FakeResponse(status=404)})
        result = agent._execute_jira_update("ENG-404", "X", None, None, None)
        assert result["status"] == "error"
        assert result["error"].startswith("Async execution failed")


# ---------------------------------------------------------------------------
# JQL guidance in the system prompt + registered tool surface
# ---------------------------------------------------------------------------


class TestSystemPromptAndTools:
    def test_prompt_includes_discovered_configuration(self, make_agent):
        agent = make_agent(
            jira_config={
                "projects": [
                    {"key": "ENG", "name": "Engineering"},
                    {"key": "OPS", "name": "Operations"},
                ],
                "issue_types": ["Bug", "Story"],
                "statuses": ["To Do", "In Review"],
                "priorities": ["Highest", "Lowest"],
            }
        )
        prompt = agent._get_system_prompt()

        assert "Available projects are ENG, OPS" in prompt
        assert "Available issue types are Bug, Story" in prompt
        assert "Available priorities are Highest, Lowest" in prompt
        assert "Available statuses are To Do, In Review" in prompt
        # Discovered config replaces the hardcoded fallback guidance
        assert 'issuetype = "Idea"' not in prompt

    def test_prompt_falls_back_to_defaults_without_config(self, make_agent):
        prompt = make_agent()._get_system_prompt()
        assert 'issuetype = "Idea"' in prompt
        assert 'status = "Parking lot"' in prompt

    def test_registered_tools_and_search_default_jql(self, agent):
        assert set(_TOOL_REGISTRY) >= {"jira_search", "jira_create", "jira_update"}

        import inspect

        search_sig = inspect.signature(_TOOL_REGISTRY["jira_search"]["function"])
        assert (
            search_sig.parameters["jql"].default
            == "created >= -30d ORDER BY updated DESC"
        )

        create_sig = inspect.signature(_TOOL_REGISTRY["jira_create"]["function"])
        assert create_sig.parameters["issue_type"].default == "Task"
        assert _TOOL_REGISTRY["jira_create"]["parameters"]["summary"]["required"]
        assert _TOOL_REGISTRY["jira_update"]["parameters"]["issue_key"]["required"]
