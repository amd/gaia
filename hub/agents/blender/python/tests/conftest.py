# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import logging
import socket

import pytest

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def is_port_in_use(port, host="localhost"):
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


# Port that the Blender MCP server uses
MCP_PORT = 9876


@pytest.fixture(scope="session")
def integration_test_marker():
    """Mark tests as integration tests."""
    return True


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test that requires the MCP server",
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests when requested or when the MCP server is down.

    Mocked unit tests always run; only ``integration``-marked tests need the
    live Blender MCP server (test_mcp_client.py self-skips via its fixture).
    """
    if config.getoption("--skip-integration"):
        skip_integration = pytest.mark.skip(reason="--skip-integration option provided")
    elif not is_port_in_use(MCP_PORT):
        skip_integration = pytest.mark.skip(
            reason=f"MCP server not running on port {MCP_PORT}"
        )
    else:
        return
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


def pytest_addoption(parser):
    """Add custom command line options to pytest."""
    parser.addoption(
        "--skip-integration",
        action="store_true",
        default=False,
        help="Skip integration tests",
    )
