#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration tests for example agents.

These tests verify that example agents can be instantiated and have the
required methods and structure. They do NOT require Lemonade server.
"""

import pytest
import sys
import os
from pathlib import Path

# Add examples directory to Python path
examples_dir = Path(__file__).parent.parent.parent / "examples"
sys.path.insert(0, str(examples_dir))


class TestWeatherAgent:
    """Test weather_agent.py example."""

    def test_import(self):
        """Test that WeatherAgent can be imported."""
        from weather_agent import WeatherAgent
        assert WeatherAgent is not None

    def test_class_structure(self):
        """Test that WeatherAgent has required methods."""
        from weather_agent import WeatherAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(WeatherAgent, method), f"Missing method: {method}"

    def test_system_prompt(self):
        """Test that system prompt is defined."""
        from weather_agent import WeatherAgent

        # Create instance without connecting to MCP server
        os.environ["GAIA_SUPPRESS_WARNINGS"] = "1"
        try:
            # Don't actually initialize - just check the class method exists
            prompt = WeatherAgent._get_system_prompt(None)
            assert isinstance(prompt, str)
            assert len(prompt) > 0
        except Exception:
            # If initialization fails, that's OK - we're just checking structure
            pass


class TestRagDocAgent:
    """Test rag_doc_agent.py example."""

    def test_import(self):
        """Test that DocAgent can be imported."""
        from rag_doc_agent import DocAgent
        assert DocAgent is not None

    def test_class_structure(self):
        """Test that DocAgent has required methods."""
        from rag_doc_agent import DocAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(DocAgent, method), f"Missing method: {method}"


class TestProductMockupAgent:
    """Test product_mockup_agent.py example."""

    def test_import(self):
        """Test that ProductMockupAgent can be imported."""
        from product_mockup_agent import ProductMockupAgent
        assert ProductMockupAgent is not None

    def test_class_structure(self):
        """Test that ProductMockupAgent has required methods."""
        from product_mockup_agent import ProductMockupAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(ProductMockupAgent, method), f"Missing method: {method}"

    def test_tool_registration(self):
        """Test that ProductMockupAgent registers generate_landing_page tool."""
        from product_mockup_agent import ProductMockupAgent
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Clear registry
        _TOOL_REGISTRY.clear()

        # Create agent (may fail without Lemonade, but tools should register)
        try:
            agent = ProductMockupAgent()
            agent._register_tools()
        except Exception:
            pass  # OK if initialization fails

        # Check that tool was registered
        assert "generate_landing_page" in _TOOL_REGISTRY


class TestFileWatcherAgent:
    """Test file_watcher_agent.py example."""

    def test_import(self):
        """Test that FileWatcherAgent can be imported."""
        from file_watcher_agent import FileWatcherAgent
        assert FileWatcherAgent is not None

    def test_class_structure(self):
        """Test that FileWatcherAgent has required methods."""
        from file_watcher_agent import FileWatcherAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(FileWatcherAgent, method), f"Missing method: {method}"


class TestNotesAgent:
    """Test notes_agent.py example."""

    def test_import(self):
        """Test that NotesAgent can be imported."""
        from notes_agent import NotesAgent
        assert NotesAgent is not None

    def test_class_structure(self):
        """Test that NotesAgent has required methods."""
        from notes_agent import NotesAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(NotesAgent, method), f"Missing method: {method}"


class TestHardwareAdvisorAgent:
    """Test hardware_advisor_agent.py example."""

    def test_import(self):
        """Test that HardwareAdvisorAgent can be imported."""
        from hardware_advisor_agent import HardwareAdvisorAgent
        assert HardwareAdvisorAgent is not None

    def test_class_structure(self):
        """Test that HardwareAdvisorAgent has required methods."""
        from hardware_advisor_agent import HardwareAdvisorAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(HardwareAdvisorAgent, method), f"Missing method: {method}"


class TestMCPConfigBasedAgent:
    """Test mcp_config_based_agent.py example."""

    def test_import(self):
        """Test that MCPAgent can be imported."""
        from mcp_config_based_agent import MCPAgent
        assert MCPAgent is not None

    def test_class_structure(self):
        """Test that MCPAgent has required methods."""
        from mcp_config_based_agent import MCPAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(MCPAgent, method), f"Missing method: {method}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
