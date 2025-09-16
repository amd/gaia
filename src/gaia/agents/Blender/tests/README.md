# Blender Agent Tests

This directory contains tests for the Blender Agent, including both unit tests and integration tests.

## Running Tests

### Unit Tests

To run unit tests only:

```bash
pytest -xvs src/gaia/agents/blender/tests/test_agent_v1.py -k "not integration"
```

### Integration Tests

Integration tests require a running MCP server (Blender with the MCP add-on). Before running integration tests:

1. Start Blender with the MCP add-on enabled
2. Ensure the MCP server is running on port 9876

Then run:

```bash
pytest -xvs src/gaia/agents/blender/tests/test_agent_v1.py -k "integration"
```

Integration tests will automatically be skipped if the MCP server is not running.

### All Tests

To run all tests:

```bash
pytest -xvs src/gaia/agents/blender/tests/test_agent_v1.py
```

To skip integration tests regardless of whether MCP server is running:

```bash
pytest -xvs src/gaia/agents/blender/tests/test_agent_v1.py --skip-integration
```

## Test Structure

- `test_agent_v1.py` - Tests for the BlenderAgentSimple class
- `conftest.py` - Pytest fixtures and configuration
- `test_mcp_client.py` - Tests for the MCPClient
- `test_mcp.py` - Lower-level MCP tests

## Integration Test Notes

Integration tests are marked with the `@pytest.mark.integration` decorator and require a running MCP server. The tests use a real LLMClient with a special system prompt that provides predictable responses for testing purposes.

For example, when asked to create a cube, the LLM will always respond with `CUBE,1,2,3,0.5,1,1.5`, which allows us to make assertions about the expected result.

## CLI Integration Testing

The Blender agent is now integrated into the main CLI. To test the CLI integration:

```bash
# Test Blender CLI command help
gaia blender --help

# Test a simple Blender example (requires MCP server running)
gaia blender --example 1

# Test interactive mode (requires MCP server running)
gaia blender --interactive
```

Note: CLI integration tests require both the Lemonade server and the Blender MCP server to be running. The CLI will automatically check for both servers and provide setup instructions if either is missing.