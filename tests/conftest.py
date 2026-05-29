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
- in_memory_keyring: Session-scoped fixture installing an in-memory keyring backend
  (used by tests/unit/connectors/ to avoid SecretService prerequisite on Linux CI)
- ui_api_client: Function-scoped TestClient against gaia.ui.server.create_app()
- mock_lemonade_client: Shared mock for LemonadeClient (requires pytest-mock)

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
    test session whether Lemonade server is running on localhost:13305.

    Returns:
        bool: True if Lemonade server is available and responding to health checks
    """
    try:
        response = requests.get("http://localhost:13305/api/v1/health", timeout=5)
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


@pytest.fixture
def mock_lemonade_client(mocker):
    """Shared mock for LemonadeClient — avoids duplicating patch targets across test files."""
    return mocker.patch("gaia.llm.lemonade_client.LemonadeClient")


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


# =============================================================================
# CONNECTIONS / KEYRING FIXTURES (issue #915)
# =============================================================================


def _make_in_memory_keyring():
    """
    Build an in-memory keyring backend used by connections tests.

    Imported lazily so that ``import tests.conftest`` does not require keyring
    to be installed (e.g. for tests that don't need it).

    Avoids the production SecretService / Keychain / DPAPI dependency in CI
    while preserving the real keyring API contract:

    - get_password() returns None for missing entries
    - set_password() overwrites in place (atomic at the backend level — see
      A5 in the plan: this is what the single-blob store relies on)
    - delete_password() raises PasswordDeleteError for missing entries
    """
    import keyring.backend
    import keyring.errors

    class _InMemoryKeyring(keyring.backend.KeyringBackend):
        # Highest priority — keyring picks the backend with the largest
        # ``priority`` value, so this guarantees the test fixture wins over
        # any production backend that happens to be installed.
        priority = 99

        def __init__(self):
            self._store: dict[tuple[str, str], str] = {}

        def get_password(self, service, username):
            return self._store.get((service, username))

        def set_password(self, service, username, password):
            self._store[(service, username)] = password

        def delete_password(self, service, username):
            try:
                del self._store[(service, username)]
            except KeyError as e:
                raise keyring.errors.PasswordDeleteError(
                    f"No password for {service}:{username}"
                ) from e

    return _InMemoryKeyring()


@pytest.fixture(scope="session")
def in_memory_keyring():
    """
    Install an in-memory keyring backend for the duration of the test session.

    Use as a session-scoped dependency in connections tests. The autouse fixture
    in tests/unit/connectors/conftest.py wraps this to ensure every connections
    test has the in-memory backend before any gaia.connectors module is imported.

    Linux CI runners ship without SecretService, and the production-default
    keyrings.alt fallback is plaintext — we explicitly refuse that backend in
    gaia.connectors.store. This fixture short-circuits the keyring lookup
    chain to a deterministic in-memory backend that no production code uses.

    Yields:
        _InMemoryKeyring: the active backend (already installed via keyring.set_keyring)
    """
    import keyring

    backend = _make_in_memory_keyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


@pytest.fixture
def ui_api_client():
    """
    TestClient bound to the in-process gaia.ui.server FastAPI app.

    Use this — NOT the api_client fixture above — for any test that hits a
    /api/* route on the AgentUI server (port 4200 in production). api_client
    targets the OpenAI-compatible server at port 8080 and will silently 404
    on UI-server routes (see plan amendment A12).

    Skips the test if the [ui] extras are not installed.
    """
    try:
        from starlette.testclient import TestClient

        from gaia.ui.server import create_app
    except ImportError as e:
        pytest.skip(f"gaia.ui not importable (install with `[ui]` extras): {e}")

    app = create_app()
    with TestClient(app) as client:
        yield client
