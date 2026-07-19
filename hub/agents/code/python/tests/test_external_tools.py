#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for external MCP service tools (Context7 and Perplexity).

This test suite validates:
- Context7Service: Documentation search functionality
- PerplexityService: Web search functionality
- ExternalToolsMixin: Tool registration and integration

Beyond the happy-path return values, these tests assert the OUTGOING
subprocess.run() call: the npx argv, the env, and the JSON-RPC request
piped to stdin (method, tool name, argument keys). A typo'd tool name
(e.g. "get-library-doc") or argument key (e.g. "context7CompatibleLibrary")
would still return whatever canned stdout a naive mock provides, so
asserting only the parsed result — as the previous version of this file
did — lets that typo pass CI while every real call to the npx service
fails (#1993).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia_agent_code.agent import CodeAgent

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.mcp.external_services import (
    Context7Service,
    ExternalMCPService,
    PerplexityService,
    get_context7_service,
    get_perplexity_service,
)

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _rpc_result_response(result):
    """Build a MagicMock CompletedProcess wrapping a canned JSON-RPC result."""
    return MagicMock(
        returncode=0,
        stdout=json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}),
        stderr="",
    )


def _rpc_responder(by_method=None, by_tool=None):
    """Build a subprocess.run side_effect that routes on the JSON-RPC
    request piped to stdin, rather than returning one fixed response for
    every call regardless of what was asked for.

    Requests are routed by "method" (e.g. "tools/list") or, for
    "tools/call", by the tool name in params["name"] (e.g.
    "get-library-docs"). A request that doesn't match the routing table
    gets a nonzero-returncode response — surfaced by the production code
    as an {"error": ...} result — instead of silently matching whatever
    canned response happens to be configured, so a typo'd tool/method name
    fails the test's result assertions instead of passing unnoticed.

    Every call is recorded on the returned callable's `.calls` list as
    {"command": argv, "env": env, "request": parsed JSON-RPC request} so
    tests can assert the exact outgoing payload.
    """
    by_method = by_method or {}
    by_tool = by_tool or {}
    calls = []

    def _run(command, *, input, capture_output, text, env, timeout, check):
        request = json.loads(input)
        calls.append({"command": command, "env": env, "request": request})
        method = request.get("method")
        if method == "tools/call":
            tool_name = request.get("params", {}).get("name")
            if tool_name in by_tool:
                return _rpc_result_response(by_tool[tool_name])
            return MagicMock(
                returncode=1,
                stdout="",
                stderr=f"unrouted tools/call for tool_name={tool_name!r}",
            )
        if method in by_method:
            return _rpc_result_response(by_method[method])
        return MagicMock(
            returncode=1, stdout="", stderr=f"unrouted JSON-RPC method={method!r}"
        )

    _run.calls = calls
    return _run


class _Context7IsolationMixin:
    """Context7Service.check_availability() caches its result on the class
    (across every instance, for the life of the process) and its cache /
    rate-limiter persist to real files under ~/.gaia/cache/context7. Left
    alone, one test's cached "unavailable" result or exhausted rate-limit
    state leaks into every test that runs after it in the same process —
    this is what made test_search_documentation_success and
    test_search_documentation_no_library fail when the full suite ran
    (passing in isolation, failing after an earlier test poisoned the
    class-level cache). Reset the cache and redirect persistence to a
    temp dir so each test is hermetic.
    """

    def setUp(self):
        super().setUp()
        Context7Service._availability_checked = False
        Context7Service._is_available = False

        import gaia.mcp.external_services as external_services_module

        self._singleton_patcher = patch.object(
            external_services_module, "_context7_service", None
        )
        self._singleton_patcher.start()

        self._tmp_home = tempfile.TemporaryDirectory()
        self._home_patcher = patch(
            "gaia.mcp.context7_cache.Path.home",
            return_value=Path(self._tmp_home.name),
        )
        self._home_patcher.start()

    def tearDown(self):
        self._home_patcher.stop()
        self._tmp_home.cleanup()
        self._singleton_patcher.stop()
        Context7Service._availability_checked = False
        Context7Service._is_available = False
        super().tearDown()


