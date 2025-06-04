import pytest
import socket
import time
import logging
from contextlib import contextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

<<<<<<< HEAD
def is_port_in_use(port, host='localhost'):
=======

def is_port_in_use(port, host="localhost"):
>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0

<<<<<<< HEAD
=======

>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
@pytest.fixture(scope="session", autouse=True)
def check_mcp_server():
    """Check if the MCP server is running before running integration tests."""
    # Port that MCP server uses
    mcp_port = 9876
<<<<<<< HEAD
    
    if not is_port_in_use(mcp_port):
        pytest.skip(f"MCP server not running on port {mcp_port}. Skipping integration tests.")
    
    logger.info("MCP server is running, proceeding with integration tests")
    return True

=======

    if not is_port_in_use(mcp_port):
        pytest.skip(
            f"MCP server not running on port {mcp_port}. Skipping integration tests."
        )

    logger.info("MCP server is running, proceeding with integration tests")
    return True


>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
@pytest.fixture(scope="session")
def integration_test_marker():
    """Mark tests as integration tests."""
    return True

<<<<<<< HEAD
=======

>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
<<<<<<< HEAD
        "integration: mark test as an integration test that requires the MCP server"
    )

=======
        "integration: mark test as an integration test that requires the MCP server",
    )


>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
def pytest_collection_modifyitems(config, items):
    """Skip integration tests if --skip-integration flag is provided."""
    if config.getoption("--skip-integration"):
        skip_integration = pytest.mark.skip(reason="--skip-integration option provided")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)

<<<<<<< HEAD
=======

>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
def pytest_addoption(parser):
    """Add custom command line options to pytest."""
    parser.addoption(
        "--skip-integration",
        action="store_true",
        default=False,
<<<<<<< HEAD
        help="Skip integration tests"
    )
=======
        help="Skip integration tests",
    )
>>>>>>> c22cf8c (Blender Agent, Agent Framework and Notebook Example (#582))
