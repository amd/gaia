# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Pytest configuration file for GAIA test suite.

This file (conftest.py) is a special pytest file that provides:
- Shared fixtures available to ALL tests in the test suite
- Custom pytest command-line options
- Test session configuration

See: https://docs.pytest.org/en/stable/reference/fixtures.html#conftest-py-sharing-fixtures-across-multiple-files

Current fixtures:
- api_server: Function-scoped fixture that starts GAIA API server for integration tests
- api_client: HTTP client (requests.Session) configured for API testing
- lemonade_available: Session-scoped fixture checking if Lemonade server is running
- require_lemonade: Fixture that skips tests if Lemonade is not available

Current options:
- --hybrid: Run tests with hybrid configuration (cloud + local models)

To add new fixtures for other test suites, define them in this file and they'll
be automatically available to all test files.
"""

import subprocess
import time

import pytest
import requests


def pytest_addoption(parser):
    parser.addoption(
        "--hybrid",
        action="store_true",
        default=False,
        help="Run with hybrid configuration (default: False)",
    )


# =============================================================================
# LEMONADE SERVER FIXTURES
# =============================================================================


@pytest.fixture(scope="session")
def lemonade_available():
    """
    Check if Lemonade server is available and healthy.

    This is a session-scoped fixture that checks once at the start of the
    test session whether Lemonade server is running on localhost:8000.

    Returns:
        bool: True if Lemonade server is available and responding to health checks
    """
    try:
        response = requests.get("http://localhost:8000/api/v1/health", timeout=5)
        return response.status_code == 200
    except (requests.RequestException, requests.ConnectionError):
        return False


@pytest.fixture
def require_lemonade(lemonade_available):
    """
    Skip test if Lemonade server is not available.

    Use this fixture in integration tests that require actual LLM responses.

    Example:
        def test_chat_completion(self, require_lemonade, api_server, api_client):
            # This test will be skipped if Lemonade is not running
            ...
    """
    if not lemonade_available:
        pytest.skip("Lemonade server not available - skipping integration test")


@pytest.fixture(scope="function")
def api_server():
    """
    Start GAIA API server for each test.

    This fixture:
    1. Checks if API server is already running
    2. Starts server if not running
    3. Waits for server to be ready
    4. Cleans up after each test completes

    Returns:
        str: Base URL of the API server (http://localhost:8080)
    """
    api_url = "http://localhost:8080"
    server_process = None

    # Check if server is already running
    try:
        response = requests.get(f"{api_url}/health", timeout=2)
        if response.status_code == 200:
            print(f"API server already running at {api_url}")
            yield api_url
            return
    except (requests.RequestException, requests.ConnectionError):
        pass  # Server not running, will start it

    # Start API server with --no-lemonade-check to allow tests to run
    # even when Lemonade server is not available. Integration tests that
    # need actual LLM responses should use the require_lemonade fixture.
    print("Starting GAIA API server (with --no-lemonade-check)...")
    try:
        server_process = subprocess.Popen(
            ["gaia", "api", "start", "--no-lemonade-check"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("GAIA CLI not found. Install with: pip install -e .")

    # Wait for server to be ready (30 second timeout)
    start_time = time.time()
    timeout = 30
    server_ready = False

    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{api_url}/health", timeout=2)
            if response.status_code == 200:
                health_data = response.json()
                print(f"API server ready: {health_data}")
                server_ready = True
                break
        except (requests.RequestException, requests.ConnectionError):
            pass  # Server not ready yet

        # Check if process crashed
        if server_process and server_process.poll() is not None:
            stdout, stderr = server_process.communicate()
            pytest.skip(
                f"API server process terminated unexpectedly.\n"
                f"STDOUT: {stdout}\nSTDERR: {stderr}"
            )

        time.sleep(1)

    if not server_ready:
        if server_process:
            server_process.terminate()
            server_process.wait(timeout=5)
        pytest.skip(f"API server not ready after {timeout} seconds")

    # Yield to tests
    yield api_url

    # Cleanup - kill processes on port 8080 directly
    print("Stopping GAIA API server...")

    import platform

    system = platform.system()

    try:
        if system == "Windows":
            # Windows: Find and kill processes on port 8080
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            pids = set()
            for line in result.stdout.splitlines():
                if ":8080" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids.add(parts[-1])

            if pids:
                for pid in pids:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True,
                            timeout=5,
                            check=False,
                        )
                        print(f"Killed PID {pid}")
                    except Exception as e:
                        print(f"Failed to kill PID {pid}: {e}")
                print("✅ API server stopped")
            else:
                print("ℹ️ No server found on port 8080")
        else:
            # Linux/Mac: Use lsof to find and kill processes
            result = subprocess.run(
                ["lsof", "-ti", ":8080"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            pids = result.stdout.strip().split("\n")
            pids = [pid for pid in pids if pid]

            if pids:
                for pid in pids:
                    try:
                        import os
                        import signal

                        os.kill(int(pid), signal.SIGKILL)
                        print(f"Killed PID {pid}")
                    except Exception as e:
                        print(f"Failed to kill PID {pid}: {e}")
                print("✅ API server stopped")
            else:
                print("ℹ️ No server found on port 8080")
    except Exception as e:
        print(f"Warning during cleanup: {e}")

    # Also terminate our subprocess if we started it
    if server_process:
        try:
            server_process.kill()
            server_process.wait(timeout=2)
            print(f"Server process {server_process.pid} killed")
        except Exception as e:
            print(f"Warning: Failed to kill server process: {e}")


@pytest.fixture
def api_client(api_server):
    """
    HTTP client for API testing.

    Args:
        api_server: Session-scoped API server fixture

    Returns:
        requests.Session: Configured session for API requests
    """
    session = requests.Session()
    session.headers.update(
        {"Content-Type": "application/json", "Accept": "application/json"}
    )
    yield session
    session.close()


# --- NEW PIPELINE FIXTURES ---

import pytest
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from gaia.pipeline.state import PipelineContext, PipelineStateMachine, PipelineState
from gaia.pipeline.loop_manager import LoopManager, LoopConfig
from gaia.pipeline.decision_engine import DecisionEngine, DecisionType
from gaia.quality.scorer import QualityScorer
from gaia.agents.registry import AgentRegistry
from gaia.hooks.registry import HookRegistry, HookExecutor
from gaia.hooks.base import HookContext


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_context() -> PipelineContext:
    """Create a sample pipeline context for testing."""
    return PipelineContext(
        pipeline_id="test-pipeline-001",
        user_goal="Implement a REST API endpoint",
        template="STANDARD",
        quality_threshold=0.90,
        max_iterations=5,
        concurrent_loops=3,
    )


@pytest.fixture
def sample_state_machine(sample_context: PipelineContext) -> PipelineStateMachine:
    """Create a sample state machine for testing."""
    return PipelineStateMachine(sample_context)


@pytest.fixture
def sample_loop_config() -> LoopConfig:
    """Create a sample loop configuration for testing."""
    return LoopConfig(
        loop_id="test-loop-001",
        phase_name="DEVELOPMENT",
        agent_sequence=["senior-developer", "quality-reviewer"],
        exit_criteria={"quality_threshold": 0.90},
        quality_threshold=0.90,
        max_iterations=3,
        timeout_seconds=60,
    )


@pytest.fixture
def sample_loop_manager() -> LoopManager:
    """Create a sample loop manager for testing."""
    return LoopManager(max_concurrent=5)


@pytest.fixture
def sample_decision_engine() -> DecisionEngine:
    """Create a sample decision engine for testing."""
    return DecisionEngine(
        config={
            "critical_patterns": ["security", "data loss", "breaking change"]
        }
    )


@pytest.fixture
def sample_quality_scorer() -> QualityScorer:
    """Create a sample quality scorer for testing."""
    return QualityScorer()


@pytest.fixture
def sample_agent_registry(tmp_path) -> AgentRegistry:
    """Create a sample agent registry for testing."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    return AgentRegistry(agents_dir=str(agents_dir), auto_reload=False)


@pytest.fixture
def sample_hook_registry() -> HookRegistry:
    """Create a sample hook registry for testing."""
    return HookRegistry()


@pytest.fixture
def sample_hook_executor(sample_hook_registry: HookRegistry) -> HookExecutor:
    """Create a sample hook executor for testing."""
    return HookExecutor(sample_hook_registry)


@pytest.fixture
def sample_hook_context() -> HookContext:
    """Create a sample hook context for testing."""
    return HookContext(
        event="TEST_EVENT",
        pipeline_id="test-pipeline-001",
        phase="DEVELOPMENT",
        agent_id="test-agent",
        state={"key": "value"},
        data={"test_data": "test"},
    )


@pytest.fixture
def sample_code() -> str:
    """Sample Python code for testing."""
    return """
def add(a: int, b: int) -> int:
    '''Add two numbers.'''
    return a + b

def multiply(a: int, b: int) -> int:
    '''Multiply two numbers.'''
    return a * b

class Calculator:
    '''Simple calculator class.'''

    def __init__(self):
        self.result = 0

    def calculate(self, operation: str, a: int, b: int) -> int:
        '''Perform a calculation.'''
        if operation == 'add':
            self.result = add(a, b)
        elif operation == 'multiply':
            self.result = multiply(a, b)
        return self.result
"""


@pytest.fixture
def sample_code_with_issues() -> str:
    """Sample Python code with quality issues for testing."""
    return """
def add(a,b):
    return a+b

def multiply(a,b):
    return a*b

# No docstrings
# No type hints
# Inconsistent spacing

class Calculator:
    def __init__(self):
        self.result=0

    def calculate(self,operation,a,b):
        if operation=='add':
            self.result=add(a,b)
        elif operation=='multiply':
            self.result=multiply(a,b)
        return self.result
"""


@pytest.fixture
def sample_requirements() -> list:
    """Sample requirements for testing."""
    return [
        "Create a REST API endpoint for user management",
        "Implement CRUD operations for users",
        "Add input validation for user data",
        "Include error handling for all endpoints",
    ]


@pytest.fixture
def sample_quality_context() -> Dict[str, Any]:
    """Sample context for quality evaluation."""
    return {
        "requirements": ["Build a REST API"],
        "language": "python",
        "template": "STANDARD",
    }
