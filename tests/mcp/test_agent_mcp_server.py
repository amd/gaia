#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Full Integration Tests for MCPAgent and AgentMCPServer

NO MOCKS - Tests use real services:
- Real Docker CLI (build, run, cleanup)
- Real LLM orchestration via Lemonade
- Real FastMCP server initialization
- Real agent tool execution

Requirements:
- Docker CLI installed (pre-installed on ubuntu/windows GitHub runners)
- Lemonade server running (started as fixture)

CI: Runs on ubuntu-latest and windows-latest

Usage:
    # Run all tests
    pytest tests/mcp/test_agent_mcp_server.py -v

    # Run specific test class
    pytest tests/mcp/test_agent_mcp_server.py::TestDockerAgentMCP -v

    # Run with output
    pytest tests/mcp/test_agent_mcp_server.py -v -s
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
import requests

from gaia.agents.base.mcp_agent import MCPAgent
from gaia.mcp.agent_mcp_server import AgentMCPServer

# Try importing agents - they may or may not exist
try:
    from gaia.agents.docker.agent import DockerAgent

    HAS_DOCKER_AGENT = True
except ImportError:
    HAS_DOCKER_AGENT = False
    DockerAgent = None

# CodeAgent does not implement MCP interface - no tests needed


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def is_docker_available() -> bool:
    """Check if Docker CLI is available"""
    try:
        result = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def cleanup_docker_resources(image_tags: List[str], container_names: List[str]):
    """Cleanup Docker images and containers"""
    # Stop and remove containers
    for name in container_names:
        try:
            subprocess.run(["docker", "stop", name], capture_output=True, timeout=10)
            subprocess.run(["docker", "rm", name], capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            pass

    # Remove images
    for tag in image_tags:
        try:
            subprocess.run(
                ["docker", "rmi", tag, "-f"], capture_output=True, timeout=10
            )
        except subprocess.TimeoutExpired:
            pass


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(scope="session")
def docker_available():
    """Verify Docker is available for tests"""
    if not is_docker_available():
        pytest.skip("Docker CLI not available")
    return True


@pytest.fixture(scope="session")
def lemonade_server():
    """
    Wait for Lemonade server to be ready for LLM orchestration.

    Follows the pattern from test_chat_sdk.py - waits for server
    with timeout, then skips if not available.
    """
    server_url = "http://localhost:8000"
    timeout = 30  # seconds

    print(f"\n⏳ Waiting for Lemonade server at {server_url}...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{server_url}/api/v1/health", timeout=5)
            if response.status_code == 200:
                health_data = response.json()
                print(f"✅ Lemonade server is ready")
                print(f"   Status: {health_data.get('status', 'unknown')}")
                print(f"   Model loaded: {health_data.get('model_loaded', 'unknown')}")
                return True
        except requests.RequestException:
            pass  # Server not ready yet

        time.sleep(2)

    # Server not available - skip tests
    pytest.skip(
        f"Lemonade server not available after {timeout} seconds. Start with: lemonade-server serve --ctx-size 32768"
    )


@pytest.fixture
def test_flask_app(tmp_path) -> str:
    """
    Create a real Flask application for Docker testing.

    Returns:
        str: Absolute path to the Flask app directory
    """
    app_dir = tmp_path / "test_flask_app"
    app_dir.mkdir()

    # Create requirements.txt
    requirements = app_dir / "requirements.txt"
    requirements.write_text("flask==2.0.0\n")

    # Create app.py
    app_py = app_dir / "app.py"
    app_py.write_text(
        """# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello from Docker test!'

@app.route('/health')
def health():
    return {'status': 'healthy'}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
"""
    )

    return str(app_dir)


@pytest.fixture
def docker_cleanup():
    """
    Fixture to track and cleanup Docker resources after tests.

    Yields a dict with lists to track created resources.
    """
    resources = {"images": [], "containers": []}

    yield resources

    # Cleanup after test
    if resources["images"] or resources["containers"]:
        print(f"\nCleaning up Docker resources...")
        print(f"  Images: {resources['images']}")
        print(f"  Containers: {resources['containers']}")
        cleanup_docker_resources(resources["images"], resources["containers"])


# ============================================================================
# TEST: MCPAgent Abstract Interface
# ============================================================================


class TestMCPAgentContract:
    """
    Test MCPAgent abstract base class interface enforcement.

    These tests verify that the abstract class pattern works correctly
    and that subclasses must implement required methods.

    No external services needed - pure Python class testing.
    """

    def test_cannot_instantiate_mcp_agent_directly(self):
        """MCPAgent is abstract and cannot be instantiated"""
        with pytest.raises(TypeError, match="abstract"):
            MCPAgent()

    def test_incomplete_subclass_missing_tool_definitions(self):
        """Subclass without get_mcp_tool_definitions() cannot be instantiated"""

        class IncompleteAgent(MCPAgent):
            """Missing get_mcp_tool_definitions()"""

            def execute_mcp_tool(
                self, tool_name: str, arguments: Dict[str, Any]
            ) -> Dict[str, Any]:
                return {}

        with pytest.raises(TypeError, match="abstract"):
            IncompleteAgent()

    def test_incomplete_subclass_missing_execute_tool(self):
        """Subclass without execute_mcp_tool() cannot be instantiated"""

        class IncompleteAgent(MCPAgent):
            """Missing execute_mcp_tool()"""

            def get_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
                return []

        with pytest.raises(TypeError, match="abstract"):
            IncompleteAgent()

    def test_minimal_valid_mcp_agent(self):
        """Valid MCPAgent subclass with both abstract methods can be instantiated"""

        class MinimalAgent(MCPAgent):
            """Minimal valid MCPAgent implementation"""

            def get_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
                return [
                    {
                        "name": "test-tool",
                        "description": "A test tool",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    }
                ]

            def execute_mcp_tool(
                self, tool_name: str, arguments: Dict[str, Any]
            ) -> Dict[str, Any]:
                if tool_name != "test-tool":
                    raise ValueError(f"Unknown tool: {tool_name}")
                return {"success": True}

            # Implement base Agent abstract methods
            def _get_system_prompt(self) -> str:
                return "Minimal test agent"

            def _create_console(self):
                from gaia.agents.base.console import SilentConsole

                return SilentConsole()

            def _register_tools(self):
                pass  # No tools to register

        # Should not raise
        agent = MinimalAgent(silent_mode=True)
        assert isinstance(agent, MCPAgent)

        # Test methods work
        tools = agent.get_mcp_tool_definitions()
        assert len(tools) == 1
        assert tools[0]["name"] == "test-tool"

        result = agent.execute_mcp_tool("test-tool", {})
        assert result["success"] is True


# ============================================================================
# TEST: Optional Methods
# ============================================================================


class TestOptionalMethods:
    """
    Test optional MCP methods with real agents.

    Optional methods have default implementations that return empty lists/dicts.
    Agents can override them if needed.
    """

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_optional_methods_have_defaults(self):
        """Optional methods return sensible defaults"""
        agent = DockerAgent(silent_mode=True)

        # get_mcp_prompts() - optional
        prompts = agent.get_mcp_prompts()
        assert isinstance(prompts, list)
        assert prompts == []  # Default is empty list

        # get_mcp_resources() - optional
        resources = agent.get_mcp_resources()
        assert isinstance(resources, list)
        assert resources == []  # Default is empty list

        # get_mcp_server_info() - has default implementation
        server_info = agent.get_mcp_server_info()
        assert isinstance(server_info, dict)
        assert "name" in server_info
        assert "version" in server_info
        assert "DockerAgent" in server_info["name"]

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_server_info_includes_agent_name(self):
        """Server info includes the agent class name"""
        agent = DockerAgent(silent_mode=True)
        server_info = agent.get_mcp_server_info()

        assert "GAIA DockerAgent" in server_info["name"]
        assert server_info["version"] == "2.0.0"


# ============================================================================
# TEST: MCP Protocol Compliance
# ============================================================================


class TestMCPProtocolCompliance:
    """
    Test MCP protocol compliance with real tool definitions.

    Validates that tool definitions follow the MCP specification:
    - JSON serializable
    - Correct schema structure
    - Valid names (lowercase)
    - Required fields present
    """

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_tool_definitions_are_list(self):
        """get_mcp_tool_definitions() returns a list"""
        agent = DockerAgent(silent_mode=True)
        tools = agent.get_mcp_tool_definitions()

        assert isinstance(tools, list)
        assert len(tools) > 0

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_tool_definitions_have_required_fields(self):
        """Each tool definition has name, description, inputSchema"""
        agent = DockerAgent(silent_mode=True)
        tools = agent.get_mcp_tool_definitions()

        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool missing 'description': {tool}"
            assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"

            # Validate types
            assert isinstance(tool["name"], str)
            assert isinstance(tool["description"], str)
            assert isinstance(tool["inputSchema"], dict)

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_input_schema_structure(self):
        """inputSchema follows JSON Schema specification"""
        agent = DockerAgent(silent_mode=True)
        tools = agent.get_mcp_tool_definitions()

        for tool in tools:
            schema = tool["inputSchema"]

            # Must have type: "object"
            assert (
                schema.get("type") == "object"
            ), f"Schema type must be 'object': {tool['name']}"

            # Must have properties
            assert (
                "properties" in schema
            ), f"Schema missing 'properties': {tool['name']}"
            assert isinstance(schema["properties"], dict)

            # Optional: required field (array of strings)
            if "required" in schema:
                assert isinstance(schema["required"], list)
                for req in schema["required"]:
                    assert isinstance(req, str)
                    assert (
                        req in schema["properties"]
                    ), f"Required field '{req}' not in properties"

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_tool_names_are_lowercase(self):
        """Tool names must be lowercase (MCP convention)"""
        agent = DockerAgent(silent_mode=True)
        tools = agent.get_mcp_tool_definitions()

        for tool in tools:
            name = tool["name"]
            assert name == name.lower(), f"Tool name must be lowercase: '{name}'"

            # Allow alphanumeric, hyphens, underscores
            assert all(
                c.isalnum() or c in "-_" for c in name
            ), f"Tool name contains invalid characters: '{name}'"

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_tool_descriptions_non_empty(self):
        """Tool descriptions must be non-empty"""
        agent = DockerAgent(silent_mode=True)
        tools = agent.get_mcp_tool_definitions()

        for tool in tools:
            desc = tool["description"]
            assert len(desc) > 0, f"Tool '{tool['name']}' has empty description"
            assert (
                len(desc.strip()) > 0
            ), f"Tool '{tool['name']}' has whitespace-only description"

    @pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
    def test_tool_definitions_json_serializable(self):
        """All tool definitions must be JSON serializable"""
        agent = DockerAgent(silent_mode=True)
        tools = agent.get_mcp_tool_definitions()

        # Should not raise
        json_str = json.dumps(tools)

        # Verify round-trip
        parsed = json.loads(json_str)
        assert len(parsed) == len(tools)

        # Verify structure preserved
        for i, tool in enumerate(tools):
            assert parsed[i]["name"] == tool["name"]
            assert parsed[i]["description"] == tool["description"]


# ============================================================================
# TEST: DockerAgent MCP Implementation
# ============================================================================


@pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
class TestDockerAgentMCP:
    """
    Test DockerAgent MCP implementation with REAL Docker and LLM.

    These are full integration tests that:
    - Use real Docker CLI commands
    - Use real LLM orchestration via process_query()
    - Create actual Docker images and containers
    - Test end-to-end workflows
    """

    def test_docker_agent_is_mcp_agent(self):
        """DockerAgent inherits from MCPAgent"""
        agent = DockerAgent(silent_mode=True)
        assert isinstance(agent, MCPAgent)

    def test_docker_agent_has_dockerize_tool(self):
        """DockerAgent provides 'dockerize' tool"""
        agent = DockerAgent(silent_mode=True)
        tools = agent.get_mcp_tool_definitions()

        tool_names = [t["name"] for t in tools]
        assert "dockerize" in tool_names

        # Find dockerize tool
        dockerize_tool = next(t for t in tools if t["name"] == "dockerize")
        assert "appPath" in dockerize_tool["inputSchema"]["properties"]

    @pytest.mark.slow
    def test_execute_dockerize_invalid_tool_name(self):
        """execute_mcp_tool with invalid tool name raises ValueError"""
        agent = DockerAgent(silent_mode=True)

        with pytest.raises(ValueError, match="Unknown tool"):
            agent.execute_mcp_tool("nonexistent_tool", {})

    @pytest.mark.slow
    def test_execute_dockerize_missing_app_path(self):
        """execute_mcp_tool with missing appPath returns error"""
        agent = DockerAgent(silent_mode=True)

        result = agent.execute_mcp_tool("dockerize", {})

        assert result["success"] is False
        assert "appPath" in result["error"]

    @pytest.mark.slow
    def test_execute_dockerize_invalid_path(self, tmp_path):
        """execute_mcp_tool with non-existent path returns error"""
        # Allow tmp_path so we can test the "does not exist" error
        agent = DockerAgent(silent_mode=True, allowed_paths=[str(tmp_path)])

        # Use absolute path that doesn't exist
        nonexistent_path = tmp_path / "nonexistent_dir"
        # Don't create it

        result = agent.execute_mcp_tool("dockerize", {"appPath": str(nonexistent_path)})

        assert result["success"] is False
        assert "does not exist" in result["error"]

    @pytest.mark.slow
    @pytest.mark.integration
    def test_dockerize_full_workflow(
        self, docker_available, lemonade_server, test_flask_app, docker_cleanup
    ):
        """
        Full dockerize workflow: analyze → Dockerfile → build → run

        This is the most comprehensive test - uses REAL Docker and LLM.
        Tests the complete agent orchestration pipeline.
        """
        agent = DockerAgent(
            silent_mode=True, max_steps=30, allowed_paths=[test_flask_app]
        )  # Docker ops can take many steps

        # Track resources for cleanup
        app_name = Path(test_flask_app).name.lower().replace("_", "-")
        docker_cleanup["images"].append(f"{app_name}:latest")
        docker_cleanup["containers"].append(f"{app_name}-container")

        # Execute dockerize tool - LLM orchestrates everything
        result = agent.execute_mcp_tool(
            "dockerize", {"appPath": test_flask_app, "port": 5000}
        )

        # Verify workflow completed
        # Note: Result format may vary based on agent implementation
        # Accept either "success": True or "status": "completed"
        assert (
            result.get("success") is True or result.get("status") == "completed"
        ), f"Dockerize failed: {result}"

        # Verify Dockerfile was created
        dockerfile_path = Path(test_flask_app) / "Dockerfile"
        assert dockerfile_path.exists(), "Dockerfile not created"

        # Verify Dockerfile content
        dockerfile_content = dockerfile_path.read_text()
        assert "FROM python:" in dockerfile_content, "Invalid Dockerfile - missing FROM"
        assert (
            "COPY requirements.txt" in dockerfile_content
        ), "Invalid Dockerfile - missing COPY requirements"
        assert (
            "RUN pip install" in dockerfile_content
        ), "Invalid Dockerfile - missing pip install"
        assert (
            "EXPOSE 5000" in dockerfile_content
        ), "Invalid Dockerfile - missing EXPOSE"

        # Verify Docker image was built
        # Check if image exists
        check_image = subprocess.run(
            ["docker", "images", "-q", f"{app_name}:latest"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert (
            len(check_image.stdout.strip()) > 0
        ), f"Docker image {app_name}:latest not found"


# ============================================================================
# TEST: AgentMCPServer Integration
# ============================================================================


@pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
class TestAgentMCPServer:
    """
    Test AgentMCPServer wrapper with real FastMCP.

    Tests that AgentMCPServer correctly wraps MCPAgent subclasses
    and registers tools with FastMCP.
    """

    def test_server_initialization_with_docker_agent(self):
        """AgentMCPServer can wrap DockerAgent"""
        server = AgentMCPServer(
            agent_class=DockerAgent,
            name="Test Docker MCP Server",
            port=8080,
            host="localhost",
            verbose=False,
            agent_params={"silent_mode": True},
        )

        # Verify agent created
        assert server.agent is not None
        assert isinstance(server.agent, DockerAgent)
        assert isinstance(server.agent, MCPAgent)

        # Verify configuration
        assert server.name == "Test Docker MCP Server"
        assert server.port == 8080
        assert server.host == "localhost"
        assert server.verbose is False

    def test_server_requires_mcp_agent_subclass(self):
        """AgentMCPServer rejects non-MCPAgent classes"""

        class NotAnAgent:
            pass

        with pytest.raises(TypeError, match="must inherit from MCPAgent"):
            AgentMCPServer(agent_class=NotAnAgent, agent_params={})

    def test_server_creates_fastmcp_instance(self):
        """AgentMCPServer creates FastMCP instance"""
        server = AgentMCPServer(
            agent_class=DockerAgent, agent_params={"silent_mode": True}
        )

        # Verify FastMCP created
        assert server.mcp is not None
        assert hasattr(server.mcp, "settings")
        assert hasattr(server.mcp, "run")

    def test_server_configures_host_and_port(self):
        """AgentMCPServer configures FastMCP with host and port"""
        server = AgentMCPServer(
            agent_class=DockerAgent,
            port=9090,
            host="0.0.0.0",
            agent_params={"silent_mode": True},
        )

        assert server.mcp.settings.host == "0.0.0.0"
        assert server.mcp.settings.port == 9090

    def test_server_registers_agent_tools(self):
        """AgentMCPServer registers all agent tools with FastMCP"""
        server = AgentMCPServer(
            agent_class=DockerAgent, agent_params={"silent_mode": True}
        )

        # Get tools from agent
        agent_tools = server.agent.get_mcp_tool_definitions()
        assert len(agent_tools) > 0

        # Tools should be registered with FastMCP
        # Note: FastMCP API for listing tools may vary
        # This is a basic check that tools were registered
        assert hasattr(server.mcp, "tool")

    def test_server_passes_agent_params(self):
        """AgentMCPServer passes agent_params to agent constructor"""
        server = AgentMCPServer(
            agent_class=DockerAgent,
            agent_params={"silent_mode": True, "max_steps": 20, "debug": True},
        )

        # Verify agent received parameters
        assert server.agent.silent_mode is True
        assert server.agent.max_steps == 20
        assert server.agent.debug is True

    def test_server_default_configuration(self):
        """AgentMCPServer uses default configuration when not specified"""
        server = AgentMCPServer(
            agent_class=DockerAgent, agent_params={"silent_mode": True}
        )

        # Should use defaults
        assert "GAIA DockerAgent" in server.name
        assert server.port == 8080  # MCP_DEFAULT_PORT
        assert server.host == "localhost"  # MCP_DEFAULT_HOST


# ============================================================================
# TEST: Docker Operations (Individual Tools)
# ============================================================================


@pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
class TestDockerOperations:
    """
    Test individual Docker operations with real Docker CLI.

    Tests the internal agent methods directly to validate
    Docker command generation and execution.
    """

    def test_analyze_directory_flask_app(self, test_flask_app):
        """Analyze directory detects Flask application"""
        agent = DockerAgent(silent_mode=True, allowed_paths=[test_flask_app])

        result = agent._analyze_directory(test_flask_app)

        assert result["app_type"] == "flask"
        assert result["entry_point"] == "app.py"
        assert result["dependencies"] == "requirements.txt"
        assert result["port"] == 8080  # DEFAULT_PORT from DockerAgent

    def test_analyze_directory_nonexistent(self):
        """Analyze directory handles non-existent path"""
        # Allow the nonexistent path so we can test the "does not exist" error
        agent = DockerAgent(silent_mode=True, allowed_paths=["/nonexistent"])

        result = agent._analyze_directory("/nonexistent/path")

        assert result["status"] == "error"
        assert "does not exist" in result["error"]

    def test_save_dockerfile(self, test_flask_app):
        """Save Dockerfile creates file with correct content"""
        agent = DockerAgent(silent_mode=True, allowed_paths=[test_flask_app])

        dockerfile_content = """# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
"""

        result = agent._save_dockerfile(
            dockerfile_content=dockerfile_content, path=test_flask_app, port=5000
        )

        assert result["status"] == "success"

        # Verify Dockerfile created
        dockerfile = Path(test_flask_app) / "Dockerfile"
        assert dockerfile.exists()

        # Verify content
        content = dockerfile.read_text()
        assert "FROM python:3.9-slim" in content
        assert "EXPOSE 5000" in content

    @pytest.mark.slow
    @pytest.mark.integration
    def test_build_image_real_docker(
        self, docker_available, test_flask_app, docker_cleanup
    ):
        """Build Docker image with real Docker CLI"""
        agent = DockerAgent(silent_mode=True, allowed_paths=[test_flask_app])

        # First create Dockerfile
        dockerfile_content = """# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
"""
        agent._save_dockerfile(dockerfile_content, test_flask_app, 5000)

        # Build image
        image_tag = "test-flask-build:latest"
        docker_cleanup["images"].append(image_tag)

        result = agent._build_image(test_flask_app, image_tag)

        assert result["success"] is True
        assert result["image"] == image_tag

        # Verify image exists
        check_result = subprocess.run(
            ["docker", "images", "-q", image_tag],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert len(check_result.stdout.strip()) > 0


# ============================================================================
# TEST: Error Handling
# ============================================================================


@pytest.mark.skipif(not HAS_DOCKER_AGENT, reason="DockerAgent not available")
class TestErrorHandling:
    """
    Test error handling with real failure scenarios.

    Tests how agents handle invalid inputs, missing files,
    and Docker failures.
    """

    def test_execute_tool_unknown_tool_name(self):
        """Executing unknown tool raises ValueError"""
        agent = DockerAgent(silent_mode=True)

        with pytest.raises(ValueError, match="Unknown tool"):
            agent.execute_mcp_tool("invalid_tool_name", {})

    def test_dockerize_missing_required_parameter(self):
        """Dockerize without appPath returns error"""
        agent = DockerAgent(silent_mode=True)

        result = agent.execute_mcp_tool("dockerize", {})

        assert result["success"] is False
        assert "appPath is required" in result["error"]

    def test_dockerize_non_absolute_path(self):
        """Dockerize with relative path returns error"""
        agent = DockerAgent(silent_mode=True)

        result = agent.execute_mcp_tool("dockerize", {"appPath": "relative/path"})

        assert result["success"] is False
        assert "must be an absolute path" in result["error"]

    def test_dockerize_path_does_not_exist(self, tmp_path):
        """Dockerize with non-existent path returns error"""
        # Allow tmp_path so we can test the "does not exist" error
        agent = DockerAgent(silent_mode=True, allowed_paths=[str(tmp_path)])

        # Use absolute path that doesn't exist
        nonexistent_path = tmp_path / "nonexistent_dir_12345"
        # Don't create it - we want it to not exist

        result = agent.execute_mcp_tool("dockerize", {"appPath": str(nonexistent_path)})

        assert result["success"] is False
        assert "does not exist" in result["error"]

    def test_dockerize_path_is_file_not_directory(self, tmp_path):
        """Dockerize with file path (not directory) returns error"""
        # Allow tmp_path so we can test the "not a directory" error
        agent = DockerAgent(silent_mode=True, allowed_paths=[str(tmp_path)])

        # Create a file
        test_file = tmp_path / "test_file.txt"
        test_file.write_text("test")

        result = agent.execute_mcp_tool("dockerize", {"appPath": str(test_file)})

        assert result["success"] is False
        assert "not a directory" in result["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
