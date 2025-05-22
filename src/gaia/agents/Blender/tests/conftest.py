import pytest
import socket
import time
import logging
from contextlib import contextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def is_port_in_use(port, host="localhost"):
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


@pytest.fixture(scope="session", autouse=True)
def check_mcp_server():
    """Check if the MCP server is running before running integration tests."""
    # Port that MCP server uses
    mcp_port = 9876

    if not is_port_in_use(mcp_port):
        pytest.skip(
            f"MCP server not running on port {mcp_port}. Skipping integration tests."
        )

    logger.info("MCP server is running, proceeding with integration tests")
    return True


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
    """Skip integration tests if --skip-integration flag is provided."""
    if config.getoption("--skip-integration"):
        skip_integration = pytest.mark.skip(reason="--skip-integration option provided")
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
