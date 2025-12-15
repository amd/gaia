#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for external MCP service tools (Context7 and Perplexity).

This test suite validates:
- Context7Service: Documentation search functionality
- PerplexityService: Web search functionality
- ExternalToolsMixin: Tool registration and integration
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.code.agent import CodeAgent
from gaia.mcp.external_services import (
    Context7Service,
    ExternalMCPService,
    PerplexityService,
    get_context7_service,
    get_perplexity_service,
)


class TestExternalMCPService(unittest.TestCase):
    """Test the base ExternalMCPService class."""

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_call_tool_success(self, mock_run):
        """Test successful tool call."""
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

        service = ExternalMCPService(command=["test", "command"])
        result = service.call_tool("test_tool", {"arg": "value"})

        self.assertIn("content", result)
        self.assertEqual(result["content"][0]["text"], "Test result")
        mock_run.assert_called_once()

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


class TestContext7Service(unittest.TestCase):
    """Test Context7Service functionality."""

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_documentation_success(self, mock_run):
        """Test successful documentation search."""
        # Mock successful Context7 response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "content": [
                            {"type": "text", "text": "Documentation for useState hook"}
                        ]
                    },
                }
            ),
            stderr="",
        )

        service = Context7Service()
        result = service.search_documentation("useState hook", library="react")

        self.assertTrue(result["success"])
        self.assertIn("useState hook", result["documentation"])

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_documentation_no_library(self, mock_run):
        """Test documentation search without library specified."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "Generic docs"}]},
                }
            ),
            stderr="",
        )

        service = Context7Service()
        result = service.search_documentation("async patterns")

        self.assertTrue(result["success"])
        self.assertIn("documentation", result)

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
        """Test library ID resolution."""
        # Mock actual Context7 response format
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
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
                    },
                }
            ),
            stderr="",
        )

        service = Context7Service()
        result = service.resolve_library_id("react")

        self.assertEqual(result, "/facebook/react")


class TestPerplexityService(unittest.TestCase):
    """Test PerplexityService functionality."""

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_web_success(self, mock_run):
        """Test successful web search."""
        # Mock successful Perplexity response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Python best practices include...",
                            }
                        ]
                    },
                }
            ),
            stderr="",
        )

        service = PerplexityService(api_key="test_key")
        result = service.search_web("Python best practices")

        self.assertTrue(result["success"])
        self.assertIn("best practices", result["answer"])

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


class TestExternalToolsMixin(unittest.TestCase):
    """Test ExternalToolsMixin integration with Code Agent."""

    def setUp(self):
        """Set up test fixtures."""
        self.agent = CodeAgent(silent_mode=True, max_steps=5)
        self.agent._register_tools()

    def tearDown(self):
        """Clean up test fixtures."""
        _TOOL_REGISTRY.clear()

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
        """Test search_documentation tool execution."""
        # Mock successful response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "Test docs"}]},
                }
            ),
            stderr="",
        )

        tool_func = _TOOL_REGISTRY["search_documentation"]["function"]
        result = tool_func("test query", library="test-lib")

        self.assertTrue(result["success"])
        self.assertIn("documentation", result)

    @patch("gaia.mcp.external_services.subprocess.run")
    def test_search_web_tool_execution(self, mock_run):
        """Test search_web tool execution."""
        # Mock successful response
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": "Test answer"}]},
                }
            ),
            stderr="",
        )

        tool_func = _TOOL_REGISTRY["search_web"]["function"]
        result = tool_func("test query")

        self.assertTrue(result["success"])
        self.assertIn("answer", result)


class TestServiceSingletons(unittest.TestCase):
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
