"""pytest fixtures for MCP integration tests.

Tests real MCP servers via npx - no mocks or test servers.
"""

import shutil

import pytest


@pytest.fixture(scope="session")
def npx_available():
    """Check if npx is available for MCP servers."""
    if shutil.which("npx") is None:
        pytest.skip("npx not available - required for MCP server tests")
    return True


@pytest.fixture(scope="function")
def temp_config_file(tmp_path):
    """Create temp config file path for MCPConfig."""
    return str(tmp_path / "mcp_servers.json")