class TestExternalMCPService(unittest.TestCase):
    """Test the base ExternalMCPService class."""

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_call_tool_success(self, mock_run):
        """Test successful tool call — and that the outgoing subprocess call
        carries the right argv, env, and JSON-RPC tools/call payload."""
        # Mock successful subprocess response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "Test result"}]},
                }
            ),
            stderr="",
        )

        service = ExternalMCPService(command=["test", "command"], env={"FOO": "bar"})
        result = service.call_tool("test_tool", {"arg": "value"})

        self.assertIn("content", result)
        self.assertEqual(result["content"][0]["text"], "Test result")
        mock_run.assert_called_once()

        # The #1993 regression this guards against: a bare
        # assert_called_once() only proves subprocess.run was invoked, not
        # that the argv, env, or JSON-RPC payload it received were correct.
        call_args, call_kwargs = mock_run.call_args
        self.assertEqual(call_args[0], ["test", "command"])
        self.assertEqual(call_kwargs["env"]["FOO"], "bar")
        self.assertTrue(call_kwargs["text"])
        self.assertEqual(call_kwargs["timeout"], service.timeout)

        request = json.loads(call_kwargs["input"])
        self.assertEqual(request["method"], "tools/call")
        self.assertEqual(
            request["params"], {"name": "test_tool", "arguments": {"arg": "value"}}
        )

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_call_tool_error(self, mock_run):
        """Test tool call with error response."""
        # Mock error subprocess response
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Test error")

        service = ExternalMCPService(command=["test", "command"])
        result = service.call_tool("test_tool", {"arg": "value"})

        self.assertIn("error", result)
        self.assertIn("Test error", result["error"])

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_call_tool_timeout(self, mock_run):
        """Test tool call timeout."""
        # Mock timeout
        from subprocess import TimeoutExpired

        mock_run.side_effect = TimeoutExpired(cmd=["test"], timeout=30)

        service = ExternalMCPService(command=["test", "command"], timeout=30)
        result = service.call_tool("test_tool", {"arg": "value"})

        self.assertIn("error", result)
        self.assertIn("timed out", result["error"])

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_call_tool_invalid_json(self, mock_run):
        """Test tool call with invalid JSON response."""
        # Mock invalid JSON response
        mock_run.return_value = MagicMock(
            returncode=0, stdout="not valid json", stderr=""
        )

        service = ExternalMCPService(command=["test", "command"])
        result = service.call_tool("test_tool", {"arg": "value"})

        self.assertIn("error", result)
        self.assertIn("Invalid JSON", result["error"])

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_list_tools_sends_tools_list_request(self, mock_run):
        """list_tools() must send a "tools/list" JSON-RPC request — not
        "tools/call" — with empty params."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "echo"}]}}
            ),
            stderr="",
        )

        service = ExternalMCPService(command=["test", "command"])
        tools = service.list_tools()

        self.assertEqual(tools, [{"name": "echo"}])
        _, call_kwargs = mock_run.call_args
        request = json.loads(call_kwargs["input"])
        self.assertEqual(request["method"], "tools/list")
        self.assertEqual(request["params"], {})


class TestContext7Service(_Context7IsolationMixin, unittest.TestCase):
    """Test Context7Service functionality."""

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_documentation_success(self, mock_run):
        """Test successful documentation search — and that the
        get-library-docs tools/call payload carries the right argument
        keys, not just that some content came back."""
        mock_run.side_effect = _rpc_responder(
            by_method={"tools/list": {"tools": [{"name": "get-library-docs"}]}},
            by_tool={
                "resolve-library-id": {
                    "content": [{"type": "text", "text": "No matches found."}]
                },
                "get-library-docs": {
                    "content": [
                        {"type": "text", "text": "Documentation for useState hook"}
                    ]
                },
            },
        )

        service = Context7Service()
        result = service.search_documentation("useState hook", library="react")

        self.assertTrue(result["success"])
        self.assertIn("useState hook", result["documentation"])

        # argv is the real npx invocation, not a stand-in command.
        first_call = mock_run.side_effect.calls[0]
        self.assertEqual(first_call["command"], ["npx", "-y", "@upstash/context7-mcp"])

        docs_call = next(
            c
            for c in mock_run.side_effect.calls
            if c["request"]["params"].get("name") == "get-library-docs"
        )
        self.assertEqual(docs_call["request"]["method"], "tools/call")
        # The canned resolve-library-id response has no parseable library
        # ID, so resolution fails and no context7CompatibleLibraryID key
        # is sent — see test_search_documentation_forwards_resolved_library_id
        # below for the case where resolution succeeds.
        self.assertEqual(
            docs_call["request"]["params"]["arguments"], {"topic": "useState hook"}
        )

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_documentation_forwards_resolved_library_id(self, mock_run):
        """When resolve-library-id resolves a library, get-library-docs
        must receive it as context7CompatibleLibraryID — the exact
        argument key #1993 flagged as unverified."""
        mock_run.side_effect = _rpc_responder(
            by_method={"tools/list": {"tools": [{"name": "get-library-docs"}]}},
            by_tool={
                "resolve-library-id": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "- Title: React\n"
                                "- Context7-compatible library ID: /facebook/react\n"
                                "- Code Snippets: 500\n"
                            ),
                        }
                    ]
                },
                "get-library-docs": {
                    "content": [{"type": "text", "text": "React docs"}]
                },
            },
        )

        service = Context7Service()
        result = service.search_documentation("useState hook", library="react")

        self.assertTrue(result["success"])

        docs_call = next(
            c
            for c in mock_run.side_effect.calls
            if c["request"]["params"].get("name") == "get-library-docs"
        )
        self.assertEqual(
            docs_call["request"]["params"]["arguments"],
            {
                "topic": "useState hook",
                "context7CompatibleLibraryID": "/facebook/react",
            },
        )

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_documentation_no_library(self, mock_run):
        """Test documentation search without library specified."""
        mock_run.side_effect = _rpc_responder(
            by_method={"tools/list": {"tools": [{"name": "get-library-docs"}]}},
            by_tool={
                "get-library-docs": {
                    "content": [{"type": "text", "text": "Generic docs"}]
                }
            },
        )

        service = Context7Service()
        result = service.search_documentation("async patterns")

        self.assertTrue(result["success"])
        self.assertIn("documentation", result)

        # No library was passed, so no resolve-library-id call should have
        # happened and get-library-docs should carry only "topic".
        tool_names = [
            c["request"]["params"].get("name")
            for c in mock_run.side_effect.calls
            if c["request"]["method"] == "tools/call"
        ]
        self.assertEqual(tool_names, ["get-library-docs"])
        docs_call = mock_run.side_effect.calls[-1]
        self.assertEqual(
            docs_call["request"]["params"]["arguments"], {"topic": "async patterns"}
        )

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_documentation_error(self, mock_run):
        """Test documentation search with error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="API error")

        service = Context7Service()
        result = service.search_documentation("test query")

        self.assertFalse(result["success"])
        self.assertIn("error", result)

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_resolve_library_id(self, mock_run):
        """Test library ID resolution — and that resolve-library-id is
        called with the "libraryName" argument key the real npx package
        expects."""
        mock_run.side_effect = _rpc_responder(
            by_tool={
                "resolve-library-id": {
                    "content": [
                        {
                            "type": "text",
                            "text": """Available Libraries:

