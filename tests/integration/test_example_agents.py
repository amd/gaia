#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration tests for example agents.

These tests actually run the agents and validate their responses.
Tests require Lemonade server to be running.
"""

import pytest
import sys
import os
from pathlib import Path
import tempfile
import shutil

# Add examples directory to Python path
examples_dir = Path(__file__).parent.parent.parent / "examples"
sys.path.insert(0, str(examples_dir))

# Check if Lemonade server is available
LEMONADE_AVAILABLE = False
try:
    from gaia.llm.lemonade_client import LemonadeClient
    client = LemonadeClient()
    client.get_system_info()
    LEMONADE_AVAILABLE = True
except Exception:
    pass

requires_lemonade = pytest.mark.skipif(
    not LEMONADE_AVAILABLE,
    reason="Lemonade server not running - start with: lemonade-server serve"
)


@requires_lemonade
class TestNotesAgent:
    """Test notes_agent.py with actual execution."""

    def test_agent_creates_and_lists_notes(self):
        """Test that NotesAgent can create and retrieve notes."""
        from notes_agent import NotesAgent

        # Create agent with temp database
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_notes.db")
            agent = NotesAgent(db_path=db_path)

            # Create a note
            result = agent.process_query("Create a note called 'Integration Test' with content 'Testing GAIA'")

            # Validate response structure
            assert result.get("status") == "success", f"Query failed: {result.get('error', 'Unknown error')}"
            assert "result" in result

            response_text = result.get("result", "").lower()
            assert "note" in response_text or "created" in response_text, "Response doesn't mention note creation"

            # List notes to verify creation
            result = agent.process_query("Show me all my notes")
            assert result.get("status") == "success"
            assert "integration test" in result.get("result", "").lower()

            agent.close_db()


@requires_lemonade
class TestProductMockupAgent:
    """Test product_mockup_agent.py with actual execution."""

    def test_agent_generates_html(self):
        """Test that ProductMockupAgent generates HTML files."""
        from product_mockup_agent import ProductMockupAgent

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = ProductMockupAgent(output_dir=tmpdir)

            # Generate a mockup
            result = agent.process_query(
                "Create a landing page for 'TestApp' with features: Authentication, API, Dashboard"
            )

            # Validate response
            assert result.get("status") == "success", f"Query failed: {result.get('error')}"

            # Verify HTML file was created
            html_files = list(Path(tmpdir).glob("*.html"))
            assert len(html_files) > 0, "No HTML file was generated"

            # Verify HTML content has required elements
            html_content = html_files[0].read_text()
            assert "<!DOCTYPE html>" in html_content, "Missing DOCTYPE"
            assert "TestApp" in html_content or "testapp" in html_content.lower(), "Product name not in HTML"
            assert "tailwindcss" in html_content.lower(), "Tailwind CSS not included"


@requires_lemonade
class TestFileWatcherAgent:
    """Test file_watcher_agent.py with actual execution."""

    def test_agent_watches_directory(self):
        """Test that FileWatcherAgent can watch directories."""
        from file_watcher_agent import FileWatcherAgent
        import time

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create agent watching temp directory
            agent = FileWatcherAgent(watch_dir=tmpdir)

            # Verify agent initialized
            assert agent is not None
            assert len(agent.watching_directories) > 0, "Agent not watching any directories"

            # Create a test file
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("Hello from integration test")

            # Give watcher time to detect file
            time.sleep(2)

            # Verify file was processed
            assert len(agent.processed_files) > 0, "No files were processed"
            assert any(f["name"] == "test.txt" for f in agent.processed_files), "test.txt not in processed files"

            agent.stop_all_watchers()


class TestHardwareAdvisorAgent:
    """Test hardware_advisor_agent.py structure (requires system info)."""

    def test_import_and_structure(self):
        """Test that HardwareAdvisorAgent has correct structure."""
        from hardware_advisor_agent import HardwareAdvisorAgent

        required_methods = ["_get_system_prompt", "_register_tools", "_get_gpu_info"]
        for method in required_methods:
            assert hasattr(HardwareAdvisorAgent, method), f"Missing method: {method}"


class TestMCPAgents:
    """Test MCP-based agents (require external MCP servers - test structure only)."""

    def test_weather_agent_structure(self):
        """Test WeatherAgent has correct structure."""
        from weather_agent import WeatherAgent
        assert hasattr(WeatherAgent, "_get_system_prompt")
        assert hasattr(WeatherAgent, "_register_tools")

    def test_mcp_config_agent_structure(self):
        """Test MCPAgent has correct structure."""
        from mcp_config_based_agent import MCPAgent
        assert hasattr(MCPAgent, "_get_system_prompt")
        assert hasattr(MCPAgent, "_register_tools")

    def test_time_agent_structure(self):
        """Test TimeAgent has correct structure."""
        from mcp_time_server_agent import TimeAgent
        assert hasattr(TimeAgent, "_get_system_prompt")
        assert hasattr(TimeAgent, "_register_tools")


class TestRAGDocAgent:
    """Test rag_doc_agent.py structure (requires documents)."""

    def test_import_and_structure(self):
        """Test that DocAgent has correct structure."""
        from rag_doc_agent import DocAgent

        required_methods = ["_get_system_prompt", "_register_tools"]
        for method in required_methods:
            assert hasattr(DocAgent, method), f"Missing method: {method}"


class TestSDAgentExample:
    """Test sd_agent_example.py structure."""

    def test_import(self):
        """Test that SD example can be imported."""
        import sd_agent_example
        assert sd_agent_example is not None


class TestWindowsSystemHealthAgent:
    """Test mcp_windows_system_health_agent.py structure (Windows-specific)."""

    def test_import_and_structure(self):
        """Test that WindowsSystemHealthAgent has correct structure."""
        from mcp_windows_system_health_agent import WindowsSystemHealthAgent
        assert hasattr(WindowsSystemHealthAgent, "_get_system_prompt")
        assert hasattr(WindowsSystemHealthAgent, "_register_tools")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
