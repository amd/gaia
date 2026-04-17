#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration tests for example agents.

These tests actually run the agents and validate their responses.
Tests require Lemonade server to be running.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Add examples directory to Python path
examples_dir = Path(__file__).parent.parent.parent / "examples"
sys.path.insert(0, str(examples_dir))

# Compact model used across every LLM-backed integration test so the stx
# runner only needs to pull/load one ~4B model.  Matches the model our CI
# workflow loads via installer/scripts/start-lemonade.ps1.
TEST_MODEL_ID = os.environ.get("GAIA_TEST_MODEL", "Qwen3-4B-Instruct-2507-GGUF")


def _check_lemonade() -> bool:
    """Check if Lemonade server is available."""
    try:
        from gaia.llm.lemonade_client import LemonadeClient

        client = LemonadeClient()
        client.get_system_info()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def lemonade_available():
    """Session-scoped fixture that checks Lemonade server once."""
    return _check_lemonade()


requires_lemonade = pytest.mark.skipif(
    not _check_lemonade(),
    reason="Lemonade server not running - start with: lemonade-server serve",
)


@requires_lemonade
class TestNotesAgent:
    """Test notes_agent.py with actual execution."""

    def test_agent_creates_and_lists_notes(self):
        """Test that NotesAgent can create and retrieve notes."""
        from notes_agent import NotesAgent

        # Create agent with temp database.  We use try/finally so that the
        # SQLite connection is always closed before the TemporaryDirectory
        # tries to delete the file — Windows will otherwise raise
        # PermissionError (WinError 32) when the db is still locked.
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_notes.db")
            agent = NotesAgent(db_path=db_path, model_id=TEST_MODEL_ID)
            try:
                # Create a note
                result = agent.process_query(
                    "Create a note called 'Integration Test' with content 'Testing GAIA'"
                )

                # Validate response structure
                assert (
                    result.get("status") == "success"
                ), f"Query failed: {result.get('error', 'Unknown error')}"
                assert "result" in result

                response_text = result.get("result", "").lower()
                assert (
                    "note" in response_text or "created" in response_text
                ), "Response doesn't mention note creation"

                # List notes to verify creation
                result = agent.process_query("Show me all my notes")
                assert result.get("status") == "success"
                assert "integration test" in result.get("result", "").lower()
            finally:
                agent.close_db()


@requires_lemonade
class TestProductMockupAgent:
    """Test product_mockup_agent.py with actual execution."""

    def test_agent_generates_html(self):
        """Test that ProductMockupAgent generates HTML files."""
        from product_mockup_agent import ProductMockupAgent

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = ProductMockupAgent(output_dir=tmpdir, model_id=TEST_MODEL_ID)

            # Generate a mockup
            result = agent.process_query(
                "Create a landing page for 'TestApp' with features: Authentication, API, Dashboard"
            )

            # Validate response
            assert (
                result.get("status") == "success"
            ), f"Query failed: {result.get('error')}"

            # Verify HTML file was created
            html_files = list(Path(tmpdir).glob("*.html"))
            assert len(html_files) > 0, "No HTML file was generated"

            # Verify HTML content has required elements
            html_content = html_files[0].read_text()
            assert "<!DOCTYPE html>" in html_content, "Missing DOCTYPE"
            assert (
                "TestApp" in html_content or "testapp" in html_content.lower()
            ), "Product name not in HTML"
            assert "tailwindcss" in html_content.lower(), "Tailwind CSS not included"


@requires_lemonade
class TestFileWatcherAgent:
    """Test file_watcher_agent.py with actual execution."""

    def test_agent_watches_directory(self):
        """Test that FileWatcherAgent can watch directories."""
        from file_watcher_agent import FileWatcherAgent

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create agent watching temp directory.  We use try/finally so
            # that the watcher is always torn down before TemporaryDirectory
            # attempts to delete the folder — Windows will otherwise raise
            # PermissionError if the observer still holds a handle to it.
            agent = FileWatcherAgent(watch_dir=tmpdir, model_id=TEST_MODEL_ID)
            try:
                # Verify agent initialized
                assert agent is not None
                assert (
                    len(agent.watching_directories) > 0
                ), "Agent not watching any directories"

                # Create a test file
                test_file = Path(tmpdir) / "test.txt"
                test_file.write_text("Hello from integration test")

                # Wait for watcher to detect file (retry to avoid flakiness on slow CI)
                detected = False
                for _ in range(10):
                    time.sleep(1)
                    if any(f["name"] == "test.txt" for f in agent.processed_files):
                        detected = True
                        break

                # Verify file was processed
                assert (
                    detected
                ), "test.txt was not detected by file watcher within 10 seconds"
            finally:
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