Each result includes:
- Library ID: Context7-compatible identifier (format: /org/project)
- Name: Library or package name

----------

- Title: React
- Context7-compatible library ID: /facebook/react
- Description: A JavaScript library for building user interfaces
- Code Snippets: 500
- Source Reputation: High""",
                        }
                    ]
                }
            }
        )

        service = Context7Service()
        result = service.resolve_library_id("react")

        self.assertEqual(result, "/facebook/react")

        self.assertEqual(len(mock_run.side_effect.calls), 1)
        call = mock_run.side_effect.calls[0]
        self.assertEqual(call["command"], ["npx", "-y", "@upstash/context7-mcp"])
        self.assertEqual(call["request"]["method"], "tools/call")
        self.assertEqual(
            call["request"]["params"],
            {"name": "resolve-library-id", "arguments": {"libraryName": "react"}},
        )

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_context7_service_passes_api_key_via_env(self, mock_run):
        """The CONTEXT7_API_KEY constructor arg must reach the npx
        subprocess's environment — not just be stored on the instance."""
        mock_run.side_effect = _rpc_responder(
            by_method={"tools/list": {"tools": [{"name": "get-library-docs"}]}}
        )

        service = Context7Service(api_key="ctx-key-123")
        service.list_tools()

        self.assertEqual(len(mock_run.side_effect.calls), 1)
        call = mock_run.side_effect.calls[0]
        self.assertEqual(call["command"], ["npx", "-y", "@upstash/context7-mcp"])
        self.assertEqual(call["env"]["CONTEXT7_API_KEY"], "ctx-key-123")


class TestPerplexityService(unittest.TestCase):
    """Test PerplexityService functionality."""

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_web_success(self, mock_run):
        """Test successful web search — and that perplexity_ask is called
        with the right argv, env, and "messages" argument shape."""
        mock_run.side_effect = _rpc_responder(
            by_tool={
                "perplexity_ask": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Python best practices include...",
                        }
                    ]
                }
            }
        )

        service = PerplexityService(api_key="test_key")
        result = service.search_web("Python best practices")

        self.assertTrue(result["success"])
        self.assertIn("best practices", result["answer"])

        self.assertEqual(len(mock_run.side_effect.calls), 1)
        call = mock_run.side_effect.calls[0]
        self.assertEqual(call["command"], ["npx", "-y", "server-perplexity-ask"])
        self.assertEqual(call["env"]["PERPLEXITY_API_KEY"], "test_key")
        self.assertEqual(call["request"]["method"], "tools/call")
        self.assertEqual(
            call["request"]["params"],
            {
                "name": "perplexity_ask",
                "arguments": {
                    "messages": [{"role": "user", "content": "Python best practices"}]
                },
            },
        )

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_web_no_api_key(self, mock_run):
        """Test web search without API key."""
        with patch.dict(os.environ, {}, clear=True):
            service = PerplexityService()
            # Service should be created but with warning logged
            self.assertIsNotNone(service)

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_web_error(self, mock_run):
        """Test web search with error."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="API rate limit"
        )

        service = PerplexityService(api_key="test_key")
        result = service.search_web("test query")

        self.assertFalse(result["success"])
        self.assertIn("error", result)


class TestExternalToolsMixin(_Context7IsolationMixin, unittest.TestCase):
    """Test ExternalToolsMixin integration with Code Agent."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()
        super().tearDown()

    def test_external_tools_registered(self):
        """Test that external tools are registered."""
        self.assertIn("search_documentation", _TOOL_REGISTRY)
        self.assertIn("search_web", _TOOL_REGISTRY)

    def test_search_documentation_tool_signature(self):
        """Test search_documentation tool signature."""
        tool = _TOOL_REGISTRY["search_documentation"]
        self.assertEqual(tool["name"], "search_documentation")
        self.assertIn("description", tool)
        self.assertIn("parameters", tool)
        self.assertIn("query", tool["parameters"])
        self.assertIn("library", tool["parameters"])

    def test_search_web_tool_signature(self):
        """Test search_web tool signature."""
        tool = _TOOL_REGISTRY["search_web"]
        self.assertEqual(tool["name"], "search_web")
        self.assertIn("description", tool)
        self.assertIn("parameters", tool)
        self.assertIn("query", tool["parameters"])

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_documentation_tool_execution(self, mock_run):
        """Test search_documentation tool execution — through the full
        tool-registry path, asserting the get-library-docs call shape."""
        mock_run.side_effect = _rpc_responder(
            by_method={"tools/list": {"tools": [{"name": "get-library-docs"}]}},
            by_tool={
                "resolve-library-id": {
                    "content": [{"type": "text", "text": "No matches."}]
                },
                "get-library-docs": {
                    "content": [{"type": "text", "text": "Test docs"}]
                },
            },
        )

        tool_func = _TOOL_REGISTRY["search_documentation"]["function"]
        result = tool_func("test query", library="test-lib")

        self.assertTrue(result["success"])
        self.assertIn("documentation", result)

        docs_call = next(
            c
            for c in mock_run.side_effect.calls
            if c["request"]["params"].get("name") == "get-library-docs"
        )
        self.assertEqual(
            docs_call["request"]["params"]["arguments"]["topic"], "test query"
        )

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_web_tool_execution(self, mock_run):
        """Test search_web tool execution — through the full tool-registry
        path, asserting the perplexity_ask call shape."""
        mock_run.side_effect = _rpc_responder(
            by_tool={
                "perplexity_ask": {
                    "content": [{"type": "text", "text": "Test answer"}]
                }
            }
        )

        tool_func = _TOOL_REGISTRY["search_web"]["function"]
        result = tool_func("test query")

        self.assertTrue(result["success"])
        self.assertIn("answer", result)

        self.assertEqual(len(mock_run.side_effect.calls), 1)
        call = mock_run.side_effect.calls[0]
        self.assertEqual(call["command"], ["npx", "-y", "server-perplexity-ask"])
        self.assertEqual(
            call["request"]["params"]["arguments"],
            {"messages": [{"role": "user", "content": "test query"}]},
        )


class TestServiceSingletons(_Context7IsolationMixin, unittest.TestCase):
    """Test singleton service instances."""

    def test_context7_singleton(self):
        """Test that get_context7_service returns same instance."""
        service1 = get_context7_service()
        service2 = get_context7_service()
        self.assertIs(service1, service2)

    def test_perplexity_singleton(self):
        """Test that get_perplexity_service returns same instance."""
        service1 = get_perplexity_service()
        service2 = get_perplexity_service()
        self.assertIs(service1, service2)


if __name__ == "__main__":
    unittest.main()
